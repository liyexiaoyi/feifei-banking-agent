"""AA is local bookkeeping only: these assertions never accept simulated payment."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE', 'rules')
    app = create_app(tmp_path / 'aa.db')
    with TestClient(app) as client:
        login(client)
        yield app, client


def login(client, user='demo'):
    response = client.post('/api/session', json={'user': user})
    assert response.status_code == 200
    client.headers['X-CSRF-Token'] = response.json()['csrf']


def body(**changes):
    return dict(total_cents=100, participant_ids=['p_mom', 'p_dad'],
                include_self=True, request_key='aa-create-first', confirmed=True, **changes)


def financial(app):
    with app.state.bank.transaction() as db:
        return {t: [tuple(x) for x in db.execute('SELECT * FROM '+t+' ORDER BY rowid')]
                for t in ('accounts', 'operations', 'transactions')}


def create(env, payload=None):
    app, client = env
    response = client.post('/api/aa', json=payload or body())
    assert response.status_code == 200, response.text
    return response.json()


def test_preview_is_exact_and_readonly(env):
    app, client = env
    before = financial(app)
    response = client.post('/api/aa/preview', json={k:v for k,v in body().items() if k not in ('confirmed','request_key')})
    assert response.status_code == 200, response.text
    data = response.json()
    assert [s['amount_cents'] for s in data['shares']] == [34,33,33]
    assert data['self_share_cents'] == 34 and data['receivable_cents'] == 66
    assert sum(s['amount_cents'] for s in data['shares']) == data['total_cents']
    assert data['shares'][0]['is_self'] is True and data['shares'][0]['participant_id'] == 'self'
    assert data['shares'][1]['name'] == '妈妈' and data['shares'][1]['masked_phone'] == '138****0001'
    assert '13800000001' not in json.dumps(data)
    assert data['simulated'] is True and '未发送' in data['notice']
    assert client.get('/api/aa').json()['requests'] == []
    assert financial(app) == before


def test_small_total_still_sums_and_zero_share_is_not_a_payment(env):
    app, client = env
    value = body();value['total_cents'] = 2
    item = create(env,value)
    assert [s['amount_cents'] for s in item['shares']] == [1,1,0]
    assert item['self_share_cents'] == 1 and item['receivable_cents'] == 1
    assert item['status'] == 'ACTIVE' and item['version'] == 1
    assert not any(x in json.dumps(item) for x in ('SUCCEEDED','transaction_id','已到账','已发送'))


def test_external_only_split_preserves_explicit_order(env):
    value = body();value.update(include_self=False,participant_ids=['p_dad','p_mom'],total_cents=101)
    item = create(env,value)
    assert [s['participant_id'] for s in item['shares']] == ['p_dad','p_mom']
    assert [s['amount_cents'] for s in item['shares']] == [51,50]
    assert item['self_share_cents'] == 0 and item['receivable_cents'] == 101


@pytest.mark.parametrize('change', [
    {'total_cents':True},{'total_cents':'100'},{'total_cents':1.5},{'total_cents':0},
    {'total_cents':100000001},{'include_self':'true'},{'include_self':1},
    {'participant_ids':[]},{'participant_ids':['p_mom','p_mom']},
    {'participant_ids':['p_mom'],'include_self':False},
    {'participant_ids':['p_guest']},{'participant_ids':['unknown']},
    {'participant_ids':['p_mom'],'user':'guest'},
    {'confirmed':False},{'confirmed':'true'}, {'request_key':'bad key'},
])
def test_invalid_or_unconfirmed_inputs_never_create_a_financial_operation(env,change):
    app, client = env;before=financial(app);value=body();value.update(change)
    response=client.post('/api/aa',json=value)
    assert response.status_code in (400,403,404,409,422),response.text
    assert financial(app)==before
    assert client.get('/api/aa').json()['requests']==[]


def test_create_and_replay_and_cancel_never_touch_bank_rows(env):
    app, client = env;before=financial(app)
    first=create(env);again=create(env)
    assert first['id']==again['id'] and again['replayed'] is True
    assert again['version']==1
    value=body();value['total_cents']=101
    assert client.post('/api/aa',json=value).status_code==409
    assert len(client.get('/api/aa').json()['requests'])==1
    cancelled=client.post('/api/aa/'+first['id']+'/cancel',json={'expected_version':1,'request_key':'aa-cancel-first'})
    assert cancelled.status_code==200,cancelled.text
    item=cancelled.json();assert item['status']=='CANCELLED' and item['version']==2
    repeat=client.post('/api/aa/'+first['id']+'/cancel',json={'expected_version':1,'request_key':'aa-cancel-first'})
    assert repeat.status_code==200 and repeat.json()['version']==2
    assert create(env)['status']=='CANCELLED'
    assert client.post('/api/aa/'+first['id']+'/cancel',json={'expected_version':1,'request_key':'aa-cancel-stale'}).status_code==409
    assert financial(app)==before


def test_personal_ownership_and_csrf(env):
    app,client=env;first=create(env);before=financial(app)
    login(client,'guest')
    assert client.get('/api/aa').json()['requests']==[]
    assert client.get('/api/aa/'+first['id']).status_code==404
    assert client.post('/api/aa/'+first['id']+'/cancel',json={'expected_version':1,'request_key':'guest-cancel-first'}).status_code==404
    login(client);client.headers.pop('X-CSRF-Token')
    assert client.post('/api/aa',json=body()).status_code==403
    assert financial(app)==before


def test_concurrent_same_key_has_one_immutable_request(env):
    app,_=env
    from ff_agent.aa_requests import AAService
    service=AAService(app.state.bank)
    def run(_):return service.create('demo',100,['p_mom','p_dad'],True,'parallel-aa-key',True)
    with ThreadPoolExecutor(max_workers=2) as pool:items=list(pool.map(run,range(2)))
    assert items[0]['id']==items[1]['id']
    assert len(service.list('demo'))==1


def test_restart_retains_cancelled_request_without_resurrection(env):
    app,client=env;item=create(env)
    client.post('/api/aa/'+item['id']+'/cancel',json={'expected_version':1,'request_key':'aa-cancel-restart'})
    from ff_agent.aa_requests import AAService
    from ff_agent.bank import Bank
    other=AAService(Bank(app.state.bank.path,clock=app.state.bank.clock))
    assert other.get('demo',item['id'])['status']=='CANCELLED'
    assert other.create('demo',100,['p_mom','p_dad'],True,'aa-create-first',True)['status']=='CANCELLED'


def test_twenty_participant_boundary_and_exact_remainder(env):
    app,client=env
    ids=['aa-person-'+str(i) for i in range(20)]
    with app.state.bank.transaction() as db:
        db.executemany('INSERT INTO payees VALUES(?,?,?,?,?)',[(pid,'demo','参与者'+str(i),'1360000'+str(i).zfill(4),'测试') for i,pid in enumerate(ids)])
    payload=body();payload.update(total_cents=101,participant_ids=ids[:19])
    item=create(env,payload)
    assert item['participant_count']==20 and sum(x['amount_cents'] for x in item['shares'])==101
    assert item['shares'][0]['amount_cents']==6
    assert all(x['amount_cents']==5 for x in item['shares'][1:])
    payload.update(participant_ids=ids,request_key='aa-too-many-people')
    assert client.post('/api/aa',json=payload).status_code in (400,422)


def test_local_request_keeps_its_original_person_snapshot(env):
    app,client=env;item=create(env)
    with app.state.bank.transaction() as db:
        db.execute("UPDATE payees SET name='更名后',phone='13900009999' WHERE id='p_mom'")
    response=client.get('/api/aa/'+item['id']);assert response.status_code==200
    shares=response.json()['shares']
    assert shares[1]['name']=='妈妈' and shares[1]['masked_phone']=='138****0001'
    assert create(env)['shares']==shares
    people=client.get('/api/aa/participants');assert people.status_code==200
    assert people.json()['participants'][0]['name']=='更名后'
    assert '13900009999' not in json.dumps(people.json())


def test_aa_audit_has_metadata_only_and_no_authorization(env):
    from datetime import datetime
    app,client=env
    app.state.bank.clock=lambda:datetime.fromisoformat('2026-10-28T12:00:00.138914+08:00')
    item=create(env)
    events=[e for e in app.state.bank.audit('demo') if e['event'].startswith('AA_')]
    assert events
    for event in events:
        assert set(event)=={'id','operation_id','event','detail','created_at'}
        assert type(event['id']) is int
        assert event['operation_id'] is None and event['event']=='AA_CREATED'
        # An opaque id or ISO microseconds may legitimately contain "138".
        # Exact allowed detail fields/values reject names, phones and participant ids.
        assert json.loads(event['detail'])=={'id':item['id'],'total_cents':item['total_cents'],'participant_count':item['participant_count']}
        datetime.fromisoformat(event['created_at'])
    assert client.post('/api/operations/'+item['id']+'/confirm',json={}).status_code==404


def test_cancel_key_cannot_alias_another_request(env):
    app,client=env;first=create(env)
    value=body();value['request_key']='aa-create-second';second=create(env,value)
    payload={'expected_version':1,'request_key':'aa-shared-cancel-key'}
    assert client.post('/api/aa/'+first['id']+'/cancel',json=payload).status_code==200
    assert client.post('/api/aa/'+second['id']+'/cancel',json=payload).status_code==409
    assert client.get('/api/aa/'+second['id']).json()['status']=='ACTIVE'


@pytest.mark.parametrize('phone',['1234567','123456','123','', '13800000009'])
def test_short_contact_numbers_are_not_reconstructed_from_the_mask(env,phone):
    app,client=env
    with app.state.bank.transaction() as db:
        db.execute('INSERT INTO payees VALUES(?,?,?,?,?)',('aa-short','demo','短号',phone,'测试'))
    payload=body();payload.update(participant_ids=['aa-short'],include_self=True)
    item=create(env,payload)
    listed=next(x for x in client.get('/api/aa/participants').json()['participants'] if x['id']=='aa-short')
    shown=[item['shares'][1]['masked_phone'],listed['masked_phone']]
    if len(phone)>=11:
        assert shown==['138****0009']*2
    else:
        assert all(''.join(c for c in mask if c.isdigit())=='' for mask in shown)

"""Local case progress never stands in for transaction reconciliation."""
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
import pytest
from ff_agent.api import create_app


class HandoffPlanner:
    mode='rules'
    def plan(self,text,*args):
        return [{'type':'transfer','payee':'朋友' if '朋友' in text else '妈妈','amount_cents':30000}]


def login(client,user='demo'):
    response=client.post('/api/session',json={'user':user})
    client.headers['X-CSRF-Token']=response.json()['csrf']


def prepare(client,key='handoff-case',text='给妈妈转300元'):
    response=client.post('/api/chat',json={'text':text,'request_key':key})
    assert response.status_code==200,response.text
    plan=client.get('/api/plans').json()['plans'][0]
    url='/api/plans/'+plan['id']+'/handoff'
    response=client.post(url,json={'expected_version':plan['version']})
    assert response.status_code==200,response.text
    return url,response.json()['handoff']


@pytest.fixture
def case(tmp_path):
    app=create_app(tmp_path/'handoff.db',planner=HandoffPlanner())
    with TestClient(app) as client:
        login(client)
        url,summary=prepare(client)
        yield app,client,url,summary


def body(version=0,status='IN_REVIEW',key='progress-key-1'):
    return {'expected_version':version,'status':status,'request_key':key}


def test_progress_persists_original_summary_and_replay_uses_current_view(case):
    app,client,url,summary=case
    initial=client.get(url+'/progress')
    assert initial.status_code==200,initial.text
    assert initial.json()['status']=='OPEN' and initial.json()['version']==0
    first=client.post(url+'/progress',json=body())
    assert first.status_code==200,first.text
    assert first.json()['status']=='IN_REVIEW' and first.json()['version']==1
    closed=client.post(url+'/progress',json=body(1,'CLOSED','progress-key-2'))
    assert closed.status_code==200 and closed.json()['version']==2
    replay=client.post(url+'/progress',json=body()).json()
    assert replay['status']=='CLOSED' and replay['version']==2 and replay['replayed'] is True
    assert replay['receipt']==first.json()['receipt']
    assert len(replay['events'])==2
    assert replay['contacted_human'] is False and replay['simulated'] is True
    assert client.get(url).json()==summary
    assert client.get('/api/state').json()['handoffs']==[summary]
    assert client.post(url+'/progress',json=body(2,'IN_REVIEW','progress-key-3')).status_code==409
    assert client.post(url[:-len('/handoff')]+'/resume',json={'expected_version':client.get(url[:-len('/handoff')]).json()['version']}).status_code==409


def test_progress_cas_key_conflict_owner_and_csrf(case):
    app,client,url,summary=case
    token=client.headers.pop('X-CSRF-Token')
    assert client.post(url+'/progress',json=body()).status_code==403
    client.headers['X-CSRF-Token']=token
    assert client.post(url+'/progress',json=body()).status_code==200
    changed=client.post(url+'/progress',json=body(1,'CLOSED'))
    assert changed.status_code==409 and changed.json()['error']['code']=='IDEMPOTENCY_MISMATCH'
    stale=client.post(url+'/progress',json=body(0,'CLOSED','different-key'))
    assert stale.status_code==409 and stale.json()['error']['code']=='VERSION_CONFLICT'
    login(client,'guest')
    assert client.get(url+'/progress').status_code==404
    assert client.post(url+'/progress',json=body()).status_code==404
    guest_url,_=prepare(client,'guest-case','给朋友转300元')
    # Identical request_key is a separate namespace for the other owner.
    assert client.post(guest_url+'/progress',json=body()).status_code==200


@pytest.mark.parametrize('bad',[
    {'expected_version':True},{'expected_version':'0'},{'expected_version':-1},
    {'status':'OPEN'},{'status':'SUCCEEDED'}, {'request_key':'short'},
    {'request_key':'全角密钥测试测试'}, {'request_key':'key-with-\n'},
    {'contacted_human':True},{'actor':'bank'},{'note':'请银行接手'},
])
def test_progress_body_is_strict(case,bad):
    _,client,url,_=case
    response=client.post(url+'/progress',json={**body(),**bad})
    assert response.status_code==422,response.text


def test_get_does_not_create_progress_and_missing_case_is_404(case):
    app,client,url,_=case
    with app.state.bank.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM handoff_progress').fetchone()[0]==0
    assert client.get(url+'/progress').status_code==200
    with app.state.bank.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM handoff_progress').fetchone()[0]==0
    assert client.get('/api/plans/missing/handoff/progress').status_code==404


def test_progress_concurrent_same_version_has_only_one_event(case):
    app,client,url,summary=case
    from ff_agent.handoff_progress import HandoffProgress
    from ff_agent.bank import DomainError
    service=HandoffProgress(app.state.bank)
    def update(key):
        try:
            return service.update('demo',summary['plan_id'],0,'IN_REVIEW',key)['version']
        except DomainError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(update,['parallel-key-1','parallel-key-2']))
    assert sorted(map(str,results))==['1','VERSION_CONFLICT']
    assert len(service.get('demo',summary['plan_id'])['events'])==1


def test_closed_case_keeps_unknown_and_locked_financial_state_untouched(case):
    app,client,url,summary=case
    from ff_agent.handoff_progress import HandoffProgress
    bank=app.state.bank
    operation_id=summary['operations'][0]['id']
    with bank.transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=? AND user_id='demo'",(operation_id,))
        db.execute("UPDATE users SET locked_until=? WHERE id='demo'",(bank.now().timestamp()+600,))
        db.execute("INSERT INTO verification_streaks VALUES('demo',3,?)",(bank.now().timestamp(),))
    def financial_rows():
        with bank.transaction() as db:
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    if not r[0].startswith('sqlite_') and r[0] not in ('handoff_progress','handoff_events','audit')]
            return {name:[tuple(row) for row in db.execute('SELECT * FROM "'+name+'" ORDER BY rowid')] for name in tables}
    before=financial_rows()
    service=HandoffProgress(bank)
    result=service.update('demo',summary['plan_id'],0,'CLOSED','close-local-case')
    assert result['status']=='CLOSED' and result['contacted_human'] is False
    assert result['operations']==[{'id':operation_id,'type':'transfer','status':'UNKNOWN'}]
    assert before==financial_rows()
    with bank.transaction() as db:
        assert db.execute("SELECT status FROM plan_store_plans WHERE id=?",(summary['plan_id'],)).fetchone()[0]=='HANDOFF'
    assert client.get(url).json()==summary


def test_progress_survives_app_restart(case):
    app,client,url,summary=case
    assert client.post(url+'/progress',json=body()).status_code==200
    reopened=create_app(app.state.bank.path,planner=HandoffPlanner())
    with TestClient(reopened) as other:
        login(other)
        result=other.get(url+'/progress').json()
        assert result['status']=='IN_REVIEW' and result['version']==1
        assert other.post(url+'/progress',json=body()).json()['replayed'] is True
        assert other.get(url).json()==summary

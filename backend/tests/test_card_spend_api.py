"""Public preparation routes keep user/amount/merchant identity server-bound."""
import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app


@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    app=create_app(tmp_path/'card-api.db')
    with TestClient(app) as client:
        login(client)
        yield app,client


def login(client,user='demo'):
    response=client.post('/api/session',json={'user':user})
    assert response.status_code==200
    client.headers['X-CSRF-Token']=response.json()['csrf']


def body(**changes):
    return dict(merchant_id='demo_market',amount_cents=100,expected_card_version=1,expected_control_version=1,request_key='card-api-first')|changes


def finance(app):
    with app.state.bank.transaction() as db:
        return {t:[tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in ('accounts','operations','transactions','card_controls')}


def test_read_catalog_and_owned_controls_have_no_writes_and_original_cards_unchanged(env):
    app,c=env;before=finance(app)
    response=c.get('/api/card-spend/merchants')
    assert response.status_code==200,response.text
    assert all(x['simulated'] is True and x['merchant_connected'] is False for x in response.json()['merchants'])
    assert c.get('/api/card-controls').status_code==200
    assert c.get('/api/cards/card_a_daily/controls').json()['control_version']==1
    assert finance(app)==before
    assert set(c.get('/api/cards').json()['cards'][0])=={'id','account_id','name','masked_number','status','version','simulated'}


def test_prepare_confirm_and_replay_use_original_bank_ledger(env):
    app,c=env;before=finance(app)
    response=c.post('/api/cards/card_a_daily/spend',json=body())
    assert response.status_code==200,response.text
    op=response.json()
    assert op['type']=='card_spend' and op['status']=='WAITING_CONFIRMATION'
    assert finance(app)['accounts']==before['accounts'] and finance(app)['transactions']==before['transactions']
    done=c.post('/api/operations/'+op['id']+'/confirm',json={})
    assert done.status_code==200,done.text
    assert done.json()['status']=='SUCCEEDED'
    after=finance(app)
    assert c.post('/api/cards/card_a_daily/spend',json=body()).json()['id']==op['id']
    assert finance(app)==after
    with app.state.bank.transaction() as db:
        assert db.execute('SELECT count(*) FROM transactions WHERE operation_id=?',(op['id'],)).fetchone()[0]==1


def test_lock_api_requires_common_confirmation_verification_then_blocks_spend(env):
    app,c=env
    response=c.post('/api/cards/card_a_daily/controls',json={'action':'lock','value':True,'expected_version':1,'request_key':'api-lock-first'})
    assert response.status_code==200,response.text
    op=response.json();assert op['risk']=='RED'
    assert c.get('/api/cards/card_a_daily/controls').json()['locked'] is False
    assert c.post('/api/operations/'+op['id']+'/confirm',json={}).json()['status']=='WAITING_VERIFICATION'
    assert c.post('/api/operations/'+op['id']+'/verify',json={'code':'246810'}).json()['status']=='SUCCEEDED'
    before=finance(app)
    assert c.post('/api/cards/card_a_daily/spend',json=body(expected_control_version=2)).status_code==409
    assert finance(app)==before


@pytest.mark.parametrize('change',[{'amount_cents':True},{'amount_cents':1.0},{'amount_cents':'100'},{'amount_cents':0},{'merchant_name':'自定商户'},{'user_id':'guest'},{'risk':'GREEN'},{'expected_card_version':True},{'expected_control_version':0},{'request_key':'short'}])
def test_strict_body_cannot_override_identity_or_loosen_cents(env,change):
    app,c=env;before=finance(app)
    response=c.post('/api/cards/card_a_daily/spend',json=body(**change))
    assert response.status_code==422,response.text
    assert finance(app)==before


def test_foreign_card_operations_are_hidden_and_csrf_required(env):
    app,c=env;login(c,'guest')
    for path in ['/api/cards/card_a_daily/controls','/api/cards/card_a_daily/spend']:
        response=c.get(path) if path.endswith('controls') else c.post(path,json=body())
        assert response.status_code==404,response.text
    login(c)
    c.headers.pop('X-CSRF-Token')
    assert c.post('/api/cards/card_a_daily/spend',json=body()).status_code==403


@pytest.mark.parametrize('value',[1.0,'100',True])
def test_control_limit_strict_type_rejected(env,value):
    app,c=env
    response=c.post('/api/cards/card_a_daily/controls',json={'action':'set_single_limit','value':value,'expected_version':1,'request_key':'api-invalid-limit'})
    assert response.status_code in (400,422),response.text


@pytest.mark.parametrize('kind',['spend','controls'])
def test_card_prepare_honors_cards_technical_pause_and_does_not_reserve(env,kind):
    app,c=env;guard=app.state.tool_guard
    for _ in range(3): guard.finish(guard.start('demo','cards'),'tech')
    before=finance(app)
    data=body() if kind=='spend' else {'action':'lock','value':True,'expected_version':1,'request_key':'api-paused-lock'}
    response=c.post('/api/cards/card_a_daily/'+kind,json=data)
    assert response.status_code==503,response.text
    assert response.json()['error']['code']=='TOOL_PAUSED'
    assert finance(app)==before
    assert c.get('/api/cards/card_a_daily/controls').status_code==200


def test_prepared_card_operation_is_recorded_once_in_personal_history(env):
    app,c=env
    response=c.post('/api/cards/card_a_daily/spend',json=body())
    assert response.status_code==200,response.text
    opid=response.json()['id']
    assert c.post('/api/cards/card_a_daily/spend',json=body()).status_code==200
    with app.state.bank.transaction() as db:
        rows=db.execute('SELECT * FROM messages WHERE user_id=? AND request_key=?',('demo','direct-'+opid)).fetchall()
        assert len(rows)==1

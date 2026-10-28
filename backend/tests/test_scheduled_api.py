"""Session-bound scheduled routes: reads never drive future financial work."""
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app


@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    app=create_app(tmp_path/'schedule-api.db')
    with TestClient(app) as c:
        login(c)
        yield app,c


def login(c,user='demo'):
    z=c.post('/api/session',json={'user':user});assert z.status_code==200
    c.headers['X-CSRF-Token']=z.json()['csrf']


def body(app,**changes):
    return dict(payee_id='p_mom',amount_cents=123,due_at=(app.state.bank.now()+timedelta(hours=1)).replace(microsecond=0).isoformat(),request_key='api-schedule-first')|changes


def test_prepare_authorize_lookup_due_and_replay_have_correct_owner(env):
    app,c=env; data=body(app)
    z=c.post('/api/scheduled-transfers',json=data);assert z.status_code==200,z.text
    item=z.json();op=item['operation'];sid=item['schedule']['id']
    assert c.get('/api/scheduled-transfers/requests/'+data['request_key']).json()['id']==sid
    assert c.get('/api/scheduled-transfers').json()['schedules'][0]['id']==sid
    assert c.get('/api/scheduled-transfers/'+sid).json()['status']=='AUTHORIZING'
    assert c.post('/api/operations/'+op['id']+'/confirm',json={}).json()['status']=='WAITING_VERIFICATION'
    assert c.post('/api/operations/'+op['id']+'/verify',json={'code':'246810'}).json()['status']=='SUCCEEDED'
    assert c.post('/api/scheduled-transfers/run-due',json={}).json()['processed']==[]
    assert c.post('/api/scheduled-transfers',json=data).json()['operation']['id']==op['id']
    login(c,'guest')
    assert c.get('/api/scheduled-transfers/'+sid).status_code==404
    assert c.get('/api/scheduled-transfers/requests/'+data['request_key']).status_code==404
    assert c.get('/api/scheduled-transfers').json()['schedules']==[]


@pytest.mark.parametrize('change',[{'amount_cents':True},{'amount_cents':1.1},{'amount_cents':'123'},{'amount_cents':0},{'user_id':'guest'},{'risk':'GREEN'},{'status':'ACTIVE'},{'request_key':'short'},{'payee_id':1}])
def test_strict_api_body_no_identity_or_risk_override(env,change):
    app,c=env;z=c.post('/api/scheduled-transfers',json=body(app,**change))
    assert z.status_code==422,z.text
    with app.state.bank.transaction() as db:assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==0


def test_csrf_and_explicit_cancel_and_empty_due_body(env):
    app,c=env;z=c.post('/api/scheduled-transfers',json=body(app));assert z.status_code==200,z.text
    s=z.json()['schedule']
    path='/api/scheduled-transfers/'+s['id']+'/cancel'
    assert c.post(path,json={'expected_version':s['version'],'request_key':'cancel-api-first','confirmed':False}).status_code in (400,422)
    assert c.post('/api/scheduled-transfers/run-due',json={'user':'demo'}).status_code==422
    c.headers.pop('X-CSRF-Token')
    assert c.post('/api/scheduled-transfers/run-due',json={}).status_code==403
    login(c)
    done=c.post(path,json={'expected_version':s['version'],'request_key':'cancel-api-first','confirmed':True})
    assert done.status_code==200,done.text
    assert done.json()['status']=='CANCELLED'


def test_due_manual_child_is_recorded_for_real_confirmation_ui(env):
    app,c=env; z=c.post('/api/scheduled-transfers',json=body(app,amount_cents=100001))
    assert z.status_code==200,z.text
    op=z.json()['operation'];sid=z.json()['schedule']['id']
    assert c.post('/api/operations/'+op['id']+'/confirm',json={}).status_code==200
    assert c.post('/api/operations/'+op['id']+'/verify',json={'code':'246810'}).status_code==200
    future=app.state.bank.now()+timedelta(hours=1)
    app.state.bank.clock=lambda:future
    result=c.post('/api/scheduled-transfers/run-due',json={})
    assert result.status_code==200,result.text
    s=c.get('/api/scheduled-transfers/'+sid).json();cid=s['child_operation_id']
    assert s['child_status']=='WAITING_CONFIRMATION'
    with app.state.bank.transaction() as db:
        assert db.execute('SELECT count(*) FROM messages WHERE user_id=? AND request_key=?',('demo','direct-'+cid)).fetchone()[0]==1


def test_authorization_confirmation_uses_transfer_technical_gate(env):
    app,c=env;z=c.post('/api/scheduled-transfers',json=body(app));assert z.status_code==200
    for _ in range(3):app.state.tool_guard.finish(app.state.tool_guard.start('demo','transfer'),'tech')
    result=c.post('/api/operations/'+z.json()['operation']['id']+'/confirm',json={})
    assert result.status_code==503,result.text

from datetime import timedelta
import json

import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app


@pytest.fixture
def environment(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    app=create_app(tmp_path/'wealth-api.db')
    with TestClient(app) as client:
        login(client)
        yield app,client


def login(client,user='demo'):
    client.headers['X-CSRF-Token']=client.post('/api/session',json={'user':user}).json()['csrf']


def body(**changes):
    return {'account_id':'a_daily','product_id':'demo_cash','action':'purchase','amount_cents':10000,'request_key':'wealth-api','safety_epoch':'',**changes}

def test_green_wealth_query_does_not_create_operations(environment):
    app,client=environment
    response=client.get('/api/wealth');assert response.status_code==200
    assert response.json()['simulated'] is True and response.json()['positions']==[]
    assert app.state.bank.snapshot('demo')['operations']==[]

def test_wealth_request_requires_csrf_strict_payload_and_integer_amount(environment):
    app,client=environment;csrf=client.headers.pop('X-CSRF-Token')
    assert client.post('/api/wealth/prepare',json=body()).status_code==403
    client.headers['X-CSRF-Token']=csrf
    for changes in [{'confirmed':True},{'risk':'YELLOW'},{'units_1e4':100},{'user':'guest'},{'amount_cents':True},{'amount_cents':1.2}]:
        assert client.post('/api/wealth/prepare',json=body(**changes)).status_code==422
    assert app.state.bank.snapshot('demo')['operations']==[]

def test_wealth_http_requires_confirm_then_verify_and_records_one_ledger(assessed_environment):
    environment=assessed_environment
    app,client=environment;initial=app.state.bank.snapshot('demo')['accounts'][0]['balance_cents']
    response=client.post('/api/wealth/prepare',json=body());assert response.status_code==200
    op=response.json();assert op['risk']=='RED' and op['status']=='WAITING_CONFIRMATION'
    assert client.post('/api/operations/'+op['id']+'/verify',json={'code':'246810'}).status_code==409
    assert client.post('/api/operations/'+op['id']+'/confirm',json={}).json()['status']=='WAITING_VERIFICATION'
    done=client.post('/api/operations/'+op['id']+'/verify',json={'code':'246810'}).json()
    assert done['status']=='SUCCEEDED' and done['result']['amount_cents']==10000
    replay=client.post('/api/wealth/prepare',json=body()).json();assert replay['id']==op['id']
    assert client.post('/api/operations/'+op['id']+'/verify',json={'code':'246810'}).json()['result']==done['result']
    assert app.state.bank.snapshot('demo')['accounts'][0]['balance_cents']==initial-10000
    result=client.get('/api/wealth').json();assert result['positions'][0]['units_1e4']==1000000
    tools=client.get('/api/state').json()['tools'];assert any(t['group']=='wealth' for t in tools)

def test_wealth_http_checks_ownership_and_original_unknown_receipt(assessed_environment):
    environment=assessed_environment
    app,client=environment
    assert client.post('/api/wealth/prepare',json=body(account_id='b_daily')).status_code==404
    op=client.post('/api/wealth/prepare',json=body()).json()
    with app.state.bank.transaction() as db:db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?",(op['id'],))
    assert client.post('/api/wealth/prepare',json=body()).status_code==409
    assert client.get('/api/operations/'+op['id']+'/receipt').json()['observed_status']=='UNKNOWN'
    login(client,'guest')
    assert client.get('/api/wealth').json()['positions']==[]
    assert client.post('/api/operations/'+op['id']+'/confirm',json={}).status_code==404

def test_wealth_prepare_rejects_stale_epoch_after_hold_closed(assessed_environment):
    environment=assessed_environment
    app,client=environment
    hold=app.state.bank.activate_safety_hold('demo',{'codes':['SAFE_ACCOUNT'],'reasons':['测试'],'advice':'核实','hold':True},'hold')
    assert client.post('/api/wealth/prepare',json=body()).status_code==409
    app.state.bank.end_safety_hold('demo',hold['id'])
    assert client.post('/api/wealth/prepare',json=body()).status_code==409
    epoch=app.state.bank.snapshot('demo')['safety_epoch']
    assert client.post('/api/wealth/prepare',json=body(safety_epoch=epoch)).status_code==200


def test_wealth_recover_is_an_explicit_health_probe_without_new_trade(environment):
    app,client=environment
    response=client.post('/api/tools/wealth/recover',json={})
    assert response.status_code==200
    assert response.json()['tool']['group']=='wealth'
    assert app.state.bank.snapshot('demo')['operations']==[]


def test_wealth_applications_outside_recent_thirty_are_still_visible_for_recovery(assessed_environment):
    environment=assessed_environment
    app,client=environment;op=app.state.bank.prepare_wealth('demo','a_daily','demo_cash','purchase',10000,'unknown')
    with app.state.bank.transaction() as db:db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?",(op['id'],))
    for n in range(31):app.state.bank.prepare_transfer('demo','p_mom',100,'other-'+str(n))
    assert op['id'] not in [x['id'] for x in app.state.bank.snapshot('demo')['operations']]
    result=client.get('/api/wealth').json()
    assert any(x['id']==op['id'] and x['status']=='UNKNOWN' for x in result['applications'])
    login(client,'guest');assert client.get('/api/wealth').json()['applications']==[]


# Explicit prerequisite for existing financial tests; never an application default.
from demo_assessment import consent_test_profile as _consent_test_profile
import pytest as _pytest_assessment

@_pytest_assessment.fixture
def assessed_environment(environment):
    _consent_test_profile(environment[0].state.bank)
    return environment

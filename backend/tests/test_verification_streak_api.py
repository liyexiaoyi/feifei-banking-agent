from datetime import timedelta
import json

import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app


@pytest.fixture
def environment(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    app=create_app(tmp_path/'verification-api.db')
    with TestClient(app) as client:
        login(client)
        yield app,client


def login(client,user='demo'):
    client.headers['X-CSRF-Token']=client.post('/api/session',json={'user':user}).json()['csrf']


def prepared(client):
    definitions=[('/api/transfers/prepare',{'payee_id':'p_mom','amount_cents':200000,'request_key':'transfer','safety_epoch':''}),
      ('/api/cards/prepare',{'card_id':'card_a_daily','action':'report_lost','expected_version':1,'request_key':'card'}),
      ('/api/wealth/prepare',{'account_id':'a_daily','product_id':'demo_cash','action':'purchase','amount_cents':10000,'request_key':'wealth','safety_epoch':''})]
    ops=[]
    for path,body in definitions:
        r=client.post(path,json=body);assert r.status_code==200,r.text;op=r.json()
        response=client.post('/api/operations/'+op['id']+'/confirm',json={})
        assert response.status_code==200 and response.json()['status']=='WAITING_VERIFICATION';ops.append(op)
    return ops

def test_http_errors_share_user_counter_across_tools_and_relogin(assessed_environment):
    environment=assessed_environment
    app,client=environment;ops=prepared(client);before=[a['balance_cents'] for a in app.state.bank.snapshot('demo')['accounts']]
    for op in ops:
        r=client.post('/api/operations/'+op['id']+'/verify',json={'code':'000000'})
        assert r.status_code==403 and r.json()['error']['code']=='INVALID_VERIFICATION'
    assert '已' in r.json()['error']['message'] and '锁定' in r.json()['error']['message']
    login(client);state=client.get('/api/state').json();assert state['locked'] is True
    assert all(t['state']=='READY' for t in state['tools'])
    assert client.post('/api/cards/virtual/prepare',json={'account_id':'a_saving','request_key':'new-key'}).status_code==423
    assert client.post('/api/operations/'+ops[0]['id']+'/verify',json={'code':'246810'}).status_code==423
    assert client.get('/api/cards').status_code==200 and client.get('/api/wealth').status_code==200
    assert client.get('/api/bill?period=2026-10').status_code==200
    assert [a['balance_cents'] for a in app.state.bank.snapshot('demo')['accounts']]==before
    login(client,'guest');assert client.get('/api/state').json()['locked'] is False
    assert client.post('/api/operations/'+ops[0]['id']+'/verify',json={'code':'000000'}).status_code==404

def test_malformed_or_extra_verification_fields_are_not_counted(assessed_environment):
    environment=assessed_environment
    app,client=environment;op=prepared(client)[0]
    for body in [{'code':'abc123'},{'code':'000000','user':'guest'},{'code':'000000','confirmed':True}]:
        assert client.post('/api/operations/'+op['id']+'/verify',json=body).status_code==422
    assert client.get('/api/state').json()['locked'] is False
    with app.state.bank.transaction() as db:assert db.execute('SELECT COUNT(*) FROM verification_streaks').fetchone()[0]==0

@pytest.mark.parametrize('seconds,blocked',[(599.999,True),(600,False),(601,False)])
def test_exact_ten_minute_boundary_uses_fresh_operation_after_unlock(assessed_environment,seconds,blocked):
    environment=assessed_environment
    app,client=environment;now=app.state.bank.now();app.state.bank.clock=lambda:now
    ops=prepared(client)
    for op in ops:client.post('/api/operations/'+op['id']+'/verify',json={'code':'000000'})
    app.state.bank.clock=lambda:now+timedelta(seconds=seconds)
    r=client.post('/api/cards/virtual/prepare',json={'account_id':'a_saving','request_key':'after-cooldown'})
    assert r.status_code==(423 if blocked else 200)
    assert client.get('/api/state').json()['locked'] is blocked


def test_nonascii_digit_codes_cannot_escape_the_user_failure_counter(assessed_environment):
    environment=assessed_environment
    app,client=environment;ops=prepared(client)
    for op in ops:
        r=client.post('/api/operations/'+op['id']+'/verify',json={'code':'００００００'})
        assert r.status_code==403 and r.json()['error']['code']=='INVALID_VERIFICATION'
    assert client.get('/api/state').json()['locked'] is True



# Explicit prerequisite for existing financial tests; never an application default.
from demo_assessment import consent_test_profile as _consent_test_profile
import pytest as _pytest_assessment

@_pytest_assessment.fixture
def assessed_environment(environment):
    _consent_test_profile(environment[0].state.bank)
    return environment

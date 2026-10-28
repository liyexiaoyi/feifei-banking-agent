"""Even malformed secret requests must not use default validation input echoes."""
import json
import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app

SECRET='OnlyDemo_sensitive-934012'


@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    app=create_app(tmp_path/'secret-api.db')
    with TestClient(app) as c:
        login(c);yield app,c


def login(c,user='demo'):
    d=c.post('/api/session',json={'user':user}).json();c.headers['X-CSRF-Token']=d['csrf']


def body(**extra):return dict(new_secret=SECRET,expected_version=0,request_key='credential-api-first')|extra


def test_secret_prepare_only_public_reference_and_confirmation_never_echoes_secret(env):
    app,c=env
    before=c.get('/api/cards/card_a_daily/credential');assert before.status_code==200,before.text
    assert before.json()['configured'] is False
    response=c.post('/api/cards/card_a_daily/credential',json=body());assert response.status_code==200,response.text
    op=response.json();assert op['type']=='card_credential_change' and op['risk']=='RED' and 'challenge_id' in op['payload']
    assert SECRET not in response.text and not any(k in op['payload'] for k in ('salt','verifier','new_secret','current_secret'))
    assert c.get('/api/cards/card_a_daily/credential').json()['configured'] is False
    assert c.post('/api/operations/'+op['id']+'/confirm',json={}).json()['status']=='WAITING_VERIFICATION'
    done=c.post('/api/operations/'+op['id']+'/verify',json={'code':'246810'});assert done.status_code==200,done.text
    assert done.json()['result']['credential_version']==1 and SECRET not in done.text


@pytest.mark.parametrize('change',[{'new_secret':SECRET+' '*70},{'new_secret':[SECRET]},{'current_secret':[SECRET]},{'expected_version':SECRET},{'expected_version':True},{'extra_secret':SECRET},{'request_key':SECRET+'!'}])
def test_malformed_body_returns_sanitized_error_with_no_input_echo(env,change):
    app,c=env;response=c.post('/api/cards/card_a_daily/credential',json=body(**change))
    assert response.status_code==400,response.text
    assert SECRET not in response.text and 'input' not in response.text
    with app.state.bank.transaction() as db:assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==0


def test_oversized_or_invalid_json_never_echoes_raw_secret(env):
    app,c=env
    for raw in [SECRET+'{','{"new_secret":"'+SECRET+'"'+(' '*5000)+'}']:
        response=c.post('/api/cards/card_a_daily/credential',content=raw,headers={'Content-Type':'application/json'})
        assert response.status_code in (400,413),response.text
        assert SECRET not in response.text


def test_owned_exact_key_lookup_is_readonly_and_missing_does_not_create(env):
    app,c=env
    before=app.state.bank.snapshot('demo')['operations']
    assert c.get('/api/cards/card_a_daily/requests/credential-api-first').status_code==404
    assert app.state.bank.snapshot('demo')['operations']==before
    response=c.post('/api/cards/card_a_daily/credential',json=body());assert response.status_code==200,response.text
    op=response.json()
    lookup=c.get('/api/cards/card_a_daily/requests/credential-api-first');assert lookup.status_code==200,lookup.text
    assert lookup.json()['read_only'] is True and lookup.json()['operation']['id']==op['id']
    assert SECRET not in lookup.text
    assert c.post('/api/cards/card_a_daily/credential',json=body()).status_code==409
    assert c.get('/api/cards/card_a_saving/requests/credential-api-first').status_code==404
    login(c,'guest')
    assert c.get('/api/cards/card_a_daily/requests/credential-api-first').status_code==404


def test_secret_api_needs_csrf_and_honors_cards_pause(env):
    app,c=env;c.headers.pop('X-CSRF-Token')
    assert c.post('/api/cards/card_a_daily/credential',json=body()).status_code==403
    login(c)
    for _ in range(3):app.state.tool_guard.finish(app.state.tool_guard.start('demo','cards'),'tech')
    response=c.post('/api/cards/card_a_daily/credential',json=body());assert response.status_code==503,response.text
    assert SECRET not in response.text and c.get('/api/cards/card_a_daily/credential').status_code==200


def test_duplicate_secret_fields_and_query_values_are_not_echoed(env):
    app,c=env
    duplicate='{"new_secret":"'+SECRET+'","new_secret":"'+SECRET+'","expected_version":0,"request_key":"duplicate-secret-key"}'
    response=c.post('/api/cards/card_a_daily/credential',content=duplicate,headers={'Content-Type':'application/json'})
    assert response.status_code==400 and SECRET not in response.text
    response=c.post('/api/cards/card_a_daily/credential?extra='+SECRET,json=body())
    assert response.status_code==400 and SECRET not in response.text


def test_lookup_uses_expiry_observation_without_mutating_operation_or_candidate(env):
    from datetime import timedelta
    app,c=env
    response=c.post('/api/cards/card_a_daily/credential',json=body());assert response.status_code==200,response.text
    op=response.json();now=app.state.bank.now();app.state.bank.clock=lambda:now+timedelta(minutes=6)
    with app.state.bank.transaction() as db:
        before=[tuple(x) for x in db.execute('SELECT * FROM operations')]
        candidate=[tuple(x) for x in db.execute('SELECT * FROM card_credential_challenges')]
    response=c.get('/api/cards/card_a_daily/requests/credential-api-first');assert response.status_code==200,response.text
    assert response.json()['recorded_status']=='WAITING_CONFIRMATION' and response.json()['observed_status']=='EXPIRED'
    with app.state.bank.transaction() as db:
        assert [tuple(x) for x in db.execute('SELECT * FROM operations')]==before
        assert [tuple(x) for x in db.execute('SELECT * FROM card_credential_challenges')]==candidate

"""Sandbox HTTP boundary with a deterministic guest program fixture."""
import json
import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app
GENERATED_WAT='(module\n  (func (export "run") (param $a i64) (param $b i64) (result i64)\n    local.get $a\n    local.get $b\n    i64.add\n    i64.const 2\n    i64.div_s))\n'

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules');app=create_app(tmp_path/'sandbox-api.db')
    with TestClient(app) as client:
        login(client);yield app,client

def login(client,user='demo'):
    result=client.post('/api/session',json={'user':user});client.headers['X-CSRF-Token']=result.json()['csrf']

def body(**changes):return {'wat':GENERATED_WAT,'args':[13,7],'request_key':'sandbox-http-first',**changes}

def test_actual_provider_generated_wat_and_readonly_recovery(env):
    app,client=env
    response=client.post('/api/sandbox/runs',json=body());assert response.status_code==200
    value=response.json();assert value['status']=='SUCCEEDED' and value['result']['result']==10 and value['simulated'] is True
    assert client.get('/api/sandbox/runs/'+value['id']).json()==value
    assert client.get('/api/sandbox/by-key/sandbox-http-first').json()==value
    assert value in client.get('/api/sandbox/runs').json()['runs']
    assert client.post('/api/sandbox/runs',json=body()).json()==value
    login(client,'guest');assert client.get('/api/sandbox/runs/'+value['id']).status_code==404
    assert client.get('/api/sandbox/by-key/sandbox-http-first').status_code==404
    assert client.get('/api/sandbox/runs').json()['runs']==[]

@pytest.mark.parametrize('changes',[{'user':'guest'},{'worker':'other.py'},{'limits':{'wall_seconds':60}},{'args':[True]},{'args':[1.5]},{'args':[0]*9}],ids=['owner','path','limits','bool','float','many'])
def test_strict_body_cannot_escalate_capabilities(env,changes):
    app,client=env;response=client.post('/api/sandbox/runs',json=body(**changes))
    assert response.status_code==422


def test_session_origin_and_csrf_are_mandatory(env):
    app,client=env;csrf=client.headers.pop('X-CSRF-Token')
    assert client.post('/api/sandbox/runs',json=body()).status_code==403
    client.headers['X-CSRF-Token']=csrf
    assert client.post('/api/sandbox/runs',json=body(),headers={'Origin':'https://untrusted.example'}).status_code==403
    with app.state.bank.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM sandbox_runs').fetchone()[0]==0
    client.cookies.clear();assert client.get('/api/sandbox/runs').status_code==401


def test_malicious_guest_same_entry_has_no_financial_side_effect(env):
    app,client=env
    def state():
        with app.state.bank.transaction() as db:return {t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in ('accounts','operations','transactions')}
    before=state()
    wat='(module (import "env" "bank_transfer" (func)) (func (export "run") (result i64) i64.const 1))'
    response=client.post('/api/sandbox/runs',json=body(wat=wat,request_key='sandbox-malicious-key'))
    assert response.status_code==200;value=response.json()
    assert value['status']=='FAILED' and value['error']['code']=='IMPORT_DENIED' and value['result'] is None
    assert state()==before
    assert client.post('/api/sandbox/runs',json=body(request_key='sandbox-after-failure')).json()['status']=='SUCCEEDED'



@pytest.mark.parametrize('decimal',['9223372036854775807','-9223372036854775808'])
def test_large_integer_has_exact_decimal_display_text(env,decimal):
    app,client=env
    wat=f'(module (func (export "run") (result i64) i64.const {decimal}))'
    value=client.post('/api/sandbox/runs',json=body(wat=wat,args=[],request_key='sandbox-large-exact')).json()
    assert value['status']=='SUCCEEDED' and value['result']['result_text']==decimal

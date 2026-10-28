"""Generated-computation HTTP is opt-in, authenticated and model-origin explicit."""
import inspect
import json
import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app
from ff_agent.codegen_types import GeneratedWat,GenerationError

WAT='(module (func (export "run") (param i64) (result i64) local.get 0))'
class FixtureGenerator:
    provenance='fixture';available=True
    def __init__(self): self.calls=0;self.error=None
    def generate(self,intent,timeout_s=20):
        self.calls+=1
        if self.error:raise self.error
        return GeneratedWat(WAT,('value',))

def login(client,user='demo'):
    result=client.post('/api/session',json={'user':user});client.headers['X-CSRF-Token']=result.json()['csrf']

def request(**extra):return {'intent':{'kind':'calc','description':'返回整数参数','args':[713]},'generation_key':'gen-api-request-01','consent':True,**extra}

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    if 'codegen_generator' not in inspect.signature(create_app).parameters:
        yield None;return
    gen=FixtureGenerator();app=create_app(tmp_path/'gen-api.db',codegen_generator=gen,allow_codegen_fixture=True)
    with TestClient(app) as client:
        login(client);yield app,client,gen

def test_generator_default_unconnected_old_application_healthy(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    with TestClient(create_app(tmp_path/'default.db')) as client:
        login(client);assert client.get('/api/health').status_code==200
        response=client.get('/api/sandbox/generation-status')
        assert response.status_code==200,'default code generation status endpoint missing'
        assert response.json()=={'available':False,'provenance':None,'simulated':True}
        assert client.post('/api/sandbox/generate',json=request()).status_code==503

def test_fixture_entire_http_generation_executes_real_sandbox_only_once(env):
    assert env is not None,'trusted server codegen registration missing'
    app,client,gen=env
    response=client.post('/api/sandbox/generate',json=request());assert response.status_code==200
    a=response.json();assert a['generation']['status']=='SUCCEEDED' and a['run']['result']['result_text']=='713'
    assert a['generation']['provenance']=='fixture' and response.headers['X-FF-Session-Owner']=='demo'
    assert client.post('/api/sandbox/generate',json=request()).json()==a and gen.calls==1
    assert client.get('/api/sandbox/generate/by-key/gen-api-request-01').json()==a
    assert client.get('/api/sandbox/generations/'+a['generation']['id']).json()==a
    assert a in client.get('/api/sandbox/generations').json()['generations']
    login(client,'guest')
    assert client.get('/api/sandbox/generate/by-key/gen-api-request-01').status_code==404
    assert client.get('/api/sandbox/generations/'+a['generation']['id']).status_code==404
    assert client.get('/api/sandbox/generations').json()['generations']==[]

@pytest.mark.parametrize('extra',[{'consent':False},{'consent':'true'},{'user':'guest'},{'provenance':'model'},
    {'model':'opus'},{'worker':'x.py'},{'intent':{'kind':'calc','description':'x','args':[True]}},
    {'intent':{'kind':'transfer','description':'x','args':[]}},{'intent':{'kind':'calc','description':'x','args':[],'amount_cents':100}},
    {'generation_key':'x'},{'generation_key':'a'*101}])
def test_invalid_or_unconsented_generation_cannot_spend_or_execute(env,extra):
    assert env is not None,'trusted server codegen registration missing'
    app,client,gen=env;response=client.post('/api/sandbox/generate',json=request(**extra))
    assert response.status_code in (400,422) and gen.calls==0
    with app.state.bank.transaction() as db:assert db.execute('SELECT COUNT(*) FROM sandbox_runs').fetchone()[0]==0

def test_generation_requires_owner_csrf_and_origin_before_payment(env):
    assert env is not None,'trusted server codegen registration missing'
    _,client,gen=env;csrf=client.headers.pop('X-CSRF-Token')
    assert client.post('/api/sandbox/generate',json=request()).status_code==403
    client.headers['X-CSRF-Token']=csrf
    assert client.post('/api/sandbox/generate',json=request(),headers={'Origin':'https://evil.invalid'}).status_code==403
    client.cookies.clear();assert client.post('/api/sandbox/generate',json=request()).status_code==401
    assert gen.calls==0

def test_unknown_generation_recovery_never_calls_again(env):
    assert env is not None,'trusted server codegen registration missing'
    _,client,gen=env;gen.error=GenerationError('SENT_UNKNOWN','HTTP_524')
    a=client.post('/api/sandbox/generate',json=request()).json()
    assert a['generation']['status']=='SENT_UNKNOWN' and a['run'] is None
    assert client.get('/api/sandbox/generate/by-key/gen-api-request-01').json()==a
    assert client.post('/api/sandbox/generate',json=request()).json()==a and gen.calls==1

def test_codegen_health_recovery_is_explicit_and_does_not_generate(env):
    assert env is not None,'trusted server codegen registration missing'
    _,client,gen=env
    response=client.post('/api/tools/codegen/recover',json={})
    assert response.status_code==200 and response.json()['tool']['group']=='codegen'
    assert gen.calls==0

import time
from fastapi.testclient import TestClient
import pytest
from ff_agent.api import create_app
from test_channel_bridge import SECRET,frame,sign
H='X-FF-Session-Owner'
@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules');app=create_app(tmp_path/'owner.db',channel_config={'enabled':True,'keys':{'demo-im':SECRET},'identities':{('demo-im','ext'):'demo'}})
    with TestClient(app) as c:yield app,c

def test_request_identity_header_is_never_reflected_without_auth(env):
    app,c=env
    for path in ['/api/health','/api/session','/api/state']:
        z=c.get(path,headers={H:'guest'});assert H not in z.headers

def test_new_and_loaded_session_headers_attest_server_owner_not_client_header(env):
    app,c=env;z=c.post('/api/session',json={'user':'demo'},headers={H:'guest'});assert z.headers.get(H)=='demo'
    assert c.get('/api/session',headers={H:'guest'}).headers.get(H)=='demo'
    assert c.get('/api/state',headers={H:'guest'}).headers.get(H)=='demo'

def test_owner_proof_follows_actual_cookie_on_failed_action(env):
    app,c=env;first=c.post('/api/session',json={'user':'demo'}).json();c.post('/api/session',json={'user':'guest'})
    z=c.post('/api/chat',json={'text':'查余额','request_key':'wrong-csrf'},headers={'X-CSRF-Token':first['csrf'],H:'demo'})
    assert z.status_code==403 and z.headers.get(H)=='guest'

def test_protected_read_receipts_and_metadata_have_current_owner(env):
    app,c=env;c.post('/api/session',json={'user':'guest'})
    for p in ['/api/state','/api/history','/api/memories','/api/integrations','/api/safety/references','/api/channel/inbox','/api/operations/not-mine/receipt']:
        z=c.get(p);assert z.headers.get(H)=='guest',p

def test_expired_session_and_public_health_do_not_reuse_prior_response_identity(env):
    app,c=env;c.post('/api/session',json={'user':'demo'});assert c.get('/api/state').headers.get(H)=='demo'
    assert H not in c.get('/api/health').headers
    with app.state.bank.transaction() as db:db.execute('UPDATE sessions SET expires_at=?',(time.time()-1,))
    z=c.get('/api/session');assert z.status_code==401 and H not in z.headers

def test_machine_channel_frame_does_not_mint_customer_owner_proof(env):
    app,c=env;raw=frame(app.state.bank);z=c.post('/api/channel/demo',content=raw,headers={'X-FF-Demo-Signature':sign(raw),H:'guest'})
    assert z.status_code==200 and H not in z.headers

def test_independent_operator_endpoint_does_not_reuse_customer_identity(env):
    app,c=env;c.post('/api/session',json={'user':'demo'});z=c.get('/api/operator/session',headers={H:'demo'})
    assert H not in z.headers

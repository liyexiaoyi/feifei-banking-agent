import inspect,json
from fastapi.testclient import TestClient
import pytest
from ff_agent.api import create_app
from test_channel_bridge import SECRET,frame,sign

def app_for(tmp_path,enabled=True):
    assert 'channel_config' in inspect.signature(create_app).parameters,'I2 server channel config and routes absent'
    cfg={'enabled':True,'keys':{'demo-im':SECRET},'identities':{('demo-im','ext'):'demo'}} if enabled else None
    return create_app(tmp_path/'channel-api.db',channel_config=cfg)
def login(c,user='demo'):
    z=c.post('/api/session',json={'user':user});assert z.status_code==200;c.headers['X-CSRF-Token']=z.json()['csrf']
def post(c,app,**changes):
    raw=frame(app.state.bank,**changes);return c.post('/api/channel/demo',content=raw,headers={'X-FF-Demo-Signature':sign(raw)})

def test_default_off_routes_are_readonly_and_receiving_is_unavailable(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules');app=app_for(tmp_path,False)
    with TestClient(app) as c:
        assert c.get('/api/channel/status').status_code==401
        assert c.post('/api/channel/demo',content=b'{}').status_code in (404,405)
        login(c);status=c.get('/api/channel/status').json()
        assert status==dict(enabled=False,protocol='FF_LOCAL_DEMO_V1',simulated=True,official_connected=False)
        assert c.get('/api/channel/inbox').json()['items']==[]

def test_signed_machine_frame_without_session_creates_no_session_or_financial_authority(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules');app=app_for(tmp_path)
    with TestClient(app) as c:
        z=post(c,app);assert z.status_code==200,z.text;body=z.json()
        assert body['effect_authorized'] is False and body['official_connected'] is False
        assert not c.cookies.get('ff_session')
        with app.state.bank.transaction() as db:
            for t in ['sessions','messages','chat_requests','operations']:
                assert db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]==0
        assert c.get('/api/channel/inbox').status_code==401
        login(c);assert c.get('/api/channel/inbox').json()['items'][0]['id']==body['inbox_id']

@pytest.mark.parametrize('user',['guest','demo'])
def test_only_owner_reads_message_and_no_external_identity_echo(tmp_path,monkeypatch,user):
    monkeypatch.setenv('FF_PLANNER_MODE','rules');app=app_for(tmp_path)
    with TestClient(app) as c:
        body=post(c,app).json();login(c,user)
        z=c.get('/api/channel/inbox/'+body['inbox_id'])
        assert z.status_code==(200 if user=='demo' else 404)
        if user=='demo':assert 'external_user' not in z.json() and 'ext' not in z.json().values()
        assert SECRET.decode() not in z.text

@pytest.mark.parametrize('change',[{'user':'guest'},{'provider_status':'VERIFIED'},{'text':''}])
def test_untrusted_json_cannot_supply_owner_or_verification(tmp_path,monkeypatch,change):
    monkeypatch.setenv('FF_PLANNER_MODE','rules');app=app_for(tmp_path)
    with TestClient(app) as c:assert post(c,app,**change).status_code==400

def test_bad_signature_oversize_and_foreign_origin_are_rejected(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules');app=app_for(tmp_path)
    with TestClient(app) as c:
        raw=frame(app.state.bank)
        assert c.post('/api/channel/demo',content=raw).status_code==401
        assert c.post('/api/channel/demo',content=raw,headers={'X-FF-Demo-Signature':'0'*64}).status_code==401
        assert c.post('/api/channel/demo',content=b'x'*16385,headers={'X-FF-Demo-Signature':'0'*64}).status_code==413
        assert c.post('/api/channel/demo',content=raw,headers={'X-FF-Demo-Signature':sign(raw),'Origin':'https://evil.invalid'}).status_code==403

def test_replay_is_same_readonly_receipt_and_does_not_duplicate_inbox(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules');app=app_for(tmp_path)
    with TestClient(app) as c:
        raw=frame(app.state.bank);headers={'X-FF-Demo-Signature':sign(raw)}
        a=c.post('/api/channel/demo',content=raw,headers=headers).json();b=c.post('/api/channel/demo',content=raw,headers=headers).json()
        assert a['inbox_id']==b['inbox_id'] and b['delivery']=='DUPLICATE'
        login(c);assert len(c.get('/api/channel/inbox').json()['items'])==1

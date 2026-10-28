import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path/'card-api.db')) as c:
        yield c


def login(client, user='demo'):
    response=client.post('/api/session',json={'user':user})
    assert response.status_code==200
    client.headers['X-CSRF-Token']=response.json()['csrf']


def body(**changes):
    return {'card_id':'card_a_daily','action':'report_lost','expected_version':1,
            'request_key':'card-api',**changes}


def own(client):
    response=client.get('/api/cards')
    assert response.status_code==200
    assert response.json()['simulated'] is True
    return next(c for c in response.json()['cards'] if c['id']=='card_a_daily')


def test_cards_query_requires_session_and_lists_only_owned_masked_cards(client):
    assert client.get('/api/cards').status_code==401
    login(client)
    assert own(client)['status']=='ACTIVE'
    assert {c['id'] for c in client.get('/api/cards').json()['cards']}=={'card_a_daily','card_a_saving'}
    login(client,'guest')
    assert {c['id'] for c in client.get('/api/cards').json()['cards']}=={'card_b_daily','card_b_saving'}


def test_cards_prepare_uses_csrf_and_strict_immutable_request(client):
    login(client)
    csrf=client.headers.pop('X-CSRF-Token')
    assert client.post('/api/cards/prepare',json=body()).status_code==403
    client.headers['X-CSRF-Token']=csrf
    for changes in ({'expected_version':True},{'expected_version':0},{'action':'bypass'},
                    {'risk':'YELLOW'},{'verified':True},{'user':'guest'}):
        assert client.post('/api/cards/prepare',json=body(**changes)).status_code==422
    assert own(client)['status']=='ACTIVE'


def test_card_status_flow_requires_red_verification_and_replay_is_safe(client):
    login(client)
    start=client.get('/api/state').json()['accounts']
    prepared=client.post('/api/cards/prepare',json=body())
    assert prepared.status_code==200
    op=prepared.json()
    assert op['risk']=='RED' and op['status']=='WAITING_CONFIRMATION'
    url='/api/operations/'+op['id']
    assert client.post(url+'/verify',json={'code':'246810'}).status_code==409
    confirmed=client.post(url+'/confirm',json={})
    assert confirmed.status_code==200
    assert confirmed.json()['risk']=='RED'
    assert confirmed.json()['status']=='WAITING_VERIFICATION'
    assert own(client)['status']=='ACTIVE'
    assert client.post(url+'/verify',json={'code':'000000'}).status_code==403
    result=client.post(url+'/verify',json={'code':'246810'})
    assert result.status_code==200 and result.json()['status']=='SUCCEEDED'
    assert result.json()['result']['simulated'] is True
    assert own(client)['status']=='LOST' and own(client)['version']==2
    assert client.post('/api/cards/prepare',json=body()).json()['id']==op['id']
    assert client.post(url+'/verify',json={'code':'246810'}).json()['id']==op['id']
    assert client.get('/api/state').json()['accounts']==start
    receipt=client.get(url+'/receipt').json()
    assert receipt['read_only'] is True and receipt['observed_status']=='SUCCEEDED'


def test_foreign_card_and_operation_are_not_accessible(client):
    login(client)
    op=client.post('/api/cards/prepare',json=body()).json()
    login(client,'guest')
    assert client.post('/api/cards/prepare',json=body()).status_code==404
    for action,data in [('confirm',{}),('verify',{'code':'246810'}),('cancel',{})]:
        assert client.post('/api/operations/'+op['id']+'/'+action,json=data).status_code==404
    assert client.get('/api/operations/'+op['id']+'/receipt').status_code==404


def test_cancelled_card_change_never_executes(client):
    login(client)
    op=client.post('/api/cards/prepare',json=body()).json()
    url='/api/operations/'+op['id']
    assert client.post(url+'/cancel',json={}).json()['status']=='CANCELLED'
    assert client.post(url+'/confirm',json={}).status_code==409
    assert client.post(url+'/verify',json={'code':'246810'}).status_code==409
    assert own(client)['status']=='ACTIVE'


def test_card_tool_has_explicit_recovery_route(client):
    login(client)
    response=client.post('/api/tools/cards/recover',json={})
    assert response.status_code==200
    assert response.json()['tool']['group']=='cards'

from datetime import timedelta
import json

import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app


@pytest.fixture
def environment(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    app=create_app(tmp_path/'wealth-chat.db')
    with TestClient(app) as client:
        login(client)
        yield app,client


def login(client,user='demo'):
    client.headers['X-CSRF-Token']=client.post('/api/session',json={'user':user}).json()['csrf']


def say(client,text,key):
    response=client.post('/api/chat',json={'text':text,'request_key':key})
    assert response.status_code==200,response.text
    return response.json()


def ops(result):
    return [card['operation'] for card in result['cards'] if card['type']=='operation']


def pending(app):
    with app.state.bank.transaction() as db:
        row=db.execute("SELECT value FROM contexts WHERE user_id='demo'").fetchone()
        return json.loads(row[0]) if row else {}


def test_chat_query_only_reads_simulated_wealth(environment):
    app,client=environment;result=say(client,'查询理财持仓和收益','query')
    assert result['cards'][0]['type']=='wealth' and result['cards'][0]['wealth']['simulated'] is True
    assert app.state.bank.snapshot('demo')['operations']==[]

def test_chat_asks_product_then_account_then_amount_without_guessing(assessed_environment):
    environment=assessed_environment
    app,client=environment
    first=say(client,'申购','missing');assert '产品' in first['message'] and not ops(first)
    assert 'pending_wealth' in pending(app)
    plan=client.get('/api/plans').json()['plans'][0];assert plan['business']['awaiting_selection'] is True
    second=say(client,'演示产品甲','product');assert '账户' in second['message'] and not ops(second)
    third=say(client,'日常账户','account');assert '金额' in third['message'] and not ops(third)
    fourth=say(client,'三百块五','ambiguous');assert not ops(fourth)
    result=say(client,'300块钱','amount');op,=ops(result)
    assert op['type']=='wealth_purchase' and op['risk']=='RED' and op['status']=='WAITING_CONFIRMATION'
    assert op['payload']['amount_cents']==30000 and 'pending_wealth' not in pending(app)
    assert app.state.bank.wealth('demo')['positions']==[]

@pytest.mark.parametrize('invalidate',['session','ttl','cancel','other_topic','denial','short_stop'])
def test_old_wealth_slot_cannot_revive_trade(environment,invalidate):
    app,client=environment
    say(client,'日常账户买演示产品甲','missing-amount')
    assert 'pending_wealth' in pending(app)
    if invalidate=='session':login(client)
    elif invalidate=='ttl':
        now=app.state.bank.now();app.state.bank.clock=lambda:now+timedelta(seconds=601)
    elif invalidate=='cancel':
        plan=client.get('/api/plans').json()['plans'][0]
        assert client.post('/api/plans/'+plan['id']+'/cancel',json={'expected_version':plan['version']}).status_code==200
    elif invalidate=='other_topic':say(client,'查询余额','other')
    elif invalidate=='denial':say(client,'不要买理财','negative')
    else:say(client,'算了','stop')
    assert not ops(say(client,'300元','old-money'))
    assert app.state.bank.snapshot('demo')['operations']==[]

def test_actual_wealth_status_is_read_without_new_trade(assessed_environment):
    environment=assessed_environment
    app,client=environment;op,=ops(say(client,'日常账户买演示产品甲300元','prepare'))
    before=say(client,'刚才申购成功了吗','before');assert ops(before)[0]['id']==op['id'] and '已完成' not in before['message']
    client.post('/api/operations/'+op['id']+'/confirm',json={})
    client.post('/api/operations/'+op['id']+'/verify',json={'code':'246810'})
    after=say(client,'申购成功了吗','after');assert ops(after)[0]['status']=='SUCCEEDED'
    assert len(app.state.bank.snapshot('demo')['operations'])==1



# Explicit prerequisite for existing financial tests; never an application default.
from demo_assessment import consent_test_profile as _consent_test_profile
import pytest as _pytest_assessment

@_pytest_assessment.fixture
def assessed_environment(environment):
    _consent_test_profile(environment[0].state.bank)
    return environment

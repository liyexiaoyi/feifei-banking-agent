from datetime import timedelta
import json

import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app


@pytest.fixture
def environment(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    app=create_app(tmp_path/'virtual-api.db')
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


def test_virtual_prepare_boundary_is_strict_and_confirm_creates_one_card(environment):
    app,client=environment
    body={'account_id':'a_daily','request_key':'virtual-api'}
    csrf=client.headers.pop('X-CSRF-Token')
    assert client.post('/api/cards/virtual/prepare',json=body).status_code==403
    client.headers['X-CSRF-Token']=csrf
    for extra in ({'confirmed':True},{'risk':'GREEN'},{'card_number':'test'},{'user':'guest'}):
        assert client.post('/api/cards/virtual/prepare',json={**body,**extra}).status_code==422
    response=client.post('/api/cards/virtual/prepare',json=body)
    assert response.status_code==200
    op=response.json()
    assert op['risk']=='YELLOW' and op['status']=='WAITING_CONFIRMATION'
    assert len(client.get('/api/cards').json()['cards'])==2
    done=client.post('/api/operations/'+op['id']+'/confirm',json={})
    assert done.status_code==200 and done.json()['status']=='SUCCEEDED'
    assert len(client.get('/api/cards').json()['cards'])==3


def test_virtual_card_chat_missing_account_then_followup_keeps_yellow_confirmation(environment):
    app,client=environment
    first=say(client,'给我申请张虚拟卡','missing')
    assert not ops(first)
    assert first['cards'][0]['type']=='virtual_accounts'
    assert pending(app)['pending_virtual_card']['account']=='未指定'
    assert '尚未实现' not in first['message']
    op,=ops(say(client,'储蓄账户','account'))
    assert op['type']=='virtual_card_create' and op['payload']['account_id']=='a_saving'
    assert op['risk']=='YELLOW' and op['status']=='WAITING_CONFIRMATION'
    assert len(app.state.bank.list_cards('demo'))==2
    assert 'pending_virtual_card' not in pending(app)
    with app.state.bank.transaction() as db:
        assert db.execute('SELECT 1 FROM operation_plan_links WHERE operation_id=?',(op['id'],)).fetchone()


@pytest.mark.parametrize('invalidate',['session','ttl','cancel','other_topic','denial'])
def test_old_virtual_selector_cannot_prepare_after_context_invalidates(environment,invalidate):
    app,client=environment
    say(client,'申请虚拟卡','missing')
    assert 'pending_virtual_card' in pending(app)
    if invalidate=='session':login(client)
    elif invalidate=='ttl':
        now=app.state.bank.now();app.state.bank.clock=lambda:now+timedelta(seconds=601)
    elif invalidate=='cancel':
        plan=client.get('/api/plans').json()['plans'][0]
        assert plan['business']['awaiting_selection'] is True
        assert client.post('/api/plans/'+plan['id']+'/cancel',json={'expected_version':plan['version']}).status_code==200
    elif invalidate=='denial':say(client,'先不申请虚拟卡','no-apply')
    else:say(client,'查询余额','balance')
    assert not ops(say(client,'储蓄账户','old-choice'))
    assert app.state.bank.snapshot('demo')['operations']==[]


def test_virtual_applications_are_owned_and_not_hidden_after_thirty_other_operations(environment):
    app,client=environment
    op=app.state.bank.prepare_virtual_card('demo','a_daily','unknown')
    with app.state.bank.transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?",(op['id'],))
    for index in range(31):app.state.bank.prepare_transfer('demo','p_mom',100,f't-{index}')
    result=client.get('/api/cards').json()
    assert result['virtual_applications'][0]['id']==op['id']
    assert result['virtual_applications'][0]['status']=='UNKNOWN'
    login(client,'guest')
    assert client.get('/api/cards').json()['virtual_applications']==[]


def test_virtual_creation_status_question_reads_actual_application_without_repeating_it(environment):
    app,client=environment
    op,=ops(say(client,'给日常账户申请虚拟卡','apply'))
    result=say(client,'刚才虚拟卡申请成功了吗','before-confirm')
    assert ops(result)[0]['id']==op['id'] and '已完成' not in result['message']
    client.post('/api/operations/'+op['id']+'/confirm',json={})
    result=say(client,'刚才虚拟卡申请成功了吗','after-confirm')
    assert ops(result)[0]['status']=='SUCCEEDED' and '已完成' in result['message']
    assert len(app.state.bank.snapshot('demo')['operations'])==1


def test_virtual_operation_plan_cancel_does_not_create_a_card(environment):
    app,client=environment
    op,=ops(say(client,'给储蓄账户申请虚拟卡','apply'))
    plan=client.get('/api/plans').json()['plans'][0]
    assert client.post('/api/plans/'+plan['id']+'/cancel',json={'expected_version':plan['version']}).status_code==200
    assert app.state.bank.operation('demo',op['id'])['status']=='CANCELLED'
    assert len(app.state.bank.list_cards('demo'))==2

@pytest.mark.parametrize('question',['虚拟卡申请成功了吗','虚拟银行卡申请完成了吗','我的虚拟卡开通了吗'])
def test_virtual_status_question_does_not_require_a_recency_word(environment,question):
    app,client=environment
    op,=ops(say(client,'给日常账户申请虚拟卡','apply'))
    result=say(client,question,'query')
    assert ops(result)[0]['id']==op['id']
    assert ops(result)[0]['status']=='WAITING_CONFIRMATION'
    assert len(app.state.bank.snapshot('demo')['operations'])==1

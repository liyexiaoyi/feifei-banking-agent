"""Recovery channels remain available without replaying bank writes."""
from datetime import timedelta

import pytest

from fastapi.testclient import TestClient

from ff_agent.api import create_app


def login(client,user='demo'):
    response=client.post('/api/session',json={'user':user})
    client.headers['X-CSRF-Token']=response.json()['csrf']


def dump(bank):
    with bank.transaction() as db:
        return '\n'.join(db.iterdump())


def test_receipt_is_read_only_even_when_pending_operation_is_expired(tmp_path):
    app=create_app(tmp_path/'receipt.db')
    bank=app.state.bank
    with TestClient(app) as client:
        login(client)
        operation=bank.prepare_transfer('demo','p_mom',30000,'one')
        current=bank.now()
        bank.clock=lambda:current+timedelta(minutes=10)
        before=dump(bank)
        response=client.get('/api/operations/'+operation['id']+'/receipt')
        assert response.status_code==200
        result=response.json()
        assert result['recorded_status']=='WAITING_CONFIRMATION'
        assert result['observed_status']=='EXPIRED'
        assert result['read_only'] is True and result['simulated'] is True
        assert dump(bank)==before


def test_completed_receipt_and_repeated_confirmation_never_create_new_transaction(tmp_path):
    app=create_app(tmp_path/'receipt.db')
    bank=app.state.bank
    with TestClient(app) as client:
        login(client)
        op=bank.prepare_transfer('demo','p_mom',30000,'one')
        executed=client.post('/api/operations/'+op['id']+'/confirm',json={}).json()
        before=dump(bank)
        receipt=client.get('/api/operations/'+op['id']+'/receipt')
        assert receipt.status_code==200
        assert receipt.json()['operation']['result']['transaction_id']==executed['result']['transaction_id']
        assert dump(bank)==before
        replay=client.post('/api/operations/'+op['id']+'/confirm',json={}).json()
        assert replay['result']['transaction_id']==executed['result']['transaction_id']
        assert dump(bank)==before


def test_receipt_ownership_and_no_session_access(tmp_path):
    app=create_app(tmp_path/'receipt.db')
    op=app.state.bank.prepare_transfer('demo','p_mom',30000,'one')
    with TestClient(app) as client:
        assert client.get('/api/operations/'+op['id']+'/receipt').status_code==401
        login(client,'guest')
        assert client.get('/api/operations/'+op['id']+'/receipt').status_code==404


def test_bill_failure_pauses_only_bill_and_recovery_requires_csrf(tmp_path,monkeypatch):
    app=create_app(tmp_path/'tools.db')
    bank=app.state.bank
    calls=[]
    original=bank.analyze
    def broken(*args):
        calls.append(1)
        raise RuntimeError('private-provider-failure')
    monkeypatch.setattr(bank,'analyze',broken)
    with TestClient(app,raise_server_exceptions=False) as client:
        login(client)
        for _ in range(3):
            response=client.get('/api/bill?period=2026-10')
            assert response.status_code==503
            assert 'private-provider-failure' not in response.text
        assert client.get('/api/bill?period=2026-10').json()['error']['code']=='TOOL_PAUSED'
        assert len(calls)==3
        state=client.get('/api/state').json()
        assert next(t for t in state['tools'] if t['group']=='bill')['state']=='PAUSED'
        assert client.get('/api/history').status_code==200
        assert client.get('/api/audit').status_code==200
        csrf=client.headers.pop('X-CSRF-Token')
        assert client.post('/api/tools/bill/recover',json={}).status_code==403
        client.headers['X-CSRF-Token']=csrf
        assert client.post('/api/tools/bill/recover',json={}).status_code==503
        current=bank.now();bank.clock=lambda:current+timedelta(seconds=31)
        monkeypatch.setattr(bank,'analyze',original)
        assert client.post('/api/tools/bill/recover',json={}).status_code==200
        assert client.get('/api/bill?period=2026-10').status_code==200
        state=client.get('/api/state').json()
        assert next(t for t in state['tools'] if t['group']=='bill')['state']=='READY'


def test_normal_business_rejection_does_not_trip_health(tmp_path):
    app=create_app(tmp_path/'tools.db')
    with TestClient(app) as client:
        login(client)
        for _ in range(4): assert client.get('/api/bill?period=not-a-month').status_code==400
        tools=client.get('/api/state').json()['tools']
        assert all(t['state']=='READY' and t['consecutive_failures']==0 for t in tools)


def test_fraud_request_is_processed_even_when_planner_is_paused(tmp_path):
    from ff_agent.planner import PlannerError
    class Broken:
        mode='model'
        calls=0
        def plan(self,*args):
            self.calls+=1
            raise PlannerError('MODEL_UNAVAILABLE','测试故障')
    planner=Broken()
    app=create_app(tmp_path/'tools.db',planner=planner)
    with TestClient(app,raise_server_exceptions=False) as client:
        login(client)
        for n in range(3):
            assert client.post('/api/chat',json={'text':'你好','request_key':str(n)}).status_code==503
        finding=client.post('/api/chat',json={'text':'客服让我先转钱验资，给妈妈转300元','request_key':'risk'})
        assert finding.status_code==200 and finding.json()['cards'][0]['type']=='safety'
        assert planner.calls==3
        state=client.get('/api/state').json()
        assert state['safety_hold']
        assert next(t for t in state['tools'] if t['group']=='planner')['state']=='PAUSED'


def test_completed_confirmation_remains_available_without_resetting_transfer_pause(tmp_path):
    app=create_app(tmp_path/'tools.db')
    bank=app.state.bank
    op=bank.prepare_transfer('demo','p_mom',30000,'one')
    done=bank.confirm('demo',op['id'])
    guard=app.state.tool_guard
    for _ in range(3):
        ticket=guard.start('demo','transfer');guard.finish(ticket,'tech')
    with TestClient(app) as client:
        login(client)
        response=client.post('/api/operations/'+op['id']+'/confirm',json={})
        assert response.status_code==200
        assert response.json()['result']['transaction_id']==done['result']['transaction_id']
        assert client.get('/api/operations/'+op['id']+'/receipt').status_code==200
        assert next(t for t in guard.snapshot('demo') if t['group']=='transfer')['state']=='PAUSED'


@pytest.mark.parametrize('site',['subscriptions','prepare_cancel'])
@pytest.mark.parametrize('failure',['business','paused','technical'])
def test_completed_query_receipt_survives_continuation_failures(tmp_path,monkeypatch,site,failure):
    from ff_agent.bank import DomainError
    from ff_agent.tool_guard import ToolPaused
    app=create_app(tmp_path/'completed-query.db');bank=app.state.bank
    parent=bank.prepare_subscription_query('demo','original-query',proposed_cancel='流光视频')
    bank.confirm('demo',parent['id'])
    calls=[]
    def forbidden(*args,**kwargs):
        calls.append(1)
        raise AssertionError('Completed query must not be confirmed or verified again')
    monkeypatch.setattr(bank,'confirm',forbidden);monkeypatch.setattr(bank,'verify',forbidden)
    if failure=='paused':
        original=app.state.tool_guard.start;starts=[0]
        def pause_at_site(user,group):
            starts[0]+=1
            if starts[0]==(1 if site=='subscriptions' else 2):raise ToolPaused('subscriptions',0,True)
            return original(user,group)
        monkeypatch.setattr(app.state.tool_guard,'start',pause_at_site)
    else:
        def broken(*args,**kwargs):
            if failure=='business':raise DomainError('SUBSCRIPTION_CONSENT_REQUIRED','需要新的查询授权',403)
            raise RuntimeError('private provider continuation failure')
        monkeypatch.setattr(bank,site,broken)
    with TestClient(app) as client:
        login(client)
        response=client.post('/api/operations/'+parent['id']+'/confirm',json={})
        assert response.status_code==200
        result=response.json()
        assert result['id']==parent['id'] and result['status']=='SUCCEEDED'
        assert result.get('notice') and 'next_operation' not in result
        assert 'private provider' not in str(result) and calls==[]
        assert bank.receipt('demo',parent['id'])['recorded_status']=='SUCCEEDED'
        assert len(bank.snapshot('demo')['operations'])==1


def test_after_prepare_failure_returns_parent_and_reuses_existing_child(tmp_path,monkeypatch):
    app=create_app(tmp_path/'continuation-after-write.db');bank=app.state.bank
    parent=bank.prepare_subscription_query('demo','original-query',proposed_cancel='流光视频')
    assert bank.receipt('demo',parent['id'])['operation']['status']!='SUCCEEDED'
    original=bank.prepare_cancel;attempts=[0]
    def interrupted(*args,**kwargs):
        child=original(*args,**kwargs);attempts[0]+=1
        if attempts[0]==1:raise RuntimeError('interrupted after preparation')
        return child
    monkeypatch.setattr(bank,'prepare_cancel',interrupted)
    original_confirm=bank.confirm;confirms=[]
    def tracked_confirm(user,opid,*args,**kwargs):
        assert opid==parent['id'] and not confirms,'Only the first actual confirm may execute the original query'
        assert bank.receipt(user,opid)['operation']['status']!='SUCCEEDED','Do not confirm completed parent again'
        confirms.append(opid)
        return original_confirm(user,opid,*args,**kwargs)
    monkeypatch.setattr(bank,'confirm',tracked_confirm)
    with TestClient(app) as client:
        login(client)
        first=client.post('/api/operations/'+parent['id']+'/confirm',json={})
        assert first.status_code==200 and first.json()['status']=='SUCCEEDED'
        assert first.json().get('notice')
        saved=bank.snapshot('demo')['operations']
        assert len(saved)==2
        child=next(op for op in saved if op['type']=='subscription_cancel')
        assert child['status']=='WAITING_CONFIRMATION'
        assert attempts==[1] and confirms==[parent['id']]
        again=client.post('/api/operations/'+parent['id']+'/confirm',json={})
        assert again.status_code==200 and again.json()['next_operation']['id']==child['id']
        assert len(bank.snapshot('demo')['operations'])==2
        assert all(s['status']=='ACTIVE' for s in bank.subscriptions('demo'))
        assert attempts==[1] and confirms==[parent['id']]

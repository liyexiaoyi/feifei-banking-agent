"""Regression cases from the independent D1 source review."""
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app
from ff_agent.bill_period import parse_bill_period

NOW=datetime(2026,10,28,10,tzinfo=timezone(timedelta(hours=8)))

@pytest.mark.parametrize('text',[
    '帮我看1至3月账单','1~3月账单','2026年1-3月账单','一到三月账单',
    '查一～三月账单','2026年1到3月的消费',
])
def test_abbreviated_month_range_requires_clarification(text):
    result=parse_bill_period(text,NOW)
    assert result.get('reason')=='multiple_periods',result

@pytest.mark.parametrize('text,period',[
    ('2026-03账单','2026-03'),('十二月账单','2026-12'),('2026年账单','2026'),
])
def test_single_period_control_still_supported(text,period):
    assert parse_bill_period(text,NOW)=={'period':period}

def login(client):
    response=client.post('/api/session',json={'user':'demo'})
    assert response.status_code==200
    client.headers['X-CSRF-Token']=response.json()['csrf']

def chat(client,text,key):
    response=client.post('/api/chat',json={'text':text,'request_key':key})
    assert response.status_code==200,response.text
    return response.json()

def test_correction_word_does_not_cancel_unrelated_api_operation(tmp_path):
    with TestClient(create_app(tmp_path/'api-origin.db')) as client:
        login(client)
        old=client.post('/api/transfers/prepare',json={'payee_id':'p_mom','amount_cents':30000,'request_key':'direct-unrelated'}).json()
        assert old['status']=='WAITING_CONFIRMATION'
        reply=chat(client,'还是给爸爸转200元','new-request-without-context')
        new=next(c['operation'] for c in reply['cards'] if c['type']=='operation')
        assert new['status']=='WAITING_CONFIRMATION'
        assert new['payload']['payee_id']=='p_dad' and new['payload']['amount_cents']==20000
        result=client.get('/api/operations/'+old['id']).json()
        assert result['status']=='WAITING_CONFIRMATION',result
        assert client.get('/api/state').json()['accounts'][0]['balance_cents']==2000000

def test_correction_in_new_session_preserves_other_session_operation(tmp_path):
    app=create_app(tmp_path/'session-origin.db')
    with TestClient(app) as client:
        login(client)
        old=chat(client,'给妈妈转300元','first-session-request')['cards'][0]['operation']
        login(client)  # A new session cookie without starting a second ASGI lifespan.
        reply=chat(client,'还是给爸爸转200元','second-session-request')
        new=next(c['operation'] for c in reply['cards'] if c['type']=='operation')
        assert new['status']=='WAITING_CONFIRMATION'
        assert new['payload']['payee_id']=='p_dad' and new['payload']['amount_cents']==20000
        result=client.get('/api/operations/'+old['id']).json()
        assert result['status']=='WAITING_CONFIRMATION',result
        assert client.get('/api/state').json()['accounts'][0]['balance_cents']==2000000

def test_explicit_same_session_correction_control(tmp_path):
    with TestClient(create_app(tmp_path/'same-session.db')) as client:
        login(client)
        old=chat(client,'给妈妈转300元','same-session-first')['cards'][0]['operation']
        new=chat(client,'还是给妈妈转200元','same-session-second')['cards'][0]['operation']
        assert client.get('/api/operations/'+old['id']).json()['status']=='CANCELLED'
        assert new['status']=='WAITING_CONFIRMATION' and new['payload']['amount_cents']==20000

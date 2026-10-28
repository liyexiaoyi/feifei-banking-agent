from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
import pytest
from fastapi.testclient import TestClient
from ff_agent.api import create_app

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    app=create_app(tmp_path/'transfer-working.db')
    with TestClient(app) as c:
        c.headers['X-CSRF-Token']=c.post('/api/session',json={'user':'demo'}).json()['csrf']
        yield c

def ask(c,text,key):
    z=c.post('/api/chat',json={'text':text,'request_key':key,'goal_mode':True})
    assert z.status_code==200,z.text
    return z.json()

@pytest.mark.parametrize('changed',['给张三转账，300元','给妈妈转账，给爸爸转账，300元','给138000000019转100元','转账给张三'])
def test_bad_or_ambiguous_new_recipient_cannot_resurrect_previous_context(client,changed):
    ask(client,'给林晓转账','working-old-payee')
    first=ask(client,changed,'working-replace')
    assert not any(c.get('type')=='operation' for c in first['cards'])
    later=ask(client,'300','working-followup')
    assert not any(c.get('type') in ('operation','payees') for c in later['cards']),later
    assert client.get('/api/state').json()['operations']==[]

def test_unique_explicit_replacement_preserves_new_payee_for_followup(client):
    ask(client,'给林晓转账','working-known-old')
    ask(client,'给妈妈转账','working-known-new')
    z=ask(client,'300','working-known-amount')
    ops=[c['operation'] for c in z['cards'] if c.get('type')=='operation']
    assert len(ops)==1 and ops[0]['payload']['payee_id']=='p_mom'
    assert ops[0]['payload']['amount_cents']==30000 and ops[0]['status']=='WAITING_CONFIRMATION'

def test_balance_interrupt_does_not_discard_the_unambiguous_pending_question(client):
    ask(client,'给妈妈转账','working-preserve-mom')
    ask(client,'查询余额','working-balance')
    z=ask(client,'300','working-preserved-amount')
    ops=[c['operation'] for c in z['cards'] if c.get('type')=='operation']
    assert len(ops)==1 and ops[0]['payload']['payee_id']=='p_mom'


def test_transfer_to_wording_binds_the_new_explicit_recipient_for_later_amount(client):
    ask(client,'给林晓转账','working-wording-old')
    ask(client,'转账给妈妈','working-wording-new')
    z=ask(client,'300','working-wording-amount')
    ops=[c['operation'] for c in z['cards'] if c.get('type')=='operation']
    assert len(ops)==1 and ops[0]['payload']['payee_id']=='p_mom'

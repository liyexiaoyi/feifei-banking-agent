"""Offline independent checks for source-review candidates; no production edits."""
import sys,hashlib
from pathlib import Path
from datetime import datetime,timedelta
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
import pytest
from ff_agent.planner import Planner
from ff_agent.safety import assess_request
from ff_agent.bank import Bank,DomainError
from ff_agent.memory import Memory
from ff_agent.birthday_service import BirthdayService
from ff_agent.birthday_chat import BirthdayChat
from ff_agent.birthday_chat_slot import derive_chat_key

NOW=datetime.fromisoformat('2026-10-28T12:00:00+08:00')

def test_explicit_payee_before_comma_overrides_old_context(monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    actions=Planner(clock=lambda:NOW).plan('给妈妈转账，300元',{'pending_transfer':{'payee':'林晓','amount_cents':10000}})
    assert all(a.get('payee')!='林晓' for a in actions),actions
    assert any(a['type']=='clarify' or a.get('payee')=='妈妈' for a in actions),actions

def test_extra_phone_digit_is_not_silently_truncated(monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    actions=Planner(clock=lambda:NOW).plan('给138000000019转100元',{})
    assert not any(a['type']=='transfer' for a in actions),actions


@pytest.mark.parametrize('text',['给张三转账，300元','给妈妈转账，给爸爸转账，300元'])
def test_new_or_multiple_explicit_recipients_never_inherit_the_old_payee(monkeypatch,text):
    monkeypatch.setenv('FF_PLANNER_MODE','rules')
    actions=Planner(clock=lambda:NOW).plan(text,{'pending_transfer':{'payee':'林晓','amount_cents':10000}})
    assert not any(a['type']=='transfer' for a in actions),actions
    assert any(a['type']=='clarify' for a in actions),actions

@pytest.mark.parametrize('text,code',[
    ('对方说不转到安全账户就会被冻结，我现在就转过去','SAFE_ACCOUNT'),
    ('对方说不交保证金就不放款，我马上转过去','LOAN_ADVANCE_FEE'),
])
def test_coercive_conditional_plus_own_payment_action_keeps_hold(text,code):
    result=assess_request(text)
    assert result and code in result['codes'] and result['hold'] is True,result

@pytest.mark.parametrize('text',['我不会转到安全账户','我不会交保证金，也不转钱'])
def test_genuine_refusal_control_never_imposes_payment_hold(text):
    result=assess_request(text)
    assert result is None or result['hold'] is False,result

def test_pronoun_does_not_erase_unique_substring_note(tmp_path):
    b=Bank(tmp_path/'pronoun.db',clock=lambda:NOW);m=Memory(b)
    n=m.remember('demo','这个月账单请详细说明','remember-pronoun')
    result=m.handle('demo','忘记这个','forget-pronoun')
    with b.transaction() as db:
        row=db.execute('SELECT removed,version FROM memory_notes WHERE id=?',(n['id'],)).fetchone()
    assert tuple(row)==(0,n['version']),(dict(row),result)

def test_birthday_integrity_failure_does_not_claim_saved(tmp_path):
    b=Bank(tmp_path/'birthday-integrity.db',clock=lambda:NOW);s=BirthdayService(b)
    chat=BirthdayChat(b,s.store);key='integrity-chat-origin'
    f=s.create('demo','妈妈','2026-10-29','a_daily',50000,derive_chat_key(key))
    with b.transaction() as db:
        db.execute('INSERT INTO birthday_chat_links VALUES(?,?,?,?,?,?)',
            ('demo',key,f['id'],hashlib.sha256('启动'.encode()).hexdigest(),'0'*64,NOW.timestamp()))
    assert chat.recover('demo',key)=={'id':None}
    context={'pending_birthday':{'stage':'summary'}}
    try:result=chat.handle('demo','启动',key,'0','test-session',1,context)
    except DomainError as error:
        assert 400<=error.status<500
    else:
        assert not result.get('_birthday_committed'),result
        assert '生日安排已保存。' not in result.get('message',''),result
        assert not any(c.get('type')=='birthday_flow' and not c.get('flow_id') for c in result.get('cards',[])),result

def test_expired_financial_node_cannot_resume_as_active(tmp_path):
    clock=[NOW];b=Bank(tmp_path/'birthday-resume.db',clock=lambda:clock[0]);s=BirthdayService(b)
    f=s.create('demo','妈妈','2026-10-29','a_daily',50000,'resume-flow-create')
    f=s.advance('demo',f['id'],f['version'],'resume-flow-advance')
    opid=f['nodes'][0]['operation_id']
    assert b.operation('demo',opid)['status']=='WAITING_CONFIRMATION'
    f=s.pause('demo',f['id'],f['version'],'resume-flow-pause')
    clock[0]+=timedelta(minutes=30)
    with b.transaction() as db:b._expire(db,'demo')
    assert b.operation('demo',opid)['status']=='EXPIRED'
    try:result=s.resume('demo',f['id'],f['version'],'resume-flow-again',True)
    except DomainError as error:assert 400<=error.status<500
    else:assert result['status']=='BLOCKED',result

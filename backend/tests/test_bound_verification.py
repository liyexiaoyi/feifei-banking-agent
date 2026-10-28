"""I2: transport/model text cannot replace a confirmation-bound offline proof."""
import importlib
import importlib.util
import inspect
import json
from datetime import datetime,timedelta
import pytest
from ff_agent.bank import Bank,DomainError,TZ

class Provider:
    offline_only=True
    simulated=True
    def __init__(self,result='VERIFIED'):
        self.result=result;self.calls=[]
    def verify(self,challenge,code):
        self.calls.append(challenge)
        if self.result=='raise':raise RuntimeError('private provider fault 765432')
        if self.result=='string':return 'VERIFIED'
        return module('verification_provider').VerifyResult(self.result)

def module(name):
    assert importlib.util.find_spec('ff_agent.'+name) is not None,'I2 bound verification module absent'
    return importlib.import_module('ff_agent.'+name)

@pytest.fixture
def env(tmp_path):
    assert 'verification_provider' in inspect.signature(Bank).parameters,'I2 injectable offline provider absent'
    now=[datetime(2026,10,28,8,tzinfo=TZ)];provider=Provider()
    b=Bank(tmp_path/'bound.db',clock=lambda:now[0],verification_provider=provider)
    return b,now,provider

def red(b,key='bound'):
    op=b.prepare_transfer('demo','p_mom',200000,key)
    assert b.confirm('demo',op['id'])['status']=='WAITING_VERIFICATION'
    return op

def challenge(b,op):
    with b.transaction() as db:
        row=db.execute('SELECT * FROM verification_challenges WHERE user_id=? AND opid=?',('demo',op['id'])).fetchone()
        return dict(row) if row else None

def balance(b):return b.snapshot('demo')['accounts'][0]['balance_cents']
def failures(b):
    with b.transaction() as db:
        row=db.execute('SELECT failures FROM verification_streaks WHERE user_id=?',('demo',)).fetchone()
        return row[0] if row else 0

def denied(call,code=None):
    with pytest.raises(DomainError) as e:call()
    if code:assert e.value.code==code
    return e.value

def test_proof_is_created_at_red_confirmation_not_at_first_verify(env):
    b,_,p=env;op=b.prepare_transfer('demo','p_mom',200000,'before')
    assert challenge(b,op) is None
    denied(lambda:b.verify('demo',op['id'],'246810'),'CONFIRMATION_REQUIRED')
    b.confirm('demo',op['id']);first=challenge(b,op)
    assert first['user_id']=='demo' and first['status']=='ACTIVE'
    b.confirm('demo',op['id']);assert challenge(b,op)==first and p.calls==[]

def test_same_demo_code_executes_once_and_receipt_replay_does_not_check_provider(env):
    b,_,p=env;op=red(b);before=balance(b)
    done=b.verify('demo',op['id'],'246810');assert done['status']=='SUCCEEDED'
    assert balance(b)==before-200000 and challenge(b,op)['status']=='USED'
    assert b.verify('demo',op['id'],'bad')['id']==done['id'] and len(p.calls)==1
    c=p.calls[0];assert c.opid==op['id'] and c.user=='demo' and c.purpose=='operation_execute'

@pytest.mark.parametrize('change',["payload",'confirmed_at','risk'])
def test_mutated_confirmed_intent_cannot_rebind_at_verify(env,change):
    b,_,p=env;op=red(b);before=balance(b)
    with b.transaction() as db:
        if change=='payload':
            row=db.execute('SELECT payload FROM operations WHERE id=?',(op['id'],)).fetchone()
            payload=json.loads(row[0]);payload['amount_cents']=199999
            db.execute('UPDATE operations SET payload=? WHERE id=?',(json.dumps(payload),op['id']))
        elif change=='confirmed_at':db.execute('UPDATE operations SET confirmed_at=confirmed_at+1 WHERE id=?',(op['id'],))
        else:db.execute("UPDATE operations SET risk='YELLOW' WHERE id=?",(op['id'],))
    denied(lambda:b.verify('demo',op['id'],'246810'))
    assert balance(b)==before and p.calls==[] and failures(b)==0

def test_foreign_owner_cannot_use_or_revoke_challenge(env):
    b,_,p=env;op=red(b)
    assert denied(lambda:b.verify('guest',op['id'],'246810')).status==404
    assert denied(lambda:module('verification_challenge').revoke(b,'guest',op['id'])).status==404
    assert challenge(b,op)['status']=='ACTIVE' and p.calls==[]

def test_revoked_proof_survives_reconfirmation_and_restart(env):
    b,now,p=env;op=red(b);before=balance(b);ledger=module('verification_challenge')
    ledger.revoke(b,'demo',op['id']);saved=challenge(b,op)
    assert saved['status']=='REVOKED'
    b.confirm('demo',op['id']);assert challenge(b,op)==saved
    reopened=Bank(b.path,clock=lambda:now[0],verification_provider=p)
    denied(lambda:reopened.verify('demo',op['id'],'246810'))
    assert balance(reopened)==before and p.calls==[] and failures(b)==0

def test_expired_proof_never_calls_provider_or_renews(env):
    b,now,p=env;op=red(b);saved=challenge(b,op)
    now[0]+=timedelta(seconds=301)
    denied(lambda:b.verify('demo',op['id'],'246810'))
    assert challenge(b,op)==saved and p.calls==[] and failures(b)==0

def test_legacy_confirmed_operation_without_proof_is_not_silently_adopted(env):
    b,_,p=env;op=red(b)
    with b.transaction() as db:db.execute('DELETE FROM verification_challenges WHERE opid=?',(op['id'],))
    b.confirm('demo',op['id'])
    denied(lambda:b.verify('demo',op['id'],'246810'))
    assert challenge(b,op) is None and p.calls==[]

@pytest.mark.parametrize('outcome',['UNKNOWN','raise','string'])
def test_unknown_verification_is_readonly_no_repeat_no_release_even_after_expiry(env,outcome):
    b,now,p=env;p.result=outcome;op=red(b);before=balance(b)
    denied(lambda:b.verify('demo',op['id'],'246810'),'VERIFICATION_UNKNOWN')
    assert b.operation('demo',op['id'])['status']=='UNKNOWN'
    assert challenge(b,op)['status']=='UNKNOWN' and failures(b)==0
    now[0]+=timedelta(seconds=601)
    reopened=Bank(b.path,clock=lambda:now[0],verification_provider=p)
    for call in [lambda:reopened.verify('demo',op['id'],'246810'),lambda:reopened.confirm('demo',op['id']),lambda:reopened.cancel('demo',op['id'])]:denied(call)
    account=reopened.snapshot('demo')['accounts'][0]
    assert account['balance_cents']==before and account['reserved_cents']>=200000
    assert len(p.calls)==1 and challenge(b,op)['status']=='UNKNOWN'

def test_provider_revocation_is_persistent_and_does_not_count_bad_code(env):
    b,_,p=env;p.result='REVOKED';op=red(b)
    denied(lambda:b.verify('demo',op['id'],'246810'))
    assert challenge(b,op)['status']=='REVOKED' and failures(b)==0
    denied(lambda:b.verify('demo',op['id'],'246810'));assert len(p.calls)==1

def test_correct_proof_business_rejection_consumes_proof_without_assets(env,monkeypatch):
    b,_,p=env;op=red(b);before=balance(b)
    def fail(db,user,row):
        db.execute('UPDATE accounts SET balance_cents=balance_cents-123 WHERE id=?',('a_daily',))
        raise DomainError('CHANGED','changed',409)
    monkeypatch.setattr(b,'_execute',fail)
    denied(lambda:b.verify('demo',op['id'],'246810'),'CHANGED')
    assert challenge(b,op)['status']=='USED' and balance(b)==before
    denied(lambda:b.verify('demo',op['id'],'246810'));assert len(p.calls)==1

def test_technical_fault_rolls_back_local_proof_and_ledger(env,monkeypatch):
    b,_,p=env;op=red(b);before=balance(b)
    def fail(db,user,row):
        db.execute('UPDATE accounts SET balance_cents=balance_cents-123 WHERE id=?',('a_daily',))
        raise RuntimeError('local transaction fault')
    monkeypatch.setattr(b,'_execute',fail)
    with pytest.raises(RuntimeError):b.verify('demo',op['id'],'246810')
    assert challenge(b,op)['status']=='ACTIVE' and balance(b)==before

def test_wrong_codes_keep_existing_three_error_user_lock(env):
    b,_,p=env;p.result='REJECTED';op=red(b)
    for _ in range(3):denied(lambda:b.verify('demo',op['id'],'wrong'),'INVALID_VERIFICATION')
    assert b.snapshot('demo')['locked'] and failures(b)==3 and len(p.calls)==3
    assert challenge(b,op)['status']=='ACTIVE'

def test_no_raw_code_or_provider_error_is_saved(env):
    b,_,p=env;p.result='raise';op=red(b)
    denied(lambda:b.verify('demo',op['id'],'123456789'),'VERIFICATION_UNKNOWN')
    with b.transaction() as db:
        rows=[tuple(r) for r in db.execute('SELECT * FROM verification_challenges')]
        rows+=[tuple(r) for r in db.execute('SELECT event,detail FROM audit')]
    assert not any(x in str(rows) for x in ['123456789','765432','private provider fault'])

def test_demo_provider_defaults_are_per_bank_not_shared(env,tmp_path):
    assert 'verification_provider' in inspect.signature(Bank).parameters
    a=Bank(tmp_path/'a.db');b=Bank(tmp_path/'b.db')
    assert a.verification_provider is not b.verification_provider
    assert a.verification_provider.offline_only is True and a.verification_provider.simulated is True

@pytest.mark.parametrize('provider',[object(),type('External',(),{'offline_only':False,'simulated':False,'verify':lambda *a:None})()])
def test_network_or_untyped_provider_is_not_accepted_into_bank_transaction(tmp_path,provider):
    assert 'verification_provider' in inspect.signature(Bank).parameters
    with pytest.raises((DomainError,ValueError,TypeError)):Bank(tmp_path/'invalid.db',verification_provider=provider)


def test_changed_safety_epoch_cannot_use_old_proof(env,monkeypatch):
    b,_,p=env;op=red(b)
    monkeypatch.setattr(b,'_safety_epoch',lambda db,user:'changed-epoch')
    denied(lambda:b.verify('demo',op['id'],'246810'))
    assert p.calls==[] and failures(b)==0

def test_low_risk_confirmation_does_not_create_or_call_verification(env):
    b,_,p=env;op=b.prepare_transfer('demo','p_mom',100,'yellow')
    assert b.confirm('demo',op['id'])['status']=='SUCCEEDED'
    assert challenge(b,op) is None and p.calls==[]

def test_default_demo_verifier_code_stays_pure_local_and_explicitly_simulated():
    m=module('verification_provider');p=m.LocalDemoProvider()
    c=m.Challenge('challenge','demo','op','operation_execute','a'*64,123.0)
    assert p.verify(c,'246810') is m.VerifyResult.VERIFIED
    for code in ['000000','错误错误错误']:
        assert p.verify(c,code) is m.VerifyResult.REJECTED

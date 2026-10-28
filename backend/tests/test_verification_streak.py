from datetime import datetime,timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from ff_agent.bank import Bank,DomainError,TZ

@pytest.fixture
def env(tmp_path):
    now=[datetime(2026,10,27,4,10,tzinfo=TZ)]
    bank=Bank(tmp_path/'verification.db',clock=lambda:now[0])
    return bank,now

def prepared(bank,prefix=''):
    ops=[bank.prepare_transfer('demo','p_mom',200000,prefix+'transfer'),
         bank.prepare_card_status('demo','card_a_daily','report_lost',prefix+'card',1),
         bank.prepare_wealth('demo','a_daily','demo_cash','purchase',10000,prefix+'wealth')]
    for op in ops:assert bank.confirm('demo',op['id'])['status']=='WAITING_VERIFICATION'
    return ops

def wrong(bank,op,user='demo'):
    with pytest.raises(DomainError) as error:bank.verify(user,op['id'],'000000')
    assert error.value.code=='INVALID_VERIFICATION' and error.value.status==403

def count(bank,user='demo'):
    with bank.transaction() as db:
        row=db.execute('SELECT failures FROM verification_streaks WHERE user_id=?',(user,)).fetchone()
        return row[0] if row else 0

def daily(bank):return bank.snapshot('demo')['accounts'][0]['balance_cents']

def test_three_different_business_operations_and_keys_share_one_lock(assessed_env):
    env=assessed_env
    bank,_=env;ops=prepared(bank);before=daily(bank)
    for op in ops:wrong(bank,op)
    assert bank.snapshot('demo')['locked'] is True
    assert count(bank)==3 and daily(bank)==before
    with bank.transaction() as db:assert [r[0] for r in db.execute('SELECT attempts FROM operations ORDER BY rowid')]==[1,1,1]
    for call in [lambda:bank.prepare_transfer('demo','p_mom',100,'new-key'),
                 lambda:bank.prepare_virtual_card('demo','a_saving','new-card'),
                 lambda:bank.verify('demo',ops[0]['id'],'246810')]:
        with pytest.raises(DomainError) as error:call()
        assert error.value.code=='SENSITIVE_LOCKED' and error.value.status==423
    assert bank.analyze('demo','2026-10')['period']=='2026-10'
    assert len(bank.list_cards('demo'))==2 and bank.wealth('demo')['simulated'] is True
    assert count(bank)==3

def test_restart_and_other_user_cannot_clear_a_users_consecutive_errors(assessed_env):
    env=assessed_env
    bank,now=env;ops=prepared(bank);wrong(bank,ops[0]);wrong(bank,ops[1])
    guest=bank.prepare_card_status('guest','card_b_daily','report_lost','guest',1);bank.confirm('guest',guest['id']);wrong(bank,guest,'guest')
    assert not bank.snapshot('guest')['locked'] and not bank.snapshot('demo')['locked']
    reopened=Bank(bank.path,clock=lambda:now[0]);wrong(reopened,ops[2])
    assert reopened.snapshot('demo')['locked'] and not reopened.snapshot('guest')['locked']
    assert count(reopened,'guest')==1

def test_real_success_breaks_the_error_sequence_but_success_receipt_replay_does_not(assessed_env):
    env=assessed_env
    bank,_=env;ops=prepared(bank);wrong(bank,ops[0]);wrong(bank,ops[1])
    done=bank.verify('demo',ops[2]['id'],'246810');assert done['status']=='SUCCEEDED' and count(bank)==0
    wrong(bank,ops[0]);wrong(bank,ops[1]);assert count(bank)==2
    assert bank.verify('demo',ops[2]['id'],'000000')['id']==done['id']
    assert count(bank)==2
    new=bank.prepare_wealth('demo','a_saving','demo_bond','purchase',10000,'third-new');bank.confirm('demo',new['id']);wrong(bank,new)
    assert bank.snapshot('demo')['locked'] and count(bank)==3

def test_cooldown_resets_count_and_does_not_immediately_relock_a_new_operation(assessed_env):
    env=assessed_env
    bank,now=env
    for op in prepared(bank):wrong(bank,op)
    assert bank.snapshot('demo')['locked']
    now[0]+=timedelta(seconds=601)
    fresh=prepared(bank,'fresh-')
    assert not bank.snapshot('demo')['locked'] and count(bank)==0
    wrong(bank,fresh[0]);assert count(bank)==1 and not bank.snapshot('demo')['locked']
    wrong(bank,fresh[1]);assert count(bank)==2 and not bank.snapshot('demo')['locked']
    wrong(bank,fresh[2]);assert bank.snapshot('demo')['locked']

def test_unconfirmed_foreign_expired_or_unknown_operations_do_not_count_as_code_errors(env):
    bank,now=env;op=bank.prepare_transfer('demo','p_mom',200000,'not-confirmed')
    for user in ['demo','guest']:
        with pytest.raises(DomainError) as error:bank.verify(user,op['id'],'000000')
        assert error.value.code!='INVALID_VERIFICATION'
    bank.confirm('demo',op['id'])
    with bank.transaction() as db:db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?",(op['id'],))
    with pytest.raises(DomainError):bank.verify('demo',op['id'],'000000')
    expired=bank.prepare_card_status('demo','card_a_daily','report_lost','expired-card',1);bank.confirm('demo',expired['id']);now[0]+=timedelta(seconds=301)
    with pytest.raises(DomainError) as error:bank.verify('demo',expired['id'],'000000')
    assert error.value.code=='OPERATION_EXPIRED'
    assert count(bank)==0 and count(bank,'guest')==0

def test_concurrent_failures_on_separate_operations_cannot_lose_the_global_count(assessed_env):
    env=assessed_env
    bank,_=env;ops=prepared(bank)
    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(lambda op:wrong(bank,op),ops))
    assert bank.snapshot('demo')['locked'] and count(bank)==3

def test_correct_code_business_failure_clears_streak_but_rolls_back_every_asset_write(assessed_env,monkeypatch):
    env=assessed_env
    bank,_=env;ops=prepared(bank);wrong(bank,ops[0]);wrong(bank,ops[1]);before=daily(bank)
    def fail_after_write(db,user,row):
        db.execute('UPDATE accounts SET balance_cents=balance_cents-123 WHERE id=?',('a_daily',))
        bank._event(db,user,row['id'],'TEST_POST_WRITE','must rollback')
        raise DomainError('BUSINESS_CHANGED','测试业务状态改变。',409)
    monkeypatch.setattr(bank,'_execute',fail_after_write)
    with pytest.raises(DomainError) as error:bank.verify('demo',ops[2]['id'],'246810')
    assert error.value.code=='BUSINESS_CHANGED' and daily(bank)==before
    assert count(bank)==0 and bank.operation('demo',ops[2]['id'])['status']=='WAITING_VERIFICATION'
    with bank.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM audit WHERE event='TEST_POST_WRITE'").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM audit WHERE event='VERIFICATION_PASSED' AND operation_id=?",(ops[2]['id'],)).fetchone()[0]==1

def test_technical_execution_failure_rolls_back_verification_metadata_and_assets(assessed_env,monkeypatch):
    env=assessed_env
    bank,_=env;ops=prepared(bank);wrong(bank,ops[0]);wrong(bank,ops[1]);before=daily(bank)
    def technical_failure(db,user,row):
        db.execute('UPDATE accounts SET balance_cents=balance_cents-123 WHERE id=?',('a_daily',))
        raise RuntimeError('isolated test fault')
    monkeypatch.setattr(bank,'_execute',technical_failure)
    with pytest.raises(RuntimeError):bank.verify('demo',ops[2]['id'],'246810')
    assert daily(bank)==before and count(bank)==2
    with bank.transaction() as db:assert db.execute("SELECT COUNT(*) FROM audit WHERE event='VERIFICATION_PASSED'").fetchone()[0]==0


def test_nonascii_wrong_code_is_rejected_without_typeerror_or_lost_count(assessed_env):
    env=assessed_env
    bank,_=env;op=prepared(bank)[0]
    with pytest.raises(DomainError) as error:bank.verify('demo',op['id'],'错误错误错误')
    assert error.value.code=='INVALID_VERIFICATION' and count(bank)==1


# Explicit prerequisite for existing financial tests; never an application default.
from demo_assessment import consent_test_profile as _consent_test_profile
import pytest as _pytest_assessment

@_pytest_assessment.fixture
def assessed_env(env):
    _consent_test_profile(env[0])
    return env

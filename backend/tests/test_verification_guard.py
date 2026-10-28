import pytest
from ff_agent.bank import Bank,DomainError
from ff_agent.db import connect
from ff_agent.verification import record_failure,record_success,check_unlocked

def test_guard_never_mutates_counter_outside_an_explicit_write_transaction(tmp_path):
    bank=Bank(tmp_path/'guard.db');db=connect(bank.path)
    try:
        for call in [lambda:record_failure(db,'demo',bank.now().timestamp()),lambda:record_success(db,'demo')]:
            with pytest.raises(DomainError) as error:call()
            assert error.value.code=='INVALID_EXECUTION'
        assert db.execute('SELECT COUNT(*) FROM verification_streaks').fetchone()[0]==0
    finally:db.close()

def test_unknown_user_error_matches_existing_bank_contract_and_creates_no_orphan(tmp_path):
    bank=Bank(tmp_path/'guard.db')
    with bank.transaction() as db:
        for call in [lambda:check_unlocked(db,'missing',bank.now().timestamp()),lambda:record_success(db,'missing')]:
            with pytest.raises(DomainError) as error:call()
            assert error.value.code=='NOT_FOUND' and error.value.status==404
        assert db.execute('SELECT COUNT(*) FROM verification_streaks').fetchone()[0]==0

def test_guard_writes_rollback_with_the_callers_transaction(tmp_path):
    bank=Bank(tmp_path/'guard.db')
    with pytest.raises(RuntimeError):
        with bank.transaction() as db:
            assert record_failure(db,'demo',bank.now().timestamp())==1
            raise RuntimeError('rollback')
    with bank.transaction() as db:assert db.execute('SELECT COUNT(*) FROM verification_streaks').fetchone()[0]==0

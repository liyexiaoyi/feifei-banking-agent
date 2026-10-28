"""The chat bridge needs the same store write within its own guarded transaction."""
from datetime import datetime

import pytest
from ff_agent.bank import Bank, DomainError
from ff_agent.birthday_store import BirthdayStore
from ff_agent.db import connect


def setup(tmp_path):
    b = Bank(tmp_path / 'locked-chat.db', clock=lambda: datetime.fromisoformat('2026-10-28T12:00:00+08:00'))
    s = BirthdayStore(b)
    assert hasattr(s, '_create_locked'), 'chat locked store seam missing'
    return b, s


def call(s, db, key='birthday-chat-locked', **changes):
    args = dict(recipient_label='妈妈', event_date='2026-10-29', account_id='a_daily', budget_cents=50000)
    return s._create_locked(db, 'demo', key=key, **(args | changes))


def test_locked_create_requires_a_caller_transaction(tmp_path):
    b, s = setup(tmp_path)
    db = connect(b.path)
    try:
        with pytest.raises(DomainError) as e:
            call(s, db)
        assert e.value.code == 'TX_REQUIRED'
        assert db.execute('SELECT count(*) FROM birthday_flows').fetchone()[0] == 0
    finally:
        db.close()


def test_locked_create_is_idempotent_owned_metadata_only(tmp_path):
    b, s = setup(tmp_path)
    with b.transaction() as db:
        f = call(s, db)
        assert call(s, db)['id'] == f['id']
        with pytest.raises(DomainError) as e:
            call(s, db, budget_cents=1)
        assert e.value.code == 'KEY_CONFLICT'
        assert db.execute('SELECT count(*) FROM birthday_flows').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM birthday_nodes').fetchone()[0] == 7
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 0


def test_caller_failure_rolls_back_flow_nodes_request_and_audit_together(tmp_path):
    b, s = setup(tmp_path)
    with pytest.raises(RuntimeError):
        with b.transaction() as db:
            call(s, db)
            raise RuntimeError('failure-before-context-clear')
    with b.transaction() as db:
        for table in ('birthday_flows', 'birthday_nodes', 'birthday_requests'):
            assert db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM audit WHERE event='BIRTHDAY_FLOW_CREATED'").fetchone()[0] == 0

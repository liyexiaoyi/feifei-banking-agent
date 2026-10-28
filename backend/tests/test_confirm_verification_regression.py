"""A pending verification is not a fresh authorization to confirm again."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app
from ff_agent.bank import Bank, DomainError, TZ
from ff_agent.planner import Planner


def bank_at(tmp_path, now=None):
    clock = [now or datetime(2026, 10, 27, 12, 0, tzinfo=TZ)]
    return Bank(tmp_path / 'confirm.db', clock=lambda: clock[0]), clock


def balances(bank):
    return {a['id']: a['balance_cents'] for a in bank.snapshot('demo')['accounts']}


def prepare_red(bank):
    other = bank.prepare_transfer('demo', 'p_mom', 90000, 'other-reservation')
    op = bank.prepare_transfer('demo', 'p_mom', 20000, 'verification-required')
    waiting = bank.confirm('demo', op['id'])
    assert waiting['status'] == 'WAITING_VERIFICATION' and waiting['risk'] == 'RED'
    return other, waiting


@pytest.mark.parametrize('restart', [False, True])
def test_reconfirm_after_other_reservation_cancelled_preserves_verification(tmp_path, restart):
    bank, now = bank_at(tmp_path)
    other, op = prepare_red(bank)
    bank.cancel('demo', other['id'])
    before = balances(bank)
    if restart:
        bank = Bank(bank.path, clock=lambda: now[0])
    replay = bank.confirm('demo', op['id'])
    assert replay['status'] == 'WAITING_VERIFICATION'
    assert replay['risk'] == 'RED'
    assert balances(bank) == before
    assert bank.verify('demo', op['id'], '246810')['status'] == 'SUCCEEDED'
    assert sum(balances(bank).values()) == sum(before.values()) - 20000
    after = balances(bank)
    assert bank.confirm('demo', op['id'])['status'] == 'SUCCEEDED'
    assert bank.verify('demo', op['id'], '000000')['status'] == 'SUCCEEDED'
    assert balances(bank) == after


def test_reconfirm_after_other_reservation_expires_preserves_verification(tmp_path):
    bank, now = bank_at(tmp_path)
    other = bank.prepare_transfer('demo', 'p_mom', 90000, 'expiring-other')
    now[0] += timedelta(minutes=2)
    op = bank.prepare_transfer('demo', 'p_mom', 20000, 'later-operation')
    waiting = bank.confirm('demo', op['id'])
    assert waiting['status'] == 'WAITING_VERIFICATION' and waiting['risk'] == 'RED'
    now[0] += timedelta(minutes=4)
    before = balances(bank)
    assert bank.operation('demo', other['id'])['status'] == 'EXPIRED'
    still_pending = bank.operation('demo', op['id'])
    assert still_pending['status'] == 'WAITING_VERIFICATION'
    assert still_pending['expires_at'] > now[0].timestamp()
    assert bank.confirm('demo', op['id'])['status'] == 'WAITING_VERIFICATION'
    assert balances(bank) == before


def test_midnight_does_not_reset_an_existing_verification_requirement(tmp_path):
    bank, now = bank_at(tmp_path, datetime(2026, 10, 27, 23, 59, tzinfo=TZ))
    first = bank.prepare_transfer('demo', 'p_mom', 90000, 'settled-today')
    assert bank.confirm('demo', first['id'])['status'] == 'SUCCEEDED'
    op = bank.prepare_transfer('demo', 'p_mom', 20000, 'wait-across-midnight')
    assert bank.confirm('demo', op['id'])['status'] == 'WAITING_VERIFICATION'
    before = balances(bank)
    now[0] += timedelta(minutes=2)
    assert bank.confirm('demo', op['id'])['status'] == 'WAITING_VERIFICATION'
    assert balances(bank) == before


def test_concurrent_reconfirmations_cannot_complete_a_verifying_operation(tmp_path):
    bank, _ = bank_at(tmp_path)
    other, op = prepare_red(bank)
    bank.cancel('demo', other['id'])
    before = balances(bank)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: bank.confirm('demo', op['id']), range(2)))
    assert [r['status'] for r in replies] == ['WAITING_VERIFICATION'] * 2
    assert balances(bank) == before


def test_concurrent_first_confirmations_create_one_confirmation_event(tmp_path):
    bank, _ = bank_at(tmp_path)
    bank.prepare_transfer('demo', 'p_mom', 90000, 'other-first-confirmation')
    op = bank.prepare_transfer('demo', 'p_mom', 20000, 'concurrent-first-confirmation')
    assert op['status'] == 'WAITING_CONFIRMATION'
    before = balances(bank)
    ready = Barrier(2)

    def confirm(_):
        ready.wait(timeout=5)
        return bank.confirm('demo', op['id'])

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(confirm, range(2)))
    assert [r['status'] for r in replies] == ['WAITING_VERIFICATION'] * 2
    assert balances(bank) == before
    with bank.transaction() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM audit WHERE operation_id=? AND event='OPERATION_CONFIRMED'",
            (op['id'],),
        ).fetchone()[0]
    assert count == 1


def test_reconfirmation_does_not_refresh_confirmation_or_append_another_event(tmp_path):
    bank, now = bank_at(tmp_path)
    _, op = prepare_red(bank)
    with bank.transaction() as db:
        confirmed = db.execute('SELECT confirmed_at FROM operations WHERE id=?', (op['id'],)).fetchone()[0]
        events = db.execute('SELECT COUNT(*) FROM audit WHERE operation_id=?', (op['id'],)).fetchone()[0]
    now[0] += timedelta(seconds=5)
    assert bank.confirm('demo', op['id'])['status'] == 'WAITING_VERIFICATION'
    with bank.transaction() as db:
        assert db.execute('SELECT confirmed_at FROM operations WHERE id=?', (op['id'],)).fetchone()[0] == confirmed
        assert db.execute('SELECT COUNT(*) FROM audit WHERE operation_id=?', (op['id'],)).fetchone()[0] == events


def test_reconfirmation_keeps_owner_lock_and_expiration_checks(tmp_path):
    bank, now = bank_at(tmp_path)
    other, op = prepare_red(bank)
    bank.cancel('demo', other['id'])
    before = balances(bank)
    with pytest.raises(DomainError) as foreign:
        bank.confirm('guest', op['id'])
    assert foreign.value.code == 'NOT_FOUND'
    for _ in range(3):
        with pytest.raises(DomainError) as bad:
            bank.verify('demo', op['id'], '000000')
        assert bad.value.code == 'INVALID_VERIFICATION'
    with pytest.raises(DomainError) as locked:
        bank.confirm('demo', op['id'])
    assert locked.value.code == 'SENSITIVE_LOCKED'
    assert balances(bank) == before
    now[0] += timedelta(minutes=11)
    with pytest.raises(DomainError) as expired:
        bank.confirm('demo', op['id'])
    assert expired.value.code == 'OPERATION_EXPIRED'
    assert balances(bank) == before


def test_real_confirm_api_cannot_replace_verification_after_cancel(tmp_path):
    planner = Planner()
    planner.mode = 'rules'
    app = create_app(tmp_path / 'api-confirm.db', planner=planner)
    with TestClient(app) as client:
        login = client.post('/api/session', json={'user': 'demo'})
        assert login.status_code == 200
        client.headers['X-CSRF-Token'] = login.json()['csrf']
        other, op = prepare_red(app.state.bank)
        assert client.post('/api/operations/' + other['id'] + '/cancel', json={}).status_code == 200
        before = balances(app.state.bank)
        response = client.post('/api/operations/' + op['id'] + '/confirm', json={})
        assert response.status_code == 200
        assert response.json()['status'] == 'WAITING_VERIFICATION'
        assert response.json()['risk'] == 'RED'
        assert balances(app.state.bank) == before

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from ff_agent.bank import Bank, DomainError


def make_bank(tmp_path):
    now = [datetime(2026, 10, 25, 12, 0, tzinfo=ZoneInfo('Asia/Shanghai'))]
    bank = Bank(tmp_path / 'test.db', clock=lambda: now[0])
    return bank, now


def risk_finding():
    return {'codes':['LOAN_ADVANCE_FEE'],'reasons':['借款验资诱导付款'],'advice':'通过独立官方渠道核实。','hold':True}


def test_safety_hold_survives_restart_and_isolates_other_user(tmp_path):
    bank,now=make_bank(tmp_path)
    hold=bank.activate_safety_hold('demo',risk_finding(),'risk-one')
    restarted=Bank(bank.path,clock=lambda:now[0])
    assert restarted.snapshot('demo')['safety_hold']['id']==hold['id']
    assert restarted.snapshot('guest')['safety_hold'] is None
    with pytest.raises(DomainError) as denied:
        restarted.prepare_transfer('demo','p_mom',10000,'held')
    assert denied.value.code=='SAFETY_HOLD'
    assert restarted.prepare_transfer('guest','p_guest',10000,'allowed')['status']=='WAITING_CONFIRMATION'


def test_hold_cancels_both_unconfirmed_and_verifying_transfers_only(tmp_path):
    bank,_=make_bank(tmp_path)
    paid=bank.prepare_transfer('demo','p_mom',10000,'paid')
    bank.confirm('demo',paid['id'])
    low=bank.prepare_transfer('demo','p_mom',10000,'low')
    high=bank.prepare_transfer('demo','p_mom',200000,'high')
    bank.confirm('demo',high['id'])
    query=bank.prepare_subscription_query('demo','query')
    bank.activate_safety_hold('demo',risk_finding(),'risk')
    assert bank.operation('demo',low['id'])['status']=='CANCELLED'
    assert bank.operation('demo',high['id'])['status']=='CANCELLED'
    assert bank.operation('demo',query['id'])['status']=='WAITING_CONFIRMATION'
    assert bank.confirm('demo',paid['id'])['status']=='SUCCEEDED'
    with pytest.raises(DomainError):
        bank.verify('demo',high['id'],'246810')
    assert bank.snapshot('demo')['accounts'][0]['reserved_cents']==0
    assert bank.snapshot('demo')['accounts'][0]['balance_cents']==1990000


def test_end_hold_is_bound_to_current_id_and_never_revives_transfer(tmp_path):
    bank,_=make_bank(tmp_path)
    op=bank.prepare_transfer('demo','p_mom',10000,'old')
    first=bank.activate_safety_hold('demo',risk_finding(),'one')
    second=bank.activate_safety_hold('demo',risk_finding(),'two')
    assert second['id']!=first['id']
    with pytest.raises(DomainError) as denied:
        bank.end_safety_hold('demo',first['id'])
    assert denied.value.code=='SAFETY_HOLD_CHANGED'
    with pytest.raises(DomainError):
        bank.end_safety_hold('guest',second['id'])
    bank.end_safety_hold('demo',second['id'])
    bank.end_safety_hold('demo',second['id'])
    assert bank.snapshot('demo')['safety_hold'] is None
    assert bank.operation('demo',op['id'])['status']=='CANCELLED'
    with pytest.raises(DomainError):
        bank.confirm('demo',op['id'])
    assert bank.prepare_transfer('demo','p_mom',10000,'fresh')['status']=='WAITING_CONFIRMATION'


def test_replayed_hold_is_idempotent_and_audit_omits_input(tmp_path):
    bank,_=make_bank(tmp_path)
    first=bank.activate_safety_hold('demo',risk_finding(),'risk')
    again=bank.activate_safety_hold('demo',risk_finding(),'risk')
    assert first['id']==again['id']
    assert sum(e['event']=='SAFETY_HOLD_ACTIVATED' for e in bank.audit('demo'))==1


def test_ending_hold_does_not_allow_old_inflight_plans(tmp_path):
    bank,_=make_bank(tmp_path)
    epoch=bank.snapshot('demo')['safety_epoch']
    first=bank.activate_safety_hold('demo',risk_finding(),'one')
    bank.end_safety_hold('demo',first['id'])
    with pytest.raises(DomainError) as denied:
        bank.prepare_transfer('demo','p_mom',10000,'late',expected_epoch=epoch)
    assert denied.value.code=='REQUEST_SUPERSEDED'
    second=bank.activate_safety_hold('demo',risk_finding(),'two')
    with pytest.raises(DomainError):
        bank.end_safety_hold('demo',first['id'])
    assert bank.snapshot('demo')['safety_hold']['id']==second['id']


def test_snapshot_exposes_only_current_user_accounts(tmp_path):
    bank, _ = make_bank(tmp_path)
    snap = bank.snapshot('demo')
    assert {a['id'] for a in snap['accounts']} == {'a_daily', 'a_saving'}
    assert snap['accounts'][0]['balance_cents'] == 2000000
    assert 'b_daily' not in str(snap)


def test_bill_totals_exclude_income_internal_transfer_and_separate_refund(tmp_path):
    bank, _ = make_bank(tmp_path)
    result = bank.analyze('demo', '2026-10')
    assert result['expense_cents'] == 379800
    assert result['refund_cents'] == 3500
    assert result['net_expense_cents'] == 376300
    assert result['income_cents'] == 1200000
    assert sum(c['amount_cents'] for c in result['categories']) == 379800
    assert result['previous_net_expense_cents'] == 386300
    assert len(result['evidence_ids']) >= 60


def test_empty_period_has_zero_totals(tmp_path):
    bank, _ = make_bank(tmp_path)
    assert bank.analyze('demo', '2025-01')['net_expense_cents'] == 0


@pytest.mark.parametrize('amount', [0, -1, 1.5, True, 9999999999])
def test_invalid_amount_never_creates_transfer(tmp_path, amount):
    bank, _ = make_bank(tmp_path)
    with pytest.raises(DomainError):
        bank.prepare_transfer('demo', 'p_mom', amount, 'bad-amount')
    assert bank.snapshot('demo')['accounts'][0]['balance_cents'] == 2000000


def test_prepare_reserves_but_does_not_debit(tmp_path):
    bank, _ = make_bank(tmp_path)
    op = bank.prepare_transfer('demo', 'p_mom', 30000, 'first')
    assert op['status'] == 'WAITING_CONFIRMATION'
    assert op['risk'] == 'YELLOW'
    account = bank.snapshot('demo')['accounts'][0]
    assert account['balance_cents'] == 2000000
    assert account['available_cents'] == 1970000


def test_confirm_debits_once_and_persists_receipt(tmp_path):
    bank, _ = make_bank(tmp_path)
    op = bank.prepare_transfer('demo', 'p_mom', 30000, 'first')
    receipt = bank.confirm('demo', op['id'])
    assert receipt['status'] == 'SUCCEEDED'
    assert receipt['result']['amount_cents'] == 30000
    assert bank.confirm('demo', op['id'])['result']['transaction_id'] == receipt['result']['transaction_id']
    assert bank.snapshot('demo')['accounts'][0]['balance_cents'] == 1970000


def test_1000_boundary_and_daily_cumulative_upgrade(tmp_path):
    bank, _ = make_bank(tmp_path)
    first = bank.prepare_transfer('demo', 'p_mom', 100000, 'boundary')
    assert first['risk'] == 'YELLOW'
    bank.confirm('demo', first['id'])
    second = bank.prepare_transfer('demo', 'p_mom', 1, 'above')
    assert second['risk'] == 'RED'
    assert bank.confirm('demo', second['id'])['status'] == 'WAITING_VERIFICATION'
    assert bank.snapshot('demo')['accounts'][0]['balance_cents'] == 1900000
    assert bank.verify('demo', second['id'], '246810')['status'] == 'SUCCEEDED'
    assert bank.snapshot('demo')['accounts'][0]['balance_cents'] == 1899999


def test_verification_cannot_skip_confirmation(tmp_path):
    bank, _ = make_bank(tmp_path)
    op = bank.prepare_transfer('demo', 'p_mom', 100001, 'red')
    with pytest.raises(DomainError) as exc:
        bank.verify('demo', op['id'], '246810')
    assert exc.value.code == 'CONFIRMATION_REQUIRED'


def test_pending_operations_count_toward_cumulative_at_execution(tmp_path):
    bank, _ = make_bank(tmp_path)
    first = bank.prepare_transfer('demo', 'p_mom', 60000, 'one')
    bank.prepare_transfer('demo', 'p_mom', 60000, 'two')
    assert bank.confirm('demo', first['id'])['status'] == 'WAITING_VERIFICATION'


def test_concurrent_transfers_cannot_bypass_cumulative_threshold(tmp_path):
    bank, _ = make_bank(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ops = list(pool.map(lambda key: bank.prepare_transfer('demo', 'p_mom', 60000, key), ['one', 'two']))
    assert sorted(op['risk'] for op in ops) == ['RED', 'YELLOW']
    assert all(bank.confirm('demo', op['id'])['status'] == 'WAITING_VERIFICATION' for op in ops)


def test_same_key_different_parameters_is_rejected(tmp_path):
    bank, _ = make_bank(tmp_path)
    first = bank.prepare_transfer('demo', 'p_mom', 30000, 'same')
    assert bank.prepare_transfer('demo', 'p_mom', 30000, 'same')['id'] == first['id']
    with pytest.raises(DomainError) as exc:
        bank.prepare_transfer('demo', 'p_mom', 20000, 'same')
    assert exc.value.code == 'IDEMPOTENCY_CONFLICT'


def test_reservations_prevent_overspending(tmp_path):
    bank, _ = make_bank(tmp_path)
    bank.prepare_transfer('demo', 'p_mom', 1900000, 'one')
    with pytest.raises(DomainError) as exc:
        bank.prepare_transfer('demo', 'p_mom', 110000, 'two')
    assert exc.value.code == 'INSUFFICIENT_FUNDS'


def test_cancel_releases_reservation_and_explicitly_blocks_later_confirm(tmp_path):
    bank, _ = make_bank(tmp_path)
    op = bank.prepare_transfer('demo', 'p_mom', 30000, 'one')
    assert bank.cancel('demo', op['id'])['status'] == 'CANCELLED'
    assert bank.snapshot('demo')['accounts'][0]['available_cents'] == 2000000
    with pytest.raises(DomainError):
        bank.confirm('demo', op['id'])


def test_expired_operation_cannot_execute_and_releases_reservation(tmp_path):
    bank, now = make_bank(tmp_path)
    op = bank.prepare_transfer('demo', 'p_mom', 30000, 'one')
    now[0] += timedelta(minutes=6)
    with pytest.raises(DomainError) as exc:
        bank.confirm('demo', op['id'])
    assert exc.value.code == 'OPERATION_EXPIRED'
    assert bank.operation('demo', op['id'])['status'] == 'EXPIRED'
    assert bank.snapshot('demo')['accounts'][0]['available_cents'] == 2000000


def test_cross_day_recalculates_daily_total(tmp_path):
    bank, now = make_bank(tmp_path)
    now[0] = now[0].replace(hour=23, minute=58)
    previous = bank.prepare_transfer('demo', 'p_mom', 100000, 'previous')
    bank.confirm('demo', previous['id'])
    waiting = bank.prepare_transfer('demo', 'p_mom', 100, 'waiting')
    assert waiting['risk'] == 'RED'
    now[0] += timedelta(minutes=3)
    assert bank.confirm('demo', waiting['id'])['status'] == 'SUCCEEDED'


def test_user_cannot_read_confirm_or_cancel_other_users_operation(tmp_path):
    bank, _ = make_bank(tmp_path)
    op = bank.prepare_transfer('demo', 'p_mom', 30000, 'one')
    for action in [bank.operation, bank.confirm, bank.cancel]:
        with pytest.raises(DomainError) as exc:
            action('guest', op['id'])
        assert exc.value.code == 'NOT_FOUND'


def test_payee_is_scoped_to_user_and_duplicate_names_return_candidates(tmp_path):
    bank, _ = make_bank(tmp_path)
    assert len(bank.resolve_payee('demo', '林晓')) == 2
    with pytest.raises(DomainError):
        bank.prepare_transfer('guest', 'p_mom', 100, 'one')


def test_subscription_query_requires_confirmation(tmp_path):
    bank, _ = make_bank(tmp_path)
    with pytest.raises(DomainError) as exc:
        bank.subscriptions('demo')
    assert exc.value.code == 'SUBSCRIPTION_CONSENT_REQUIRED'
    op = bank.prepare_subscription_query('demo', 'query')
    assert op['risk'] == 'YELLOW'
    assert 'subscriptions' not in op['payload']
    result = bank.confirm('demo', op['id'])
    assert len(result['result']['subscriptions']) == 5
    assert len(bank.subscriptions('demo')) == 5


def test_subscription_cancel_requires_query_consent_and_confirmation(tmp_path):
    bank, _ = make_bank(tmp_path)
    with pytest.raises(DomainError):
        bank.prepare_cancel('demo', 'sub_video', 'cancel')
    query = bank.prepare_subscription_query('demo', 'query')
    bank.confirm('demo', query['id'])
    cancel = bank.prepare_cancel('demo', 'sub_video', 'cancel')
    assert bank.subscriptions('demo')[0]['status'] == 'ACTIVE'
    assert bank.confirm('demo', cancel['id'])['result']['subscription_status'] == 'CANCELLED'
    assert bank.subscriptions('demo')[0]['status'] == 'CANCELLED'


def test_external_subscription_returns_instructions_without_false_cancellation(tmp_path):
    bank, _ = make_bank(tmp_path)
    bank.confirm('demo', bank.prepare_subscription_query('demo', 'query')['id'])
    with pytest.raises(DomainError) as exc:
        bank.prepare_cancel('demo', 'sub_gym', 'cancel')
    assert exc.value.code == 'MERCHANT_ACTION_REQUIRED'
    assert next(s for s in bank.subscriptions('demo') if s['id'] == 'sub_gym')['status'] == 'ACTIVE'


def test_failed_verification_is_counted_and_locks_sensitive_operations(tmp_path):
    bank, _ = make_bank(tmp_path)
    op = bank.prepare_transfer('demo', 'p_mom', 100001, 'red')
    bank.confirm('demo', op['id'])
    for _ in range(3):
        with pytest.raises(DomainError):
            bank.verify('demo', op['id'], '000000')
    with pytest.raises(DomainError) as exc:
        bank.verify('demo', op['id'], '246810')
    assert exc.value.code == 'SENSITIVE_LOCKED'
    with pytest.raises(DomainError):
        bank.prepare_transfer('demo', 'p_mom', 100, 'new')
    assert bank.snapshot('demo')['accounts'][0]['balance_cents'] == 2000000


def test_reopen_database_preserves_receipt_after_response_loss(tmp_path):
    bank, now = make_bank(tmp_path)
    op = bank.prepare_transfer('demo', 'p_mom', 30000, 'one')
    bank.confirm('demo', op['id'])
    reopened = Bank(tmp_path / 'test.db', clock=lambda: now[0])
    assert reopened.operation('demo', op['id'])['status'] == 'SUCCEEDED'
    assert reopened.confirm('demo', op['id'])['status'] == 'SUCCEEDED'
    assert reopened.snapshot('demo')['accounts'][0]['balance_cents'] == 1970000


def test_audit_is_persisted_with_transfer_and_contains_no_verification_code(tmp_path):
    bank, _ = make_bank(tmp_path)
    op = bank.prepare_transfer('demo', 'p_mom', 100001, 'one')
    bank.confirm('demo', op['id'])
    bank.verify('demo', op['id'], '246810')
    events = bank.audit('demo')
    assert any(e['event'] == 'TRANSFER_SUCCEEDED' for e in events)
    assert '246810' not in str(events)
    with sqlite3.connect(tmp_path / 'test.db') as db:
        assert db.execute("select count(*) from transactions where operation_id=?", (op['id'],)).fetchone()[0] == 1

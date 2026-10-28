"""Whole birthday commands must be atomic; JSON is never financial approval."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
import importlib
import importlib.util
import json

import pytest

from ff_agent.bank import Bank, DomainError
from ff_agent.birthday_budget import BirthdayBudgets
from ff_agent.birthday_orders import BirthdayOrders


@pytest.fixture
def env(tmp_path):
    assert importlib.util.find_spec('ff_agent.birthday_service'), 'birthday service not implemented'
    clock = [datetime.fromisoformat('2026-10-28T12:00:00+08:00')]
    bank = Bank(tmp_path / 'service.db', clock=lambda: clock[0])
    service = importlib.import_module('ff_agent.birthday_service').BirthdayService(bank)
    return bank, service, clock


def create(env, **changes):
    args = dict(recipient_label='妈妈', event_date='2026-10-29', account_id='a_daily', budget_cents=100000, key='create-birthday-service')
    args.update(changes)
    return env[1].create('demo', **args)


def advance(env, f, key):
    return env[1].advance('demo', f['id'], f['version'], key)


def pay(env, opid):
    done = env[0].confirm('demo', opid)
    if done['status'] == 'WAITING_VERIFICATION':
        done = env[0].verify('demo', opid, '246810')
    assert done['status'] == 'SUCCEEDED'
    return done


def dump(env):
    with env[0].transaction() as db:
        return {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY rowid')] for t in
                ('accounts', 'transactions', 'operations', 'audit', 'birthday_flows', 'birthday_nodes', 'birthday_tickets', 'birthday_requests', 'birthday_budgets', 'birthday_order_ledger')}


def ready(env, cake=False, amount=100000):
    f = advance(env, create(env, budget_cents=amount), 'advance-first-budget')
    pay(env, f['nodes'][0]['operation_id'])
    f = env[1].decide_cake('demo', f['id'], cake, f['version'], 'decide-cake-first')
    f = env[1].select_item('demo', f['id'], 'flowers', 'flowers_simple', f['version'], 'select-flower-first')
    if cake:
        f = env[1].select_item('demo', f['id'], 'cake', 'cake_small', f['version'], 'select-cake-first')
    return f


def test_create_is_metadata_only_and_advance_never_approves_financial_effect(env, monkeypatch):
    f = create(env)
    before = dump(env)
    assert before['operations'] == before['birthday_budgets'] == []
    for name in ('confirm', 'verify', '_execute'):
        monkeypatch.setattr(env[0], name, lambda *a, **k: pytest.fail('service must not approve or execute'))
    got = advance(env, f, 'first-service-advance')
    assert got['version'] == 2 and got['nodes'][0]['state'] == 'WAITING_CONFIRMATION'
    assert len(dump(env)['operations']) == 1
    assert dump(env)['accounts'] == before['accounts'] and dump(env)['birthday_budgets'] == []


def test_public_commands_do_not_reenter_bank_transaction(env, monkeypatch):
    original = env[0].transaction
    depth = [0]
    @contextmanager
    def guarded():
        assert depth[0] == 0, 'nested bank transaction'
        depth[0] += 1
        try:
            with original() as db:
                yield db
        finally:
            depth[0] -= 1
    monkeypatch.setattr(env[0], 'transaction', guarded)
    f = create(env)
    f = advance(env, f, 'no-nesting-advance')
    f = env[1].pause('demo', f['id'], f['version'], 'no-nesting-pause')
    f = env[1].resume('demo', f['id'], f['version'], 'no-nesting-resume', True)
    assert env[1].cancel('demo', f['id'], f['version'], 'no-nesting-cancel', True)['status'] == 'CANCELLED'


def test_fault_after_binding_rolls_back_request_node_ticket_and_operation(env, monkeypatch):
    f = create(env)
    before = dump(env)
    original = BirthdayBudgets._prepare_locked
    def broken(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError('injected-after-real-binding')
    monkeypatch.setattr(BirthdayBudgets, '_prepare_locked', broken)
    with pytest.raises(RuntimeError, match='injected-after-real-binding'):
        advance(env, f, 'atomic-fault-advance')
    assert dump(env) == before


def test_concurrent_same_key_is_one_command_and_one_financial_operation(env):
    f = create(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: advance(env, f, 'parallel-same-command'), range(2)))
    assert results[0] == results[1]
    assert len(dump(env)['operations']) == 1 and results[0]['version'] == 2


def test_concurrent_different_keys_on_one_version_has_only_one_winner(env):
    f = create(env)
    def go(key):
        try:
            return advance(env, f, key)
        except DomainError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(go, ['parallel-key-first', 'parallel-key-second']))
    assert sum(r is not None for r in results) == 1 and len(dump(env)['operations']) == 1


@pytest.mark.parametrize('cake', [False, True])
def test_each_branch_waits_for_real_payments_and_replay_returns_current_facts(env, cake):
    f = ready(env, cake)
    pre_advance = dict(f)
    f = advance(env, f, 'prepare-explicit-orders')
    order_ids = [n['operation_id'] for n in f['nodes'] if n['type'] == 'birthday_order' and n['operation_id']]
    assert len(order_ids) == (2 if cake else 1) and f['status'] == 'ACTIVE'
    assert BirthdayOrders(env[0]).list('demo') == []
    for opid in order_ids:
        pay(env, opid)
    f = advance(env, f, 'finish-real-birthday')
    assert f['status'] == 'COMPLETED' and f['nodes'][6]['state'] == 'SUCCEEDED'
    assert len(BirthdayOrders(env[0]).list('demo')) == len(order_ids)
    old_replay = advance(env, pre_advance, 'prepare-explicit-orders')
    assert old_replay == f
    lookup = env[1].lookup_request('demo', 'prepare-explicit-orders')
    assert lookup['action'] == 'advance' and lookup['flow'] == f
    assert lookup['simulated'] is True and lookup['merchant_connected'] is False


@pytest.mark.parametrize('cake', [0, 1, 'false', None, [], {}])
def test_cake_choice_is_an_explicit_strict_boolean(env, cake):
    f = advance(env, create(env), 'cake-strict-budget')
    pay(env, f['nodes'][0]['operation_id'])
    before = dump(env)
    with pytest.raises(DomainError):
        env[1].decide_cake('demo', f['id'], cake, f['version'], 'bad-cake-choice')
    assert dump(env) == before


def test_pause_blocks_old_confirmation_and_resume_never_reissues(env):
    f = advance(env, create(env), 'pause-first-budget')
    opid = f['nodes'][0]['operation_id']
    f = env[1].pause('demo', f['id'], f['version'], 'pause-flow-first')
    with pytest.raises(DomainError):
        env[0].confirm('demo', opid)
    before = dump(env)['operations']
    f = env[1].resume('demo', f['id'], f['version'], 'resume-flow-first', True)
    assert f['status'] == 'ACTIVE' and dump(env)['operations'] == before
    assert pay(env, opid)['result']['simulated'] is True


@pytest.mark.parametrize('method', ['resume', 'cancel'])
@pytest.mark.parametrize('confirmed', [False, 1, 'true', None])
def test_resume_and_cancel_need_explicit_boolean_confirmation(env, method, confirmed):
    f = advance(env, create(env), 'explicit-first-budget')
    if method == 'resume':
        f = env[1].pause('demo', f['id'], f['version'], 'explicit-pause-first')
    before = dump(env)
    with pytest.raises(DomainError):
        getattr(env[1], method)('demo', f['id'], f['version'], 'invalid-explicit-action', confirmed)
    assert dump(env) == before


def test_new_risk_epoch_cannot_be_used_to_resume_old_confirmation(env):
    f = advance(env, create(env), 'risk-first-budget')
    f = env[1].pause('demo', f['id'], f['version'], 'risk-pause-first')
    env[0].activate_safety_hold('demo', {'codes': ['SCAM'], 'reasons': ['测试'], 'advice': '核对'}, 'risk-during-birthday')
    before = dump(env)
    with pytest.raises(DomainError):
        env[1].resume('demo', f['id'], f['version'], 'risk-resume-first', True)
    assert dump(env) == before
    # Explicitly stopping work remains possible even during the risk hold.
    assert env[1].cancel('demo', f['id'], f['version'], 'risk-cancel-first', True)['status'] == 'CANCELLED'


@pytest.mark.parametrize('status', ['UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW'])
def test_unknown_never_reprepares_or_releases_its_amount(env, status):
    f = advance(env, ready(env), 'prepare-unknown-order')
    opid = f['nodes'][4]['operation_id']
    with env[0].transaction() as db:
        db.execute('UPDATE operations SET status=? WHERE id=?', (status, opid))
    before = dump(env)['accounts']
    f = advance(env, f, 'observe-unknown-only')
    assert f['status'] == 'UNKNOWN' and f['nodes'][4]['observed_state'] == 'UNKNOWN'
    count = len(dump(env)['operations'])
    f = env[1].cancel('demo', f['id'], f['version'], 'cancel-known-parts', True)
    assert f['status'] == 'UNKNOWN' and f['nodes'][4]['observed_state'] == 'UNKNOWN'
    assert len(dump(env)['operations']) == count and dump(env)['accounts'] == before
    assert env[0].snapshot('demo')['accounts'][0]['reserved_cents'] == 30000
    with pytest.raises(DomainError):
        env[1].resume('demo', f['id'], f['version'], 'never-resume-unknown', True)


def test_cancel_keeps_paid_order_and_only_stops_unexecuted_other_gift(env):
    f = advance(env, ready(env, cake=True), 'cancel-partial-orders')
    flowers, cake = f['nodes'][4]['operation_id'], f['nodes'][5]['operation_id']
    pay(env, flowers)
    before = dump(env)['accounts']
    old = dict(f)
    f = env[1].cancel('demo', f['id'], f['version'], 'cancel-unpaid-cake', True)
    assert f['status'] == 'CANCELLED' and f['nodes'][4]['observed_state'] == 'SUCCEEDED'
    assert env[0].receipt('demo', cake)['observed_status'] == 'CANCELLED'
    assert len(BirthdayOrders(env[0]).list('demo')) == 1 and dump(env)['accounts'] == before
    assert env[0].snapshot('demo')['accounts'][0]['reserved_cents'] == 0
    assert env[1].cancel('demo', old['id'], old['version'], 'cancel-unpaid-cake', True) == f


def test_expired_quote_rolls_back_claim_and_can_be_explicitly_selected_again(env):
    f = ready(env)
    env[2][0] += timedelta(seconds=301)
    before = dump(env)
    with pytest.raises(DomainError):
        advance(env, f, 'try-expired-quote')
    assert dump(env) == before
    f = env[1].select_item('demo', f['id'], 'flowers', 'flowers_simple', f['version'], 'new-explicit-selection')
    assert advance(env, f, 'try-new-real-quote')['nodes'][4]['state'] == 'WAITING_CONFIRMATION'


def test_two_orders_over_budget_roll_back_all_preparation_not_just_second(env):
    f = ready(env, cake=True, amount=40000)
    before = dump(env)
    with pytest.raises(DomainError):
        advance(env, f, 'both-over-budget-orders')
    assert dump(env) == before


def test_reads_and_key_replay_are_owner_bound_and_never_expire_or_write(env):
    f = advance(env, create(env), 'readonly-first-budget')
    env[2][0] += timedelta(seconds=301)
    before = dump(env)
    service = env[1]
    assert service.get('demo', f['id'])['id'] == f['id']
    assert len(service.list('demo')) == 1
    assert service.lookup_request('demo', 'readonly-first-budget')['flow']['id'] == f['id']
    assert service.list('guest') == []
    for fn in (lambda: service.get('guest', f['id']), lambda: service.lookup_request('guest', 'readonly-first-budget')):
        with pytest.raises(DomainError):
            fn()
    assert dump(env) == before


def test_key_reuse_changed_action_or_expected_version_conflicts(env):
    f = create(env)
    current = advance(env, f, 'original-command-key')
    before = dump(env)
    with pytest.raises(DomainError):
        advance(env, current, 'original-command-key')
    with pytest.raises(DomainError):
        env[1].pause('demo', f['id'], f['version'], 'original-command-key')
    assert dump(env) == before


def test_cancel_must_not_touch_an_unrelated_owned_operation_after_bad_link(env):
    f = advance(env, create(env), 'flow-with-bad-link')
    other = env[0].prepare_transfer('demo', 'p_dad', 50000, 'unrelated-transfer')
    with env[0].transaction() as db:
        # Isolated damaged provenance fixture, not an exposed API capability.
        db.execute('UPDATE birthday_nodes SET operation_id=? WHERE flow_id=? AND idx=0', (other['id'], f['id']))
    result = env[1].cancel('demo', f['id'], f['version'], 'cancel-damaged-link', True)
    assert env[0].receipt('demo', other['id'])['recorded_status'] == 'WAITING_CONFIRMATION'
    assert result['status'] == 'UNKNOWN'


@pytest.mark.parametrize('method', ['resume', 'cancel'])
def test_unknown_without_operation_id_is_not_misreported_as_resumable_or_cancelled(env, method):
    f = advance(env, create(env), 'orphan-source-budget')
    with env[0].transaction() as db:
        db.execute("UPDATE birthday_nodes SET state='RUNNING',claim_version=2,version=2 WHERE flow_id=? AND idx=4", (f['id'],))
    f = env[1].pause('demo', f['id'], f['version'], 'pause-orphan-source')
    assert f['nodes'][4]['observed_state'] == 'UNKNOWN'
    if method == 'resume':
        with pytest.raises(DomainError):
            env[1].resume('demo', f['id'], f['version'], 'no-orphan-resume', True)
    else:
        assert env[1].cancel('demo', f['id'], f['version'], 'no-orphan-certainty', True)['status'] == 'UNKNOWN'


def test_red_budget_stays_unpaid_until_fresh_explicit_verification(env):
    f = advance(env, create(env, budget_cents=150000), 'red-budget-first')
    opid = f['nodes'][0]['operation_id']
    op = env[0].confirm('demo', opid)
    assert op['status'] == 'WAITING_VERIFICATION'
    f = advance(env, f, 'red-still-waiting')
    assert f['nodes'][1]['state'] == 'PENDING' and dump(env)['birthday_budgets'] == []
    assert env[0].verify('demo', opid, '246810')['status'] == 'SUCCEEDED'
    assert len(dump(env)['birthday_budgets']) == 1


@pytest.mark.parametrize('operation_status,expected_flow', [('UNKNOWN', 'UNKNOWN'), ('FAILED', 'BLOCKED')])
def test_pause_preserves_the_more_important_unresolved_or_blocked_status(env, operation_status, expected_flow):
    f = advance(env, create(env), 'pause-actual-broken-source')
    with env[0].transaction() as db:
        db.execute('UPDATE operations SET status=? WHERE id=?', (operation_status, f['nodes'][0]['operation_id']))
    before = dump(env)['accounts']
    paused = env[1].pause('demo', f['id'], f['version'], 'pause-not-certainty')
    assert paused['status'] == expected_flow
    assert dump(env)['accounts'] == before


@pytest.mark.parametrize('action', ['cancel', 'available_after_expiry'])
def test_unrecognised_bank_status_fails_closed_instead_of_releasing_allocation(env, action):
    f = advance(env, ready(env), 'malformed-bank-status-order')
    with env[0].transaction() as db:
        db.execute("UPDATE operations SET status='RUNNING' WHERE id=?", (f['nodes'][4]['operation_id'],))
    # RUNNING is a node state, not a supported Bank operation status.
    f = advance(env, f, 'observe-malformed-status')
    assert f['status'] == 'UNKNOWN'
    before = dump(env)
    if action == 'cancel':
        with pytest.raises(DomainError):
            env[1].cancel('demo', f['id'], f['version'], 'do-not-release-corrupt-status', True)
        assert dump(env) == before
    else:
        env[2][0] += timedelta(seconds=3601)
        with pytest.raises(DomainError):
            env[0].snapshot('demo')

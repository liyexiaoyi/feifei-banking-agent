"""Persistent birthday ownership is evidence, never a model's permission."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import importlib
import importlib.util
import json

import pytest

from ff_agent.bank import Bank, DomainError
from ff_agent.birthday_budget import BirthdayBudgets
from ff_agent.birthday_orders import BirthdayOrders, CATALOG


def modules():
    for name in ('birthday_store', 'birthday_tickets'):
        assert importlib.util.find_spec('ff_agent.' + name), 'missing persistent birthday ' + name
    return tuple(importlib.import_module('ff_agent.' + n) for n in ('birthday_store', 'birthday_tickets'))


@pytest.fixture
def env(tmp_path):
    s, t = modules()
    clock = [datetime.fromisoformat('2026-10-28T12:00:00+08:00')]
    bank = Bank(tmp_path / 'persistent.db', clock=lambda: clock[0])
    s.bootstrap_birthday_store(bank)
    return bank, clock, s, t


def create(env, key='flow-request-001', **changes):
    args = dict(recipient_label='妈妈', event_date='2026-10-29', account_id='a_daily', budget_cents=100000, key=key)
    args.update(changes)
    return env[2].BirthdayStore(env[0]).create('demo', **args)


def assets(env):
    with env[0].transaction() as db:
        return {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY id')]
                for t in ('accounts', 'transactions', 'operations', 'birthday_budgets', 'birthday_order_ledger')}


def account(env):
    return next(a for a in env[0].snapshot('demo')['accounts'] if a['id'] == 'a_daily')


def flowrow(env, fid):
    with env[0].transaction() as db:
        return env[2].load_flow(db, 'demo', fid)


def nodes(env, fid):
    with env[0].transaction() as db:
        return env[2].load_nodes(db, 'demo', fid)


def prepare_budget(env, f):
    bank, _, s, t = env
    with bank.transaction() as db:
        row = s.load_flow(db, 'demo', f['id'])
        n = s.load_nodes(db, 'demo', f['id'])[0]
        s.claim_node(bank, db, 'demo', f['id'], 0, row['version'], n['version'])
        binding = t.issue_ticket(bank, db, 'demo', f['id'], 0)
        op = BirthdayBudgets(bank)._prepare_locked(db, 'demo', row['account_id'], row['budget_cents'], row['event_date'], binding.effect_key, binding)
    return binding, op


def complete(env, op):
    done = env[0].confirm('demo', op['id'])
    if done['status'] == 'WAITING_VERIFICATION':
        done = env[0].verify('demo', op['id'], '246810')
    assert done['status'] == 'SUCCEEDED'
    return done


def ready_order(env, f, item='flowers_simple', cake=False):
    _, op = prepare_budget(env, f)
    done = complete(env, op)
    bank, _, s, _ = env
    with bank.transaction() as db:
        s.sync_nodes(bank, db, 'demo', f['id'])
        row = s.load_flow(db, 'demo', f['id'])
        s.cas_flow(db, 'demo', f['id'], row['version'], {'cake_choice': int(cake)})
        s.cas_node(db, 'demo', f['id'], 1, 1, {'state': 'SUCCEEDED'})
        c = CATALOG[item]
        s.cas_node(db, 'demo', f['id'], 2, 1, dict(state='SUCCEEDED', item_id=item,
            quote_version=c['quote_version'], price_cents=c['price_cents'],
            quote_expires_at=bank.now().timestamp() + 240))
    return done['result']['budget_id']


def prepare_order(env, f, bid, item='flowers_simple'):
    bank, _, s, t = env
    with bank.transaction() as db:
        row = s.load_flow(db, 'demo', f['id'])
        n = s.load_nodes(db, 'demo', f['id'])[4]
        s.claim_node(bank, db, 'demo', f['id'], 4, row['version'], n['version'])
        binding = t.issue_ticket(bank, db, 'demo', f['id'], 4)
        b = db.execute('SELECT * FROM birthday_budgets WHERE id=?', (bid,)).fetchone()
        op = BirthdayOrders(bank)._prepare_locked(db, 'demo', bid, item, b['version'], binding.effect_key, binding)
    return binding, op


def test_create_metadata_has_no_operation_cash_hold_or_merchant_effect(env):
    before = assets(env)
    f = create(env)
    assert assets(env) == before and account(env)['reserved_cents'] == 0
    assert f['recipient_label'] == '妈妈' and f['cake_choice'] is None
    assert f['status'] == 'ACTIVE' and f['version'] == 1
    assert f['simulated'] is True and f['merchant_connected'] is False
    assert [n['idx'] for n in f['nodes']] == list(range(7))
    assert all(n['state'] == n['observed_state'] == 'PENDING' and n['operation_id'] is None for n in f['nodes'])
    encoded = json.dumps(f, ensure_ascii=False)
    for private in ('token', 'hash', 'effect_key', 'risk_epoch', 'graph_json', 'create_key', 'request_key'):
        assert private not in encoded
    with env[0].transaction() as db:
        assert '妈妈' not in str([tuple(r) for r in db.execute('SELECT * FROM audit')])


@pytest.mark.parametrize('changes', [dict(recipient_label=''), dict(recipient_label=' '), dict(recipient_label='妈'*41),
    dict(recipient_label=12), dict(event_date='明天'), dict(event_date='2026-02-30'), dict(event_date='2026-10-27'),
    dict(event_date='2027-10-30'), dict(budget_cents=True), dict(budget_cents=1.0), dict(budget_cents=0),
    dict(budget_cents=100000001), dict(account_id='a_guest'), dict(key='短键'), dict(key='x'*65)])
def test_create_strict_explicit_input_and_owned_account(env, changes):
    before = assets(env)
    with pytest.raises(DomainError):
        create(env, **changes)
    assert assets(env) == before


def test_create_idempotency_does_not_revive_paused_or_changed_intent(env):
    f = create(env)
    with env[0].transaction() as db:
        env[2].cas_flow(db, 'demo', f['id'], 1, {'status': 'PAUSED'})
    assert create(env)['status'] == 'PAUSED'
    with pytest.raises(DomainError):
        create(env, budget_cents=99999)
    assert len(env[2].BirthdayStore(env[0]).list('demo')) == 1


def test_read_is_select_only_owned_and_survives_restart(env):
    f = create(env)
    before = assets(env)
    with env[0].transaction() as db:
        before_meta = [[tuple(r) for r in db.execute('SELECT * FROM ' + t)] for t in
                       ('birthday_flows', 'birthday_nodes', 'birthday_tickets', 'birthday_requests', 'audit')]
    env[1][0] += timedelta(days=2)
    b2 = Bank(env[0].path, clock=lambda: env[1][0])
    store = env[2].BirthdayStore(b2)
    assert store.get('demo', f['id'])['id'] == f['id']
    assert store.list('guest') == []
    with pytest.raises(DomainError):
        store.get('guest', f['id'])
    with env[0].transaction() as db:
        assert before_meta == [[tuple(r) for r in db.execute('SELECT * FROM ' + t)] for t in
                              ('birthday_flows', 'birthday_nodes', 'birthday_tickets', 'birthday_requests', 'audit')]
    assert assets(env) == before


@pytest.mark.parametrize('version,changes', [(True, {'status': 'PAUSED'}), (0, {'status': 'PAUSED'}),
    (2, {'status': 'PAUSED'}), (1, {'budget_cents': 1}), (1, {'risk_epoch': 999}),
    (1, {'graph_json': '[]'}), (1, {'status': 'NOPE'}), (1, {'cake_choice': 'yes'})])
def test_flow_cas_rejects_stale_bool_and_immutable_fields(env, version, changes):
    f = create(env)
    with env[0].transaction() as db, pytest.raises(DomainError):
        env[2].cas_flow(db, 'demo', f['id'], version, changes)
    assert flowrow(env, f['id'])['version'] == 1


@pytest.mark.parametrize('idx,changes', [(0, {'state': 'SUCCEEDED'}), (4, {'state': 'SUCCEEDED'}),
    (1, {'state': 'SUCCEEDED'}), (3, {'state': 'SKIPPED'}), (0, {'operation_id': 'op_fake'}),
    (0, {'claim_version': 2}), (2, {'price_cents': True}), (2, {'quote_version': True})])
def test_node_cas_cannot_invent_payment_or_skip_unresolved_ancestors(env, idx, changes):
    f = create(env)
    with env[0].transaction() as db, pytest.raises(DomainError):
        env[2].cas_node(db, 'demo', f['id'], idx, 1, changes)
    assert nodes(env, f['id'])[idx]['state'] == 'PENDING'


def test_claim_ready_once_under_parallel_callers(env):
    f = create(env)
    def claim(_):
        try:
            with env[0].transaction() as db:
                return env[2].claim_node(env[0], db, 'demo', f['id'], 0, 1, 1)
        except DomainError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(x is not None for x in pool.map(claim, range(2))) == 1
    n = nodes(env, f['id'])[0]
    assert n['state'] == 'RUNNING' and n['version'] == n['claim_version'] == 2
    assert flowrow(env, f['id'])['version'] == 1


@pytest.mark.parametrize('idx', [1, 4, 5, True, -1, 7])
def test_claim_requires_ready_financial_node(env, idx):
    f = create(env)
    with env[0].transaction() as db, pytest.raises(DomainError):
        env[2].claim_node(env[0], db, 'demo', f['id'], idx, 1, 1)


def test_issued_capability_is_memory_only_single_use_and_json_is_not_authority(env):
    f = create(env)
    bank, _, s, t = env
    with bank.transaction() as db:
        s.claim_node(bank, db, 'demo', f['id'], 0, 1, 1)
        binding = t.issue_ticket(bank, db, 'demo', f['id'], 0)
        assert len(binding.token) >= 32 and binding.effect_key.startswith('bday-')
        row = dict(db.execute('SELECT * FROM birthday_tickets').fetchone())
        assert binding.token not in json.dumps(row) and row['token_hash'] != binding.token
        for bad in (dict(ticket_id=binding.ticket_id, token=binding.token, effect_key=binding.effect_key),
                    t.BirthdayBinding(binding.ticket_id, 'wrong', binding.effect_key)):
            with pytest.raises(DomainError):
                t.assert_claim(bank, db, 'demo', bad, binding.effect_key, 'birthday_budget_reserve')
        with pytest.raises(DomainError):
            t.assert_claim(bank, db, 'guest', binding, binding.effect_key, 'birthday_budget_reserve')
        op = BirthdayBudgets(bank)._prepare_locked(db, 'demo', 'a_daily', 100000, '2026-10-29', binding.effect_key, binding)
        with pytest.raises(DomainError):
            t.assert_claim(bank, db, 'demo', binding, binding.effect_key, 'birthday_budget_reserve')
    assert op['status'] == 'WAITING_CONFIRMATION'
    assert binding.token not in env[0].path.read_bytes().decode('latin1')


def test_reserved_keys_cannot_be_adopted_by_public_prepare_or_raw_create(env):
    bank = env[0]
    with pytest.raises(DomainError):
        BirthdayBudgets(bank).prepare('demo', 'a_daily', 100000, '2026-10-29', 'bday-forged')
    with bank.transaction() as db, pytest.raises(DomainError):
        bank._create(db, 'demo', 'birthday_budget_reserve', dict(account_id='a_daily', account_name='日常账户',
            amount_cents=100000, event_date='2026-10-29', risk_epoch=bank._safety_epoch(db, 'demo'), simulated=True), 'YELLOW', 'bday-raw')
    assert assets(env)['operations'] == []


def test_claim_ticket_prepare_binding_is_one_atomic_transaction(env):
    f = create(env)
    bank, _, s, t = env
    with pytest.raises(RuntimeError, match='injected rollback'):
        with bank.transaction() as db:
            s.claim_node(bank, db, 'demo', f['id'], 0, 1, 1)
            binding = t.issue_ticket(bank, db, 'demo', f['id'], 0)
            BirthdayBudgets(bank)._prepare_locked(db, 'demo', 'a_daily', 100000, '2026-10-29', binding.effect_key, binding)
            raise RuntimeError('injected rollback')
    assert nodes(env, f['id'])[0]['state'] == 'PENDING'
    with bank.transaction() as db:
        assert db.execute('SELECT count(*) FROM birthday_tickets').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 0
    _, op = prepare_budget(env, f)
    assert nodes(env, f['id'])[0]['operation_id'] == op['id']


def test_budget_confirmation_is_real_hold_and_sync_version_preserves_verification_binding(env):
    f = create(env, budget_cents=200000)
    before = assets(env)['accounts']
    binding, op = prepare_budget(env, f)
    assert account(env)['reserved_cents'] == 0
    confirm = env[0].confirm('demo', op['id'])
    assert confirm['status'] == 'WAITING_VERIFICATION'
    with env[0].transaction() as db:
        env[2].sync_nodes(env[0], db, 'demo', f['id'])
    n = nodes(env, f['id'])[0]
    assert n['version'] > n['claim_version'] == 2
    done = env[0].verify('demo', op['id'], '246810')
    assert done['status'] == 'SUCCEEDED' and account(env)['reserved_cents'] == 200000
    assert assets(env)['accounts'] == before
    assert env[0].confirm('demo', op['id']) == done
    assert env[2].BirthdayStore(env[0]).get('demo', f['id'])['nodes'][0]['observed_state'] == 'SUCCEEDED'


@pytest.mark.parametrize('change', ['pause', 'cancel', 'epoch', 'expiry', 'recipient', 'payload'])
def test_bound_budget_rechecks_current_source_before_money_effect(env, change):
    f = create(env)
    _, op = prepare_budget(env, f)
    bank = env[0]
    if change in ('pause', 'cancel'):
        with bank.transaction() as db:
            env[2].cas_flow(db, 'demo', f['id'], 1, {'status': 'PAUSED' if change == 'pause' else 'CANCELLED'})
    elif change == 'epoch':
        bank.activate_safety_hold('demo', {'codes': ['SCAM'], 'reasons': ['测试'], 'advice': '核对'}, 'epoch-change')
    elif change == 'expiry':
        env[1][0] += timedelta(seconds=301)
    elif change == 'recipient':
        with bank.transaction() as db:
            db.execute("UPDATE birthday_flows SET recipient_label='爸爸' WHERE id=?", (f['id'],))
    else:
        with bank.transaction() as db:
            p = dict(op['payload']); p['amount_cents'] = 1
            db.execute('UPDATE operations SET payload=? WHERE id=?', (json.dumps(p, ensure_ascii=False, sort_keys=True), op['id']))
    before = assets(env)
    with pytest.raises(DomainError):
        bank.confirm('demo', op['id'])
    assert assets(env)['accounts'] == before['accounts']
    assert assets(env)['birthday_budgets'] == []


def test_orders_use_confirmed_budget_real_catalog_and_one_ledger_debit(env):
    f = create(env)
    bid = ready_order(env, f)
    before = account(env)
    _, op = prepare_order(env, f, bid)
    assert op['payload']['budget_id'] == bid and op['payload']['amount_cents'] == 30000
    assert account(env)['reserved_cents'] == 100000
    done = complete(env, op)
    after = account(env)
    assert before['balance_cents'] - after['balance_cents'] == 30000
    assert after['reserved_cents'] == 70000 and after['available_cents'] == before['available_cents']
    assert len(BirthdayOrders(env[0]).list('demo', bid)) == 1
    assert env[0].confirm('demo', op['id']) == done
    assert env[2].BirthdayStore(env[0]).get('demo', f['id'])['nodes'][4]['observed_state'] == 'SUCCEEDED'


@pytest.mark.parametrize('change', ['quote', 'parent_version', 'parent_cancel', 'node_quote'])
def test_order_ticket_binds_actual_quote_and_current_parent_version(env, monkeypatch, change):
    f = create(env)
    bid = ready_order(env, f)
    _, op = prepare_order(env, f, bid)
    if change == 'quote':
        m = importlib.import_module('ff_agent.birthday_orders')
        monkeypatch.setattr(m, 'CATALOG', {**m.CATALOG, 'flowers_simple': {**m.CATALOG['flowers_simple'], 'quote_version': 2}})
    elif change == 'parent_cancel':
        BirthdayBudgets(env[0]).cancel('demo', bid, 1)
    else:
        with env[0].transaction() as db:
            if change == 'parent_version':
                db.execute('UPDATE birthday_budgets SET version=version+1 WHERE id=?', (bid,))
            else:
                db.execute('UPDATE birthday_nodes SET price_cents=1 WHERE flow_id=? AND idx=2', (f['id'],))
    before = assets(env)
    with pytest.raises(DomainError):
        complete(env, op)
    assert assets(env)['accounts'] == before['accounts'] and BirthdayOrders(env[0]).list('demo', bid) == []


def test_observation_is_historical_but_never_upgrades_unknown_even_with_real_ledger(env):
    f = create(env)
    bid = ready_order(env, f)
    _, op = prepare_order(env, f, bid)
    complete(env, op)
    BirthdayBudgets(env[0]).cancel('demo', bid, 1)
    with env[0].transaction() as db:
        env[2].cas_flow(db, 'demo', f['id'], 2, {'status': 'CANCELLED'})
    store = env[2].BirthdayStore(env[0])
    assert store.get('demo', f['id'])['nodes'][4]['observed_state'] == 'SUCCEEDED'
    with env[0].transaction() as db:
        db.execute("UPDATE birthday_nodes SET state='UNKNOWN' WHERE flow_id=? AND idx=4", (f['id'],))
        env[2].sync_nodes(env[0], db, 'demo', f['id'])
    assert store.get('demo', f['id'])['nodes'][4]['observed_state'] == 'UNKNOWN'
    assert nodes(env, f['id'])[4]['state'] == 'UNKNOWN' and len(BirthdayOrders(env[0]).list('demo', bid)) == 1


def test_standalone_legacy_budget_and_order_still_use_explicit_confirmation(env):
    op = BirthdayBudgets(env[0]).prepare('demo', 'a_daily', 50000, '2026-10-29', 'legacy-budget')
    bid = complete(env, op)['result']['budget_id']
    order = BirthdayOrders(env[0]).prepare('demo', bid, 'flowers_simple', 1, 'legacy-order')
    assert complete(env, order)['result']['amount_cents'] == 30000



def test_competing_prepares_create_exactly_one_bound_operation(env):
    f = create(env)
    def run(_):
        try:
            return prepare_budget(env, f)[1]
        except DomainError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        got = [x for x in pool.map(run, range(2)) if x is not None]
    assert len(got) == 1 and account(env)['reserved_cents'] == 0
    with env[0].transaction() as db:
        assert db.execute('SELECT count(*) FROM birthday_tickets WHERE operation_id IS NOT NULL').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 1


def test_matching_payload_under_occupied_effect_key_cannot_be_adopted(env):
    f = create(env)
    bank, _, s, t = env
    with bank.transaction() as db:
        s.claim_node(bank, db, 'demo', f['id'], 0, 1, 1)
        binding = t.issue_ticket(bank, db, 'demo', f['id'], 0)
    old = BirthdayBudgets(bank).prepare('demo', 'a_daily', 100000, '2026-10-29', 'unrelated-operation')
    with bank.transaction() as db:
        db.execute('UPDATE operations SET idempotency_key=? WHERE id=?', (binding.effect_key, old['id']))
        with pytest.raises(DomainError):
            BirthdayBudgets(bank)._prepare_locked(db, 'demo', 'a_daily', 100000, '2026-10-29', binding.effect_key, binding)
        assert db.execute('SELECT operation_id FROM birthday_tickets').fetchone()[0] is None
    assert nodes(env, f['id'])[0]['operation_id'] is None


def test_parent_budget_version_is_read_from_actual_confirmed_record(env):
    f = create(env)
    bid = ready_order(env, f)
    with env[0].transaction() as db:
        db.execute('UPDATE birthday_budgets SET version=7 WHERE id=?', (bid,))
    _, op = prepare_order(env, f, bid)
    assert op['payload']['budget_version'] == 7
    assert complete(env, op)['result']['amount_cents'] == 30000


@pytest.mark.parametrize('status', ['UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW'])
def test_late_ledger_is_not_permission_to_resolve_unknown_or_release_money(env, status):
    f = create(env)
    bid = ready_order(env, f)
    _, op = prepare_order(env, f, bid)
    complete(env, op)
    with env[0].transaction() as db:
        db.execute('UPDATE operations SET status=? WHERE id=?', (status, op['id']))
    before = assets(env)['accounts']
    assert env[2].BirthdayStore(env[0]).get('demo', f['id'])['nodes'][4]['observed_state'] == 'UNKNOWN'
    BirthdayBudgets(env[0]).cancel('demo', bid, 1)
    assert account(env)['reserved_cents'] == 30000
    with pytest.raises(DomainError):
        env[0].confirm('demo', op['id'])
    assert assets(env)['accounts'] == before and len(BirthdayOrders(env[0]).list('demo', bid)) == 1


def test_forged_success_without_real_budget_or_ledger_observes_unknown(env):
    f = create(env)
    _, op = prepare_budget(env, f)
    with env[0].transaction() as db:
        db.execute("UPDATE operations SET status='SUCCEEDED',result=? WHERE id=?",
                   (json.dumps(dict(budget_id='missing', simulated=True)), op['id']))
    assert env[2].BirthdayStore(env[0]).get('demo', f['id'])['nodes'][0]['observed_state'] == 'UNKNOWN'
    assert assets(env)['birthday_budgets'] == []


def test_skip_cake_and_join_summary_require_actual_flower_payment(env):
    f = create(env)
    bid = ready_order(env, f, cake=False)
    _, op = prepare_order(env, f, bid)
    complete(env, op)
    bank, _, s, _ = env
    with bank.transaction() as db:
        s.sync_nodes(bank, db, 'demo', f['id'])
        for idx in (3, 5):
            s.cas_node(db, 'demo', f['id'], idx, 1, {'state': 'SKIPPED'})
        s.cas_node(db, 'demo', f['id'], 6, 1, {'state': 'SUCCEEDED'})
        s.cas_flow(db, 'demo', f['id'], 2, {'status': 'COMPLETED'})
    got = s.BirthdayStore(bank).get('demo', f['id'])
    assert got['status'] == 'COMPLETED'
    assert [got['nodes'][i]['observed_state'] for i in (0, 3, 4, 5, 6)] == ['SUCCEEDED', 'SKIPPED', 'SUCCEEDED', 'SKIPPED', 'SUCCEEDED']
    assert len(BirthdayOrders(bank).list('demo', bid)) == 1


def test_bound_read_never_expires_or_changes_financial_records(env):
    f = create(env)
    _, op = prepare_budget(env, f)
    env[1][0] += timedelta(seconds=301)
    before = assets(env)
    store = env[2].BirthdayStore(env[0])
    store.get('demo', f['id']); store.list('demo')
    assert assets(env) == before


def test_locked_helpers_refuse_autocommit_connection(env):
    from ff_agent.db import connect
    f = create(env)
    bank, _, s, t = env
    db = connect(bank.path)
    try:
        calls = [lambda: s.cas_flow(db, 'demo', f['id'], 1, {'status': 'PAUSED'}),
                 lambda: s.cas_node(db, 'demo', f['id'], 0, 1, {'state': 'CANCELLED'}),
                 lambda: s.claim_node(bank, db, 'demo', f['id'], 0, 1, 1),
                 lambda: s.sync_nodes(bank, db, 'demo', f['id']),
                 lambda: t.issue_ticket(bank, db, 'demo', f['id'], 0),
                 lambda: BirthdayBudgets(bank)._prepare_locked(db, 'demo', 'a_daily', 1, '2026-10-29', 'no-transaction'),
                 lambda: BirthdayBudgets(bank)._cancel_locked(db, 'demo', 'missing', 1),
                 lambda: BirthdayOrders(bank)._prepare_locked(db, 'demo', 'missing', 'flowers_simple', 1, 'no-transaction')]
        for call in calls:
            assert not db.in_transaction
            with pytest.raises(DomainError):
                call()
    finally:
        db.close()


@pytest.mark.parametrize('node_idx', [1, 6])
def test_catalog_fields_cannot_be_attached_to_choice_or_summary(env, node_idx):
    f = create(env)
    with env[0].transaction() as db, pytest.raises(DomainError):
        env[2].cas_node(db, 'demo', f['id'], node_idx, 1,
                        {'item_id': 'flowers_simple', 'price_cents': 'abc'})


def test_latest_unknown_bank_fact_blocks_summary_and_completed_flow(env):
    f = create(env)
    bid = ready_order(env, f)
    _, op = prepare_order(env, f, bid)
    complete(env, op)
    bank, _, s, _ = env
    with bank.transaction() as db:
        s.sync_nodes(bank, db, 'demo', f['id'])
        for idx in (3, 5):
            s.cas_node(db, 'demo', f['id'], idx, 1, {'state': 'SKIPPED'})
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?", (op['id'],))
    with bank.transaction() as db, pytest.raises(DomainError):
        s.cas_node(db, 'demo', f['id'], 6, 1, {'state': 'SUCCEEDED'})
    with bank.transaction() as db:
        # Deliberately corrupted terminal summary fixture: the flow gate must
        # independently check actual financial proof, not trust the label.
        db.execute("UPDATE birthday_nodes SET state='SUCCEEDED' WHERE flow_id=? AND idx=6", (f['id'],))
        with pytest.raises(DomainError):
            s.cas_flow(db, 'demo', f['id'], 2, {'status': 'COMPLETED'})
        s.sync_nodes(bank, db, 'demo', f['id'])
    assert nodes(env, f['id'])[4]['state'] == 'UNKNOWN'


def test_expired_selection_can_be_explicitly_requoted_before_any_order_claim(env):
    f = create(env)
    bid = ready_order(env, f)
    env[1][0] += timedelta(seconds=250)
    with pytest.raises(DomainError):
        prepare_order(env, f, bid)
    assert nodes(env, f['id'])[4]['state'] == 'PENDING'
    s = env[2]
    assert hasattr(s, 'reselect_node'), 'explicit requote helper not implemented'
    before = assets(env)
    with env[0].transaction() as db:
        s.reselect_node(env[0], db, 'demo', f['id'], 2, 2, 2, 'flowers_simple')
    assert assets(env) == before
    _, op = prepare_order(env, f, bid)
    assert complete(env, op)['result']['amount_cents'] == 30000
    with env[0].transaction() as db, pytest.raises(DomainError):
        n = s.load_nodes(db, 'demo', f['id'])[2]
        s.reselect_node(env[0], db, 'demo', f['id'], 2, 2, n['version'], 'flowers_large')


def test_both_explicit_gifts_join_only_after_both_real_payments(env):
    f = create(env)
    bid = ready_order(env, f, cake=True)
    bank, _, s, t = env
    with bank.transaction() as db:
        item = CATALOG['cake_small']
        s.cas_node(db, 'demo', f['id'], 3, 1, dict(state='SUCCEEDED', item_id='cake_small',
                   quote_version=item['quote_version'], price_cents=item['price_cents'],
                   quote_expires_at=bank.now().timestamp() + 240))
    _, flowers = prepare_order(env, f, bid)
    with bank.transaction() as db:
        s.claim_node(bank, db, 'demo', f['id'], 5, 2, 1)
        binding = t.issue_ticket(bank, db, 'demo', f['id'], 5)
        cake = BirthdayOrders(bank)._prepare_locked(db, 'demo', bid, 'cake_small', 1, binding.effect_key, binding)
    complete(env, flowers)
    with bank.transaction() as db:
        s.sync_nodes(bank, db, 'demo', f['id'])
        with pytest.raises(DomainError):
            s.cas_node(db, 'demo', f['id'], 6, 1, {'state': 'SUCCEEDED'})
    complete(env, cake)
    with bank.transaction() as db:
        s.sync_nodes(bank, db, 'demo', f['id'])
        s.cas_node(db, 'demo', f['id'], 6, 1, {'state': 'SUCCEEDED'})
        s.cas_flow(db, 'demo', f['id'], 2, {'status': 'COMPLETED'})
    assert BirthdayBudgets(bank).get('demo', bid)['spent_cents'] == 50000
    assert len(BirthdayOrders(bank).list('demo', bid)) == 2


def test_explicit_reselection_requires_the_callers_transaction(env):
    from ff_agent.db import connect
    f = create(env)
    ready_order(env, f)
    db = connect(env[0].path)
    try:
        assert not db.in_transaction
        with pytest.raises(DomainError):
            env[2].reselect_node(env[0], db, 'demo', f['id'], 2, 2, 2, 'flowers_simple')
    finally:
        db.close()

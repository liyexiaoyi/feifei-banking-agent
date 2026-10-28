from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ff_agent.bank import Bank, DomainError


def make_bank(tmp_path):
    now = [datetime(2026, 10, 25, 12, 0, tzinfo=ZoneInfo('Asia/Shanghai'))]
    bank = Bank(tmp_path / 'cards.db', clock=lambda: now[0])
    return bank, now


def own_card(bank):
    return next(c for c in bank.list_cards('demo') if c['id'] == 'card_a_daily')


def prepare(bank, key, action='report_lost', version=None):
    if version is None:
        version = own_card(bank)['version']
    return bank.prepare_card_status('demo', 'card_a_daily', action, key, version)


def execute(bank, op):
    confirmed = bank.confirm('demo', op['id'])
    assert confirmed['risk'] == 'RED'
    assert confirmed['status'] == 'WAITING_VERIFICATION'
    return bank.verify('demo', op['id'], '246810')


def finances(bank):
    snap = bank.snapshot('demo')
    balances = [(a['balance_cents'], a['reserved_cents']) for a in snap['accounts']]
    with bank.transaction() as db:
        rows = [tuple(r) for r in db.execute('SELECT id, account_id, kind, amount_cents, operation_id FROM transactions WHERE user_id=? ORDER BY id', ('demo',))]
    return balances, rows


def test_cards_are_owned_masked_and_cross_user_access_is_hidden(tmp_path):
    bank, _ = make_bank(tmp_path)
    demo = bank.list_cards('demo')
    guest = bank.list_cards('guest')
    assert {c['id'] for c in demo} == {'card_a_daily', 'card_a_saving'}
    assert {c['id'] for c in guest} == {'card_b_daily', 'card_b_saving'}
    assert set(demo[0]) == {
        'id', 'account_id', 'name', 'masked_number', 'status', 'version', 'simulated'
    }
    assert demo[0]['simulated'] is True
    assert 'pan' not in str(demo[0]).lower()
    assert 'cvv' not in str(demo[0]).lower()
    assert not demo[0]['masked_number'].isdigit()
    with pytest.raises(DomainError) as error:
        bank.prepare_card_status('guest', 'card_a_daily', 'report_lost', 'foreign', demo[0]['version'])
    assert error.value.code == 'NOT_FOUND'
    with pytest.raises(DomainError) as error:
        bank.prepare_card_status('demo', 'card_b_daily', 'report_lost', 'foreign', guest[0]['version'])
    assert error.value.code == 'NOT_FOUND'


def test_report_lost_requires_confirmation_and_simulated_red_verification(tmp_path):
    bank, _ = make_bank(tmp_path)
    before = finances(bank)
    op = prepare(bank, 'lost')
    assert op['type'] == 'card_report_lost'
    assert op['risk'] == 'RED'
    assert op['status'] == 'WAITING_CONFIRMATION'
    with pytest.raises(DomainError):
        bank.verify('demo', op['id'], '246810')
    assert own_card(bank)['status'] == 'ACTIVE'
    assert execute(bank, op)['status'] == 'SUCCEEDED'
    assert own_card(bank)['status'] == 'LOST'
    assert own_card(bank)['version'] == op['payload']['expected_version'] + 1
    assert bank.operation('demo', op['id'])['status'] == 'SUCCEEDED'
    assert bank.receipt('demo', op['id'])
    assert finances(bank) == before
    assert sum(e['event'] == 'CARD_STATUS_CHANGED' for e in bank.audit('demo')) == 1


def test_restore_increments_version_and_restart_preserves_cards(tmp_path):
    bank, now = make_bank(tmp_path)
    execute(bank, prepare(bank, 'lost'))
    lost = own_card(bank)
    restored = prepare(bank, 'restore', 'restore', lost['version'])
    assert restored['type'] == 'card_restore'
    assert execute(bank, restored)['status'] == 'SUCCEEDED'
    assert own_card(bank)['status'] == 'ACTIVE'
    assert own_card(bank)['version'] == lost['version'] + 1
    restarted = Bank(bank.path, clock=lambda: now[0])
    assert own_card(restarted) == own_card(bank)
    assert {c['id'] for c in restarted.list_cards('guest')} == {'card_b_daily', 'card_b_saving'}


def test_two_prepared_operations_cannot_execute_the_same_version(tmp_path):
    bank, _ = make_bank(tmp_path)
    version = own_card(bank)['version']
    first = prepare(bank, 'first', version=version)
    second = prepare(bank, 'second', version=version)
    execute(bank, first)
    with pytest.raises(DomainError) as error:
        execute(bank, second)
    assert error.value.status == 409
    assert own_card(bank)['version'] == version + 1
    assert sum(e['event'] == 'CARD_STATUS_CHANGED' for e in bank.audit('demo')) == 1


def test_expired_and_cancelled_operations_do_not_change_card(tmp_path):
    bank, now = make_bank(tmp_path)
    original = own_card(bank)
    cancelled = prepare(bank, 'cancelled')
    bank.cancel('demo', cancelled['id'])
    with pytest.raises(DomainError):
        bank.confirm('demo', cancelled['id'])
    expired = prepare(bank, 'expired')
    now[0] += timedelta(minutes=6)
    with pytest.raises(DomainError):
        bank.confirm('demo', expired['id'])
    assert bank.operation('demo', cancelled['id'])['status'] != 'SUCCEEDED'
    assert bank.operation('demo', expired['id'])['status'] != 'SUCCEEDED'
    assert own_card(bank) == original


def test_three_wrong_codes_lock_sensitive_operations(tmp_path):
    bank, _ = make_bank(tmp_path)
    op = prepare(bank, 'wrong-codes')
    bank.confirm('demo', op['id'])
    for _ in range(3):
        with pytest.raises(DomainError):
            bank.verify('demo', op['id'], '000000')
    with pytest.raises(DomainError) as error:
        prepare(bank, 'while-locked')
    assert error.value.status == 423
    assert own_card(bank)['status'] == 'ACTIVE'


def test_successful_key_replays_without_another_change_and_rejects_new_content(tmp_path):
    bank, _ = make_bank(tmp_path)
    version = own_card(bank)['version']
    op = prepare(bank, 'same-key', version=version)
    execute(bank, op)
    replay = prepare(bank, 'same-key', version=version)
    assert replay['id'] == op['id']
    assert replay['status'] == 'SUCCEEDED'
    assert own_card(bank)['version'] == version + 1
    with pytest.raises(DomainError) as error:
        prepare(bank, 'same-key', 'restore', version)
    assert error.value.code == 'IDEMPOTENCY_CONFLICT'
    assert sum(e['event'] == 'CARD_STATUS_CHANGED' for e in bank.audit('demo')) == 1


def test_version_validation_stale_prepare_and_operation_ownership(tmp_path):
    bank, _ = make_bank(tmp_path)
    version = own_card(bank)['version']
    with pytest.raises(DomainError):
        prepare(bank, 'boolean-version', version=True)
    op = prepare(bank, 'owned', version=version)
    for method in (bank.operation, bank.receipt, bank.cancel):
        with pytest.raises(DomainError) as error:
            method('guest', op['id'])
        assert error.value.code == 'NOT_FOUND'
    execute(bank, op)
    with pytest.raises(DomainError) as error:
        prepare(bank, 'stale', version=version)
    assert error.value.status == 409


def test_key_already_bound_to_transfer_is_an_idempotency_conflict(tmp_path):
    bank, _ = make_bank(tmp_path)
    bank.prepare_transfer('demo', 'p_mom', 100, 'shared-key')
    with pytest.raises(DomainError) as error:
        prepare(bank, 'shared-key')
    assert error.value.code == 'IDEMPOTENCY_CONFLICT'
    assert own_card(bank)['status'] == 'ACTIVE'


def test_replaying_an_expired_prepare_key_cannot_present_a_live_operation(tmp_path):
    bank, now = make_bank(tmp_path)
    op = prepare(bank, 'expire-replay')
    now[0] += timedelta(minutes=6)
    replay = prepare(bank, 'expire-replay', version=op['payload']['expected_version'])
    assert replay['id'] == op['id']
    assert replay['status'] == 'EXPIRED'
    assert own_card(bank)['status'] == 'ACTIVE'


def test_concurrent_repeated_preparation_and_verification_change_card_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    bank, _ = make_bank(tmp_path)
    before = finances(bank)
    with ThreadPoolExecutor(max_workers=6) as pool:
        operations = list(pool.map(lambda _: prepare(bank, 'parallel', version=1), range(6)))
    assert len({op['id'] for op in operations}) == 1
    opid = operations[0]['id']
    bank.confirm('demo', opid)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: bank.verify('demo', opid, '246810'), range(6)))
    assert all(result['status'] == 'SUCCEEDED' for result in results)
    assert own_card(bank)['version'] == 2
    assert sum(e['event'] == 'CARD_STATUS_CHANGED' for e in bank.audit('demo')) == 1
    assert finances(bank) == before


def test_unknown_card_result_is_read_only_and_never_replayed(tmp_path):
    bank, _ = make_bank(tmp_path)
    op = prepare(bank, 'unknown')
    with bank.transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?", (op['id'],))
    for method in (lambda: prepare(bank, 'unknown', version=1),
                   lambda: bank.confirm('demo', op['id']),
                   lambda: bank.verify('demo', op['id'], '246810'),
                   lambda: bank.cancel('demo', op['id'])):
        with pytest.raises(DomainError):
            method()
    assert bank.receipt('demo', op['id'])['observed_status'] == 'UNKNOWN'
    assert own_card(bank)['status'] == 'ACTIVE'


def test_risk_hold_does_not_restore_a_lost_card_or_change_money(tmp_path):
    bank, _ = make_bank(tmp_path)
    execute(bank, prepare(bank, 'lost-before-risk'))
    before = finances(bank)
    card = own_card(bank)
    bank.activate_safety_hold('demo', {'codes':['FAKE_CUSTOMER_SERVICE'],
        'reasons':['合成测试：客服要求转安全账户'], 'advice':'先核对，不按陌生人要求转账。'}, 'fraud')
    assert own_card(bank) == card
    assert finances(bank) == before


def test_a_new_key_cannot_bypass_an_unreconciled_card_operation(tmp_path):
    bank, _ = make_bank(tmp_path)
    old = prepare(bank, 'unreconciled')
    with bank.transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?", (old['id'],))
    with pytest.raises(DomainError) as error:
        prepare(bank, 'a-different-key', version=1)
    assert error.value.status == 409
    with bank.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM operations WHERE type='card_report_lost'").fetchone()[0] == 1
    assert own_card(bank)['status'] == 'ACTIVE'


@pytest.mark.parametrize('status',['READY','EXECUTING','MANUAL_REVIEW'])
def test_card_executor_fails_closed_for_unsettled_internal_states(tmp_path,status):
    from ff_agent.cards import CardService
    bank, _ = make_bank(tmp_path)
    op = prepare(bank, 'guard-state')
    bank.confirm('demo',op['id'])
    with bank.transaction() as db:
        db.execute('UPDATE operations SET status=? WHERE id=?',(status,op['id']))
        row=dict(db.execute('SELECT * FROM operations WHERE id=?',(op['id'],)).fetchone())
        with pytest.raises(DomainError):
            CardService(bank).execute(db,'demo',row)
    assert own_card(bank)['status'] == 'ACTIVE'


@pytest.mark.parametrize('status',['UNKNOWN','EXECUTING','MANUAL_REVIEW'])
@pytest.mark.parametrize('action',['report_lost','restore'])
def test_prepared_sibling_cannot_execute_while_card_result_is_unreconciled(tmp_path,status,action):
    bank, _ = make_bank(tmp_path)
    if action=='restore':
        execute(bank,prepare(bank,'setup-loss'))
    version=own_card(bank)['version']
    first=prepare(bank,'first-before-unknown',action,version)
    second=prepare(bank,'second-before-unknown',action,version)
    before_card=own_card(bank)
    before_money=finances(bank)
    before_changes=sum(e['event']=='CARD_STATUS_CHANGED' for e in bank.audit('demo'))
    with bank.transaction() as db:
        db.execute('UPDATE operations SET status=? WHERE id=?',(status,first['id']))
    with pytest.raises(DomainError) as error:
        execute(bank,second)
    assert error.value.code=='CARD_RESULT_PENDING'
    assert own_card(bank)==before_card
    assert finances(bank)==before_money
    assert sum(e['event']=='CARD_STATUS_CHANGED' for e in bank.audit('demo'))==before_changes


def test_prepared_restore_waits_for_unreconciled_prior_loss_even_at_new_version(tmp_path):
    bank, _ = make_bank(tmp_path)
    first=prepare(bank,'prior-loss')
    execute(bank,first)
    second=prepare(bank,'restore-before-unknown','restore',own_card(bank)['version'])
    before_card=own_card(bank)
    with bank.transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?",(first['id'],))
    with pytest.raises(DomainError) as error:
        execute(bank,second)
    assert error.value.code=='CARD_RESULT_PENDING'
    assert own_card(bank)==before_card

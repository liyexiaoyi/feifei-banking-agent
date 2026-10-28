from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from ff_agent.bank import Bank, DomainError, TZ
from ff_agent.card_chat_controls import CardControls


STOP = {'type': 'card_stop', 'card': '未指定', 'action': 'report_lost'}
CORRECT = {'type': 'card_correct', 'card': '储蓄账户'}


def fixture(tmp_path):
    now = [datetime(2026, 10, 27, 1, tzinfo=TZ)]
    bank = Bank(tmp_path/'card-control.db', clock=lambda: now[0])
    return bank, CardControls(bank), now


def source(bank, key='old', card='card_a_daily', user='demo'):
    return bank.prepare_card_status(user, card, 'report_lost', key, 1)


def events(bank):
    return [e for e in bank.audit('demo') if e['event'] == 'OPERATION_CANCELLED']


def test_cancel_and_journal_are_committed_together_and_replay_is_not_another_cancel(tmp_path):
    bank, controls, _ = fixture(tmp_path)
    op = source(bank)
    result = controls.cancel('demo', 'stop', 'hash', STOP, [op['id']])
    assert result['phase'] == 'cancelled' and result['source_ids'] == [op['id']]
    assert bank.operation('demo', op['id'])['status'] == 'CANCELLED'
    new = source(bank, 'new')
    replay = controls.cancel('demo', 'stop', 'hash', STOP, [new['id']])
    assert replay['source_ids'] == [op['id']]
    assert bank.operation('demo', new['id'])['status'] == 'WAITING_CONFIRMATION'
    assert len(events(bank)) == 1
    assert controls.load('demo', 'stop', 'hash')['source_ids'] == [op['id']]


@pytest.mark.parametrize('status', ['UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW', 'SUCCEEDED', 'FAILED', 'CANCELLED'])
def test_forbidden_source_status_cannot_be_claimed_or_reported_cancelled(tmp_path, status):
    bank, controls, _ = fixture(tmp_path)
    op = source(bank)
    with bank.transaction() as db:
        db.execute('UPDATE operations SET status=? WHERE id=?', (status, op['id']))
    with pytest.raises(DomainError):
        controls.cancel('demo', 'stop', 'hash', STOP, [op['id']])
    assert bank.operation('demo', op['id'])['status'] == status
    assert controls.load('demo', 'stop', 'hash') is None
    assert events(bank) == []


def test_all_stop_preflights_every_source_and_rolls_back_if_any_is_unknown(tmp_path):
    bank, controls, _ = fixture(tmp_path)
    first = source(bank)
    second = source(bank, 'second', 'card_a_saving')
    with bank.transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?", (second['id'],))
    with pytest.raises(DomainError):
        controls.cancel('demo', 'all', 'hash', {**STOP, 'card': '全部'}, [first['id'], second['id']])
    assert bank.operation('demo', first['id'])['status'] == 'WAITING_CONFIRMATION'
    assert controls.load('demo', 'all', 'hash') is None
    with bank.transaction() as db:
        assert db.execute('SELECT count(*) FROM card_chat_claims').fetchone()[0] == 0


@pytest.mark.parametrize('kind', ['foreign', 'transfer', 'expired'])
def test_nonowned_noncard_or_expired_source_is_never_cancelled(tmp_path, kind):
    bank, controls, now = fixture(tmp_path)
    op = source(bank, user='guest', card='card_b_daily') if kind == 'foreign' else (
        bank.prepare_transfer('demo', 'p_mom', 100, 'transfer') if kind == 'transfer' else source(bank))
    if kind == 'expired':
        now[0] += timedelta(seconds=601)
    with pytest.raises(DomainError):
        controls.cancel('demo', 'stop', 'hash', STOP, [op['id']])
    assert events(bank) == []


def test_same_key_different_hash_or_action_rejected_without_another_effect(tmp_path):
    bank, controls, _ = fixture(tmp_path)
    op = source(bank)
    controls.cancel('demo', 'stop', 'hash', STOP, [op['id']])
    with pytest.raises(DomainError):
        controls.load('demo', 'stop', 'different')
    with pytest.raises(DomainError):
        controls.cancel('demo', 'stop', 'hash', {**STOP, 'action': 'restore'}, [op['id']])
    assert len(events(bank)) == 1


@pytest.mark.parametrize('same_key', [False, True])
def test_concurrent_claims_have_one_effect_and_only_same_key_replays(tmp_path, same_key):
    bank, controls, _ = fixture(tmp_path)
    op = source(bank)
    def run(index):
        try:
            return controls.cancel('demo', 'shared' if same_key else f'key-{index}', 'hash', STOP, [op['id']])
        except DomainError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, (0, 1)))
    assert sum(r is not None for r in results) == (2 if same_key else 1)
    assert len(events(bank)) == 1
    with bank.transaction() as db:
        assert db.execute('SELECT count(*) FROM card_chat_claims').fetchone()[0] == 1


@pytest.mark.parametrize('target', [{'id': 'card_b_saving', 'version': 1}, {'id': 'card_a_saving', 'version': 99}])
def test_invalid_correction_target_cannot_cancel_the_old_request(tmp_path, target):
    bank, controls, _ = fixture(tmp_path)
    op = source(bank)
    with pytest.raises(DomainError):
        controls.cancel('demo', 'correct', 'hash', CORRECT, [op['id']], target)
    assert bank.operation('demo', op['id'])['status'] == 'WAITING_CONFIRMATION'


def test_prepare_receipt_is_bound_to_the_pinned_target_and_same_effect_key(tmp_path):
    bank, controls, _ = fixture(tmp_path)
    op = source(bank)
    result = controls.cancel('demo', 'correct', 'hash', CORRECT, [op['id']], {'id': 'card_a_saving', 'version': 1})
    assert result['source_action'] == 'report_lost'
    controls.record_prepare('demo', 'correct', error='CARD_CONFLICT')
    assert controls.load('demo', 'correct', 'hash')['phase'] == 'prepare_failed'
    wrong = source(bank, 'wrong', 'card_a_saving')
    with pytest.raises(DomainError):
        controls.record_prepare('demo', 'correct', operation_id=wrong['id'])
    new = bank.prepare_card_status('demo', 'card_a_saving', 'report_lost', 'correct', 1)
    result = controls.record_prepare('demo', 'correct', operation_id=new['id'])
    assert result['phase'] == 'prepared' and result['new_op_id'] == new['id']

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from threading import Event, current_thread

import pytest

from ff_agent.bank import Bank, DomainError, TZ
from ff_agent.card_chat import CardChat


ACTION = {'type': 'card_correct', 'card': '储蓄账户'}
TEXT = '不是那张是储蓄那张'


def fixture(tmp_path):
    bank = Bank(tmp_path/'chat-recovery.db', clock=lambda: datetime(2026, 10, 27, 2, tzinfo=TZ))
    chat = CardChat(bank)
    old = bank.prepare_card_status('demo', 'card_a_daily', 'report_lost', 'old', 1)
    context = {'recent_card': {'operation_id': old['id'], 'started_at': old['created_at']}}
    return bank, chat, old, context


def ops(result):
    return [c['operation'] for c in result['cards'] if c['type'] == 'operation']


@pytest.mark.parametrize('status', ['SUCCEEDED', 'UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW', 'EXPIRED'])
def test_forbidden_old_correction_source_never_creates_a_new_request(tmp_path, status):
    bank, chat, old, context = fixture(tmp_path)
    with bank.transaction() as db:
        db.execute('UPDATE operations SET status=? WHERE id=?', (status, old['id']))
    result = chat.handle('demo', ACTION, TEXT, 'correction', context)
    assert result['state'] == 'BLOCKED'
    assert len(bank.snapshot('demo')['operations']) == 1
    assert bank.operation('demo', old['id'])['status'] == status


@pytest.mark.parametrize('status', ['UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW'])
def test_unknown_new_correction_result_replays_receipt_without_cancelling_or_repreparing(tmp_path, status):
    bank, chat, old, context = fixture(tmp_path)
    first = chat.handle('demo', ACTION, TEXT, 'correction', context)
    new, = [op for op in ops(first) if op['id'] != old['id']]
    with bank.transaction() as db:
        db.execute('UPDATE operations SET status=? WHERE id=?', (status, new['id']))
    changed = {'recent_card': {'operation_id': new['id'], 'started_at': new['created_at']}}
    again = chat.handle('demo', ACTION, TEXT, 'correction', changed)
    assert again['state'] == 'BLOCKED'
    assert {op['id'] for op in ops(again)} == {old['id'], new['id']}
    assert bank.operation('demo', new['id'])['status'] == status
    assert len(bank.snapshot('demo')['operations']) == 2


@pytest.mark.parametrize('same_key', [False, True])
def test_concurrent_corrections_claim_old_once_and_same_key_does_not_cancel_the_new_request(tmp_path, same_key):
    bank, chat, old, context = fixture(tmp_path)
    original = deepcopy(context)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda i: chat.handle('demo', ACTION, TEXT, 'shared' if same_key else f'key-{i}', context), (0, 1)))
    assert context == original
    assert bank.operation('demo', old['id'])['status'] == 'CANCELLED'
    pending = [op for op in bank.snapshot('demo')['operations'] if op['status'] == 'WAITING_CONFIRMATION']
    assert len(pending) == 1 and pending[0]['payload']['card_id'] == 'card_a_saving'
    assert sum(r['state'] == 'WAITING_USER' for r in results) == (2 if same_key else 1)


def test_failed_new_prepare_keeps_the_original_target_version_instead_of_auto_refreshing_it(tmp_path, monkeypatch):
    bank, chat, old, context = fixture(tmp_path)
    prepare = bank.prepare_card_status
    def fail(*args, **kwargs):
        raise DomainError('TEST_PREPARE_FAILED', '测试准备失败')
    monkeypatch.setattr(bank, 'prepare_card_status', fail)
    first = chat.handle('demo', ACTION, TEXT, 'correction', context)
    assert first['state'] == 'BLOCKED' and bank.operation('demo', old['id'])['status'] == 'CANCELLED'
    monkeypatch.setattr(bank, 'prepare_card_status', prepare)
    changed = prepare('demo', 'card_a_saving', 'report_lost', 'separate-intent', 1)
    bank.confirm('demo', changed['id'])
    bank.verify('demo', changed['id'], '246810')
    again = chat.handle('demo', ACTION, TEXT, 'correction', context)
    assert again['state'] == 'BLOCKED'
    assert '未准备' in ''.join(again['lines'])
    assert len(bank.snapshot('demo')['operations']) == 2


def test_stop_does_not_hide_ambiguity_behind_the_bank_snapshot_limit(tmp_path):
    bank, chat, first, _ = fixture(tmp_path)
    second = bank.prepare_card_status('demo', 'card_a_saving', 'report_lost', 'second', 1)
    for index in range(31):
        bank.prepare_transfer('demo', 'p_mom', 100, f'transfer-{index}')
    result = chat.handle('demo', {'type':'card_stop','card':'未指定','action':'report_lost'}, '先不要挂失', 'stop', {})
    assert result['state'] == 'WAITING_USER' and result['working']['pending_card']['mode'] == 'stop'
    assert all(bank.operation('demo', op['id'])['status'] == 'WAITING_CONFIRMATION' for op in (first, second))


def test_a_stale_empty_journal_read_recovers_the_same_key_committed_by_another_caller(tmp_path, monkeypatch):
    bank, chat, old, context = fixture(tmp_path)
    read = chat.controls.load
    entered, release = Event(), Event()
    def hold_first_empty_read(*args):
        result = read(*args)
        if current_thread().name != 'MainThread' and not entered.is_set():
            assert result is None
            entered.set()
            assert release.wait(5)
        return result
    monkeypatch.setattr(chat.controls, 'load', hold_first_empty_read)
    with ThreadPoolExecutor(max_workers=1) as pool:
        delayed = pool.submit(chat.handle, 'demo', ACTION, TEXT, 'shared', context)
        try:
            assert entered.wait(5)
            first = chat.handle('demo', ACTION, TEXT, 'shared', context)
        finally:
            release.set()
        second = delayed.result(timeout=5)
    assert first['state'] == second['state'] == 'WAITING_USER'
    assert {op['id'] for op in ops(first)} == {op['id'] for op in ops(second)}
    assert len(bank.snapshot('demo')['operations']) == 2

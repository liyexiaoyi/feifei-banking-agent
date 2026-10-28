from datetime import datetime, timezone

import pytest

from ff_agent.bank import Bank, DomainError
from ff_agent.card_chat import CardChat


ACTION = {'type': 'card_status', 'card': '刚才那张', 'action': 'report_lost'}
TEXT = '挂失刚才那张'


def receipts(result):
    return [card['operation'] for card in result['cards'] if card['type'] == 'operation']


@pytest.mark.parametrize('status', ['WAITING_CONFIRMATION', 'UNKNOWN', 'EXECUTING'])
@pytest.mark.parametrize('new_context', ['missing', 'other-card'])
def test_original_card_receipt_survives_context_change_and_restart(tmp_path, status, new_context):
    path = tmp_path/'replay-identity.db'
    clock = lambda: datetime(2030, 1, 2, 12, tzinfo=timezone.utc)
    bank = Bank(path, clock=clock)
    chat = CardChat(bank)
    previous = bank.prepare_card_status('demo', 'card_a_daily', 'report_lost', 'previous', 1)
    context = {'recent_card': {'operation_id': previous['id'], 'started_at': previous['created_at']}}
    original, = receipts(chat.handle('demo', ACTION, TEXT, 'same-request', context))
    with bank.transaction() as db:
        db.execute('UPDATE operations SET status=? WHERE id=?', (status, original['id']))
    context = {}
    if new_context == 'other-card':
        other = bank.prepare_card_status('demo', 'card_a_saving', 'report_lost', 'other', 1)
        context = {'recent_card': {'operation_id': other['id'], 'started_at': other['created_at']}}
    count = len(bank.snapshot('demo')['operations'])
    again = CardChat(Bank(path, clock=clock)).handle('demo', ACTION, TEXT, 'same-request', context)
    replay, = receipts(again)
    assert (replay['id'], replay['payload']['card_id'], replay['status']) == (
        original['id'], 'card_a_daily', status)
    assert len(bank.snapshot('demo')['operations']) == count
    assert bank.list_cards('demo')[0]['status'] == 'ACTIVE'


def test_same_key_changed_original_text_is_rejected_even_if_selector_resolves_to_same_card(tmp_path):
    bank = Bank(tmp_path/'text-conflict.db')
    chat = CardChat(bank)
    old = bank.prepare_card_status('demo', 'card_a_daily', 'report_lost', 'old', 1)
    context = {'recent_card': {'operation_id': old['id'], 'started_at': old['created_at']}}
    original, = receipts(chat.handle('demo', ACTION, TEXT, 'same-key', context))
    changed = chat.handle('demo', ACTION, '挂失那张', 'same-key', context)
    assert changed['state'] == 'BLOCKED' and receipts(changed) == []
    assert bank.operation('demo', original['id'])['status'] == 'WAITING_CONFIRMATION'
    assert len(bank.snapshot('demo')['operations']) == 2


def test_failed_prepare_keeps_original_binding_when_context_is_lost(tmp_path, monkeypatch):
    bank = Bank(tmp_path/'failed-prepare.db')
    chat = CardChat(bank)
    old = bank.prepare_card_status('demo', 'card_a_daily', 'report_lost', 'old', 1)
    context = {'recent_card': {'operation_id': old['id'], 'started_at': old['created_at']}}
    original_prepare = bank.prepare_card_status
    def unavailable(*args, **kwargs):
        raise DomainError('TEST_UNAVAILABLE', '合成准备失败')
    monkeypatch.setattr(bank, 'prepare_card_status', unavailable)
    assert chat.handle('demo', ACTION, TEXT, 'failed-key', context)['state'] == 'BLOCKED'
    monkeypatch.setattr(bank, 'prepare_card_status', original_prepare)
    op, = receipts(chat.handle('demo', ACTION, TEXT, 'failed-key', {}))
    assert op['payload']['card_id'] == 'card_a_daily'
    assert op['payload']['expected_version'] == 1
    assert len(bank.snapshot('demo')['operations']) == 2


def test_parallel_changed_body_cannot_replace_first_bound_request(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    bank = Bank(tmp_path/'concurrent-prepare.db')
    chat = CardChat(bank)
    def invoke(card):
        action = {'type': 'card_status', 'card': card, 'action': 'report_lost'}
        return chat.handle('demo', action, '挂失' + card, 'shared-key', {})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, ['日常账户', '储蓄账户']))
    assert sorted(r['state'] for r in results) == ['BLOCKED', 'WAITING_USER']
    assert len(bank.snapshot('demo')['operations']) == 1
    with bank.transaction() as db:
        assert db.execute('SELECT count(*) FROM card_chat_preparations').fetchone()[0] == 1


def test_bound_receipt_does_not_remove_current_plan_ticket_requirement(tmp_path):
    from ff_agent.api import create_app
    from test_card_chat_auth import ticket_for, ACTION as STATIC_ACTION
    app = create_app(tmp_path/'owned-prepare.db')
    bank = app.state.bank
    plan, ticket, key = ticket_for(app.state.plans)
    chat = CardChat(bank)
    original, = receipts(chat.handle('demo', STATIC_ACTION, '挂失日常账户', key, {}, ticket))
    with bank.transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?", (original['id'],))
    # No ticket cannot read a plan-owned receipt through this preparation path.
    result = chat.handle('demo', STATIC_ACTION, '挂失日常账户', key, {}, None)
    assert result['state'] == 'BLOCKED' and receipts(result) == []
    assert receipts(chat.handle('demo', STATIC_ACTION, '挂失日常账户', key, {}, ticket))[0]['id'] == original['id']
    app.state.plans.control('demo', plan['id'], plan['version'], 'pause', '')
    with pytest.raises(DomainError):
        chat.handle('demo', STATIC_ACTION, '挂失日常账户', key, {}, ticket)

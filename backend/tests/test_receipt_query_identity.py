"""Status answers bind the stated payee instead of the most recent payment."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app
from ff_agent.bank import Bank
from ff_agent.memory import Memory


def setup_bank(tmp_path):
    bank = Bank(tmp_path / 'receipt-query.db', clock=lambda: datetime(2026, 10, 27, 12, tzinfo=ZoneInfo('Asia/Shanghai')))
    target = bank.prepare_transfer('demo', 'p_dad', 10000, 'older-dad')
    bank.cancel('demo', target['id'])
    other = bank.prepare_transfer('demo', 'p_mom', 20000, 'newer-mom')
    bank.confirm('demo', other['id'])
    return bank, Memory(bank), target, other


def operation_ids(reply):
    assert reply is not None
    return [c['operation']['id'] for c in reply['cards'] if c['type'] == 'operation']


def money_rows(bank):
    with bank.transaction() as db:
        return {t: [tuple(row) for row in db.execute('SELECT * FROM ' + t + ' ORDER BY id')]
                for t in ('accounts', 'transactions', 'operations', 'subscriptions', 'bank_cards')}


def test_phone_query_selects_that_payee_cancelled_payment(tmp_path):
    bank, memory, target, _ = setup_bank(tmp_path)
    before = money_rows(bank)
    reply = memory.handle('demo', '刚才给13800000002的转账成功了吗', 'query-phone')
    assert operation_ids(reply) == [target['id']]
    assert '已取消' in reply['message'] and '已完成' not in reply['message']
    assert money_rows(bank) == before


@pytest.mark.parametrize('query', [
    '刚才给13700000033的转账成功了吗',  # belongs to guest, not this user
    '刚才给13911119999的转账成功了吗',
    '刚才给朋友的转账成功了吗',
    '刚才给小王的转账成功了吗',
    '刚才给妈妈和爸爸的转账成功了吗',
    '刚才给13800000001和13800000002的转账成功了吗',
    '刚才给妈妈的朋友的转账成功了吗',
])
def test_unknown_foreign_or_multiple_payees_never_fall_back_to_latest(tmp_path, query):
    bank, memory, _, _ = setup_bank(tmp_path)
    before = money_rows(bank)
    reply = memory.handle('demo', query, 'query-ambiguous')
    assert operation_ids(reply) == []
    assert '已完成' not in reply['message']
    assert money_rows(bank) == before


def test_named_single_payee_and_generic_latest_keep_working(tmp_path):
    _, memory, dad, mom = setup_bank(tmp_path)
    assert operation_ids(memory.handle('demo', '刚才给爸爸的转账成功了吗', 'dad')) == [dad['id']]
    assert operation_ids(memory.handle('demo', '刚才转账成功了吗', 'latest')) == [mom['id']]


def test_payee_older_than_snapshot_window_is_still_found(tmp_path):
    bank, memory, dad, _ = setup_bank(tmp_path)
    for i in range(31):
        op = bank.prepare_transfer('demo', 'p_mom', 100, 'newer-' + str(i))
        bank.cancel('demo', op['id'])
    assert dad['id'] not in [x['id'] for x in bank.snapshot('demo')['operations']]
    reply = memory.handle('demo', '上次给13800000002的转账是什么状态', 'old-phone')
    assert operation_ids(reply) == [dad['id']]


def test_duplicate_name_requires_specific_payee(tmp_path):
    bank, memory, _, _ = setup_bank(tmp_path)
    for pid in ('p_lin_1', 'p_lin_2'):
        op = bank.prepare_transfer('demo', pid, 100, 'lin-' + pid)
        bank.cancel('demo', op['id'])
    reply = memory.handle('demo', '刚才给林晓的转账成功了吗', 'lin-ambiguous')
    assert operation_ids(reply) == []


def test_explicit_operation_id_is_not_replaced_with_latest(tmp_path):
    _, memory, target, _ = setup_bank(tmp_path)
    assert operation_ids(memory.handle('demo', '上次转账' + target['id'] + '成功了吗', 'exact-op')) == [target['id']]
    reply = memory.handle('demo', '上次转账op_' + '0' * 32 + '成功了吗', 'unknown-op')
    assert operation_ids(reply) == []


def test_explicit_amount_does_not_select_a_different_amount(tmp_path):
    _, memory, target, _ = setup_bank(tmp_path)
    reply = memory.handle('demo', '刚才给爸爸转100元成功了吗', 'same-amount')
    assert operation_ids(reply) == [target['id']]
    reply = memory.handle('demo', '刚才给爸爸转200元成功了吗', 'wrong-amount')
    assert operation_ids(reply) == []


def test_phone_query_through_authenticated_chat_does_not_return_wrong_payee(tmp_path):
    app = create_app(tmp_path / 'api-receipt-query.db')
    bank = app.state.bank
    target = bank.prepare_transfer('demo', 'p_dad', 10000, 'older-target')
    bank.cancel('demo', target['id'])
    other = bank.prepare_transfer('demo', 'p_mom', 20000, 'newer-other')
    bank.confirm('demo', other['id'])
    with TestClient(app) as client:
        session = client.post('/api/session', json={'user': 'demo'}).json()
        client.headers['X-CSRF-Token'] = session['csrf']
        before = money_rows(bank)
        request = {'text': '刚才给13800000002的转账成功了吗', 'request_key': 'identity-api-query'}
        response = client.post('/api/chat', json=request)
        assert response.status_code == 200
        assert operation_ids(response.json()) == [target['id']]
        assert client.post('/api/chat', json=request).json() == response.json()
        assert money_rows(bank) == before


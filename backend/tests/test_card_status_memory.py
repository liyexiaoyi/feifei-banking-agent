from datetime import datetime

import pytest

from ff_agent.bank import Bank, TZ
from ff_agent.memory import Memory


def fixture(tmp_path):
    bank = Bank(tmp_path / 'status-memory.db', clock=lambda: datetime(2026, 10, 27, 1, tzinfo=TZ))
    return Memory(bank), bank


def cardop(bank, card='card_a_daily', action='report_lost', key='card'):
    version = next(c['version'] for c in bank.list_cards('demo') if c['id'] == card)
    return bank.prepare_card_status('demo', card, action, key, version)


@pytest.mark.parametrize('status', ['WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'CANCELLED', 'UNKNOWN', 'MANUAL_REVIEW'])
def test_card_status_question_reads_bank_not_success_claims(tmp_path, status):
    memory, bank = fixture(tmp_path)
    op = cardop(bank)
    with bank.transaction() as db:
        db.execute('UPDATE operations SET status=? WHERE id=?', (status, op['id']))
    result = memory.handle('demo', '刚才挂失成功了吗', 'status')
    assert result is not None
    assert result['cards'][0]['operation']['id'] == op['id']
    assert result['cards'][0]['operation']['status'] == status
    assert '已完成' not in result['message']
    assert bank.list_cards('demo')[0]['status'] == 'ACTIVE'


def test_restore_status_does_not_select_newer_transfer_or_other_card(tmp_path):
    memory, bank = fixture(tmp_path)
    lost = cardop(bank)
    bank.confirm('demo', lost['id'])
    bank.verify('demo', lost['id'], '246810')
    restored = cardop(bank, action='restore', key='restore')
    cardop(bank, card='card_a_saving', key='saving')
    bank.prepare_transfer('demo', 'p_mom', 100, 'newer-transfer')
    result = memory.handle('demo', '刚才日常卡解除挂失成功了吗', 'status')
    assert result['cards'][0]['operation']['id'] == restored['id']
    assert '等待你的确认' in result['message']


def test_status_reads_matching_card_operation_beyond_snapshot_limit(tmp_path):
    memory, bank = fixture(tmp_path)
    op = cardop(bank)
    for i in range(31):
        bank.prepare_transfer('demo', 'p_mom', 100, f'transfer-{i}')
    assert op['id'] not in {o['id'] for o in bank.snapshot('demo')['operations']}
    result = memory.handle('demo', '刚才挂失操作的状态', 'status')
    assert result['cards'][0]['operation']['id'] == op['id']


def test_status_target_is_not_inferred_from_note_and_no_record_never_claims_success(tmp_path):
    memory, bank = fixture(tmp_path)
    foreign = bank.prepare_card_status('guest', 'card_b_daily', 'report_lost', 'foreign', 1)
    result = memory.handle('demo', '刚才挂失成功了吗', 'status')
    assert result['cards'] == []
    assert '已完成' not in result['message']
    assert foreign['id'] not in str(result)


def test_loss_and_restore_status_queries_filter_the_requested_action(tmp_path):
    memory, bank = fixture(tmp_path)
    op = cardop(bank)
    bank.confirm('demo', op['id'])
    bank.verify('demo', op['id'], '246810')
    cardop(bank, action='restore', key='restore')
    result = memory.handle('demo', '上次挂失成功了吗', 'status')
    assert result['cards'][0]['operation']['id'] == op['id']
    assert '已完成' in result['message']


def test_ambiguous_cancel_loss_question_does_not_pick_restore_or_cancel_claim(tmp_path):
    memory, bank = fixture(tmp_path)
    op = cardop(bank)
    result = memory.handle('demo', '刚才取消挂失成功了吗', 'status')
    assert result is None or not result['cards']
    assert bank.operation('demo', op['id'])['status'] == 'WAITING_CONFIRMATION'

from test_receipt_query_identity import setup_bank, operation_ids, money_rows

import pytest


@pytest.mark.parametrize('text', [
    '你还记得刚才那笔转账状态吗',
    '你记得上次转账成功了吗',
    '还记得最近转账结果吗',
])
def test_recall_prefix_can_query_generic_latest_without_mutation(tmp_path, text):
    bank, memory, _, latest = setup_bank(tmp_path)
    before = money_rows(bank)
    assert operation_ids(memory.handle('demo', text, 'recall-latest')) == [latest['id']]
    assert money_rows(bank) == before


@pytest.mark.parametrize('text', [
    '你还记得刚才给小王的转账状态吗',
    '你还记得刚才帮朋友转账成功了吗',
])
def test_recall_prefix_does_not_drop_unknown_recipient(tmp_path, text):
    bank, memory, _, _ = setup_bank(tmp_path)
    before = money_rows(bank)
    assert operation_ids(memory.handle('demo', text, 'recall-unknown')) == []
    assert money_rows(bank) == before


@pytest.mark.parametrize('text', [
    '刚才给爸爸转-100元成功了吗',
    '刚才给爸爸转1,100元成功了吗',
    '刚才给妈妈转200.000元成功了吗',
    '刚才给爸爸转100美元成功了吗',
    '刚才给爸爸转了200成功了吗',
    '刚才给爸爸转两百元成功了吗',
    '刚才给爸爸转￥200成功了吗',
    '刚才帮朋友转的账成功了吗',
    '刚才转小王的那笔成功了吗',
    '刚才替同事转账成功了吗',
])
def test_invalid_or_foreign_amount_never_silently_selects_another_transfer(tmp_path, text):
    bank, memory, _, _ = setup_bank(tmp_path)
    before = money_rows(bank)
    assert operation_ids(memory.handle('demo', text, 'amount-constraint')) == []
    assert money_rows(bank) == before


def test_unknown_payment_stays_unknown_and_keeps_its_reservation(tmp_path):
    bank, memory, target, _ = setup_bank(tmp_path)
    with bank.transaction() as db:
        db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?", (target['id'],))
    before = money_rows(bank)
    reserved = bank.snapshot('demo')['accounts'][0]['reserved_cents']
    reply = memory.handle('demo', '上次给13800000002的转账成功了吗', 'unknown-check')
    assert operation_ids(reply) == [target['id']]
    assert reply['cards'][0]['operation']['status'] == 'UNKNOWN'
    assert '已完成' not in reply['message']
    assert bank.snapshot('demo')['accounts'][0]['reserved_cents'] == reserved == 10000
    assert money_rows(bank) == before


def test_phone_and_name_conflict_does_not_select_either_payee(tmp_path):
    _, memory, _, _ = setup_bank(tmp_path)
    reply = memory.handle('demo', '刚才给妈妈13800000002的转账成功了吗', 'conflicting-identity')
    assert operation_ids(reply) == []


def test_amount_without_unit_can_identify_the_right_transfer(tmp_path):
    _, memory, target, _ = setup_bank(tmp_path)
    reply = memory.handle('demo', '刚才给爸爸转了100成功了吗', 'bare-amount')
    assert operation_ids(reply) == [target['id']]


def test_operation_id_numeric_run_is_not_a_payee_phone(tmp_path):
    bank, memory, target, _ = setup_bank(tmp_path)
    special = 'op_' + 'ab' + '12345678901' + 'c' * 19
    assert len(special) == 35
    with bank.transaction() as db:
        row = dict(db.execute('SELECT * FROM operations WHERE id=?', (target['id'],)).fetchone())
        row.update(id=special, idempotency_key='special-numeric-id')
        cols = list(row)
        db.execute('INSERT INTO operations(' + ','.join(cols) + ') VALUES(' + ','.join('?' for _ in cols) + ')', [row[k] for k in cols])
    reply = memory.handle('demo', '上次转账' + special + '成功了吗', 'numeric-op-id')
    assert operation_ids(reply) == [special]

"""Read a transfer status using explicit identity constraints, never note content."""
import json
import re
import unicodedata
from decimal import Decimal

from .bank import PENDING


def _unresolved():
    return {'message': '没有查到唯一符合条件的本人转账，请核对收款人或具体操作记录。不能据此认定已经付款。',
            'cards': [], 'tasks': [{'type': 'operation_status', 'state': 'WAITING_USER'}]}


def transfer_status(bank, user, text):
    sample = unicodedata.normalize('NFKC', text[:2000])
    ids = set(re.findall(r'op_[0-9a-f]{32}(?![0-9a-f])', sample))
    scan = re.sub(r'op_[0-9A-Za-z]+', ' ', sample)
    phones = set(re.findall(r'(?<![0-9])[0-9]{11}(?![0-9])', scan))
    if re.search(r'美元|美金|港元|港币|日元|欧元|英镑|USD|HKD|JPY|EUR|GBP|[$€£]', scan, re.I):
        return _unresolved()
    amounts = set(re.findall(r'(?<![0-9.,+\-])([0-9]+(?:\.[0-9]{1,2})?)\s*[元块]', scan))
    amounts.update(re.findall(r'转(?:了)?\s*([0-9]+(?:\.[0-9]{1,2})?)(?![0-9.,])', scan))
    money_hint = re.search(r'[¥￥]|[零一二两三四五六七八九十百千万]+\s*[元块]|[0-9][0-9.,]*\s*[元块]|转(?:了)?\s*[+\-]?[0-9]', scan)
    if money_hint and not amounts:
        return _unresolved()
    if len(phones) > 1 or len(ids) > 1 or len(amounts) > 1 or ('op_' in sample and not ids):
        return _unresolved()
    amount = int(Decimal(next(iter(amounts))) * 100) if amounts else None
    target = re.search(r'(?:转给|给|向)\s*(.+?)(?=的?(?:转账|转款|汇款|付款|打款)|转(?:了)?\s*[0-9])', scan)
    label = re.sub(r'(?:的|那笔|这笔|那一笔|这一笔)+$', '', target[1]).strip() if target else None
    with bank.transaction() as db:
        bank._user(db, user)
        payees = [dict(row) for row in db.execute('SELECT id,name,phone FROM payees WHERE user_id=?', (user,))]
        mentioned = [p for p in payees if p['name'] in scan]
        selected = None
        if phones:
            matches = [p for p in payees if p['phone'] == next(iter(phones))]
            if len(matches) != 1 or any(p['name'] != matches[0]['name'] for p in mentioned):
                return _unresolved()
            selected = matches[0]['id']
            if label and label not in (matches[0]['name'], matches[0]['phone']):
                return _unresolved()
        elif label:
            matches = [p for p in payees if p['name'] == label]
            if len(matches) != 1:
                return _unresolved()
            selected = matches[0]['id']
        elif mentioned:
            if len(mentioned) != 1:
                return _unresolved()
            selected = mentioned[0]['id']
        elif not ids and not re.fullmatch(
                r'\s*(?:(?:你)?(?:还)?记得)?(?:我)?(?:刚才|上次|最近)(?:的|那笔|一笔)*转(?:账|款)?'
                r'(?:是|是否|有没有)?(?:成功|完成|结果|状态|多少钱|多少元|怎么样)'
                r'(?:了|吗|没|呢|怎么样|如何|是什么|是啥|[？?。！!\s])*', scan):
            return _unresolved()
        # Filter the owner's complete transfer history before selecting the latest.
        # The generic account snapshot's 30-row display window is not a query bound.
        params = [user]
        sql = "SELECT * FROM operations WHERE user_id=? AND type='transfer'"
        if ids:
            sql += ' AND id=?'
            params.append(next(iter(ids)))
        sql += ' ORDER BY created_at DESC,rowid DESC'
        found = None
        for row in db.execute(sql, params):
            try:
                payload = json.loads(row['payload'])
            except (ValueError, TypeError):
                return _unresolved()
            if not isinstance(payload, dict):
                return _unresolved()
            if selected is not None and payload.get('payee_id') != selected:
                continue
            if amount is not None and payload.get('amount_cents') != amount:
                continue
            found = bank._view(row)
            break
        if found is None:
            return _unresolved()
        if found['status'] in PENDING and found['expires_at'] <= bank.now().timestamp():
            found['status'] = 'EXPIRED'
    states = {'SUCCEEDED': '已完成', 'CANCELLED': '已取消，未执行', 'EXPIRED': '已过期，未执行',
              'WAITING_CONFIRMATION': '等待你的确认，尚未执行', 'WAITING_VERIFICATION': '等待验证，尚未执行',
              'UNKNOWN': '结果待核实，不会重复执行', 'EXECUTING': '结果待核实，不会重复执行',
              'MANUAL_REVIEW': '需要进一步核查，不能认定已付款'}
    return {'message': '按本人银行操作记录，这笔转账' + states.get(found['status'], '结果仍需核实') + '。',
            'cards': [{'type': 'operation', 'operation': found}],
            'tasks': [{'type': 'operation_status', 'state': 'COMPLETED'}]}

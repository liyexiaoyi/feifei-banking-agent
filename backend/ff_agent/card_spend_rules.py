"""Read-only validation for LOCAL SIMULATED card spends. No bank or merchant is connected."""
import json
import re
from datetime import datetime

KIND = 'card_spend'
PENDING = ('WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY')
UNRESOLVED = ('UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW')
KEYS = frozenset(('card_id', 'account_id', 'card_name', 'masked_number', 'storage_kind',
                  'expected_card_version', 'expected_control_version', 'merchant_id',
                  'merchant_name', 'amount_cents', 'risk_epoch', 'simulated'))
INTENT = ('card_id', 'merchant_id', 'amount_cents', 'expected_card_version', 'expected_control_version')
MAX_CENTS = 100000000
_KEY_RE = re.compile(r'[A-Za-z0-9_-]{8,64}')


def _err(code, message, status=400):
    from .bank import DomainError
    return DomainError(code, message, status)


def _invalid():
    return _err('CARD_SPEND_INVALID', '卡片消费记录无效，请先核对原记录。', 409)


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _cents(v):
    return _int(v) and 1 <= v <= MAX_CENTS


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _now(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise _err('CLOCK_INVALID', '系统时间无效。', 500)
    return now.timestamp(), now.date().isoformat()


def _encode(p):
    return json.dumps(p, ensure_ascii=False, sort_keys=True)


def _parse(raw):
    # Strict: exact keys, strict types, canonical encoding.
    try:
        p = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        raise _invalid() from None
    if not isinstance(p, dict) or set(p) != KEYS or _encode(p) != raw:
        raise _invalid()
    for k in ('card_id', 'account_id', 'card_name', 'masked_number', 'storage_kind',
              'merchant_id', 'merchant_name', 'risk_epoch'):
        if not isinstance(p[k], str):
            raise _invalid()
    if (not _cents(p['amount_cents']) or p['simulated'] is not True
            or not _int(p['expected_card_version']) or not _int(p['expected_control_version'])):
        raise _invalid()
    return p


def _ledger(db, user):
    """All own card_spend rows that hold cash or count as exposure; malformed rows fail closed."""
    from .cards import CardService
    marks = ','.join('?' * (len(PENDING) + len(UNRESOLVED) + 1))
    out, owners = [], {}
    for row in db.execute(f'SELECT * FROM operations WHERE user_id=? AND type=? AND status IN ({marks})',
                          (user, KIND, *PENDING, *UNRESOLVED, 'SUCCEEDED')).fetchall():
        try:
            p = json.loads(row['payload'])
        except (TypeError, ValueError, RecursionError):
            raise _invalid() from None
        if (not isinstance(p, dict) or not isinstance(p.get('card_id'), str)
                or not isinstance(p.get('account_id'), str) or not _cents(p.get('amount_cents'))):
            raise _invalid()
        if p['card_id'] not in owners:
            owners[p['card_id']] = CardService._card(db, user, p['card_id'])['account_id']
        if owners[p['card_id']] != p['account_id']:
            raise _invalid()
        if row['status'] in PENDING and not _num(row['expires_at']):
            raise _invalid()
        if row['status'] == 'SUCCEEDED' and not isinstance(row['executed_day'], str):
            raise _invalid()
        out.append((row, p))
    return out


def _held(row, ts, exclude):
    if row['status'] in UNRESOLVED:
        return True  # never released by day, expiry or exclude
    return row['status'] in PENDING and row['expires_at'] > ts and row['id'] != exclude


def reserved_cash(db, user, now, account_id, exclude=None) -> int:
    ts, _ = _now(now)
    return sum(p['amount_cents'] for r, p in _ledger(db, user)
               if p['account_id'] == account_id and _held(r, ts, exclude))


def daily_exposure(db, user, now, exclude=None, card_id=None) -> int:
    ts, today = _now(now)
    return sum(p['amount_cents'] for r, p in _ledger(db, user)
               if (card_id is None or p['card_id'] == card_id)
               and ((r['status'] == 'SUCCEEDED' and r['executed_day'] == today) or _held(r, ts, exclude)))


def settled_cents(db, user, now, card_id) -> int:
    _, today = _now(now)
    return sum(p['amount_cents'] for r, p in _ledger(db, user)
               if p['card_id'] == card_id and r['status'] == 'SUCCEEDED' and r['executed_day'] == today)


def _check_intent(user, card_id, merchant_id, amount, cv, vv, merchants):
    if not isinstance(user, str) or not isinstance(card_id, str) or not card_id:
        raise _err('NOT_FOUND', '未找到可访问的数据。', 404)
    if not isinstance(merchants, dict) or not isinstance(merchant_id, str) or not isinstance(merchants.get(merchant_id), str):
        raise _err('INVALID_MERCHANT', '商户无效。')
    if not _cents(amount):
        raise _err('INVALID_AMOUNT', '金额必须为有效的整数分。')
    if not (_int(cv) and cv >= 1 and _int(vv) and vv >= 1):
        raise _err('INVALID_VERSION', '版本号无效。')


def _fresh(bank, db, user, card_id, merchant_id, amount, cv, vv, merchants, exclude=None):
    """Re-derive the canonical payload from current owned state; any drift rejects."""
    from .cards import CardService
    from .card_controls import CardControls, assert_card_reconciled
    card = CardService._card(db, user, card_id)
    if card['status'] != 'ACTIVE' or card['version'] != cv:
        raise _err('CARD_CONFLICT', '卡片状态或版本已改变，请刷新。', 409)
    assert_card_reconciled(db, user, card_id)
    bank._unlocked(db, user)
    bank._assert_transfer_allowed(db, user)
    acct = db.execute('SELECT * FROM accounts WHERE id=? AND user_id=?', (card['account_id'], user)).fetchone()
    if acct is None or not _int(acct['balance_cents']):
        raise _err('NOT_FOUND', '未找到可访问的数据。', 404)
    state, version = CardControls._controls(db, user, card)
    if version != vv:
        raise _err('CONTROL_CONFLICT', '卡片控制已改变，请刷新。', 409)
    if state['locked'] is not False:
        raise _err('CARD_LOCKED', '卡片已锁定，无法消费。', 409)
    single, daily = state['single_limit_cents'], state['daily_limit_cents']
    for lim in (single, daily):
        if lim is not None and not _int(lim):
            raise _invalid()
    if single is not None and amount > single:
        raise _err('SINGLE_LIMIT_EXCEEDED', '超出单笔限额。', 409)
    now = bank.now()
    if daily is not None and daily_exposure(db, user, now, exclude, card_id) + amount > daily:
        raise _err('DAILY_LIMIT_EXCEEDED', '超出本卡当日限额。', 409)
    if acct['balance_cents'] - bank._reserved_amount(db, user, acct['id'], exclude) < amount:
        raise _err('INSUFFICIENT_FUNDS', '可用余额不足。', 409)
    payload = {'card_id': card_id, 'account_id': acct['id'], 'card_name': card['name'],
               'masked_number': card['masked_number'], 'storage_kind': card['storage_kind'],
               'expected_card_version': cv, 'expected_control_version': vv,
               'merchant_id': merchant_id, 'merchant_name': merchants[merchant_id],
               'amount_cents': amount, 'risk_epoch': bank._safety_epoch(db, user), 'simulated': True}
    return payload, dict(acct)


def prepare_context(bank, db, user, card_id, merchant_id, amount_cents, expected_card_version,
                    expected_control_version, key, merchants, plan_ticket=None):
    if plan_ticket is not None or (isinstance(key, str) and key.startswith('task-')):
        raise _err('PLAN_CONTEXT_REQUIRED', '计划消费须来自已核验的原任务上下文。', 409)
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
        raise _err('INVALID_KEY', '请求编号无效。')
    _check_intent(user, card_id, merchant_id, amount_cents, expected_card_version, expected_control_version, merchants)
    intent = dict(zip(INTENT, (card_id, merchant_id, amount_cents, expected_card_version, expected_control_version)))
    old = db.execute('SELECT * FROM operations WHERE user_id=? AND idempotency_key=?', (user, key)).fetchone()
    if old is not None:  # replay before any dynamic state check; returned as-is, read-only
        if old['type'] != KIND or any(_parse(old['payload'])[k] != v or type(_parse(old['payload'])[k]) is not type(v)
                                      for k, v in intent.items()):
            raise _err('IDEMPOTENCY_CONFLICT', '同一请求编号的操作内容已改变，请重新发起。', 409)
        return {'existing': bank._view(old), 'payload': None, 'risk': None}
    payload, _ = _fresh(bank, db, user, card_id, merchant_id, amount_cents,
                        expected_card_version, expected_control_version, merchants)
    return {'existing': None, 'payload': payload, 'risk': bank._risk(db, user, amount_cents)}


def execution_context(bank, db, user, row, merchants):
    opid = row['id'] if row is not None else None
    op = db.execute('SELECT * FROM operations WHERE id=? AND user_id=?', (opid, user)).fetchone()
    if op is None or op['type'] != KIND or op['payload'] != row['payload']:
        raise _invalid()
    p = _parse(op['payload'])
    ts, _ = _now(bank.now())
    if ((op['status'], op['risk']) not in (('WAITING_CONFIRMATION', 'YELLOW'), ('WAITING_VERIFICATION', 'RED'))
            or not _num(op['confirmed_at']) or not _num(op['expires_at']) or op['expires_at'] <= ts):
        raise _err('OPERATION_NOT_EXECUTABLE', '操作未确认或已过期。', 409)
    _check_intent(user, p['card_id'], p['merchant_id'], p['amount_cents'],
                  p['expected_card_version'], p['expected_control_version'], merchants)
    fresh, acct = _fresh(bank, db, user, p['card_id'], p['merchant_id'], p['amount_cents'],
                         p['expected_card_version'], p['expected_control_version'], merchants, exclude=opid)
    if _encode(fresh) != op['payload']:
        raise _err('CARD_SPEND_STALE', '卡片、商户或风险状态已改变，请重新发起。', 409)
    if op['risk'] != 'RED' and bank._risk(db, user, p['amount_cents'], exclude=opid) == 'RED':
        raise _err('RISK_ESCALATED', '风险等级已升高，需重新确认并验证。', 409)
    return {'operation': dict(op), 'payload': p, 'account': acct}

# Pure validation rules for simulated birthday gift orders. Reads only; no writes or effects.
import json
from datetime import date, datetime

from .birthday_budget import DomainError

KIND = 'birthday_order'
BDAY_PREFIX = 'bday-'
GIFTS = ('flowers', 'cake')
PENDING = ('WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY')
UNRESOLVED = ('UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW')
CONFIRMABLE = {('WAITING_CONFIRMATION', 'YELLOW'), ('WAITING_VERIFICATION', 'RED')}
MAX_CENTS = 100000000
PAYLOAD_KEYS = frozenset(('budget_id', 'budget_version', 'budget_expires_at', 'account_id', 'account_name', 'gift',
                          'item_id', 'item_name', 'quote_version', 'amount_cents', 'event_date', 'risk_epoch',
                          'simulated', 'merchant_connected'))
_ORDERS_SQL = ('SELECT id,status,payload,expires_at,executed_day FROM operations '
               'WHERE user_id=? AND type=? AND status IN (?,?,?,?,?,?,?)')

CATALOG = {
    'flowers_simple': {'name': '简约花束', 'gift': 'flowers', 'price_cents': 30000, 'quote_version': 1},
    'flowers_large': {'name': '豪华花束', 'gift': 'flowers', 'price_cents': 80000, 'quote_version': 1},
    'cake_small': {'name': '小蛋糕', 'gift': 'cake', 'price_cents': 20000, 'quote_version': 1},
    'cake_large': {'name': '大蛋糕', 'gift': 'cake', 'price_cents': 80000, 'quote_version': 1},
}


def _err(code, msg, status=409):
    return DomainError(code, msg, status)


def _invalid():
    return _err('ORDER_STATE_INVALID', '生日订单数据异常，已停止处理。')


def _amount_ok(v):
    return type(v) is int and 1 <= v <= MAX_CENTS


def _pos_int(v):
    return type(v) is int and v > 0


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _dump(p):
    return json.dumps(p, ensure_ascii=False, sort_keys=True)


def _item(catalog, item_id):
    item = catalog.get(item_id) if isinstance(catalog, dict) and isinstance(item_id, str) else None
    if (not isinstance(item, dict) or item.get('gift') not in GIFTS or not isinstance(item.get('name'), str)
            or not _amount_ok(item.get('price_cents')) or not _pos_int(item.get('quote_version'))):
        raise _err('ITEM_NOT_FOUND', '未找到该礼物选项。', 404)
    return item


def catalog_items(catalog, gift=None):
    if gift is not None and gift not in GIFTS:
        raise _err('INVALID_GIFT', '礼物类型无效。', 400)
    out = []
    for item_id in catalog:
        item = _item(catalog, item_id)
        if gift is None or item['gift'] == gift:
            out.append({'item_id': item_id, 'name': item['name'], 'gift': item['gift'],
                        'price_cents': item['price_cents'], 'quote_version': item['quote_version'],
                        'simulated': True, 'merchant_connected': False})
    return out


def _orders(db, user):
    # Owned orders that hold budget or count toward exposure; each must bind to an owned budget on the same account.
    parents = {r['id']: r['account_id'] for r in db.execute(
        'SELECT id,account_id FROM birthday_budgets WHERE user_id=?', (user,))}
    out = []
    for row in db.execute(_ORDERS_SQL, (user, KIND, *PENDING, *UNRESOLVED, 'SUCCEEDED')):
        try:
            p = json.loads(row['payload'])
        except (TypeError, ValueError, RecursionError) as exc:
            raise _invalid() from exc
        if (not isinstance(p, dict) or not isinstance(p.get('budget_id'), str)
                or not isinstance(p.get('account_id'), str) or not _amount_ok(p.get('amount_cents'))):
            raise _invalid()
        if parents.get(p['budget_id']) != p['account_id']:
            raise _invalid()
        if row['status'] in PENDING and not _num(row['expires_at']):
            raise _invalid()
        out.append((dict(row), p))
    return out


def _held(row, ts, exclude=None):
    if row['status'] in UNRESOLVED:
        return True
    return row['status'] in PENDING and row['expires_at'] > ts and row['id'] != exclude


def daily_exposure(db, user, now, exclude=None) -> int:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise _invalid()
    ts, today = now.timestamp(), now.date().isoformat()
    total = 0
    for row, p in _orders(db, user):
        if (row['status'] == 'SUCCEEDED' and row['executed_day'] == today) or _held(row, ts, exclude):
            total += p['amount_cents']
    return total


def _live_budget(bank, db, user, budget_id, version, now):
    b = db.execute('SELECT * FROM birthday_budgets WHERE id=? AND user_id=?', (budget_id, user)).fetchone()
    if not b:
        raise _err('NOT_FOUND', '未找到可访问的数据', 404)
    b = dict(b)
    amount, spent = b['amount_cents'], b['spent_cents']
    if (not _amount_ok(amount) or type(spent) is not int or not 0 <= spent <= amount
            or not _num(b['expires_at']) or not _pos_int(b['version'])):
        raise _invalid()
    if b['status'] != 'ACTIVE' or not b['expires_at'] > now.timestamp():
        raise _err('BUDGET_INACTIVE', '生日预算已取消或过期。')
    if not _pos_int(version) or b['version'] != version:
        raise _err('BUDGET_CHANGED', '生日预算已更新，请重新选择礼物。')
    if b['risk_epoch'] != bank._safety_epoch(db, user):
        raise _err('REQUEST_SUPERSEDED', '该预算早于最近一次风险提示，请重新提出业务需求。')
    try:
        ev = date.fromisoformat(b['event_date'])
    except (TypeError, ValueError) as exc:
        raise _invalid() from exc
    if ev < now.date():
        raise _err('EVENT_PASSED', '生日日期已过，不能再下单。')
    return b


def _account(db, user, account_id):
    a = db.execute('SELECT * FROM accounts WHERE id=? AND user_id=?', (account_id, user)).fetchone()
    if not a:
        raise _err('NOT_FOUND', '未找到可访问的数据', 404)
    a = dict(a)
    if not isinstance(a.get('name'), str) or type(a.get('balance_cents')) is not int:
        raise _invalid()
    return a


def _check_capacity(db, user, budget, gift, amount, now, exclude=None):
    ts, used = now.timestamp(), 0
    for row, p in _orders(db, user):
        if p['budget_id'] != budget['id']:
            continue
        held = _held(row, ts, exclude)
        if held:
            used += p['amount_cents']
        current = row['id'] == exclude and row['status'] in PENDING
        if not current and (held or row['status'] == 'SUCCEEDED') and p.get('gift') == gift:
            raise _err('GIFT_ALREADY_ORDERED', '该生日预算已有同类礼物订单。')
    if budget['amount_cents'] - budget['spent_cents'] - used < amount:
        raise _err('BUDGET_EXCEEDED', '生日预算剩余额度不足。')


def prepare_context(bank, db, user, budget_id, item_id, budget_version, key, catalog, plan_ticket=None,
                    birthday_binding=None):
    if plan_ticket is not None or (isinstance(key, str) and key.startswith('task-')):
        raise _err('PLAN_CONTEXT_REQUIRED', '计划操作须来自已核验的原任务上下文。')
    if not isinstance(key, str) or not 1 <= len(key) <= 120:
        raise _err('INVALID_KEY', '请求编号无效。', 400)
    if not _pos_int(budget_version) or not isinstance(budget_id, str) or not isinstance(item_id, str):
        raise _err('INVALID_REQUEST', '请求参数无效。', 400)
    is_bday = key.startswith(BDAY_PREFIX)
    if is_bday != (birthday_binding is not None):
        raise _err('BIRTHDAY_BINDING_INVALID', '生日订单绑定无效。', 400)
    if is_bday:
        from .birthday_tickets import assert_claim  # lazy: avoid import cycle
        assert_claim(bank, db, user, birthday_binding, key, KIND)
    old = db.execute('SELECT * FROM operations WHERE user_id=? AND idempotency_key=?', (user, key)).fetchone()
    if old:
        try:
            p = json.loads(old['payload'])
        except (TypeError, ValueError, RecursionError):
            p = None
        if (old['type'] != KIND or not isinstance(p, dict) or p.get('budget_id') != budget_id
                or p.get('item_id') != item_id or not _pos_int(p.get('budget_version'))
                or p['budget_version'] != budget_version):
            raise _err('IDEMPOTENCY_CONFLICT', '同一请求编号的操作内容已改变，请重新发起。')
        return {'existing': bank._view(old)}
    bank._user(db, user)
    bank._unlocked(db, user)
    bank._assert_transfer_allowed(db, user)
    now = bank.now()
    item = _item(catalog, item_id)
    budget = _live_budget(bank, db, user, budget_id, budget_version, now)
    account = _account(db, user, budget['account_id'])
    amount = item['price_cents']
    _check_capacity(db, user, budget, item['gift'], amount, now)
    payload = {'budget_id': budget['id'], 'budget_version': budget['version'],
               'budget_expires_at': budget['expires_at'], 'account_id': account['id'],
               'account_name': account['name'], 'gift': item['gift'], 'item_id': item_id,
               'item_name': item['name'], 'quote_version': item['quote_version'], 'amount_cents': amount,
               'event_date': budget['event_date'], 'risk_epoch': budget['risk_epoch'],
               'simulated': True, 'merchant_connected': False}
    risk = bank._risk(db, user, amount)
    if risk not in ('YELLOW', 'RED'):
        raise _invalid()
    return {'payload': payload, 'risk': risk}


def execution_context(bank, db, user, row, catalog):
    if not getattr(db, 'in_transaction', False):
        raise _err('TRANSACTION_REQUIRED', '订单执行必须在数据库事务内进行。', 500)
    bank._unlocked(db, user)
    now = bank.now()
    op = db.execute('SELECT * FROM operations WHERE id=? AND user_id=?', (row['id'], user)).fetchone()
    if not op or op['payload'] != row['payload']:
        raise _err('NOT_FOUND', '未找到可访问的数据', 404)
    op = dict(op)
    if (op['type'] != KIND or op['confirmed_at'] is None or (op['status'], op['risk']) not in CONFIRMABLE
            or not _num(op['expires_at']) or not op['expires_at'] > now.timestamp()):
        raise _err('OPERATION_NOT_EXECUTABLE', '订单状态不可执行，请重新准备。')
    try:
        p = json.loads(op['payload'])
    except (TypeError, ValueError, RecursionError) as exc:
        raise _invalid() from exc
    if (not isinstance(p, dict) or set(p) != PAYLOAD_KEYS or _dump(p) != op['payload']
            or p['simulated'] is not True or p['merchant_connected'] is not False
            or not _amount_ok(p['amount_cents'])):
        raise _invalid()
    bank._assert_transfer_allowed(db, user, expected_epoch=p['risk_epoch'])
    budget = _live_budget(bank, db, user, p['budget_id'], p['budget_version'], now)
    if ((budget['expires_at'], budget['event_date'], budget['risk_epoch'], budget['account_id'])
            != (p['budget_expires_at'], p['event_date'], p['risk_epoch'], p['account_id'])):
        raise _err('BUDGET_CHANGED', '生日预算已更新，请重新选择礼物。')
    account = _account(db, user, p['account_id'])
    if account['name'] != p['account_name']:
        raise _err('ACCOUNT_CHANGED', '付款账户信息已变化，请重新准备。')
    item = _item(catalog, p['item_id'])
    if ((item['gift'], item['name'], item['quote_version'], item['price_cents'])
            != (p['gift'], p['item_name'], p['quote_version'], p['amount_cents'])):
        raise _err('QUOTE_CHANGED', '礼物报价已变化，请重新准备。')
    amount = p['amount_cents']
    _check_capacity(db, user, budget, p['gift'], amount, now, exclude=op['id'])
    if account['balance_cents'] - bank._reserved_amount(db, user, account['id'], exclude=op['id']) < amount:
        raise _err('INSUFFICIENT_FUNDS', '账户可用余额不足。')
    if op['risk'] == 'YELLOW' and bank._risk(db, user, amount, exclude=op['id']) != 'YELLOW':
        raise _err('RISK_ESCALATED', '今日转账与订单总额已超出限额，请重新发起并完成验证。')
    return {'operation': op, 'payload': p, 'budget': budget, 'account': account}

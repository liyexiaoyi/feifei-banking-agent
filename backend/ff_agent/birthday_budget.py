"""Simulated birthday budget holds sharing Bank availability.

A budget is a structural hold on simulated cash. It never debits an account,
never books a merchant, and never consumes spent_cents in this module.
"""
import json
import re
from datetime import date, timedelta
from sqlite3 import Row

from .bank import DomainError

OP_TYPE = 'birthday_budget_reserve'
CHILD_TYPE = 'birthday_order'
BDAY_KEY_PREFIX = 'bday-'
MAX_AMOUNT = 100000000
HOLD_SECONDS = 1800
MAX_DAYS = 366
PENDING = ('WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY')
UNRESOLVED = ('UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW')
PAYLOAD_KEYS = {'account_id', 'account_name', 'amount_cents', 'event_date', 'risk_epoch', 'simulated'}
_DATE_RE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS birthday_budgets(
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        amount_cents INTEGER NOT NULL CHECK(amount_cents BETWEEN 1 AND 100000000),
        spent_cents INTEGER NOT NULL DEFAULT 0 CHECK(spent_cents >= 0 AND spent_cents <= amount_cents),
        version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
        status TEXT NOT NULL CHECK(status IN ('ACTIVE','CANCELLED')),
        expires_at REAL NOT NULL,
        event_date TEXT NOT NULL,
        risk_epoch NOT NULL,
        origin_operation_id TEXT NOT NULL UNIQUE
    )""",
    'CREATE INDEX IF NOT EXISTS idx_birthday_budgets_user_account ON birthday_budgets(user_id, account_id)',
)

_CHILD_SQL = """
SELECT id, status, payload
FROM operations
WHERE user_id = ?
  AND type = ?
  AND (
    (status IN ('WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY') AND expires_at > ?)
    OR status IN ('UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW')
  )
"""


def bootstrap_birthday_budgets(bank):
    # Plain execute statements keep the schema inside Bank's own transaction.
    with bank.transaction() as db:
        for statement in _SCHEMA:
            db.execute(statement)


def _rows(db, sql, args):
    cursor = db.cursor()
    cursor.row_factory = Row
    cursor.execute(sql, args)
    return cursor.fetchall()


def _one(db, sql, args):
    rows = _rows(db, sql, args)
    return rows[0] if rows else None


def _invalid():
    return DomainError('INVALID_OPERATION', '生日预算相关记录无法核对，请先核实已有记录。', 409)


def _not_found():
    return DomainError('NOT_FOUND', '未找到可访问的生日预算', 404)


def _valid_amount(value):
    return type(value) is int and 1 <= value <= MAX_AMOUNT


def _check_amount(value):
    if not _valid_amount(value):
        raise DomainError('INVALID_AMOUNT', '预算金额须为1至100000000分的整数。')
    return value


def _check_date(value, today):
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise DomainError('INVALID_DATE', '请使用明确的日期，例如2026-10-28。')
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise DomainError('INVALID_DATE', '日期不存在，请重新核对。') from None
    if not today <= parsed <= today + timedelta(days=MAX_DAYS):
        raise DomainError('INVALID_DATE', '生日日期须在今天至366天之内。')
    return value


def _check_key(key):
    if not isinstance(key, str) or not 1 <= len(key) <= 120:
        raise DomainError('INVALID_KEY', '请求编号无效。')
    if key.startswith('task-'):
        raise DomainError('PLAN_CONTEXT_REQUIRED', '计划操作须来自已核验的原任务上下文。', 409)
    return key


def _holds(db, user, now, exclude=None):
    """Return {budget_id: (account_id, hold_cents)} for the owner's budgets only."""
    budgets = {}
    for row in _rows(db, 'SELECT id, account_id, amount_cents, spent_cents, status, expires_at FROM birthday_budgets WHERE user_id = ?', (user,)):
        amount, spent = row['amount_cents'], row['spent_cents']
        if not _valid_amount(amount) or type(spent) is not int or not 0 <= spent <= amount:
            raise _invalid()
        if row['status'] not in ('ACTIVE', 'CANCELLED'):
            raise _invalid()
        budgets[row['id']] = dict(row)
    # Fail closed on corrupt Bank statuses; never normalize or silently omit them.
    if _one(db, "SELECT 1 FROM operations WHERE user_id=? AND type=? AND (status IS NULL OR status NOT IN "
            "('WAITING_CONFIRMATION','WAITING_VERIFICATION','READY','SUCCEEDED','CANCELLED','EXPIRED',"
            "'FAILED','REJECTED','UNKNOWN','EXECUTING','MANUAL_REVIEW')) LIMIT 1", (user, CHILD_TYPE)):
        raise _invalid()
    children = {bid: [] for bid in budgets}
    for row in _rows(db, _CHILD_SQL, (user, CHILD_TYPE, now)):
        try:
            payload = json.loads(row['payload'])
        except (TypeError, ValueError, RecursionError) as exc:
            raise _invalid() from exc
        if not isinstance(payload, dict):
            raise _invalid()
        bid, account, amount = payload.get('budget_id'), payload.get('account_id'), payload.get('amount_cents')
        if not isinstance(bid, str) or not isinstance(account, str) or not _valid_amount(amount):
            raise _invalid()
        budget = budgets.get(bid)
        # Children must bind to an owned budget on the same account; nothing is invented.
        if budget is None or budget['account_id'] != account:
            raise _invalid()
        children[bid].append((row['id'], row['status'], amount))
    holds = {}
    for bid, budget in budgets.items():
        remaining = budget['amount_cents'] - budget['spent_cents']
        kids = children[bid]
        if sum(amount for _, _, amount in kids) > remaining:
            raise _invalid()
        live = budget['status'] == 'ACTIVE' and budget['expires_at'] > now
        if live:
            # Full remaining is held once; pending children are allocations, not extra holds.
            hold = remaining - sum(amount for cid, status, amount in kids if cid == exclude and status in PENDING)
        else:
            # Cancelled/expired: only unresolved allocations stay reserved.
            hold = sum(amount for _, status, amount in kids if status in UNRESOLVED)
        holds[bid] = (budget['account_id'], hold)
    return holds


def reserved_cash(db, user, now, account_id, exclude=None) -> int:
    """Sum birthday holds on one account; user is the stored user_id, now a timestamp."""
    return sum(hold for account, hold in _holds(db, user, now, exclude).values() if account == account_id)


class BirthdayBudgets:
    def __init__(self, bank):
        self.bank = bank

    def _ts(self):
        return self.bank.now().timestamp()

    def _view(self, row, holds, now):
        status = row['status']
        if status == 'ACTIVE' and row['expires_at'] <= now:
            status = 'EXPIRED'
        return {
            'id': row['id'],
            'account_id': row['account_id'],
            'amount_cents': row['amount_cents'],
            'spent_cents': row['spent_cents'],
            'remaining_cents': row['amount_cents'] - row['spent_cents'],
            'reserved_cents': holds.get(row['id'], (row['account_id'], 0))[1],
            'event_date': row['event_date'],
            'expires_at': row['expires_at'],
            'version': row['version'],
            'status': status,
            'simulated': True,
            'operation_id': row['origin_operation_id'],
        }

    def _owned_account(self, db, user, account_id):
        if not isinstance(account_id, str):
            raise DomainError('NOT_FOUND', '未找到可访问的账户', 404)
        account = _one(db, 'SELECT * FROM accounts WHERE id = ? AND user_id = ?', (account_id, user))
        if account is None:
            raise DomainError('NOT_FOUND', '未找到可访问的账户', 404)
        return account

    def prepare(self, user, account_id, amount_cents, event_date, key, plan_ticket=None):
        if plan_ticket is not None:
            raise DomainError('PLAN_CONTEXT_UNSUPPORTED', '生日预算暂不接受计划任务上下文，请单独确认。', 409)
        _check_amount(amount_cents)
        _check_key(key)
        if key.startswith(BDAY_KEY_PREFIX):
            raise DomainError('PLAN_CONTEXT_REQUIRED', '生日任务操作须来自已核验的原任务上下文。', 409)
        with self.bank.transaction() as db:
            return self._prepare_locked(db, user, account_id, amount_cents, event_date, key)

    def _prepare_locked(self, db, user, account_id, amount_cents, event_date, key, binding=None):
        """Runs inside the caller's Bank transaction; never opens its own."""
        if getattr(db, 'in_transaction', False) is not True:
            raise DomainError('TRANSACTION_REQUIRED', '生日预算写入须在银行事务内进行。', 409)
        _check_amount(amount_cents)
        _check_key(key)
        reserved_key = key.startswith(BDAY_KEY_PREFIX)
        if binding is None and reserved_key:
            raise DomainError('PLAN_CONTEXT_REQUIRED', '生日任务操作须来自已核验的原任务上下文。', 409)
        if binding is not None and not reserved_key:
            raise DomainError('PLAN_CONTEXT_INVALID', '生日任务上下文与请求编号不匹配。', 409)
        bank = self.bank
        bank._user(db, user)
        if binding is not None:
            from . import birthday_tickets
            birthday_tickets.assert_claim(bank, db, user, binding, key, OP_TYPE)
        existing = _one(db, 'SELECT * FROM operations WHERE user_id = ? AND idempotency_key = ?', (user, key))
        if existing is not None:
            # Replay returns the original only; availability is never rechecked for it.
            try:
                old = json.loads(existing['payload'])
            except (TypeError, ValueError, RecursionError):
                old = None
            if (existing['type'] != OP_TYPE or not isinstance(old, dict)
                    or old.get('account_id') != account_id
                    or type(old.get('amount_cents')) is not int or old.get('amount_cents') != amount_cents
                    or old.get('event_date') != event_date):
                raise DomainError('IDEMPOTENCY_CONFLICT', '同一请求编号的操作内容已改变，请重新发起。', 409)
            return bank._view(existing)
        _check_date(event_date, bank.now().date())
        account = self._owned_account(db, user, account_id)
        bank._unlocked(db, user)
        bank._assert_transfer_allowed(db, user)
        # Funds checked now, but no hold exists until the budget executes.
        if account['balance_cents'] - bank._reserved_amount(db, user, account_id) < amount_cents:
            raise DomainError('INSUFFICIENT_FUNDS', '可用余额不足，无法预留该生日预算。')
        risk = bank._risk(db, user, amount_cents)
        name = account['name'] if 'name' in account.keys() else account_id
        payload = {
            'account_id': account_id,
            'account_name': name,
            'amount_cents': amount_cents,
            'event_date': event_date,
            'risk_epoch': bank._safety_epoch(db, user),
            'simulated': True,
        }
        if binding is None:
            return bank._create(db, user, OP_TYPE, payload, risk, key)
        return bank._create(db, user, OP_TYPE, payload, risk, key, birthday_binding=binding)

    def execute(self, db, user, row):
        """Runs inside Bank's transaction on the caller's db; never opens its own."""
        bank = self.bank
        now = self._ts()
        opid = row['id']
        current = _one(db, 'SELECT * FROM operations WHERE id = ? AND user_id = ?', (opid, user))
        if current is None:
            raise DomainError('NOT_FOUND', '未找到可访问的操作', 404)
        if current['type'] != OP_TYPE or row['type'] != OP_TYPE:
            raise _invalid()
        if current['confirmed_at'] is None:
            raise DomainError('CONFIRMATION_REQUIRED', '请先核对并确认具体操作。', 409)
        state = (current['status'], current['risk'])
        if state not in (('WAITING_CONFIRMATION', 'YELLOW'), ('WAITING_VERIFICATION', 'RED')):
            raise DomainError('OPERATION_NOT_PENDING', '该操作当前状态不允许执行。', 409)
        if not current['expires_at'] > now:
            raise DomainError('OPERATION_EXPIRED', '确认已过期，请重新发起。', 409)
        if current['payload'] != row['payload']:
            raise _invalid()
        try:
            payload = json.loads(current['payload'])
        except (TypeError, ValueError, RecursionError) as exc:
            raise _invalid() from exc
        if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS or payload['simulated'] is not True:
            raise _invalid()
        if json.dumps(payload, ensure_ascii=False, sort_keys=True) != current['payload']:
            raise _invalid()
        amount = payload['amount_cents']
        if not _valid_amount(amount) or not isinstance(payload['account_id'], str):
            raise _invalid()
        bank._user(db, user)
        _check_date(payload['event_date'], bank.now().date())
        if payload['risk_epoch'] != bank._safety_epoch(db, user):
            raise DomainError('RISK_EPOCH_CHANGED', '安全提示已更新，原预算确认失效，请重新发起。', 409)
        bank._unlocked(db, user)
        bank._assert_transfer_allowed(db, user)
        account = self._owned_account(db, user, payload['account_id'])
        if payload['account_name'] != account['name']:
            raise DomainError('ACCOUNT_CHANGED', '账户资料已变化，请重新核对原预算。', 409)
        if current['risk'] == 'YELLOW' and bank._risk(db, user, amount, exclude=opid) == 'RED':
            raise DomainError('RISK_ESCALATED', '风险等级已升高，需要重新确认并验证。', 409)
        if _one(db, 'SELECT id FROM birthday_budgets WHERE origin_operation_id = ?', (opid,)) is not None:
            raise _invalid()
        if account['balance_cents'] - bank._reserved_amount(db, user, account['id'], opid) < amount:
            raise DomainError('INSUFFICIENT_FUNDS', '执行前重新检查发现可用余额不足。')
        budget_id = 'bb_' + (opid[3:] if opid.startswith('op_') else opid)
        db.execute(
            "INSERT INTO birthday_budgets(id,user_id,account_id,amount_cents,spent_cents,version,status,expires_at,event_date,risk_epoch,origin_operation_id) VALUES(?,?,?,?,0,1,'ACTIVE',?,?,?,?)",
            (budget_id, user, account['id'], amount, now + HOLD_SECONDS, payload['event_date'], payload['risk_epoch'], opid),
        )
        bank._event(db, user, opid, 'BIRTHDAY_BUDGET_RESERVED', f'模拟预留 {amount} 分 / 预算 {budget_id} / 未扣款')
        return {
            'budget_id': budget_id,
            'simulated': True,
            'message': '已模拟预留生日预算30分钟，未扣款，也未预订任何商品。',
        }

    def get(self, user, budget_id):
        bank = self.bank
        with bank.transaction() as db:
            # SELECT only: no _expire, no lazy status writes.
            bank._user(db, user)
            row = _one(db, 'SELECT * FROM birthday_budgets WHERE id = ? AND user_id = ?', (budget_id, user))
            if row is None:
                raise _not_found()
            now = self._ts()
            return self._view(row, _holds(db, user, now), now)

    def list(self, user):
        bank = self.bank
        with bank.transaction() as db:
            bank._user(db, user)
            rows = _rows(db, 'SELECT * FROM birthday_budgets WHERE user_id = ? ORDER BY expires_at DESC, id', (user,))
            now = self._ts()
            holds = _holds(db, user, now)
            return [self._view(row, holds, now) for row in rows]

    def cancel(self, user, budget_id, expected_version):
        if type(expected_version) is not int or expected_version < 1:
            raise DomainError('VERSION_CONFLICT', '预算版本无效，请刷新后重试。', 409)
        with self.bank.transaction() as db:
            return self._cancel_locked(db, user, budget_id, expected_version)

    def _cancel_locked(self, db, user, budget_id, expected_version):
        """Runs inside the caller's Bank transaction; never opens its own."""
        if getattr(db, 'in_transaction', False) is not True:
            raise DomainError('TRANSACTION_REQUIRED', '生日预算写入须在银行事务内进行。', 409)
        if type(expected_version) is not int or expected_version < 1:
            raise DomainError('VERSION_CONFLICT', '预算版本无效，请刷新后重试。', 409)
        bank = self.bank
        bank._user(db, user)
        row = _one(db, 'SELECT * FROM birthday_budgets WHERE id = ? AND user_id = ?', (budget_id, user))
        if row is None:
            raise _not_found()
        if row['version'] != expected_version:
            raise DomainError('VERSION_CONFLICT', '预算已更新，请刷新后重试。', 409)
        now = self._ts()
        if row['status'] == 'CANCELLED':
            return self._view(row, _holds(db, user, now), now)
        updated = db.execute(
            "UPDATE birthday_budgets SET status = 'CANCELLED', version = version + 1 WHERE id = ? AND user_id = ? AND version = ? AND status = 'ACTIVE'",
            (budget_id, user, expected_version),
        ).rowcount
        if updated != 1:
            raise DomainError('VERSION_CONFLICT', '预算已更新，请刷新后重试。', 409)
        # Only known unexecuted children stop; unresolved/succeeded ones stay as recorded.
        for child in _rows(db, "SELECT id, status, expires_at, payload FROM operations WHERE user_id = ? AND type = ? AND status IN ('WAITING_CONFIRMATION','WAITING_VERIFICATION','READY')", (user, CHILD_TYPE)):
            try:
                payload = json.loads(child['payload'])
            except (TypeError, ValueError, RecursionError) as exc:
                raise _invalid() from exc
            if not isinstance(payload, dict) or not isinstance(payload.get('budget_id'), str):
                raise _invalid()
            if payload['budget_id'] != budget_id:
                continue
            expires = child['expires_at']
            if type(expires) not in (int, float):
                raise _invalid()
            # Already-expired children are recorded as EXPIRED, not as user cancellations.
            target = 'CANCELLED' if expires > now else 'EXPIRED'
            db.execute("UPDATE operations SET status = ? WHERE id = ? AND user_id = ? AND status = ?",
                       (target, child['id'], user, child['status']))
            if target == 'EXPIRED':
                bank._event(db, user, child['id'], 'OPERATION_EXPIRED', f'确认已过期 / 生日预算 {budget_id} 已取消 / 本笔未执行')
            else:
                bank._event(db, user, child['id'], 'OPERATION_CANCELLED', f'生日预算 {budget_id} 已取消 / 本笔未执行')
        # Fails closed on bad unknown data; reports only the actual unresolved amount still held.
        holds = _holds(db, user, now)
        retained = holds.get(budget_id, (row['account_id'], 0))[1]
        note = f'未决订单金额仍保留 {retained} 分' if retained else '无未决订单占用'
        bank._event(db, user, row['origin_operation_id'], 'BIRTHDAY_BUDGET_CANCELLED',
                    f'预算 {budget_id} 已取消 / 未退款未改余额 / {note}')
        fresh = _one(db, 'SELECT * FROM birthday_budgets WHERE id = ? AND user_id = ?', (budget_id, user))
        return self._view(fresh, holds, now)

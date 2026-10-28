"""Birthday order core: transaction driver + append-only local ledger.

All binding/allocation/price/date/risk validation lives in birthday_order_rules.
Effects are local SQLite only; no merchant adapter, network or UNKNOWN resolution.
"""
import uuid

from . import birthday_order_rules as rules
from .bank import DomainError

# Module-level aliases. Methods read CATALOG at call time so monkeypatching
# this module's CATALOG is what every rules helper receives.
CATALOG = rules.CATALOG
daily_exposure = rules.daily_exposure

MESSAGE = '本机模拟订单已记账，未发送真实商户。'

_DDL = (
    """CREATE TABLE IF NOT EXISTS birthday_order_ledger(
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        budget_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        child_operation_id TEXT UNIQUE NOT NULL,
        transaction_id TEXT UNIQUE NOT NULL,
        amount_cents INTEGER NOT NULL CHECK(amount_cents BETWEEN 1 AND 100000000),
        gift TEXT NOT NULL CHECK(gift IN ('flowers','cake')),
        item_id TEXT NOT NULL,
        quote_version INTEGER NOT NULL,
        created_at REAL NOT NULL,
        simulated INTEGER NOT NULL CHECK(simulated=1))""",
    'CREATE INDEX IF NOT EXISTS ix_bol_user_budget ON birthday_order_ledger(user_id,budget_id)',
    # Append-only: ledger rows are never updated or deleted.
    """CREATE TRIGGER IF NOT EXISTS bol_no_update BEFORE UPDATE ON birthday_order_ledger
        BEGIN SELECT RAISE(ABORT,'birthday_order_ledger is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bol_no_delete BEFORE DELETE ON birthday_order_ledger
        BEGIN SELECT RAISE(ABORT,'birthday_order_ledger is append-only'); END""",
)


def bootstrap_birthday_orders(bank):
    with bank.transaction() as db:
        for stmt in _DDL:
            db.execute(stmt)


def _order_id(op_id):
    # Deterministic id derived from the child operation id.
    return 'bo_' + str(op_id)


def _row(r):
    d = dict(r)
    d['simulated'] = True
    d['merchant_connected'] = False
    return d


class BirthdayOrders:
    def __init__(self, bank):
        self.bank = bank

    def catalog(self, gift=None):
        return rules.catalog_items(CATALOG, gift)

    def prepare(self, user, budget_id, item_id, budget_version, key, plan_ticket=None):
        if isinstance(key, str) and key.startswith(rules.BDAY_PREFIX):
            raise DomainError('RESERVED_KEY', '该请求编号前缀为系统保留。', 400)
        bank = self.bank
        with bank.transaction() as db:
            if plan_ticket is not None:
                # Unchanged rules check; always raises PLAN_CONTEXT_REQUIRED.
                rules.prepare_context(bank, db, user, budget_id, item_id,
                                      budget_version, key, CATALOG, plan_ticket)
            return self._prepare_locked(db, user, budget_id, item_id, budget_version, key)

    def _prepare_locked(self, db, user, budget_id, item_id, budget_version, key, binding=None):
        if not getattr(db, 'in_transaction', False):
            raise DomainError('TRANSACTION_REQUIRED', '订单准备必须在数据库事务内进行。', 500)
        bank = self.bank
        if binding is None:
            ctx = rules.prepare_context(bank, db, user, budget_id, item_id,
                                        budget_version, key, CATALOG, None)
        else:
            ctx = rules.prepare_context(bank, db, user, budget_id, item_id,
                                        budget_version, key, CATALOG, birthday_binding=binding)
        if 'existing' in ctx:
            return ctx['existing']
        if binding is None:
            return bank._create(db, user, 'birthday_order', ctx['payload'], ctx['risk'], key)
        return bank._create(db, user, 'birthday_order', ctx['payload'], ctx['risk'], key,
                            birthday_binding=binding)

    def execute(self, db, user, row):
        """Run inside the Bank caller's transaction; any raise rolls everything back.
        Returns the result dict only; Bank._execute finalizes status/executed_day."""
        bank = self.bank
        ctx = rules.execution_context(bank, db, user, row, CATALOG)
        op, p, b, acct = ctx['operation'], ctx['payload'], ctx['budget'], ctx['account']
        op_id, amount = op['id'], p['amount_cents']
        if type(amount) is not int or not 1 <= amount <= 100000000:
            raise DomainError('INVALID_AMOUNT', '订单金额无效。')
        now = bank.now()
        ts = now.timestamp()
        oid = _order_id(op_id)

        if db.execute('SELECT 1 FROM birthday_order_ledger WHERE user_id=? AND (id=? OR child_operation_id=?)',
                      (user, oid, op_id)).fetchone():
            raise DomainError('ALREADY_EXECUTED', '订单已记账。', 409)

        cur = db.execute(
            """UPDATE birthday_budgets SET spent_cents=spent_cents+?
               WHERE id=? AND user_id=? AND account_id=? AND version=? AND status='ACTIVE'
                 AND expires_at>? AND amount_cents-spent_cents>=?""",
            (amount, b['id'], user, p['account_id'], p['budget_version'], ts, amount))
        if cur.rowcount != 1:
            raise DomainError('BUDGET_CHANGED', '生日预算已变化或不足。', 409)

        cur = db.execute(
            'UPDATE accounts SET balance_cents=balance_cents-? WHERE id=? AND user_id=? AND balance_cents>=?',
            (amount, acct['id'], user, amount))
        if cur.rowcount != 1:
            raise DomainError('INSUFFICIENT_FUNDS', '账户余额不足。', 409)

        tx_id = str(uuid.uuid4())
        db.execute(
            """INSERT INTO transactions(id,user_id,account_id,kind,amount_cents,category,
               merchant,occurred_at,note,operation_id) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (tx_id, user, acct['id'], 'EXPENSE', amount, '生日礼物', '本机:' + p['item_name'],
             now.isoformat(), '本机模拟，未连接商户', op_id))

        db.execute(
            """INSERT INTO birthday_order_ledger(id,user_id,budget_id,account_id,child_operation_id,
               transaction_id,amount_cents,gift,item_id,quote_version,created_at,simulated)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,1)""",
            (oid, user, b['id'], acct['id'], op_id, tx_id, amount, p['gift'], p['item_id'],
             p['quote_version'], ts))

        bal = db.execute('SELECT balance_cents FROM accounts WHERE id=? AND user_id=?',
                         (acct['id'], user)).fetchone()['balance_cents']

        bank._event(db, user, op_id, 'BIRTHDAY_ORDER_SUCCEEDED',
                    f'本机模拟订单 {oid} / {amount} 分 / 流水 {tx_id} / 未连接商户')

        return {'order_id': oid, 'transaction_id': tx_id, 'amount_cents': amount,
                'balance_cents': bal, 'simulated': True, 'merchant_connected': False,
                'message': MESSAGE}

    def get(self, user, order_id):
        with self.bank.transaction() as db:
            r = db.execute('SELECT * FROM birthday_order_ledger WHERE user_id=? AND id=?',
                           (user, order_id)).fetchone()
        if r is None:
            raise DomainError('NOT_FOUND', '未找到可访问的订单。', 404)
        return _row(r)

    def list(self, user, budget_id=None):
        sql, args = 'SELECT * FROM birthday_order_ledger WHERE user_id=?', [user]
        if budget_id is not None:
            sql += ' AND budget_id=?'
            args.append(budget_id)
        with self.bank.transaction() as db:
            return [_row(r) for r in db.execute(sql + ' ORDER BY created_at,id', args)]

"""Synthetic wealth assets; Bank owns confirmation and execution transaction."""
import json
import uuid
from .bank import DomainError
from .wealth_reservations import reserved_cash,reserved_units
from .wealth_suitability import SuitabilityGuard

TYPES={'purchase':'wealth_purchase','redeem':'wealth_redemption'}
FIELDS={'account_id','account_name','product_id','product_name','action','amount_cents','units_1e4','nav_cents','quote_version','fee_cents','simulated'}

def bootstrap_wealth(bank):
    with bank.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS wealth_products(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,nav_cents INTEGER NOT NULL CHECK(nav_cents>0),quote_version INTEGER NOT NULL CHECK(quote_version>0))')
        db.execute('CREATE TABLE IF NOT EXISTS wealth_positions(user_id TEXT NOT NULL,account_id TEXT NOT NULL,product_id TEXT NOT NULL,units_1e4 INTEGER NOT NULL CHECK(units_1e4>=0),cost_cents INTEGER NOT NULL CHECK(cost_cents>=0),version INTEGER NOT NULL CHECK(version>=1),PRIMARY KEY(user_id,account_id,product_id))')
        db.execute('CREATE TABLE IF NOT EXISTS wealth_operation_epochs(operation_id TEXT PRIMARY KEY REFERENCES operations(id),user_id TEXT NOT NULL,epoch TEXT NOT NULL)')
        db.executemany('INSERT OR IGNORE INTO wealth_products VALUES(?,?,100,1)',[('demo_cash','演示产品甲'),('demo_bond','演示产品乙')])

class WealthService:
    def __init__(self,bank):self.bank=bank

    @staticmethod
    def _account(db,user,account_id):
        row=db.execute('SELECT * FROM accounts WHERE id=? AND user_id=?',(account_id,user)).fetchone()
        if row is None:raise DomainError('NOT_FOUND','未找到本人可使用的模拟账户。',404)
        return row

    @staticmethod
    def _product(db,product_id):
        row=db.execute('SELECT * FROM wealth_products WHERE id=?',(product_id,)).fetchone()
        if row is None:raise DomainError('NOT_FOUND','未找到该演示产品。',404)
        return row

    @staticmethod
    def _position(db,user,account_id,product_id):
        row=db.execute('SELECT * FROM wealth_positions WHERE user_id=? AND account_id=? AND product_id=?',(user,account_id,product_id)).fetchone()
        return dict(row) if row else {'units_1e4':0,'cost_cents':0,'version':0}

    @staticmethod
    def _units(amount,nav):
        if type(amount) is not int or not 1<=amount<=100000000:
            raise DomainError('INVALID_AMOUNT','金额必须是正数整数分，最多100万元。')
        if type(nav) is not int or nav<=0 or amount*10000%nav:
            raise DomainError('INVALID_PRECISION','这笔金额不能按当前演示估值精确换算份额，请调整金额。')
        return amount*10000//nav

    @staticmethod
    def _payload(row):
        try:p=json.loads(row['payload'])
        except (ValueError,TypeError):raise DomainError('INVALID_OPERATION','原理财申请无法核对。',409) from None
        if not isinstance(p,dict) or set(p)!=FIELDS or p.get('simulated') is not True or not isinstance(p.get('action'),str) or p['action'] not in TYPES:
            raise DomainError('INVALID_OPERATION','原理财申请字段无法核对。',409)
        if any(not isinstance(p[k],str) or not 1<=len(p[k])<=300 for k in ('account_id','account_name','product_id','product_name')):
            raise DomainError('INVALID_OPERATION','原账户或产品无法核对。',409)
        if any(type(p[k]) is not int or p[k]<=0 for k in ('amount_cents','units_1e4','nav_cents','quote_version')) or type(p['fee_cents']) is not int or p['fee_cents']!=0:
            raise DomainError('INVALID_OPERATION','原金额或份额无法核对。',409)
        if p['amount_cents']>100000000 or p['amount_cents']*10000!=p['units_1e4']*p['nav_cents']:
            raise DomainError('INVALID_OPERATION','原金额和份额不一致。',409)
        return p

    def check_epoch(self,db,user,opid):
        row=db.execute('SELECT epoch FROM wealth_operation_epochs WHERE operation_id=? AND user_id=?',(opid,user)).fetchone()
        if row is None:raise DomainError('INVALID_OPERATION','原申请的风险上下文无法核对。',409)
        self.bank._assert_transfer_allowed(db,user,row['epoch'])

    def _assert_reconciled(self,db,user,payload,exclude=None):
        rows=db.execute("SELECT * FROM operations WHERE user_id=? AND type=? AND status IN ('UNKNOWN','EXECUTING','MANUAL_REVIEW')",(user,TYPES[payload['action']])).fetchall()
        for row in rows:
            if row['id']==exclude:continue
            p=self._payload(row)
            if p['account_id']==payload['account_id'] and p['product_id']==payload['product_id']:
                raise DomainError('RESULT_PENDING','同一账户和产品已有结果待核实，请先核对原申请。',409)

    def _current(self,db,user,row):
        if not db.in_transaction:raise DomainError('INVALID_EXECUTION','交易必须在银行验证事务内。',409)
        self.bank._user(db,user)
        current=db.execute('SELECT * FROM operations WHERE id=? AND user_id=?',(row['id'],user)).fetchone()
        if current is None or row['user_id']!=user or any(current[k]!=row[k] for k in ('type','payload','risk')):
            raise DomainError('INVALID_OPERATION','原理财申请已改变。',409)
        if current['risk']!='RED' or current['status']!='WAITING_VERIFICATION' or current['confirmed_at'] is None or current['expires_at']<=self.bank.now().timestamp():
            raise DomainError('INVALID_OPERATION','原理财申请未确认、已过期或结果需要核对。',409)
        p=self._payload(current)
        if current['type']!=TYPES[p['action']]:raise DomainError('INVALID_OPERATION','原交易类型与请求不一致。',409)
        self.check_epoch(db,user,current['id'])
        SuitabilityGuard(self.bank).check(db,user,current)
        self._assert_reconciled(db,user,p,current['id'])
        return current,p

    def prepare(self, user, account_id, product_id, action, amount_cents, key,
                expected_epoch=None, plan_ticket=None):
        if any(not isinstance(v, str) or not 1 <= len(v) <= 120
               for v in (account_id, product_id, key)):
            raise DomainError('INVALID_REQUEST', '账户、产品和幂等键须为1至120字符的字符串。')
        if not isinstance(action, str) or action not in TYPES:
            raise DomainError('INVALID_REQUEST', '不支持该理财操作。')
        self._units(amount_cents, 100)
        kind = TYPES[action]
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            self.bank._expire(db, user)
            old = db.execute(
                'SELECT * FROM operations WHERE user_id=? AND idempotency_key=?',
                (user, key)
            ).fetchone()
            if old is not None:
                if old['type'] != kind or old['risk'] != 'RED':
                    raise DomainError('IDEMPOTENCY_CONFLICT', '幂等键已用于不同申请。', 409)
                try:
                    payload = self._payload(old)
                except DomainError:
                    raise DomainError('IDEMPOTENCY_CONFLICT', '原申请字段无法匹配。', 409) from None
                if tuple(payload[k] for k in (
                    'account_id', 'product_id', 'action', 'amount_cents'
                )) != (account_id, product_id, action, amount_cents):
                    raise DomainError('IDEMPOTENCY_CONFLICT', '幂等键已用于不同申请。', 409)
                owner = self.bank._assert_plan_ticket(db, user, plan_ticket, key)
                op = self.bank._create(db, user, kind, payload, 'RED', key, owner=owner)
                epoch_row = db.execute(
                    'SELECT epoch FROM wealth_operation_epochs '
                    'WHERE operation_id=? AND user_id=?', (old['id'], user)
                ).fetchone()
                if epoch_row is None:
                    raise DomainError('INVALID_OPERATION', '原申请的风险上下文无法核对。', 409)
                if old['status'] in ('UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW'):
                    raise DomainError('RESULT_PENDING', '原申请结果待核实，请先核对。', 409)
                return op
            owner = self.bank._assert_plan_ticket(db, user, plan_ticket, key)
            self.bank._assert_transfer_allowed(db, user, expected_epoch)
            self.bank._unlocked(db, user)
            account = self._account(db, user, account_id)
            product = self._product(db, product_id)
            units = self._units(amount_cents, product['nav_cents'])
            binding = (SuitabilityGuard(self.bank).prepare(db,user,product,amount_cents)
                       if action == 'purchase' else None)
            payload = {
                'account_id': account_id,
                'account_name': account['name'],
                'product_id': product_id,
                'product_name': product['name'],
                'action': action,
                'amount_cents': amount_cents,
                'units_1e4': units,
                'nav_cents': product['nav_cents'],
                'quote_version': product['quote_version'],
                'fee_cents': 0,
                'simulated': True,
            }
            self._assert_reconciled(db, user, payload)
            if action == 'purchase':
                available = account['balance_cents'] - self.bank._reserved_amount(
                    db, user, account_id
                )
                if available < amount_cents:
                    raise DomainError('INSUFFICIENT_FUNDS', '模拟账户可用余额不足。')
            else:
                position = self._position(db, user, account_id, product_id)
                available = position['units_1e4'] - reserved_units(
                    db, user, self.bank.now().timestamp(), account_id, product_id
                )
                if available < units:
                    raise DomainError('INSUFFICIENT_UNITS', '可用模拟份额不足。')
            op = self.bank._create(db, user, kind, payload, 'RED', key, owner=owner)
            db.execute(
                'INSERT INTO wealth_operation_epochs(operation_id,user_id,epoch) '
                'VALUES(?,?,?)',
                (op['id'], user, self.bank._safety_epoch(db, user))
            )
            if binding is not None:
                SuitabilityGuard(self.bank).record(db,user,op['id'],binding)
            return op

    def execute(self, db, user, row):
        current, p = self._current(db, user, row)
        account_id, product_id = p['account_id'], p['product_id']
        account = self._account(db, user, account_id)
        product = self._product(db, product_id)
        position = self._position(db, user, account_id, product_id)
        if (account['name'] != p['account_name']
                or product['name'] != p['product_name']
                or product['nav_cents'] != p['nav_cents']
                or product['quote_version'] != p['quote_version']):
            raise DomainError(
                'QUOTE_CHANGED', '账户、产品名称或估值已变化，请取消原申请重新准备。', 409)
        units, amount = p['units_1e4'], p['amount_cents']
        old_units, old_cost = position['units_1e4'], position['cost_cents']
        old_balance = account['balance_cents']
        now = self.bank.now()
        if p['action'] == 'purchase':
            reserved = self.bank._reserved_amount(db, user, account_id, current['id'])
            if old_balance - reserved < amount:
                raise DomainError('INSUFFICIENT_FUNDS', '模拟账户可用余额不足。', 409)
            new_units = old_units + units
            new_cost = old_cost + amount
            new_balance = old_balance - amount
            kind = 'INVESTMENT_PURCHASE'
            message = '模拟申购已完成。'
        else:
            reserved = reserved_units(
                db, user, now.timestamp(), account_id, product_id, current['id'])
            if units > old_units - reserved:
                raise DomainError('INSUFFICIENT_UNITS', '模拟产品可用份额不足。', 409)
            cost_reduction = old_cost * units // old_units
            new_units = old_units - units
            new_cost = 0 if new_units == 0 else old_cost - cost_reduction
            new_balance = old_balance + amount
            kind = 'INVESTMENT_REDEMPTION'
            message = '模拟赎回已完成。'
        if position['version'] == 0:
            changed = db.execute(
                'INSERT INTO wealth_positions '
                '(user_id,account_id,product_id,units_1e4,cost_cents,version) '
                'VALUES (?,?,?,?,?,?)',
                (user, account_id, product_id, new_units, new_cost, 1))
        else:
            changed = db.execute(
                'UPDATE wealth_positions SET units_1e4=?,cost_cents=?,version=version+1 '
                'WHERE user_id=? AND account_id=? AND product_id=? AND version=?',
                (new_units, new_cost, user, account_id, product_id, position['version']))
        if changed.rowcount != 1:
            raise DomainError('POSITION_CHANGED', '模拟持仓已变化，请重新核对。', 409)
        changed = db.execute(
            'UPDATE accounts SET balance_cents=? WHERE id=? AND user_id=?',
            (new_balance, account_id, user))
        if changed.rowcount != 1:
            raise DomainError('ACCOUNT_CHANGED', '模拟账户已变化，请重新核对。', 409)
        tid = 'tx_' + uuid.uuid4().hex
        db.execute(
            'INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)',
            (tid, user, account_id, kind, amount, '模拟理财', product['name'],
             now.isoformat(), '合成演示资产转移，不计消费或收入', current['id']))
        self.bank._event(
            db, user, current['id'], 'WEALTH_TRADE_SUCCEEDED',
            kind + ' / ' + str(amount) + ' 分')
        return {
            'transaction_id': tid, 'amount_cents': amount,
            'account_id': account_id, 'product_id': product_id,
            'action': p['action'], 'units_1e4': units,
            'position_units_1e4': new_units, 'cost_cents': new_cost,
            'balance_cents': new_balance, 'simulated': True, 'message': message,
        }

    def query(self, user):
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            self.bank._expire(db, user)
            product_rows = db.execute(
                'SELECT id, name, nav_cents, quote_version '
                'FROM wealth_products ORDER BY id'
            ).fetchall()
            products = [
                {
                    'id': row['id'],
                    'name': row['name'],
                    'nav_cents': row['nav_cents'],
                    'quote_version': row['quote_version'],
                    'simulated': True,
                }
                for row in product_rows
            ]
            rows = db.execute(
                'SELECT wp.account_id, a.name AS account_name, '
                'wp.product_id, p.name AS product_name, wp.units_1e4, '
                'wp.cost_cents, wp.version, p.nav_cents '
                'FROM wealth_positions wp '
                'JOIN accounts a ON a.id=wp.account_id AND a.user_id=wp.user_id '
                'JOIN wealth_products p ON p.id=wp.product_id '
                'WHERE wp.user_id=? ORDER BY wp.account_id, wp.product_id',
                (user,),
            ).fetchall()
            positions = []
            for row in rows:
                value = row['units_1e4'] * row['nav_cents'] // 10000
                reserved = reserved_units(
                    db, user, self.bank.now().timestamp(), row['account_id'], row['product_id']
                )
                positions.append({
                    'account_id': row['account_id'],
                    'account_name': row['account_name'],
                    'product_id': row['product_id'],
                    'product_name': row['product_name'],
                    'units_1e4': row['units_1e4'],
                    'cost_cents': row['cost_cents'],
                    'version': row['version'],
                    'value_cents': value,
                    'gain_cents': value - row['cost_cents'],
                    'reserved_units_1e4': reserved,
                    'available_units_1e4': row['units_1e4'] - reserved,
                    'simulated': True,
                })
            applications=[self.bank._view(row) for row in db.execute(
                "SELECT * FROM operations WHERE user_id=? AND type IN ('wealth_purchase','wealth_redemption') AND status NOT IN ('CANCELLED','EXPIRED','FAILED') ORDER BY created_at DESC,rowid DESC",(user,))]
        return {
            'applications':applications,
            'products': products,
            'positions': positions,
            'simulated': True,
            'valuation_note': '固定演示估值，不代表真实报价或预期收益。',
        }

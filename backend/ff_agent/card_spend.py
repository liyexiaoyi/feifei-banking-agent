"""Card spend core: thin transaction driver around card_spend_rules.

All ownership/limit/lock/version/risk/payload validation lives in card_spend_rules.
Effects are local SQLite only (simulated); no bank or merchant is connected.
"""
import uuid
from datetime import datetime, timezone

from . import card_spend_rules as rules
from .bank import DomainError

MERCHANTS = {'demo_market': '演示超市', 'demo_cafe': '演示咖啡店'}
MESSAGE = '本机模拟消费已完成，未连接银行或商户。'
NOTE = '本机模拟，未连接商户'


def _now(bank):
    fn = getattr(bank, 'now', None)
    now = fn() if callable(fn) else datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise DomainError('INVALID_TIME', '时间缺少时区。', 500)
    return now


class CardSpend:
    def __init__(self, bank):
        self.bank = bank

    def merchants(self):
        return [{'id': k, 'name': v, 'simulated': True, 'merchant_connected': False}
                for k, v in MERCHANTS.items()]

    def prepare(self, user, card_id, merchant_id, amount_cents, expected_card_version,
                expected_control_version, key, plan_ticket=None):
        bank = self.bank
        with bank.transaction() as db:
            ctx = rules.prepare_context(bank, db, user, card_id, merchant_id, amount_cents,
                                        expected_card_version, expected_control_version,
                                        key, dict(MERCHANTS), plan_ticket=plan_ticket)
            if ctx.get('existing') is not None:
                return ctx['existing']
            return bank._create(db, user, 'card_spend', ctx['payload'], ctx['risk'], key)

    def execute(self, db, user, row):
        """Run inside the Bank caller's transaction; any raise rolls everything back.
        Returns the result dict only; Bank finalizes status/executed_day."""
        bank = self.bank
        ctx = rules.execution_context(bank, db, user, row, dict(MERCHANTS))
        op, p, acct = ctx['operation'], ctx['payload'], ctx['account']
        op_id, amount = op['id'], p['amount_cents']
        if type(amount) is not int or not 1 <= amount <= 100000000:
            raise DomainError('INVALID_AMOUNT', '消费金额无效。')
        if acct['id'] != p['account_id']:
            raise DomainError('ACCOUNT_CHANGED', '扣款账户已变化。', 409)
        name = p['merchant_name']
        if MERCHANTS.get(p['merchant_id']) != name:
            raise DomainError('MERCHANT_CHANGED', '商户信息已变化。', 409)
        now = _now(bank)

        cur = db.execute(
            'UPDATE accounts SET balance_cents=balance_cents-? WHERE id=? AND user_id=? AND balance_cents>=?',
            (amount, acct['id'], user, amount))
        if cur.rowcount != 1:
            raise DomainError('INSUFFICIENT_FUNDS', '账户余额不足。', 409)

        tx_id = str(uuid.uuid4())
        db.execute(
            """INSERT INTO transactions(id,user_id,account_id,kind,amount_cents,category,
               merchant,occurred_at,note,operation_id) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (tx_id, user, acct['id'], 'EXPENSE', amount, '卡片消费', name,
             now.isoformat(), NOTE, op_id))
        bal = db.execute('SELECT balance_cents FROM accounts WHERE id=? AND user_id=?',
                         (acct['id'], user)).fetchone()[0]

        bank._event(db, user, op_id, 'CARD_SPEND_SUCCEEDED',
                    f'本机模拟消费 {p["card_id"]} / {name} / {amount} 分 / 流水 {tx_id} / 未连接商户')

        return {'transaction_id': tx_id, 'card_id': p['card_id'],
                'merchant_id': p['merchant_id'], 'merchant_name': name,
                'amount_cents': amount, 'balance_cents': bal,
                'simulated': True, 'merchant_connected': False, 'message': MESSAGE}

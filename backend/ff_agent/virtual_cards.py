"""Local-only virtual demonstration cards; confirmation remains in Bank."""
import hashlib
import json
import sqlite3

from .bank import DomainError


def bootstrap_virtual_cards(bank):
    with bank.transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS bank_virtual_cards(
            id TEXT PRIMARY KEY,user_id TEXT NOT NULL,account_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,masked_number TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','LOST')),
            version INTEGER NOT NULL CHECK(version>=1),origin_operation_id TEXT NOT NULL UNIQUE)''')
        db.execute('''CREATE VIEW IF NOT EXISTS bank_all_cards AS
            SELECT id,user_id,account_id,name,masked_number,status,version,'physical' AS storage_kind FROM bank_cards
            UNION ALL
            SELECT id,user_id,account_id,name,masked_number,status,version,'virtual' AS storage_kind FROM bank_virtual_cards''')


class VirtualCardService:
    def __init__(self, bank):
        self.bank = bank

    def applications(self,user):
        with self.bank.transaction() as db:
            self.bank._user(db,user)
            self.bank._expire(db,user)
            rows=db.execute("SELECT * FROM operations WHERE user_id=? AND type='virtual_card_create' "
                            "AND status NOT IN ('CANCELLED','EXPIRED','FAILED') ORDER BY created_at DESC,rowid DESC",
                            (user,)).fetchall()
            return [self.bank._view(row) for row in rows]

    @staticmethod
    def _account(db, user, account_id):
        row = db.execute('SELECT * FROM accounts WHERE id=? AND user_id=?', (account_id, user)).fetchone()
        if row is None:
            raise DomainError('NOT_FOUND', '未找到可关联的本人模拟账户。', 404)
        return row

    @staticmethod
    def _payload(row):
        try:
            value = json.loads(row['payload'])
        except (ValueError, TypeError):
            raise DomainError('INVALID_OPERATION', '原申请数据无法核对。', 409) from None
        if (not isinstance(value, dict) or set(value) != {'account_id','account_name','card_name','simulated'}
                or value['simulated'] is not True
                or any(not isinstance(value[k], str) or not 1 <= len(value[k]) <= 300
                       for k in ('account_id','account_name','card_name'))):
            raise DomainError('INVALID_OPERATION', '原申请字段不完整或已改变。', 409)
        return value

    def _pending(self, db, user, account_id):
        rows = db.execute("SELECT payload FROM operations WHERE user_id=? AND type='virtual_card_create' "
                          "AND status IN ('WAITING_CONFIRMATION','WAITING_VERIFICATION','READY','UNKNOWN','EXECUTING','MANUAL_REVIEW')",
                          (user,)).fetchall()
        if any(self._payload(row)['account_id'] == account_id for row in rows):
            raise DomainError('VIRTUAL_APPLICATION_PENDING', '这个账户已有申请待处理，请先核对原申请记录。', 409)

    def _current(self, db, user, row):
        if not db.in_transaction:
            raise DomainError('INVALID_EXECUTION', '申请必须在银行确认事务内完成。', 409)
        current = db.execute('SELECT * FROM operations WHERE user_id=? AND id=?', (user, row['id'])).fetchone()
        if (current is None or row['user_id'] != user or current['type'] != 'virtual_card_create'
                or any(current[k] != row[k] for k in ('type','payload','risk'))
                or current['risk'] != 'YELLOW' or current['status'] != 'WAITING_CONFIRMATION'
                or current['confirmed_at'] is None or current['expires_at'] <= self.bank.now().timestamp()):
            raise DomainError('INVALID_OPERATION', '原申请尚未确认、已过期或需要核对。', 409)
        return current, self._payload(current)

    def execute(self, db, user, row):
        current, payload = self._current(db, user, row)
        account = self._account(db, user, payload['account_id'])
        if (account['name'] != payload['account_name']
                or payload['card_name'] != account['name'] + '虚拟演示卡'):
            raise DomainError(
                'INVALID_OPERATION', '账户名称与已确认的申请不一致。', status=409
            )

        operation_id = current['id']
        card_id = 'vcard_' + operation_id.removeprefix('op_')
        digits = int(hashlib.sha256(operation_id.encode('utf-8')).hexdigest(), 16) % 10000
        masked_number = f'DEMO VIRTUAL ···· {digits:04d}'
        try:
            db.execute(
                'INSERT INTO bank_virtual_cards '
                '(id, user_id, account_id, name, masked_number, status, version, '
                'origin_operation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (card_id, current['user_id'], account['id'], payload['card_name'],
                 masked_number, 'ACTIVE', 1, operation_id),
            )
        except sqlite3.IntegrityError as exc:
            raise DomainError(
                'VIRTUAL_CARD_CONFLICT',
                '无法创建模拟虚拟卡：记录发生冲突或不满足本机演示约束。',
                status=409,
            ) from exc

        from .card_controls import ensure_controls_row
        ensure_controls_row(db,user,card_id,'virtual')

        self.bank._event(
            db, user, operation_id, 'VIRTUAL_CARD_CREATED',
            json.dumps({'card_id': card_id, 'account_id': account['id'],
                        'simulated': True}, ensure_ascii=False),
        )
        return {
            'card_id': card_id, 'account_id': account['id'],
            'status': 'ACTIVE', 'version': 1, 'masked_number': masked_number,
            'simulated': True,
            'message': '模拟虚拟卡申请已完成，仅限本机演示。',
        }

    def prepare(self, user, account_id, key, plan_ticket=None):
        if not isinstance(account_id, str) or not 1 <= len(account_id) <= 120:
            raise DomainError('INVALID_ACCOUNT', '账户编号无效。')
        if not isinstance(key, str) or not 1 <= len(key) <= 120:
            raise DomainError('INVALID_KEY', '请求编号无效。')
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            self.bank._expire(db, user)
            old = db.execute(
                'SELECT * FROM operations WHERE user_id=? AND idempotency_key=?',
                (user, key),
            ).fetchone()
            if old is not None:
                if old['type'] != 'virtual_card_create' or old['risk'] != 'YELLOW':
                    raise DomainError('IDEMPOTENCY_CONFLICT', '原请求的操作类型或风险等级不匹配。', 409)
                payload = self._payload(old)
                if payload['account_id'] != account_id:
                    raise DomainError('IDEMPOTENCY_CONFLICT', '同一请求编号的账户已改变，请重新发起。', 409)
                owner = self.bank._assert_plan_ticket(db, user, plan_ticket, key)
                operation = self.bank._create(
                    db, user, 'virtual_card_create', payload, 'YELLOW', key, owner=owner,
                )
                if old['status'] in ('UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW'):
                    raise DomainError('RESULT_PENDING', '原操作结果尚未确定，请查询原操作回执。', 409)
                return operation
            account = self._account(db, user, account_id)
            self.bank._unlocked(db, user)
            owner = self.bank._assert_plan_ticket(db, user, plan_ticket, key)
            existing = db.execute(
                'SELECT id FROM bank_virtual_cards WHERE account_id=?',
                (account_id,),
            ).fetchone()
            if existing is not None:
                raise DomainError('VIRTUAL_CARD_EXISTS', '本机演示每个账户仅支持一张虚拟卡，该账户已有虚拟卡。', 409)
            self._pending(db, user, account_id)
            payload = {
                'account_id': account_id,
                'account_name': account['name'],
                'card_name': account['name'] + '虚拟演示卡',
                'simulated': True,
            }
            return self.bank._create(
                db, user, 'virtual_card_create', payload, 'YELLOW', key, owner=owner,
            )

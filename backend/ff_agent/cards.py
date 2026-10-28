"""Simulated card status operations; Bank owns confirmation and verification."""
import json

from .bank import DomainError


_ACTIONS = {
    'report_lost': ('card_report_lost', 'ACTIVE', 'LOST', '模拟挂失成功'),
    'restore': ('card_restore', 'LOST', 'ACTIVE', '模拟解挂成功'),
}
_FIELDS = ('id', 'account_id', 'name', 'masked_number', 'status', 'version')
_PAYLOAD_FIELDS = {
    'card_id', 'account_id', 'card_name', 'masked_number', 'action',
    'expected_version', 'from_status', 'to_status', 'simulated',
}
_CARD_WRITE_SQL = {
    'physical': "UPDATE bank_cards SET status=?,version=version+1 WHERE id=? AND user_id=? AND account_id=? AND status=? AND version=?",
    'virtual': "UPDATE bank_virtual_cards SET status=?,version=version+1 WHERE id=? AND user_id=? AND account_id=? AND status=? AND version=?",
}


def bootstrap_cards(bank):
    with bank.transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS bank_cards(
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            account_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            masked_number TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','LOST')),
            version INTEGER NOT NULL CHECK(version >= 1))""")
        db.execute("""INSERT OR IGNORE INTO bank_cards
            (id,user_id,account_id,name,masked_number,status,version)
            SELECT 'card_' || id,user_id,id,name || '演示卡',
                   'DEMO ···· ' || substr(masked_number,-4),'ACTIVE',1
            FROM accounts""")


class CardService:
    def __init__(self, bank):
        self.bank = bank

    @staticmethod
    def _validate(card_id, action, version):
        if not isinstance(card_id, str) or not card_id:
            raise DomainError('INVALID_CARD', '卡片编号无效。', 400)
        if not isinstance(action, str) or action not in _ACTIONS:
            raise DomainError('INVALID_ACTION', '卡片操作无效。', 400)
        if type(version) is not int or version < 1:
            raise DomainError('INVALID_VERSION', '版本必须为正整数。', 400)

    @staticmethod
    def _payload(row):
        try:
            payload = json.loads(row['payload'])
        except (TypeError, ValueError):
            raise DomainError('INVALID_OPERATION', '操作数据无效。', 409) from None
        if not isinstance(payload, dict) or set(payload) != _PAYLOAD_FIELDS:
            raise DomainError('INVALID_OPERATION', '操作数据不完整。', 409)
        return payload

    @staticmethod
    def _card(db, user, card_id):
        rows = db.execute("""SELECT c.* FROM bank_all_cards c
            JOIN accounts a ON a.id=c.account_id AND a.user_id=c.user_id
            WHERE c.id=? AND c.user_id=?""", (card_id, user)).fetchall()
        if not rows:
            raise DomainError('NOT_FOUND', '未找到可访问的数据。', 404)
        if len(rows) != 1:
            raise DomainError('CARD_CONFLICT', '卡片来源存在歧义，请先核对原记录。', 409)
        return rows[0]

    @staticmethod
    def _check(card, version, status):
        if card['version'] != version or card['status'] != status:
            raise DomainError('CARD_CONFLICT', '卡片状态或版本已改变，请刷新。', 409)

    def _assert_reconciled(self,db,user,card_id,exclude=None):
        from .card_controls import assert_card_reconciled
        return assert_card_reconciled(db,user,card_id,exclude)

    def list_cards(self, user):
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            rows = db.execute("""SELECT c.* FROM bank_all_cards c
                JOIN accounts a ON a.id=c.account_id AND a.user_id=c.user_id
                WHERE c.user_id=? ORDER BY c.id""", (user,)).fetchall()
            if len({row['id'] for row in rows}) != len(rows):
                raise DomainError('CARD_CONFLICT', '卡片来源存在歧义，请先核对原记录。', 409)
            return [dict({field: row[field] for field in _FIELDS}, simulated=True)
                    for row in rows]

    def prepare_status(self, user, card_id, action, key, expected_version,
                       plan_ticket=None):
        self._validate(card_id, action, expected_version)
        if not isinstance(key, str) or not 1 <= len(key) <= 120:
            raise DomainError('INVALID_KEY', '请求编号无效。', 400)
        kind, before, after, _ = _ACTIONS[action]
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            self.bank._expire(db, user)
            old = db.execute("""SELECT * FROM operations
                WHERE user_id=? AND idempotency_key=?""", (user, key)).fetchone()
            if old is not None:
                if old['type'] != kind:
                    raise DomainError('IDEMPOTENCY_CONFLICT', '请求编号已绑定其他内容。', 409)
                payload = self._payload(old)
                if (old['type'] != kind or payload['card_id'] != card_id
                        or payload['action'] != action
                        or type(payload['expected_version']) is not int
                        or payload['expected_version'] != expected_version):
                    raise DomainError('IDEMPOTENCY_CONFLICT', '请求编号已绑定其他内容。', 409)
                if old['risk'] != 'RED' or old['status'] == 'UNKNOWN':
                    raise DomainError('INVALID_OPERATION', '原操作需核实，不能重试执行。', 409)
                owner = self.bank._assert_plan_ticket(db, user, plan_ticket, key)
                return self.bank._create(db, user, kind, payload, 'RED', key, owner=owner)
            self.bank._unlocked(db, user)
            owner = self.bank._assert_plan_ticket(db, user, plan_ticket, key)
            card = self._card(db, user, card_id)
            self._check(card, expected_version, before)
            self._assert_reconciled(db,user,card_id)
            payload = {
                'card_id': card_id, 'account_id': card['account_id'],
                'card_name': card['name'], 'masked_number': card['masked_number'],
                'action': action, 'expected_version': expected_version,
                'from_status': before, 'to_status': after, 'simulated': True,
            }
            return self.bank._create(db, user, kind, payload, 'RED', key, owner=owner)

    def execute(self, db, user, row):
        # Bank calls this only after RED confirmation and successful verification,
        # inside its execution transaction; Bank also persists the final result.
        if not db.in_transaction:
            raise DomainError('INVALID_EXECUTION', '执行必须位于银行事务内。', 409)
        current = db.execute('SELECT * FROM operations WHERE id=? AND user_id=?',
                             (row['id'], user)).fetchone()
        if current is None or row['user_id'] != user:
            raise DomainError('NOT_FOUND', '未找到可访问的数据。', 404)
        if any(current[k] != row[k] for k in ('type', 'payload', 'risk')):
            raise DomainError('INVALID_OPERATION', '操作内容已改变。', 409)
        if (current['risk'] != 'RED' or current['confirmed_at'] is None
                or current['status'] != 'WAITING_VERIFICATION'):
            raise DomainError('INVALID_OPERATION', '操作当前不可执行。', 409)
        if current['expires_at'] <= self.bank.now().timestamp():
            raise DomainError('OPERATION_EXPIRED', '操作已过期。', 409)
        payload = self._payload(current)
        card_id, action = payload['card_id'], payload['action']
        version = payload['expected_version']
        self._validate(card_id, action, version)
        kind, before, after, message = _ACTIONS[action]
        if (current['type'] != kind or payload['simulated'] is not True
                or payload['from_status'] != before or payload['to_status'] != after):
            raise DomainError('INVALID_OPERATION', '操作绑定无效。', 409)
        card = self._card(db, user, card_id)
        self._assert_reconciled(db,user,card_id,row['id'])
        self._check(card, version, before)
        if any(payload[p] != card[c] for p, c in (
                ('account_id', 'account_id'), ('card_name', 'name'),
                ('masked_number', 'masked_number'))):
            raise DomainError('CARD_CONFLICT', '卡片资料已改变，请重新准备。', 409)
        statement = _CARD_WRITE_SQL.get(card['storage_kind'])
        if statement is None:
            raise DomainError('CARD_CONFLICT', '卡片来源无法核对。', 409)
        changed = db.execute(statement,
            (after, card_id, user, card['account_id'], before, version))
        if changed.rowcount != 1:
            raise DomainError('CARD_CONFLICT', '卡片状态或版本已改变，请刷新。', 409)
        self.bank._event(db, user, row['id'], 'CARD_STATUS_CHANGED', json.dumps({
            'card_id': card_id, 'from_status': before, 'to_status': after,
            'version': version + 1, 'simulated': True,
        }, ensure_ascii=False, sort_keys=True))
        return {'card_id': card_id, 'status': after, 'version': version + 1,
                'simulated': True, 'message': message}

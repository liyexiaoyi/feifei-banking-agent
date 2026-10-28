"""Bind explicit demo assessments to existing Bank purchases, never execute funds."""
import hashlib
import json

from .bank import DomainError, PENDING
from .investment_profile import ProfileService
from . import wealth_compare as policy


def bootstrap_suitability(bank):
    with bank.transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS wealth_suitability_bindings(
            operation_id TEXT PRIMARY KEY REFERENCES operations(id), user_id TEXT NOT NULL,
            profile_version INTEGER NOT NULL, profile_expires_at REAL NOT NULL,
            survey_version TEXT NOT NULL, policy_version TEXT NOT NULL, policy_hash TEXT NOT NULL,
            product_id TEXT NOT NULL, quote_version INTEGER NOT NULL, payload_hash TEXT NOT NULL,
            amount_cents INTEGER NOT NULL, created_at REAL NOT NULL,
            FOREIGN KEY(user_id,profile_version) REFERENCES investment_profiles(user_id,version))''')
        for action in ('UPDATE', 'DELETE'):
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS wealth_suitability_no_{action.lower()}
                BEFORE {action} ON wealth_suitability_bindings
                BEGIN SELECT RAISE(ABORT,'immutable suitability binding'); END''')


def _payload(row):
    try:
        value = json.loads(row['payload'])
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (TypeError, ValueError):
        raise DomainError('INVALID_OPERATION', '原理财申请无法核对。', 409) from None


def _digest(row):
    return hashlib.sha256(row['payload'].encode('utf8')).hexdigest()


class SuitabilityGuard:
    def __init__(self, bank):
        self.bank = bank

    @staticmethod
    def _owned(db, user, opid):
        if not db.in_transaction:
            raise DomainError('INVALID_EXECUTION', '测评绑定必须位于银行事务内。', 409)
        row = db.execute('SELECT * FROM operations WHERE id=? AND user_id=?', (opid, user)).fetchone()
        if row is None:
            raise DomainError('NOT_FOUND', '未找到本人可访问的申请。', 404)
        return row

    def prepare(self, db, user, product, amount):
        profiles = ProfileService(self.bank)
        view = profiles.current(db, user)
        if view['profile'] is None:
            raise DomainError('ASSESSMENT_REQUIRED', '请先逐题完成演示风险测评。', 409)
        snapshot = profiles.binding_snapshot(db, user, view['profile']['version'])
        pid = product['id']
        if pid not in policy.METADATA:
            raise DomainError('POLICY_CHANGED', '演示产品规则无法核对，请先刷新。', 409)
        metadata = policy.METADATA[pid]
        if snapshot['level'] < metadata['risk_level']:
            raise DomainError('SUITABILITY_MISMATCH', '该演示产品风险级别高于当前测评结果，不能准备申购。', 409)
        if amount < metadata['minimum_cents']:
            raise DomainError('BELOW_MINIMUM', '金额低于该演示产品的起购金额，请先核对。', 409)
        return {'profile_version': snapshot['version'], 'profile_expires_at': snapshot['expires_at'],
                'survey_version': snapshot['survey_version'], 'policy_version': policy.POLICY_VERSION,
                'policy_hash': policy.policy_fingerprint(pid), 'product_id': pid,
                'quote_version': product['quote_version'], 'amount_cents': amount}

    def record(self, db, user, opid, binding):
        row = self._owned(db, user, opid)
        payload = _payload(row)
        if (row['type'] != 'wealth_purchase' or row['status'] != 'WAITING_CONFIRMATION'
                or row['risk'] != 'RED' or payload.get('action') != 'purchase'
                or any(payload.get(key) != binding[key] for key in ('product_id', 'quote_version', 'amount_cents'))):
            raise DomainError('INVALID_OPERATION', '无法将测评绑定到这笔申请。', 409)
        fields = ('profile_version', 'profile_expires_at', 'survey_version', 'policy_version',
                  'policy_hash', 'product_id', 'quote_version', 'amount_cents')
        db.execute('''INSERT INTO wealth_suitability_bindings(
            operation_id,user_id,profile_version,profile_expires_at,survey_version,policy_version,
            policy_hash,product_id,quote_version,amount_cents,payload_hash,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (opid, user, *(binding[key] for key in fields), _digest(row), self.bank.now().timestamp()))
        self.check(db, user, row)

    def check(self, db, user, row):
        current = self._owned(db, user, row['id'])
        if current['type'] != 'wealth_purchase':
            return
        if current['status'] in ('UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW'):
            raise DomainError('RESULT_PENDING', '原申请结果待核实，请先核对，不重复执行。', 409)
        if current['status'] not in PENDING:
            return
        binding = db.execute('SELECT * FROM wealth_suitability_bindings WHERE operation_id=? AND user_id=?',
                             (current['id'], user)).fetchone()
        if binding is None:
            raise DomainError('ASSESSMENT_BINDING_REQUIRED', '原申请缺少测评绑定，请取消旧申请后重新准备。', 409)
        if _digest(current) != binding['payload_hash']:
            raise DomainError('INVALID_OPERATION', '原申请内容已改变，不能继续。', 409)
        snapshot = ProfileService(self.bank).binding_snapshot(db, user, binding['profile_version'])
        if (snapshot['expires_at'] != binding['profile_expires_at']
                or snapshot['survey_version'] != binding['survey_version']):
            raise DomainError('PROFILE_CHANGED', '原测评记录已改变，请重新核对。', 409)
        pid = binding['product_id']
        if (pid not in policy.METADATA or policy.POLICY_VERSION != binding['policy_version']
                or policy.policy_fingerprint(pid) != binding['policy_hash']):
            raise DomainError('POLICY_CHANGED', '演示产品规则已更新，请取消原申请后重新准备。', 409)
        payload = _payload(current)
        metadata = policy.METADATA[pid]
        if snapshot['level'] < metadata['risk_level']:
            raise DomainError('SUITABILITY_MISMATCH', '该演示产品风险级别高于当前测评结果。', 409)
        if type(payload.get('amount_cents')) is not int:
            raise DomainError('INVALID_OPERATION', '原申请金额无法核对。', 409)
        if payload['amount_cents'] < metadata['minimum_cents']:
            raise DomainError('BELOW_MINIMUM', '原申请低于演示产品起购金额。', 409)
        product = db.execute('SELECT * FROM wealth_products WHERE id=?', (pid,)).fetchone()
        if (product is None or product['quote_version'] != binding['quote_version']
                or product['quote_version'] != payload.get('quote_version')
                or product['nav_cents'] != payload.get('nav_cents') or product['name'] != payload.get('product_name')):
            raise DomainError('QUOTE_CHANGED', '演示报价或产品资料已改变，请取消原申请后重新准备。', 409)

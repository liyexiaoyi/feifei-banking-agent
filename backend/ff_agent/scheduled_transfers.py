"""Scheduled mandate service: prepare, activate after RED authorization, read, cancel. LOCAL SIMULATED only."""
import hashlib
import re
import uuid
from contextlib import closing

from .bank import DomainError, PENDING, connect
from .scheduled_rules import (authorization_payload, guard_authorization, load_schedule,
                              schedule_view, validate_intent)

AUTH_KIND = 'scheduled_transfer_authorize'
IN_FLIGHT = ('UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW')
TERMINAL = ('CANCELLED', 'NOT_PAID')
KEY_RE = re.compile(r'[A-Za-z0-9_-]{8,64}')
RESERVED = ('task-', 'select-', 'sched-')
COLUMNS = ('id', 'user_id', 'status', 'version', 'request_key', 'request_hash', 'account_id', 'account_name',
           'payee_id', 'payee_name', 'payee_phone_masked', 'payee_phone_digest', 'amount_cents', 'due_at',
           'due_at_iso', 'grace_seconds', 'safety_epoch', 'authorization_operation_id', 'child_operation_id',
           'outcome_code', 'cancel_key', 'created_at', 'updated_at', 'cancelled_at')


def _key_ok(key):
    return isinstance(key, str) and key.isascii() and KEY_RE.fullmatch(key) is not None and not key.startswith(RESERVED)


class ScheduledTransfers:
    def __init__(self, bank):
        self.bank = bank

    def _read(self):
        return closing(connect(self.bank.path))

    def _op(self, db, user, opid):
        if not opid:
            return None
        return db.execute('SELECT * FROM operations WHERE user_id=? AND id=?', (user, opid)).fetchone()

    def _view(self, db, user, sid, replayed=False):
        return schedule_view(self.bank, db, user, load_schedule(db, user, sid), replayed)

    def _pair(self, db, user, sid, replayed):
        s = self._view(db, user, sid, replayed)
        op = self._op(db, user, s['authorization_operation_id'])
        return {'schedule': s, 'operation': self.bank._view(op)}

    def prepare(self, user, payee_id, amount_cents, due_at, key, expected_epoch=None):
        due_ts, due_iso, rhash = validate_intent(self.bank, payee_id, amount_cents, due_at, key)
        b = self.bank
        with b.transaction() as db:
            row = db.execute('SELECT id,request_hash FROM scheduled_transfers WHERE user_id=? AND request_key=?',
                             (user, key)).fetchone()
            if row:
                if row['request_hash'] != rhash:
                    raise DomainError('IDEMPOTENCY_CONFLICT', '同一请求编号的定时转账内容已改变，请重新发起。', 409)
                return self._pair(db, user, row['id'], True)
            b._user(db, user)
            b._unlocked(db, user)
            b._assert_transfer_allowed(db, user, expected_epoch)
            now = b.now().timestamp()
            if not now + 60 <= due_ts <= now + 30 * 86400:
                raise DomainError('INVALID_DUE_AT', '执行时间须在未来 60 秒至 30 天之间。')
            payee = db.execute('SELECT * FROM payees WHERE user_id=? AND id=?', (user, payee_id)).fetchone()
            if not payee:
                raise DomainError('NOT_FOUND', '未找到可访问的收款人', 404)
            account = db.execute('SELECT * FROM accounts WHERE user_id=? ORDER BY id LIMIT 1', (user,)).fetchone()
            if not account:
                raise DomainError('NOT_FOUND', '未找到可访问的账户', 404)
            if account['balance_cents'] - b._reserved_amount(db, user, account['id']) < amount_cents:
                raise DomainError('INSUFFICIENT_FUNDS', '可用余额不足，其他待确认转账可能已预留资金。')
            sid = 'sch_' + uuid.uuid4().hex
            opkey = 'schedauth-' + sid
            if db.execute('SELECT 1 FROM operations WHERE user_id=? AND idempotency_key=?', (user, opkey)).fetchone():
                raise DomainError('IDEMPOTENCY_CONFLICT', '授权操作编号已被占用，本次准备未保存。', 409)
            phone = payee['phone']
            rec = {'id': sid, 'user_id': user, 'status': 'AUTHORIZING', 'version': 1, 'request_key': key,
                   'request_hash': rhash, 'account_id': account['id'], 'account_name': account['name'],
                   'payee_id': payee_id, 'payee_name': payee['name'],
                   'payee_phone_masked': phone[:3] + '****' + phone[-4:],
                   'payee_phone_digest': hashlib.sha256(phone.encode()).hexdigest(),
                   'amount_cents': amount_cents, 'due_at': due_ts, 'due_at_iso': due_iso, 'grace_seconds': 86400,
                   'safety_epoch': b._safety_epoch(db, user), 'authorization_operation_id': None,
                   'child_operation_id': None, 'outcome_code': None, 'cancel_key': None,
                   'created_at': now, 'updated_at': now, 'cancelled_at': None}
            op = b._create(db, user, AUTH_KIND, authorization_payload(rec), 'RED', opkey)
            rec['authorization_operation_id'] = op['id']
            db.execute('INSERT INTO scheduled_transfers(' + ','.join(COLUMNS) + ') VALUES(' + ','.join('?' * len(COLUMNS)) + ')',
                       tuple(rec[c] for c in COLUMNS))
            b._event(db, user, op['id'], 'SCHEDULE_PREPARED', f'定时转账 {sid} 等待授权，不预留资金')
            return self._pair(db, user, sid, False)

    def activate_locked(self, db, user, operation):
        s = guard_authorization(self.bank, db, user, operation, 'execute')
        now = self.bank.now().timestamp()
        cur = db.execute("UPDATE scheduled_transfers SET status='ACTIVE',version=version+1,updated_at=? "
                         "WHERE id=? AND user_id=? AND status='AUTHORIZING' AND version=?",
                         (now, s['id'], user, s['version']))
        if cur.rowcount != 1:
            raise DomainError('INVALID_STATE', '定时转账状态已变化，授权未生效。', 409)
        self.bank._event(db, user, operation['id'], 'SCHEDULE_ACTIVATED', f'定时转账 {s["id"]} 已授权生效，未扣款')
        return {'schedule_id': s['id'], 'status': 'ACTIVE', 'amount_cents': s['amount_cents'],
                'due_at': s['due_at_iso'], 'simulated': True, 'message': '定时转账已授权，到期时按规则模拟执行。'}

    def get(self, user, schedule_id):
        with self._read() as db:
            return self._view(db, user, schedule_id)

    def list(self, user):
        with self._read() as db:
            ids = [r['id'] for r in db.execute('SELECT id FROM scheduled_transfers WHERE user_id=? ORDER BY created_at,id', (user,))]
            return [self._view(db, user, i) for i in ids]

    def lookup_request(self, user, key):
        if not isinstance(key, str):
            raise DomainError('NOT_FOUND', '未找到可访问的数据', 404)
        with self._read() as db:
            row = db.execute('SELECT id FROM scheduled_transfers WHERE user_id=? AND request_key=?', (user, key)).fetchone()
            if not row:
                raise DomainError('NOT_FOUND', '未找到可访问的数据', 404)
            return self._view(db, user, row['id'])

    def lookup_child(self, user, operation_id):
        with self._read() as db:
            row = db.execute('SELECT schedule_id,mode FROM scheduled_transfer_children WHERE user_id=? AND operation_id=?',
                             (user, operation_id)).fetchone()
            return {'schedule_id': row['schedule_id'], 'mode': row['mode']} if row else None

    def cancel(self, user, schedule_id, expected_version, key, confirmed):
        if confirmed is not True:
            raise DomainError('CONFIRMATION_REQUIRED', '取消定时转账需要明确确认。')
        if type(expected_version) is not int or expected_version < 1:
            raise DomainError('INVALID_VERSION', '版本号无效。')
        if not _key_ok(key):
            raise DomainError('INVALID_KEY', '请求编号无效。')
        b = self.bank
        with b.transaction() as db:
            row = load_schedule(db, user, schedule_id)
            prior = db.execute('SELECT schedule_id FROM scheduled_transfer_cancels WHERE user_id=? AND cancel_key=?',
                               (user, key)).fetchone()
            if prior:
                if prior['schedule_id'] != row['id']:
                    raise DomainError('IDEMPOTENCY_CONFLICT', '该取消编号已用于其他定时转账。', 409)
                return self._view(db, user, row['id'], True)
            ops = [self._op(db, user, row['authorization_operation_id']), self._op(db, user, row['child_operation_id'])]
            if any(o is not None and o['status'] in IN_FLIGHT for o in ops):
                raise DomainError('RESULT_PENDING', '付款结果待核实，不能取消，请用原操作编号查询。', 409)
            if (ops[1] is not None and ops[1]['status'] == 'SUCCEEDED') or row['status'] == 'SUCCEEDED':
                raise DomainError('ALREADY_EXECUTED', '此定时转账已完成付款，需要独立的撤销业务。', 409)
            if row['status'] in TERMINAL:
                return self._view(db, user, row['id'])
            if row['status'] == 'CHILD_IN_FLIGHT':
                raise DomainError('RESULT_PENDING', '付款结果待核实，不能取消。', 409)
            if row['version'] != expected_version:
                raise DomainError('VERSION_CONFLICT', '定时转账已变化，请刷新后再操作。', 409)
            for o in ops:
                if o is not None and o['status'] in PENDING:
                    db.execute("UPDATE operations SET status='CANCELLED' WHERE id=? AND user_id=? AND status=?",
                               (o['id'], user, o['status']))
                    b._event(db, user, o['id'], 'OPERATION_CANCELLED', '定时转账取消，同步取消未执行操作并释放预留')
            now = b.now().timestamp()
            cur = db.execute("UPDATE scheduled_transfers SET status='CANCELLED',version=version+1,outcome_code='USER_CANCELLED',"
                             'cancel_key=?,cancelled_at=?,updated_at=? WHERE id=? AND user_id=? AND version=?',
                             (key, now, now, row['id'], user, expected_version))
            if cur.rowcount != 1:
                raise DomainError('VERSION_CONFLICT', '定时转账已变化，请刷新后再操作。', 409)
            db.execute('INSERT INTO scheduled_transfer_cancels(user_id,cancel_key,schedule_id,version_after,created_at) VALUES(?,?,?,?,?)',
                       (user, key, row['id'], expected_version + 1, now))
            b._event(db, user, row['authorization_operation_id'], 'SCHEDULE_CANCELLED', f'用户取消定时转账 {row["id"]}')
            return self._view(db, user, row['id'])

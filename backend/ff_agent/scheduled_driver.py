"""Scheduled transfer due driver and child-payment guard. LOCAL SIMULATED only; no network or background service."""
import json
from datetime import datetime

from .bank import DomainError
from .scheduled_rules import (NOTICE, PENDING, VERIFY_WINDOW, canonical_transfer, check_context, load_schedule,
                              payload_matches)

CHILD_STATES = ('CHILD_PENDING', 'CHILD_IN_FLIGHT')
IN_FLIGHT = ('UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW')
DEAD = ('CANCELLED', 'EXPIRED', 'FAILED', 'REJECTED')


def _corrupt():
    return DomainError('SCHEDULE_INVALID', '定时转账记录校验失败，已停止处理且未扣款。', 409)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _event_after(db, user, opid, event, since):
    for ev in db.execute('SELECT created_at FROM audit WHERE user_id=? AND operation_id=? AND event=?', (user, opid, event)):
        try:
            if datetime.fromisoformat(ev['created_at']).timestamp() >= since:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _fresh_confirm(op, now):
    c = op.get('confirmed_at')
    return _num(c) and 0 <= now - c <= VERIFY_WINDOW


def guard_child(bank, db, user, row, stage):
    """None for ordinary transfers; {schedule_id,mode} for a valid bound child; DomainError otherwise."""
    if stage not in ('confirm', 'verify', 'execute'):
        raise _corrupt()
    opid = row.get('id')
    op = db.execute('SELECT * FROM operations WHERE id=? AND user_id=?', (opid, user)).fetchone()
    op = dict(op) if op else None
    link = db.execute('SELECT * FROM scheduled_transfer_children WHERE operation_id=?', (opid,)).fetchone()
    key = (op or {}).get('idempotency_key') or row.get('idempotency_key') or ''
    if not link:
        if isinstance(key, str) and key.startswith('sched-'):
            raise DomainError('SCHEDULE_CHILD_UNBOUND', '该定时付款未绑定有效计划，已拒绝。', 409)
        return None
    link = dict(link)
    if not op or op['type'] != 'transfer' or row.get('type') not in (None, 'transfer') or link['user_id'] != user:
        raise _corrupt()
    passed = row.get('payload')
    passed = passed if isinstance(passed, str) else json.dumps(passed, ensure_ascii=False, sort_keys=True)
    if passed is None or not payload_matches(op['payload'], json.loads(passed)) if passed else True:
        raise _corrupt()
    sid = link['schedule_id']
    if link['child_key'] != 'sched-' + sid or op['idempotency_key'] != link['child_key']:
        raise _corrupt()
    s = load_schedule(db, user, sid)
    if s['child_operation_id'] != opid:
        raise _corrupt()
    s, auth = check_context(bank, db, user, s)
    if not payload_matches(op['payload'], canonical_transfer(s)) or auth['status'] != 'SUCCEEDED':
        raise _corrupt()
    if s['status'] not in CHILD_STATES:
        raise DomainError('SCHEDULE_NOT_PAYABLE', '该定时转账已不可付款。', 409)
    now = bank.now().timestamp()
    if not s['due_at'] <= now <= s['due_at'] + s['grace_seconds']:
        raise DomainError('SCHEDULE_OUT_OF_WINDOW', '不在约定付款时间范围内，本次未付款。', 409)
    if not _num(op.get('expires_at')) or now >= op['expires_at']:
        raise DomainError('OPERATION_EXPIRED', '该付款已过期，未扣款。', 409)
    bank._unlocked(db, user)
    mode, st = link['mode'], op['status']
    if mode == 'AUTO':
        if (stage != 'execute' or op['risk'] != 'YELLOW' or st != 'WAITING_CONFIRMATION'
                or bank._risk(db, user, op_amount(op), exclude=opid) == 'RED'):
            raise DomainError('SCHEDULE_AUTO_REJECTED', '自动付款条件不满足，未扣款。', 409)
        if not _fresh_confirm(op, now) or not _event_after(db, user, opid, 'SCHEDULE_AUTO_CONFIRMED', op['confirmed_at']):
            raise DomainError('CONFIRMATION_REQUIRED', '缺少本次自动确认记录，未扣款。', 409)
    elif mode == 'MANUAL':
        if op['risk'] != 'RED':
            raise _corrupt()
        if stage == 'confirm':
            if st == 'WAITING_VERIFICATION' and _fresh_confirm(op, now):
                pass
            elif st != 'WAITING_CONFIRMATION':
                raise DomainError('OPERATION_NOT_PENDING', '该付款已不可确认。', 409)
        else:
            if st != 'WAITING_VERIFICATION' or not _fresh_confirm(op, now):
                raise DomainError('CONFIRMATION_REQUIRED', '请先确认本次付款并完成验证。', 409)
            if stage == 'execute' and not _event_after(db, user, opid, 'VERIFICATION_PASSED', op['confirmed_at']):
                raise DomainError('VERIFICATION_REQUIRED', '未找到本次付款的验证记录。', 409)
    else:
        raise _corrupt()
    return {'schedule_id': sid, 'mode': mode}


def op_amount(op):
    return json.loads(op['payload'])['amount_cents']


class ScheduledDriver:
    def __init__(self, bank):
        self.bank = bank

    def _iso(self, ts):
        return None if ts is None else datetime.fromtimestamp(ts, self.bank.now().tzinfo).isoformat(timespec='seconds')

    def run_due(self, user, limit=20):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise DomainError('INVALID_INPUT', 'limit须为1到20的整数', 400)
        now = self.bank.now().timestamp()
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            ids = [r['id'] for r in db.execute(
                "SELECT id FROM scheduled_transfers WHERE user_id=? AND (status IN ('CHILD_PENDING','CHILD_IN_FLIGHT') "
                "OR (status='ACTIVE' AND due_at<=?)) ORDER BY CASE WHEN status='ACTIVE' THEN 0 ELSE 1 END,due_at,id LIMIT ?",
                (user, now, limit))]
        processed = []
        for sid in ids:
            with self.bank.transaction() as db:
                item = self._one(db, user, sid)
            if item:
                processed.append(item)
        with self.bank.transaction() as db:
            nxt = db.execute("SELECT MIN(due_at) m FROM scheduled_transfers WHERE user_id=? AND status='ACTIVE' AND due_at>?",
                             (user, self.bank.now().timestamp())).fetchone()
        return {'checked_at': self._iso(now), 'simulated': True, 'processed': processed,
                'next_due_at': self._iso(nxt['m'] if nxt else None), 'notice': NOTICE}

    def _out(self, row, action):
        return {'schedule_id': row['id'], 'version': row['version'], 'status': row['status'],
                'outcome_code': row['outcome_code'], 'child_operation_id': row['child_operation_id'], 'action': action}

    def _set(self, db, user, row, status, code, now, child=None):
        cur = db.execute('UPDATE scheduled_transfers SET status=?,outcome_code=?,child_operation_id=COALESCE(?,child_operation_id),'
                         'version=version+1,updated_at=? WHERE id=? AND user_id=? AND version=? AND status=?',
                         (status, code, child, now, row['id'], user, row['version'], row['status']))
        if cur.rowcount != 1:
            raise DomainError('INVALID_STATE', '定时转账状态已变化。', 409)
        self.bank._event(db, user, child or row['child_operation_id'] or row['authorization_operation_id'],
                         'SCHEDULE_' + status, f'定时转账 {row["id"]} -> {status} / {code or "-"}')
        return load_schedule(db, user, row['id'])

    def _one(self, db, user, sid):
        now = self.bank.now().timestamp()
        try:
            row = load_schedule(db, user, sid)
        except DomainError:
            return None
        if row['status'] in CHILD_STATES:
            return self._reconcile(db, user, row, now)
        if row['status'] != 'ACTIVE' or now < row['due_at']:
            return None
        if row['child_operation_id'] or db.execute('SELECT 1 FROM scheduled_transfer_children WHERE schedule_id=?', (sid,)).fetchone():
            return self._out(row, 'DEFERRED')
        if now > row['due_at'] + row['grace_seconds']:
            return self._out(self._set(db, user, row, 'NOT_PAID', 'MISSED_GRACE', now), 'NOT_PAID')
        try:
            self.bank._unlocked(db, user)
            self.bank._assert_transfer_allowed(db, user)
        except DomainError:
            return self._out(row, 'DEFERRED')
        try:
            row, auth = check_context(self.bank, db, user, row)
            if auth['status'] != 'SUCCEEDED':
                raise _corrupt()
        except DomainError as e:
            code = e.code if e.code == 'SCHEDULE_INVALID' else 'CONTEXT_CHANGED'
            return self._out(self._set(db, user, row, 'NOT_PAID', code, now), 'NOT_PAID')
        acct = db.execute('SELECT balance_cents FROM accounts WHERE id=? AND user_id=?', (row['account_id'], user)).fetchone()
        if not acct or acct['balance_cents'] - self.bank._reserved_amount(db, user, row['account_id']) < row['amount_cents']:
            return self._out(self._set(db, user, row, 'NOT_PAID', 'INSUFFICIENT_FUNDS', now), 'NOT_PAID')
        key = 'sched-' + sid
        if db.execute('SELECT 1 FROM operations WHERE user_id=? AND idempotency_key=?', (user, key)).fetchone() or \
                db.execute('SELECT 1 FROM scheduled_transfer_children WHERE child_key=?', (key,)).fetchone():
            return self._out(self._set(db, user, row, 'NOT_PAID', 'CHILD_KEY_CONFLICT', now), 'NOT_PAID')
        risk = self.bank._risk(db, user, row['amount_cents'])
        if risk == 'RED':
            mode = 'MANUAL'
        elif risk == 'YELLOW':
            mode = 'AUTO'
        else:
            return self._out(self._set(db, user, row, 'NOT_PAID', 'RISK_UNKNOWN', now), 'NOT_PAID')
        db.execute('SAVEPOINT scheduled_child')
        try:
            op = self.bank._create(db, user, 'transfer', canonical_transfer(row), risk, key, owner=None, origin='ticket')
            opid = op['id']
            db.execute('INSERT INTO scheduled_transfer_children(operation_id,schedule_id,user_id,child_key,mode,created_at) '
                       'VALUES(?,?,?,?,?,?)', (opid, sid, user, key, mode, now))
            row = self._set(db, user, row, 'CHILD_PENDING', None, now, child=opid)
            if mode == 'AUTO':
                db.execute('UPDATE operations SET confirmed_at=? WHERE id=? AND user_id=? AND status=?',
                           (now, opid, user, 'WAITING_CONFIRMATION'))
                self.bank._event(db, user, opid, 'SCHEDULE_AUTO_CONFIRMED', f'定时转账 {sid} 低风险到期自动确认（模拟）')
                child = dict(db.execute('SELECT * FROM operations WHERE id=? AND user_id=?', (opid, user)).fetchone())
                guard_child(self.bank, db, user, child, 'execute')
                result = self.bank._execute(db, user, child)
                if not isinstance(result, dict) or result.get('status') != 'SUCCEEDED' or result.get('id') != opid:
                    raise DomainError('INVALID_STATE', '付款状态已变化。', 409)
                row = self._set(db, user, row, 'SUCCEEDED', 'PAID', now)
        except DomainError as e:
            db.execute('ROLLBACK TO scheduled_child')
            db.execute('RELEASE scheduled_child')
            row = load_schedule(db, user, sid)
            return self._out(self._set(db, user, row, 'NOT_PAID', e.code, now), 'NOT_PAID')
        db.execute('RELEASE scheduled_child')
        return self._out(row, 'EXECUTED' if mode == 'AUTO' else 'CHILD_PENDING')

    def _reconcile(self, db, user, row, now):
        cid = row['child_operation_id']
        link = db.execute('SELECT * FROM scheduled_transfer_children WHERE schedule_id=? AND user_id=?', (row['id'], user)).fetchone()
        op = db.execute('SELECT * FROM operations WHERE id=? AND user_id=?', (cid, user)).fetchone() if cid else None
        if (not link or not op or link['operation_id'] != cid or link['child_key'] != 'sched-' + row['id']
                or op['idempotency_key'] != link['child_key'] or op['type'] != 'transfer'):
            return self._out(row, 'DEFERRED')
        st = op['status']
        if st == 'SUCCEEDED':
            return self._out(self._set(db, user, row, 'SUCCEEDED', 'PAID', now), 'RECONCILED')
        if st in IN_FLIGHT:
            if row['status'] == 'CHILD_IN_FLIGHT':
                return self._out(row, 'RECONCILED')
            return self._out(self._set(db, user, row, 'CHILD_IN_FLIGHT', 'CHILD_' + st, now), 'RECONCILED')
        if st in DEAD or (st in PENDING + ('READY',) and _num(op['expires_at']) and now >= op['expires_at']):
            code = 'CHILD_' + (st if st in DEAD else 'EXPIRED')
            return self._out(self._set(db, user, row, 'NOT_PAID', code, now), 'NOT_PAID')
        if st in PENDING + ('READY',):
            return self._out(row, 'RECONCILED')
        return self._out(row, 'DEFERRED')

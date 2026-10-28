'''I2 bound verification ledger: one challenge per RED confirmation, never rebound or renewed.'''
import hashlib
import hmac
import json
import uuid

PURPOSE = 'operation_execute'


def _error(code, message, status):
    from .bank import DomainError  # lazy import avoids the bank init cycle
    return DomainError(code, message, status)


def _require_transaction(db):
    if not db.in_transaction:
        raise _error('INVALID_EXECUTION', '验证状态须在银行事务内更新。', 409)


def bootstrap(bank):
    with bank.transaction() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS verification_challenges (
                challenge_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
                opid TEXT NOT NULL,
                purpose TEXT NOT NULL CHECK (purpose = 'operation_execute'),
                action_hash TEXT NOT NULL,
                risk_epoch TEXT NOT NULL,
                expires_at REAL NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('ACTIVE','USED','REVOKED','UNKNOWN')),
                used_at REAL,
                UNIQUE (user_id, opid, purpose)
            )
        ''')


def _operation(db, user, opid):
    row = db.execute('SELECT * FROM operations WHERE id=? AND user_id=?', (opid, user)).fetchone()
    if row is None:
        raise _error('NOT_FOUND', '未找到可访问的操作', 404)
    return dict(row)


def _challenge(db, user, opid):
    row = db.execute('SELECT * FROM verification_challenges WHERE user_id=? AND opid=? AND purpose=?',
                     (user, opid, PURPOSE)).fetchone()
    return dict(row) if row else None


def _action_hash(user, op, epoch):
    # Canonical immutable intent; the verification code is never part of the binding.
    body = {'owner': user, 'opid': op['id'], 'type': op['type'], 'payload': json.loads(op['payload']),
            'confirmed_at': op['confirmed_at'], 'risk': op['risk'], 'risk_epoch': epoch}
    text = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _meta(ch):
    return {'challenge_id': ch['challenge_id'], 'purpose': ch['purpose'], 'status': ch['status'],
            'expires_at': ch['expires_at'], 'simulated': True}


def _invalid():
    return _error('VERIFICATION_BINDING_INVALID', '确认后的操作或安全状态已变化，请重新发起。', 409)


def issue(bank, db, user, row):
    '''Called only inside the RED confirm transaction after confirmed_at and WAITING_VERIFICATION are written.'''
    _require_transaction(db)
    op = _operation(db, user, row['id'])
    if (op['status'] != 'WAITING_VERIFICATION' or op['risk'] != 'RED' or op['confirmed_at'] is None
            or op['type'] != row['type'] or op['payload'] != row['payload']):
        raise _invalid()
    existing = _challenge(db, user, op['id'])
    if existing is not None:
        return _meta(existing)  # never replace, rebind or extend
    epoch = str(bank._safety_epoch(db, user))
    ch = {'challenge_id': uuid.uuid4().hex, 'user_id': user, 'opid': op['id'], 'purpose': PURPOSE,
          'action_hash': _action_hash(user, op, epoch), 'risk_epoch': epoch,
          'expires_at': float(op['expires_at']), 'status': 'ACTIVE'}
    db.execute('INSERT INTO verification_challenges(challenge_id,user_id,opid,purpose,action_hash,risk_epoch,expires_at,status,used_at) '
               'VALUES(?,?,?,?,?,?,?,?,NULL)',
               (ch['challenge_id'], user, ch['opid'], PURPOSE, ch['action_hash'], epoch, ch['expires_at'], 'ACTIVE'))
    return _meta(ch)


def _check_bound(bank, db, user, op, ch):
    if ch is None:
        raise _error('VERIFICATION_BINDING_MISSING', '此操作缺少确认时建立的验证绑定，请重新发起。', 409)
    if ch['status'] == 'UNKNOWN':
        raise _error('VERIFICATION_UNKNOWN', '验证结果待核实，本操作未执行，请使用原操作编号查询。', 409)
    if ch['status'] != 'ACTIVE':
        raise _error('VERIFICATION_UNAVAILABLE', '此验证已使用或已撤销，不能再次使用。', 409)
    if (ch['user_id'] != user or ch['purpose'] != PURPOSE or op['status'] != 'WAITING_VERIFICATION'
            or op['risk'] != 'RED' or op['confirmed_at'] is None):
        raise _invalid()
    try:
        if bank.now().timestamp() >= float(ch['expires_at']) or float(ch['expires_at']) > float(op['expires_at']):
            raise _error('VERIFICATION_EXPIRED', '验证已过期，请重新发起操作。', 409)
        epoch = str(bank._safety_epoch(db, user))
        expected = _action_hash(user, op, ch['risk_epoch'])
    except (TypeError, ValueError):
        raise _invalid() from None
    if not hmac.compare_digest(epoch.encode('utf-8'), str(ch['risk_epoch']).encode('utf-8')):
        raise _invalid()
    if not hmac.compare_digest(expected.encode('utf-8'), str(ch['action_hash']).encode('utf-8')):
        raise _invalid()


def authorize(bank, db, user, row, code):
    '''Validate binding, then ask the trusted local provider. Returns VERIFIED or REJECTED; raises otherwise.'''
    from .verification_provider import Challenge, VerifyResult
    _require_transaction(db)
    op = _operation(db, user, row['id'])
    ch = _challenge(db, user, op['id'])
    _check_bound(bank, db, user, op, ch)
    cid, opid = ch['challenge_id'], op['id']
    proof = Challenge(cid, user, opid, PURPOSE, ch['action_hash'], float(ch['expires_at']))
    try:
        result = bank.verification_provider.verify(proof, code)
    except Exception:
        result = None  # raw provider errors are never stored or surfaced
    if type(result) is not VerifyResult:
        result = VerifyResult.UNKNOWN
    if result is VerifyResult.VERIFIED:
        cur = db.execute('UPDATE verification_challenges SET status=?,used_at=? WHERE challenge_id=? AND status=?',
                         ('USED', bank.now().timestamp(), cid, 'ACTIVE'))
        if cur.rowcount != 1:
            raise _error('VERIFICATION_UNAVAILABLE', '此验证已使用或已撤销，不能再次使用。', 409)
        return VerifyResult.VERIFIED
    if result is VerifyResult.REJECTED:
        return VerifyResult.REJECTED  # Bank owns attempts/streak handling
    if result is VerifyResult.REVOKED:
        db.execute('UPDATE verification_challenges SET status=? WHERE challenge_id=? AND status=?', ('REVOKED', cid, 'ACTIVE'))
        bank._event(db, user, opid, 'VERIFICATION_REVOKED', '本机模拟验证已撤销 / 未执行业务')
        db.commit()
        raise _error('VERIFICATION_REVOKED', '此验证已撤销，请重新发起操作。', 409)
    db.execute('UPDATE verification_challenges SET status=? WHERE challenge_id=? AND status=?', ('UNKNOWN', cid, 'ACTIVE'))
    db.execute('UPDATE operations SET status=? WHERE id=? AND user_id=? AND status=?',
               ('UNKNOWN', opid, user, 'WAITING_VERIFICATION'))
    bank._event(db, user, opid, 'VERIFICATION_UNKNOWN', '本机模拟验证结果未核实 / 未执行业务 / 保留预留')
    db.commit()
    raise _error('VERIFICATION_UNKNOWN', '验证结果待核实，本操作未执行，请使用原操作编号查询。', 409)


def revoke(bank, user, opid):
    '''Trusted server-side revocation of an existing ACTIVE local challenge; cannot clear USED or UNKNOWN.'''
    with bank.transaction() as db:
        op = bank._load(db, user, opid)
        ch = _challenge(db, user, op['id'])
        if ch is None:
            raise _error('NOT_FOUND', '未找到可访问的验证', 404)
        if ch['status'] == 'ACTIVE':
            db.execute('UPDATE verification_challenges SET status=? WHERE challenge_id=? AND status=?',
                       ('REVOKED', ch['challenge_id'], 'ACTIVE'))
            bank._event(db, user, op['id'], 'VERIFICATION_REVOKED', '撤销本机模拟验证绑定 / 未执行业务')
        elif ch['status'] != 'REVOKED':
            raise _error('VERIFICATION_FINAL', '此验证已使用或结果待核实，不能撤销。', 409)
        return {'challenge_id': ch['challenge_id'], 'status': 'REVOKED', 'simulated': True}

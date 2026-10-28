"""Scheduled transfer rules: schema, strict input, read-only views, authorization binding. LOCAL SIMULATED only."""
import hashlib
import json
import re
from datetime import datetime

from .bank import DomainError

NOTICE = '定时转账为本地模拟演示，不连接真实银行、短信或其他外部系统；到期后需满足当时的风险与确认要求，不预留未来资金。'
AUTH_TYPE = 'scheduled_transfer_authorize'
STATUSES = ('AUTHORIZING', 'ACTIVE', 'CHILD_PENDING', 'CHILD_IN_FLIGHT', 'SUCCEEDED', 'NOT_PAID', 'CANCELLED')
PENDING = ('WAITING_CONFIRMATION', 'WAITING_VERIFICATION')
VERIFY_WINDOW = 300
GRACE_SECONDS = 86400
_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')
_KEY = re.compile(r'[A-Za-z0-9_-]{8,64}')
_RESERVED = ('task-', 'select-', 'sched-')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS scheduled_transfers(
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('AUTHORIZING','ACTIVE','CHILD_PENDING','CHILD_IN_FLIGHT','SUCCEEDED','NOT_PAID','CANCELLED')),
 version INTEGER NOT NULL CHECK(version>=1), request_key TEXT NOT NULL, request_hash TEXT NOT NULL,
 account_id TEXT NOT NULL, account_name TEXT NOT NULL, payee_id TEXT NOT NULL, payee_name TEXT NOT NULL,
 payee_phone_masked TEXT NOT NULL, payee_phone_digest TEXT NOT NULL,
 amount_cents INTEGER NOT NULL CHECK(typeof(amount_cents)='integer' AND amount_cents>0),
 due_at REAL NOT NULL, due_at_iso TEXT NOT NULL, grace_seconds INTEGER NOT NULL CHECK(grace_seconds>=0),
 safety_epoch TEXT NOT NULL, authorization_operation_id TEXT NOT NULL UNIQUE, child_operation_id TEXT UNIQUE,
 outcome_code TEXT, cancel_key TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL, cancelled_at REAL,
 UNIQUE(user_id,request_key));
CREATE TABLE IF NOT EXISTS scheduled_transfer_children(
 operation_id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL UNIQUE, user_id TEXT NOT NULL,
 child_key TEXT NOT NULL UNIQUE, mode TEXT NOT NULL CHECK(mode IN ('AUTO','MANUAL')), created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS scheduled_transfer_cancels(
 user_id TEXT NOT NULL, cancel_key TEXT NOT NULL, schedule_id TEXT NOT NULL,
 version_after INTEGER NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(user_id,cancel_key));
CREATE INDEX IF NOT EXISTS scheduled_user_due ON scheduled_transfers(user_id,status,due_at);
'''


def _bad(msg='定时转账请求无效', code='INVALID_INPUT', status=400):
    return DomainError(code, msg, status)


def _corrupt():
    return DomainError('SCHEDULE_INVALID', '定时转账记录校验失败，已停止处理且未扣款。', 409)


def bootstrap_scheduled(bank):
    with bank.transaction() as db:
        for stmt in SCHEMA.split(';'):
            if stmt.strip():
                db.execute(stmt)


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def sha256_hex(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def phone_digest(phone):
    return sha256_hex(str(phone))


def mask_phone(phone):
    phone = str(phone)
    return phone[:3] + '****' + phone[-4:] if len(phone) >= 7 else '****'


def _strict_loads(text):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise _corrupt()
            out[k] = v
        return out
    if not isinstance(text, str):
        raise _corrupt()
    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError):
        raise _corrupt()
    if not isinstance(value, dict):
        raise _corrupt()
    return value


def _strict_int(v):
    return type(v) is int


def request_hash(payee_id, amount_cents, due_iso):
    return sha256_hex(canonical_json({'payee_id': payee_id, 'amount_cents': amount_cents, 'due_at_iso': due_iso}))


def validate_intent(bank, payee_id, amount_cents, due_at, key):
    if not isinstance(payee_id, str) or not payee_id.isascii() or not _ID.fullmatch(payee_id):
        raise _bad('收款人标识无效')
    if not _strict_int(amount_cents) or not 1 <= amount_cents <= 100000000:
        raise _bad('金额必须是1到100000000之间的整数分')
    if not isinstance(key, str) or not key.isascii() or not _KEY.fullmatch(key) or key.startswith(_RESERVED):
        raise _bad('请求键无效')
    if not isinstance(due_at, str) or not due_at.isascii() or 'T' not in due_at or '.' in due_at or ',' in due_at:
        raise _bad('到期时间须为带时区的ISO时间，精确到秒')
    try:
        parsed = datetime.fromisoformat(due_at)
    except ValueError:
        raise _bad('到期时间须为带时区的ISO时间，精确到秒')
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond:
        raise _bad('到期时间须为带时区的ISO时间，精确到秒')
    local = parsed.astimezone(bank.now().tzinfo)
    due_iso = local.isoformat(timespec='seconds')
    return local.timestamp(), due_iso, request_hash(payee_id, amount_cents, due_iso)


def canonical_transfer(row):
    return {'account_id': row['account_id'], 'account_name': row['account_name'], 'payee_id': row['payee_id'],
            'payee_name': row['payee_name'], 'payee_phone': row['payee_phone_masked'],
            'amount_cents': row['amount_cents'], 'fee_cents': 0}


def authorization_payload(row):
    payload = canonical_transfer(row)
    payload['schedule_id'] = row['id']
    payload['due_at_iso'] = row['due_at_iso']
    return payload


def payload_matches(text, expected):
    return canonical_json(_strict_loads(text)) == canonical_json(expected)


def _nonempty(v):
    return isinstance(v, str) and v.strip() != ''


def _check_row(row):
    try:
        version, amount, grace = row['version'], row['amount_cents'], row['grace_seconds']
        due, iso = row['due_at'], row['due_at_iso']
        if row['status'] not in STATUSES:
            raise _corrupt()
        if not _strict_int(version) or version < 1:
            raise _corrupt()
        if not _strict_int(amount) or not 1 <= amount <= 100000000:
            raise _corrupt()
        if not _strict_int(grace) or grace != GRACE_SECONDS:
            raise _corrupt()
        if not isinstance(due, (int, float)) or isinstance(due, bool) or due - due != 0:
            raise _corrupt()
        if (not isinstance(iso, str) or not iso.isascii() or 'T' not in iso or '.' in iso or ',' in iso):
            raise _corrupt()
        parsed = datetime.fromisoformat(iso)
        if (parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond
                or parsed.utcoffset().total_seconds() != 28800
                or parsed.isoformat(timespec='seconds') != iso or parsed.timestamp() != due):
            raise _corrupt()
        for k in ('id', 'user_id', 'account_id', 'payee_id', 'authorization_operation_id'):
            if not isinstance(row[k], str) or not row[k].isascii() or not _ID.fullmatch(row[k]):
                raise _corrupt()
        if not isinstance(row['request_key'], str) or not _KEY.fullmatch(row['request_key']):
            raise _corrupt()
        for k in ('account_name', 'payee_name'):
            if not _nonempty(row[k]):
                raise _corrupt()
        if not isinstance(row['safety_epoch'], str):
            raise _corrupt()
        if not isinstance(row['payee_phone_digest'], str) or not re.fullmatch(r'[0-9a-f]{64}', row['payee_phone_digest']):
            raise _corrupt()
        if not isinstance(row['payee_phone_masked'], str) or not re.fullmatch(r'.{3}\*{4}.{4}|\*{4}', row['payee_phone_masked']):
            raise _corrupt()
        if not isinstance(row['request_hash'], str) or row['request_hash'] != request_hash(row['payee_id'], amount, iso):
            raise _corrupt()
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise _corrupt()


def load_schedule(db, user, schedule_id):
    if not isinstance(schedule_id, str) or not _ID.fullmatch(schedule_id):
        raise DomainError('NOT_FOUND', '未找到可访问的定时转账', 404)
    row = db.execute('SELECT * FROM scheduled_transfers WHERE id=? AND user_id=?', (schedule_id, user)).fetchone()
    if not row:
        raise DomainError('NOT_FOUND', '未找到可访问的定时转账', 404)
    return dict(row)


def _op(db, user, opid):
    row = db.execute('SELECT * FROM operations WHERE id=? AND user_id=?', (opid, user)).fetchone() if opid else None
    return dict(row) if row else None


def _iso(bank, ts):
    return None if ts is None else datetime.fromtimestamp(ts, bank.now().tzinfo).isoformat(timespec='seconds')


AUTH_OBSERVED = ('CANCELLED', 'EXPIRED', 'FAILED', 'REJECTED', 'UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW')


def schedule_view(bank, db, user, row, replayed=False):
    row = dict(row)
    if row['user_id'] != user:
        raise DomainError('NOT_FOUND', '未找到可访问的定时转账', 404)
    _check_row(row)
    now = bank.now().timestamp()
    auth = _op(db, user, row['authorization_operation_id'])
    child = _op(db, user, row['child_operation_id'])
    link = db.execute('SELECT mode FROM scheduled_transfer_children WHERE schedule_id=? AND user_id=?',
                      (row['id'], user)).fetchone()
    observed = row['status']
    if row['status'] == 'AUTHORIZING' and auth and auth['status'] in PENDING + ('READY',) and now >= auth['expires_at']:
        observed = 'AUTH_EXPIRED'
    elif row['status'] == 'AUTHORIZING' and auth and not child and auth['status'] in AUTH_OBSERVED:
        # Read-only: surface a closed/unknown authorization outcome without rewriting the schedule.
        observed = auth['status']
    elif row['status'] == 'ACTIVE' and now > row['due_at'] + row['grace_seconds']:
        observed = 'MISSED_GRACE'
    elif child:
        observed = child['status']
    return {'id': row['id'], 'status': row['status'], 'observed_status': observed, 'version': row['version'],
            'amount_cents': row['amount_cents'], 'account_id': row['account_id'], 'account_name': row['account_name'],
            'payee_id': row['payee_id'], 'payee_name': row['payee_name'], 'payee_phone': row['payee_phone_masked'],
            'due_at': row['due_at_iso'], 'grace_until': _iso(bank, row['due_at'] + row['grace_seconds']),
            'authorization_operation_id': row['authorization_operation_id'],
            'child_operation_id': row['child_operation_id'], 'child_status': child['status'] if child else None,
            'child_mode': link['mode'] if link else None, 'outcome_code': row['outcome_code'], 'reserved_cents': 0,
            'created_at': _iso(bank, row['created_at']), 'updated_at': _iso(bank, row['updated_at']),
            'cancelled_at': _iso(bank, row['cancelled_at']), 'replayed': bool(replayed), 'simulated': True,
            'notice': NOTICE}


def check_context(bank, db, user, row):
    """Verify owner/epoch/account/payee facts and the bound authorization payload. Does not require SUCCEEDED."""
    bank._user(db, user)
    row = load_schedule(db, user, row['id'])
    _check_row(row)
    bank._assert_transfer_allowed(db, user, expected_epoch=row['safety_epoch'])
    acct = db.execute('SELECT * FROM accounts WHERE id=? AND user_id=?', (row['account_id'], user)).fetchone()
    if not acct or acct['name'] != row['account_name']:
        raise DomainError('SCHEDULE_CONTEXT_CHANGED', '付款账户信息已变化，本次未付款。', 409)
    payee = db.execute('SELECT * FROM payees WHERE id=? AND user_id=?', (row['payee_id'], user)).fetchone()
    if (not payee or payee['name'] != row['payee_name'] or phone_digest(payee['phone']) != row['payee_phone_digest']
            or mask_phone(payee['phone']) != row['payee_phone_masked']):
        raise DomainError('SCHEDULE_CONTEXT_CHANGED', '收款人信息已变化，本次未付款。', 409)
    auth = _op(db, user, row['authorization_operation_id'])
    if not auth or auth['type'] != AUTH_TYPE or not payload_matches(auth['payload'], authorization_payload(row)):
        raise _corrupt()
    return row, auth


def guard_authorization(bank, db, user, operation, stage):
    if stage not in ('confirm', 'verify', 'execute'):
        raise _corrupt()
    if operation.get('type') != AUTH_TYPE or operation.get('user_id') not in (None, user):
        raise _corrupt()
    found = db.execute('SELECT * FROM scheduled_transfers WHERE authorization_operation_id=? AND user_id=?',
                       (operation.get('id'), user)).fetchone()
    if not found:
        raise _corrupt()
    row, auth = check_context(bank, db, user, dict(found))
    if auth['id'] != operation['id'] or not payload_matches(operation['payload'], authorization_payload(row)):
        raise _corrupt()
    if row['status'] != 'AUTHORIZING':
        raise DomainError('SCHEDULE_NOT_AUTHORIZING', '该定时转账已不在待授权状态。', 409)
    now = bank.now().timestamp()
    if now >= row['due_at']:
        raise DomainError('SCHEDULE_DUE_PASSED', '已过约定时间，授权不再生效，请重新设置。', 409)
    bank._unlocked(db, user)
    if stage in ('confirm', 'verify'):
        if auth['status'] not in PENDING:
            raise DomainError('OPERATION_NOT_PENDING', '该授权已不可继续。', 409)
        return row
    confirmed = auth['confirmed_at']
    if (auth['status'] != 'WAITING_VERIFICATION' or not isinstance(confirmed, (int, float)) or isinstance(confirmed, bool)
            or not 0 <= now - confirmed <= VERIFY_WINDOW):
        raise DomainError('CONFIRMATION_REQUIRED', '请先确认并完成本次授权验证。', 409)
    passed = False
    for ev in db.execute("SELECT created_at FROM audit WHERE user_id=? AND operation_id=? AND event='VERIFICATION_PASSED'",
                         (user, auth['id'])):
        try:
            if datetime.fromisoformat(ev['created_at']).timestamp() >= confirmed:
                passed = True
        except (TypeError, ValueError):
            continue
    if not passed:
        raise DomainError('VERIFICATION_REQUIRED', '未找到本次授权的验证记录。', 409)
    return row

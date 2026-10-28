"""Local AA split requests: bookkeeping only, never a bank payment or outbound message."""
import hashlib
import json
import re
import uuid

from .bank import DomainError

NOTICE = '本地模拟AA记录：未发送给任何参与者，未扣款，未核实到账；份额为0分的参与者无需付款。'
KEY = re.compile(r'[A-Za-z0-9_-]{8,64}')
COLS = 'id,preview,status,version,created_at,updated_at,input_hash'
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS aa_requests(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, request_key TEXT NOT NULL,"
    " input_hash TEXT NOT NULL, preview TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('ACTIVE','CANCELLED')),"
    " version INTEGER NOT NULL CHECK(version>=1), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
    " UNIQUE(user_id,request_key))",
    'CREATE INDEX IF NOT EXISTS aa_requests_owner ON aa_requests(user_id,created_at)',
    "CREATE TABLE IF NOT EXISTS aa_cancels(user_id TEXT NOT NULL, request_key TEXT NOT NULL, request_id TEXT NOT NULL"
    " REFERENCES aa_requests(id), binding_hash TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(user_id,request_key))",
    "CREATE TRIGGER IF NOT EXISTS aa_requests_guard BEFORE UPDATE ON aa_requests WHEN NEW.id IS NOT OLD.id"
    " OR NEW.user_id IS NOT OLD.user_id OR NEW.request_key IS NOT OLD.request_key OR NEW.input_hash IS NOT OLD.input_hash"
    " OR NEW.preview IS NOT OLD.preview OR NEW.created_at IS NOT OLD.created_at OR OLD.status IS NOT 'ACTIVE'"
    " OR NEW.status IS NOT 'CANCELLED' OR NEW.version IS NOT OLD.version+1"
    " BEGIN SELECT RAISE(ABORT,'aa_requests immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS aa_requests_no_delete BEFORE DELETE ON aa_requests"
    " BEGIN SELECT RAISE(ABORT,'aa_requests immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS aa_cancels_no_update BEFORE UPDATE ON aa_cancels"
    " BEGIN SELECT RAISE(ABORT,'aa_cancels immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS aa_cancels_no_delete BEFORE DELETE ON aa_cancels"
    " BEGIN SELECT RAISE(ABORT,'aa_cancels immutable'); END",
)


def bootstrap_aa(bank):
    with bank.transaction() as db:
        for sql in SCHEMA:
            db.execute(sql)


def _bad(message='输入不符合要求'):
    return DomainError('INVALID_INPUT', message, 400)


def _not_found(message='未找到可访问的数据'):
    return DomainError('NOT_FOUND', message, 404)


def _check_key(key):
    if type(key) is not str or not KEY.fullmatch(key):
        raise _bad('请求编号格式不正确')


def _check_input(total, ids, include_self):
    if type(total) is not int or not 1 <= total <= 100000000:
        raise _bad('金额需为1到100000000分的整数')
    if type(include_self) is not bool:
        raise _bad('请明确本人是否参与')
    if type(ids) is not list or not ids or len(ids) > 20:
        raise _bad('请选择参与者')
    if any(type(x) is not str or not x or len(x) > 64 or x == 'self' for x in ids):
        raise _bad('参与者编号不正确')
    if len(set(ids)) != len(ids):
        raise _bad('参与者不能重复')
    if not 2 <= len(ids) + int(include_self) <= 20:
        raise _bad('参与人数需为2到20人')


def _digest(value):
    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def mask_phone(phone):
    text = str(phone)
    return text[:3] + '****' + text[-4:] if len(text) >= 11 else '****'


def _view(row, replayed):
    item = json.loads(row[1])
    item.update(id=row[0], status=row[2], version=row[3], created_at=row[4],
                updated_at=row[5], replayed=replayed)
    return item


class AAService:
    def __init__(self, bank):
        self.bank = bank

    def _preview(self, db, user, total, ids, include_self):
        self.bank._user(db, user)
        people = [('self', '本人', '', True)] if include_self else []
        for pid in ids:
            row = db.execute('SELECT name,phone FROM payees WHERE id=? AND user_id=?', (pid, user)).fetchone()
            if not row:
                raise _not_found('未找到可访问的收款人')
            people.append((pid, row[0], mask_phone(row[1]), False))
        base, rem = divmod(total, len(people))
        shares = [dict(participant_id=p, name=n, masked_phone=m, is_self=s,
                       amount_cents=base + (1 if i < rem else 0))
                  for i, (p, n, m, s) in enumerate(people)]
        mine = shares[0]['amount_cents'] if include_self else 0
        return dict(total_cents=total, participant_count=len(shares), include_self=include_self,
                    shares=shares, self_share_cents=mine, receivable_cents=total - mine,
                    simulated=True, notice=NOTICE)

    def _row(self, db, user, rid):
        self.bank._user(db, user)
        if type(rid) is not str:
            raise _not_found()
        row = db.execute('SELECT ' + COLS + ' FROM aa_requests WHERE id=? AND user_id=?', (rid, user)).fetchone()
        if not row:
            raise _not_found()
        return row

    def preview(self, user, total_cents, participant_ids, include_self):
        _check_input(total_cents, participant_ids, include_self)
        with self.bank.transaction() as db:
            return self._preview(db, user, total_cents, list(participant_ids), include_self)

    def create(self, user, total_cents, participant_ids, include_self, key, confirmed=False):
        _check_key(key)
        if confirmed is not True:
            raise DomainError('CONFIRMATION_REQUIRED', '请先明确确认后再生成AA请求', 400)
        _check_input(total_cents, participant_ids, include_self)
        ids = list(participant_ids)
        digest = _digest([total_cents, ids, include_self])
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            row = db.execute('SELECT ' + COLS + ' FROM aa_requests WHERE user_id=? AND request_key=?',
                             (user, key)).fetchone()
            if row:
                if row[6] != digest:
                    raise DomainError('IDEMPOTENCY_MISMATCH', '该请求编号已用于不同内容', 409)
                return _view(row, True)
            preview = self._preview(db, user, total_cents, ids, include_self)
            rid, now = 'aa_' + uuid.uuid4().hex, self.bank.now().isoformat()
            stored = json.dumps(preview, ensure_ascii=False, separators=(',', ':'))
            db.execute('INSERT INTO aa_requests(id,user_id,request_key,input_hash,preview,status,version,'
                       'created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                       (rid, user, key, digest, stored, 'ACTIVE', 1, now, now))
            self.bank._event(db, user, None, 'AA_CREATED', json.dumps(
                {'id': rid, 'total_cents': total_cents, 'participant_count': preview['participant_count']}))
            return _view((rid, stored, 'ACTIVE', 1, now, now, digest), False)

    def get(self, user, id):
        with self.bank.transaction() as db:
            return _view(self._row(db, user, id), False)

    def list(self, user):
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            rows = db.execute('SELECT ' + COLS + ' FROM aa_requests WHERE user_id=? ORDER BY created_at,rowid',
                              (user,)).fetchall()
            return [_view(row, False) for row in rows]

    def cancel(self, user, id, expected_version, key):
        if type(expected_version) is not int or expected_version < 1:
            raise _bad('版本号需为正整数')
        _check_key(key)
        with self.bank.transaction() as db:
            row = self._row(db, user, id)
            binding = _digest([id, expected_version])
            prior = db.execute('SELECT binding_hash FROM aa_cancels WHERE user_id=? AND request_key=?',
                               (user, key)).fetchone()
            if prior:
                if prior[0] != binding:
                    raise DomainError('IDEMPOTENCY_MISMATCH', '该请求编号已用于不同内容', 409)
                return _view(row, True)
            if row[3] != expected_version:
                raise DomainError('VERSION_CONFLICT', '记录已变化，请刷新后再试', 409)
            if row[2] != 'ACTIVE':
                raise DomainError('NOT_ACTIVE', '该AA请求已取消', 409)
            now = self.bank.now().isoformat()
            cur = db.execute("UPDATE aa_requests SET status='CANCELLED',version=version+1,updated_at=? "
                             "WHERE id=? AND user_id=? AND version=? AND status='ACTIVE'",
                             (now, id, user, expected_version))
            if cur.rowcount != 1:
                raise DomainError('VERSION_CONFLICT', '记录已变化，请刷新后再试', 409)
            db.execute('INSERT INTO aa_cancels(user_id,request_key,request_id,binding_hash,created_at) '
                       'VALUES(?,?,?,?,?)', (user, key, id, binding, now))
            self.bank._event(db, user, None, 'AA_CANCELLED',
                             json.dumps({'id': id, 'version': expected_version + 1}))
            return _view(self._row(db, user, id), False)

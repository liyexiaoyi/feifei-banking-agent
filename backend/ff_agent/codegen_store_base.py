"""Codegen store base: schema, owner-scoped reads, public projections.

Writes (reserve/mark_sent/save_generated/fail/claim_run/settle) live in
codegen_store.CodegenStore; this module never calls them.
"""
import hashlib
import json
import re
import secrets

from .bank import DomainError
from .codegen_types import CalcIntent, intent_sha256, parse_generated  # noqa: F401

STATUSES = ('RESERVED', 'SENT', 'GENERATED', 'RUNNING', 'SUCCEEDED',
            'FAILED', 'SENT_UNKNOWN', 'UNKNOWN')
STALE_S = 30.0
WAT_TTL_S = 7 * 86400.0
_ID = re.compile(r'cgen_[0-9a-f]{32}')
_KEY = re.compile(r'[A-Za-z0-9_-]{8,100}')
_HEX = re.compile(r'[0-9a-f]{64}')
_CODE = re.compile(r'[A-Z][A-Z0-9_]{1,40}')
_MESSAGES = {400: '请求参数无效', 404: '记录不存在', 409: '请求键冲突',
             429: '请求过于频繁，请稍后再试', 503: '服务暂不可用'}
_DEFAULT_CODES = {404: 'NOT_FOUND', 409: 'REQUEST_KEY_CONFLICT',
                  429: 'RATE_LIMITED', 503: 'UNAVAILABLE'}
_PUBLIC_ERROR = '生成或执行未能完成'

_DDL = ('CREATE TABLE IF NOT EXISTS codegen_requests ('
        "id TEXT PRIMARY KEY CHECK (id GLOB 'cgen_*' AND length(id) = 37),"
        'owner TEXT NOT NULL,'
        'generation_key TEXT NOT NULL,'
        'intent_sha256 TEXT NOT NULL,'
        'arg_count INTEGER NOT NULL,'
        "provenance TEXT NOT NULL CHECK (provenance IN ('model','fixture')),"
        'status TEXT NOT NULL CHECK (status IN (' + ','.join("'%s'" % s for s in STATUSES) + ')),'
        'nonce_sha256 TEXT NOT NULL,'
        'wat TEXT,'
        'wat_sha256 TEXT,'
        "arg_names_json TEXT NOT NULL DEFAULT '[]',"
        "private_meta_json TEXT NOT NULL DEFAULT '{}',"
        'run_id TEXT,'
        'error_code TEXT,'
        'created_at TEXT NOT NULL,'
        'created_epoch REAL NOT NULL,'
        'updated_epoch REAL,'
        'wat_expires_epoch REAL,'
        'UNIQUE (owner, generation_key))')
_INDEX = ('CREATE INDEX IF NOT EXISTS codegen_requests_owner_recent '
          'ON codegen_requests (owner, created_epoch)')


def reject(status, code='INVALID_INPUT'):
    """Build a DomainError(code, message, status) with a fixed generic message."""
    if status not in _MESSAGES:
        status = 400
    if code == 'INVALID_INPUT' and status in _DEFAULT_CODES:
        code = _DEFAULT_CODES[status]
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        code = 'INVALID_INPUT'
    return DomainError(code, _MESSAGES[status], status)


def _sha(text):
    return hashlib.sha256(text.encode('utf-8', 'surrogatepass')).hexdigest()


def _owner_ok(user):
    return isinstance(user, str) and 0 < len(user) <= 64 and user.isprintable()


class CodegenStoreBase:
    def __init__(self, bank, sandbox):
        self.bank, self.sandbox = bank, sandbox
        with bank.transaction() as db:
            db.execute(_DDL)
            db.execute(_INDEX)

    def _now(self):
        return self.bank.now().timestamp()

    def _owner(self, db, user):
        # Actual owner check against the bank users table, same transaction.
        try:
            self.bank._user(db, user)
        except DomainError:
            raise reject(404) from None

    def _key(self, key):
        if not isinstance(key, str) or not key.isascii() or not _KEY.fullmatch(key):
            raise reject(400)

    def _load(self, db, user, id, nonce=None):
        if not _owner_ok(user) or not isinstance(id, str) or not _ID.fullmatch(id):
            raise reject(404)
        self._owner(db, user)
        row = db.execute('SELECT * FROM codegen_requests WHERE id = ? AND owner = ?',
                         (id, user)).fetchone()
        if row is None:
            raise reject(404)
        if nonce is not None:
            if not isinstance(nonce, str) or not secrets.compare_digest(
                    _sha(nonce), str(row['nonce_sha256'])):
                raise reject(404)
        return row

    def _linked(self, db, row):
        run_id, digest, wat = row['run_id'], row['wat_sha256'], row['wat']
        if not isinstance(run_id, str) or not isinstance(digest, str) or not _HEX.fullmatch(digest):
            return None
        if wat is not None and (not isinstance(wat, str)
                                or not secrets.compare_digest(_sha(wat), digest)):
            return None
        # Read-only origin check: the link row must bind this generation, owner and run.
        link = db.execute('SELECT run_id FROM sandbox_generation_links '
                          'WHERE generation_id = ? AND owner = ? AND run_id = ?',
                          (row['id'], row['owner'], run_id)).fetchone()
        if link is None:
            return None
        run = db.execute('SELECT * FROM sandbox_runs WHERE id = ? AND owner = ? AND source_sha256 = ?',
                         (run_id, row['owner'], digest)).fetchone()
        if run is None or not secrets.compare_digest(str(run['source_sha256']), digest):
            return None
        return run

    def _run_view(self, db, row):
        linked = self._linked(db, row)
        view = self.sandbox._view(linked) if linked is not None else None
        return view if isinstance(view, dict) else None

    def _view(self, db, row):
        d, now = dict(row), self._now()
        exp = d['wat_expires_epoch']
        if d['wat'] is not None and exp is not None and now >= exp:
            # Access-time redaction only; hash, receipt and run link are retained.
            db.execute('UPDATE codegen_requests SET wat = NULL WHERE id = ? AND owner = ?',
                       (d['id'], d['owner']))
            d['wat'] = None
        raw = status = d['status']
        run = None
        base = d['updated_epoch'] if d['updated_epoch'] is not None else d['created_epoch']
        age = now - float(base)
        if raw == 'SUCCEEDED':
            run = self._run_view(db, d)
            if run is None or run.get('status') != 'SUCCEEDED':
                status, run = 'UNKNOWN', None
        elif raw == 'RUNNING':
            run = self._run_view(db, d)
            if run is None or run.get('status') == 'UNKNOWN':
                status = 'UNKNOWN'
        elif raw in ('RESERVED', 'GENERATED') and age > STALE_S:
            status = 'UNKNOWN'
        elif raw == 'SENT' and age > STALE_S:
            status = 'SENT_UNKNOWN'
        elif raw == 'UNKNOWN' and d['run_id'] is not None:
            run = self._run_view(db, d)
        elif raw == 'FAILED' and d['run_id'] is not None:
            linked = self._run_view(db, d)
            run = linked if linked is not None and linked.get('status') == 'FAILED' else None
        if status not in STATUSES:
            status, run = 'UNKNOWN', None
        names = []
        if raw not in ('RESERVED', 'SENT'):
            try:
                parsed = json.loads(d['arg_names_json'] or '[]')
            except (TypeError, ValueError):
                parsed = []
            if isinstance(parsed, list) and all(isinstance(n, str) for n in parsed):
                names = parsed
        error = None
        if status in ('FAILED', 'SENT_UNKNOWN', 'UNKNOWN'):
            code = d['error_code']
            if not isinstance(code, str) or not _CODE.fullmatch(code):
                code = 'DELIVERY_UNKNOWN' if status == 'SENT_UNKNOWN' else 'STATE_UNKNOWN'
            error = {'code': code, 'message': _PUBLIC_ERROR}
        available = d['wat'] is not None and exp is not None and now < exp
        return {'generation': {
            'id': d['id'], 'generation_key': d['generation_key'], 'status': status,
            'provenance': d['provenance'], 'wat_sha256': d['wat_sha256'],
            'arg_count': int(d['arg_count']), 'arg_names': names, 'error': error,
            'created_at': str(d['created_at']), 'source_available': bool(available)},
            'run': run, 'simulated': True}

    def get(self, user, id):
        with self.bank.transaction() as db:
            return self._view(db, self._load(db, user, id))

    def by_key(self, user, key):
        self._key(key)
        if not _owner_ok(user):
            raise reject(404)
        with self.bank.transaction() as db:
            self._owner(db, user)
            row = db.execute('SELECT * FROM codegen_requests WHERE owner = ? AND generation_key = ?',
                             (user, key)).fetchone()
            if row is None:
                raise reject(404)
            return self._view(db, row)

    def list(self, user):
        if not _owner_ok(user):
            return []
        with self.bank.transaction() as db:
            try:
                self._owner(db, user)
            except DomainError:
                return []
            rows = db.execute('SELECT * FROM codegen_requests WHERE owner = ? '
                              'ORDER BY created_epoch DESC, id DESC LIMIT 20', (user,)).fetchall()
            return [self._view(db, r) for r in rows]

    def replay(self, user, intent, key):
        if type(intent) is not CalcIntent or not _owner_ok(user):
            raise reject(400)
        self._key(key)
        try:
            digest = intent_sha256(intent)
        except ValueError:
            raise reject(400) from None
        with self.bank.transaction() as db:
            self._owner(db, user)
            row = db.execute('SELECT * FROM codegen_requests WHERE owner = ? AND generation_key = ?',
                             (user, key)).fetchone()
            if row is None:
                return None
            if not secrets.compare_digest(str(row['intent_sha256']), digest):
                raise reject(409)
            return self._view(db, row)

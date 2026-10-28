"""Owner-only sandbox computation receipts.

Receipts record simulated computations only. This module never calls Bank
monetary methods, never persists WAT source or args (hashes only), and never
retries a run automatically. Every DB transaction is short; the runner is
invoked only after the RUNNING reservation has been committed.
"""
import hashlib
import json
import re
import secrets
import sqlite3
from datetime import timedelta, timezone

from .bank import DomainError
from .sandbox_process import SandboxError, _frame

KEY_RE = re.compile('[A-Za-z0-9_-]{8,100}')
RUN_ID_RE = re.compile('sbx_[0-9a-f]{32}')
GEN_RE = re.compile('cgen_[0-9a-f]{32}')
CODE_RE = re.compile('[A-Z0-9_]{1,64}')
RATE_LIMIT = 30
RATE_WINDOW = timedelta(seconds=3600)
UNKNOWN_CODE = 'UNKNOWN_OUTCOME'
UNKNOWN_MESSAGE = '运行结果未知，系统不会自动重试，请人工核查。'

SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS sandbox_runs (
        id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        request_key TEXT NOT NULL,
        frame_sha256 TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'UNKNOWN')),
        result_json TEXT,
        error_code TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        created_epoch REAL NOT NULL,
        finished_at TEXT,
        UNIQUE (owner, request_key)
    )''',
    'CREATE INDEX IF NOT EXISTS sandbox_runs_owner_created '
    'ON sandbox_runs (owner, created_epoch)',
    # Origin capability: only the SHA-256 of the internal token is stored.
    '''CREATE TABLE IF NOT EXISTS sandbox_generation_links (
        generation_id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        run_id TEXT NOT NULL UNIQUE,
        token_sha256 TEXT NOT NULL,
        execution_claimed INTEGER NOT NULL DEFAULT 0
            CHECK (execution_claimed IN (0, 1))
    )''',
)


def _domain_error(status, _detail):
    codes = {400: ('INVALID_INPUT', '计算请求参数无效。'),
             404: ('NOT_FOUND', '未找到本人的计算记录。'),
             409: ('REQUEST_KEY_CONFLICT', '这次请求与原记录不同，请先核对原结果。'),
             429: ('RATE_LIMITED', '本小时计算次数已达上限，请稍后再试。')}
    code, message = codes.get(status, ('RUNTIME_UNAVAILABLE', '计算服务暂不可用。'))
    return DomainError(code, message, status)


def _utc_now(bank):
    now = bank.now()
    if now.tzinfo is None or now.utcoffset() is None:
        raise RuntimeError('bank.now() must be timezone-aware')
    return now.astimezone(timezone.utc)


def _safe_error(exc):
    # Only whitelisted SandboxError fields are stored; never the raw exception.
    code = getattr(exc, 'code', None)
    message = getattr(exc, 'message', None)
    if not isinstance(code, str) or not CODE_RE.fullmatch(code):
        code = 'SANDBOX_ERROR'
    if not isinstance(message, str):
        message = code
    return code, message[:200]


class SandboxService:
    def __init__(self, bank, runner):
        self.bank = bank
        self.runner = runner
        with bank.transaction() as db:
            for stmt in SCHEMA:
                db.execute(stmt)

    def _view(self, row):
        # This is a read-only uncertainty projection, not evidence of termination.
        # Keep the reservation RUNNING so a still-live original owner can settle it.
        stale = (row['status'] == 'RUNNING'
                 and _utc_now(self.bank).timestamp() - row['created_epoch'] > 30)
        error = None
        if row['error_code'] is not None:
            error = {'code': row['error_code'], 'message': row['error_message'] or ''}
        result = None
        if row['result_json'] is not None:
            result = json.loads(row['result_json'])
            # Browsers cannot represent every i64 as a JSON number exactly.
            # Keep the machine value and add a lossless display representation.
            if type(result.get('result')) is int:
                result['result_text'] = str(result['result'])
        if stale:
            result = None
            error = {'code': UNKNOWN_CODE, 'message': UNKNOWN_MESSAGE}
        return {
            'id': row['id'],
            'status': 'UNKNOWN' if stale else row['status'],
            'result': result,
            'error': error,
            'simulated': True,
            'request_key': row['request_key'],
            'source_sha256': row['source_sha256'],
            'created_at': row['created_at'],
        }

    def _audit(self, db, user, event, **detail):
        text = json.dumps(detail, sort_keys=True, separators=(',', ':'))
        self.bank._event(db, user, None, event, text)

    def _execute(self, wat, args):
        # Runs outside any DB transaction. No automatic retry on any outcome.
        try:
            result = self.runner.run(wat, args)
            if (type(result) is not dict or result.get('ok') is not True
                    or type(result.get('result')) is not int
                    or not -(2**63) <= result['result'] <= 2**63-1
                    or type(result.get('fuel_used')) is not int
                    or not 0 <= result['fuel_used'] <= 100000):
                raise TypeError('unexpected runner result')
            encoded = json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False)
            return 'SUCCEEDED', encoded, None, None
        except SandboxError as exc:
            if exc.code == 'CLEANUP_UNVERIFIED':
                return 'UNKNOWN', None, UNKNOWN_CODE, UNKNOWN_MESSAGE
            code, message = _safe_error(exc)
            return 'FAILED', None, code, message
        except Exception:
            return 'UNKNOWN', None, UNKNOWN_CODE, UNKNOWN_MESSAGE

    def run(self, user, wat, args, request_key):
        if not isinstance(user, str) or not user:
            raise _domain_error(400, 'invalid user')
        if not isinstance(request_key, str) or not KEY_RE.fullmatch(request_key):
            raise _domain_error(400, 'request_key must be 8-100 chars of [A-Za-z0-9_-]')
        if not isinstance(args, list):
            raise _domain_error(400, 'args must be a list')
        src, frame = _frame(wat, args)  # strict validation before any DB write or worker
        source_sha = hashlib.sha256(src).hexdigest()
        frame_sha = hashlib.sha256(frame).hexdigest()

        replay = None
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            row = db.execute(
                'SELECT * FROM sandbox_runs WHERE owner = ? AND request_key = ?',
                (user, request_key)).fetchone()
            if row is not None:
                if row['frame_sha256'] != frame_sha or row['source_sha256'] != source_sha:
                    raise _domain_error(409, 'request_key reused with a different payload')
                replay = self._view(row)
            else:
                now = _utc_now(self.bank)
                since = (now - RATE_WINDOW).timestamp()
                count = db.execute(
                    'SELECT COUNT(*) FROM sandbox_runs WHERE owner = ? AND created_epoch > ?',
                    (user, since)).fetchone()[0]
                if count >= RATE_LIMIT:
                    raise _domain_error(429, 'sandbox run rate limit exceeded')
                run_id = 'sbx_' + secrets.token_hex(16)
                db.execute(
                    'INSERT INTO sandbox_runs (id, owner, request_key, frame_sha256, '
                    'source_sha256, status, created_at, created_epoch) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (run_id, user, request_key, frame_sha, source_sha, 'RUNNING',
                     now.isoformat(), now.timestamp()))
                self._audit(db, user, 'sandbox_run_reserved', run_id=run_id,
                            request_key=request_key, source_sha256=source_sha)
        if replay is not None:
            return replay  # exact current stored view; never re-executes

        return self._settle(user, run_id, *self._execute(wat, args))

    def _settle(self, user, run_id, status, result_json, code, message):
        # If this transaction fails the error propagates and the row stays RUNNING.
        with self.bank.transaction() as db:
            finished = _utc_now(self.bank).isoformat()
            cur = db.execute(
                'UPDATE sandbox_runs SET status = ?, result_json = ?, error_code = ?, '
                'error_message = ?, finished_at = ? '
                'WHERE id = ? AND owner = ? AND status = ?',
                (status, result_json, code, message, finished, run_id, user, 'RUNNING'))
            if cur.rowcount == 1:
                self._audit(db, user, 'sandbox_run_finished', run_id=run_id,
                            status=status, error_code=code)
            row = db.execute(
                'SELECT * FROM sandbox_runs WHERE id = ? AND owner = ?',
                (run_id, user)).fetchone()
        return self._view(row)

    def reserve_generated(self, db, user, wat, args, request_key, generation_id):
        # Internal only. Joins the caller's open Bank transaction so the RUNNING
        # row, the origin link and the caller's generation state commit atomically.
        if getattr(db, 'in_transaction', False) is not True:
            raise RuntimeError('reserve_generated requires an open transaction')
        if not isinstance(user, str) or not user:
            raise _domain_error(400, 'invalid user')
        if not isinstance(request_key, str) or not KEY_RE.fullmatch(request_key):
            raise _domain_error(400, 'invalid key')
        if not isinstance(generation_id, str) or not GEN_RE.fullmatch(generation_id):
            raise _domain_error(400, 'invalid generation id')
        if not isinstance(args, list):
            raise _domain_error(400, 'args must be a list')
        try:
            src, frame = _frame(wat, args)
        except SandboxError:
            raise _domain_error(400, 'invalid frame') from None
        source_sha = hashlib.sha256(src).hexdigest()
        frame_sha = hashlib.sha256(frame).hexdigest()
        self.bank._user(db, user)
        # Never adopt an existing owner+key row, even with an identical frame.
        if db.execute('SELECT 1 FROM sandbox_runs WHERE owner = ? AND request_key = ?',
                      (user, request_key)).fetchone() is not None:
            raise _domain_error(409, 'request_key already used')
        if db.execute('SELECT 1 FROM sandbox_generation_links WHERE generation_id = ?',
                      (generation_id,)).fetchone() is not None:
            raise _domain_error(409, 'generation already linked')
        now = _utc_now(self.bank)
        count = db.execute(
            'SELECT COUNT(*) FROM sandbox_runs WHERE owner = ? AND created_epoch > ?',
            (user, (now - RATE_WINDOW).timestamp())).fetchone()[0]
        if count >= RATE_LIMIT:
            raise _domain_error(429, 'sandbox run rate limit exceeded')
        run_id = 'sbx_' + secrets.token_hex(16)
        token = secrets.token_hex(32)  # 256-bit, returned to the internal caller only
        try:
            db.execute(
                'INSERT INTO sandbox_runs (id, owner, request_key, frame_sha256, '
                'source_sha256, status, created_at, created_epoch) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (run_id, user, request_key, frame_sha, source_sha, 'RUNNING',
                 now.isoformat(), now.timestamp()))
            db.execute(
                'INSERT INTO sandbox_generation_links (generation_id, owner, run_id, '
                'token_sha256, execution_claimed) VALUES (?, ?, ?, ?, 0)',
                (generation_id, user, run_id,
                 hashlib.sha256(token.encode('utf-8')).hexdigest()))
        except sqlite3.IntegrityError:
            # Caller's transaction rollback discards any partial insert.
            raise _domain_error(409, 'conflict') from None
        self._audit(db, user, 'sandbox_generated_run_reserved', run_id=run_id,
                    request_key=request_key, source_sha256=source_sha,
                    generation_id=generation_id)
        return {'run_id': run_id, 'token': token}

    def execute_generated(self, user, run_id, token, wat, args, generation_id):
        if (not isinstance(user, str) or not user or not isinstance(token, str)
                or not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id)
                or not isinstance(generation_id, str)
                or not GEN_RE.fullmatch(generation_id)):
            raise _domain_error(404, 'generated run not found')
        if not isinstance(args, list):
            raise _domain_error(400, 'args must be a list')
        try:
            src, frame = _frame(wat, args)
        except SandboxError:
            raise _domain_error(400, 'invalid frame') from None
        source_sha = hashlib.sha256(src).hexdigest()
        frame_sha = hashlib.sha256(frame).hexdigest()
        token_sha = hashlib.sha256(token.encode('utf-8', 'replace')).hexdigest()
        view = None
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            link = db.execute(
                'SELECT * FROM sandbox_generation_links '
                'WHERE generation_id = ? AND run_id = ? AND owner = ?',
                (generation_id, run_id, user)).fetchone()
            row = db.execute('SELECT * FROM sandbox_runs WHERE id = ? AND owner = ?',
                             (run_id, user)).fetchone()
            # All checks precede any mutation, so a bad request cannot poison the claim.
            if (link is None or row is None
                    or not secrets.compare_digest(link['token_sha256'], token_sha)):
                raise _domain_error(404, 'generated run not found')
            if row['source_sha256'] != source_sha or row['frame_sha256'] != frame_sha:
                raise _domain_error(409, 'generated source mismatch')
            claimed = False
            if row['status'] == 'RUNNING' and link['execution_claimed'] == 0:
                claimed = db.execute(
                    'UPDATE sandbox_generation_links SET execution_claimed = 1 '
                    'WHERE generation_id = ? AND execution_claimed = 0',
                    (generation_id,)).rowcount == 1
            if not claimed:
                view = self._view(row)  # read-only; never runs a second time
        if view is not None:
            return view
        return self._settle(user, run_id, *self._execute(wat, args))

    def get(self, user, run_id):
        if not isinstance(user, str) or not user:
            raise _domain_error(400, 'invalid user')
        if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
            raise _domain_error(404, 'sandbox run not found')
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            row = db.execute(
                'SELECT * FROM sandbox_runs WHERE id = ? AND owner = ?',
                (run_id, user)).fetchone()
        if row is None:
            raise _domain_error(404, 'sandbox run not found')
        return self._view(row)

    def by_key(self, user, request_key):
        if type(request_key) is not str or not KEY_RE.fullmatch(request_key):
            raise _domain_error(400, 'invalid key')
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            row = db.execute('SELECT * FROM sandbox_runs WHERE owner=? AND request_key=?',
                             (user, request_key)).fetchone()
        if row is None:
            raise _domain_error(404, 'not found')
        return self._view(row)

    def list(self, user):
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            rows = db.execute('SELECT * FROM sandbox_runs WHERE owner=? ORDER BY created_epoch DESC,rowid DESC LIMIT 20',
                              (user,)).fetchall()
        return [self._view(row) for row in rows]

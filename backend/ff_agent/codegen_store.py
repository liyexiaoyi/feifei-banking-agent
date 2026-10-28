"""Write half of the codegen store: nonce-guarded, single-transaction state transitions.

Read/view/replay logic lives in CodegenStoreBase; nothing here grants Bank authority.
"""
import hashlib
import json
import re
import secrets

from .codegen_store_base import CodegenStoreBase, reject
from .codegen_types import CalcIntent, GeneratedWat, intent_sha256, parse_generated

HOUR = 3600
WEEK = 7 * 86400
RATE_LIMIT = 5
PROVENANCES = ('model', 'fixture')
OUTCOMES = ('FAILED', 'SENT_UNKNOWN')
TERMINAL = ('SUCCEEDED', 'FAILED', 'UNKNOWN')
_NONCE_RE = re.compile(r'[0-9a-f]{64}')
_CODE_RE = re.compile(r'[A-Z][A-Z0-9_]{1,39}')
_META_KEY_RE = re.compile(r'[a-z][a-z0-9_]{0,31}')
_META_STR_RE = re.compile(r'[A-Za-z0-9_.:-]{0,64}')
MAX_META_JSON = 2048
_SEP = (',', ':')


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


_USAGE_MAX_KEYS = 16
_I64_MAX = 2 ** 63 - 1


def _usage(value):
    """Bounded token-usage counts only: ASCII keys -> nonnegative exact ints; else None."""
    if type(value) is not dict or len(value) > _USAGE_MAX_KEYS:
        return None
    out = {}
    for k, v in value.items():
        # Nested objects, strings, floats, bools and nulls are skipped, never persisted.
        if (type(k) is str and _META_KEY_RE.fullmatch(k)
                and type(v) is int and 0 <= v <= _I64_MAX):
            out[k] = v
    return out or None


def _meta_json(meta):
    """Keep only bounded scalar metadata; anything invalid collapses to '{}' (no raw text)."""
    if type(meta) is not dict or len(meta) > 16:
        return '{}'
    out = {}
    for k, v in meta.items():
        if type(k) is not str or not _META_KEY_RE.fullmatch(k):
            return '{}'
        if k == 'usage':
            # Only a bounded count object survives; invalid usage is omitted, not stored raw.
            usage = _usage(v)
            if usage is not None:
                out[k] = usage
            continue
        if v is None or type(v) in (bool, int):
            out[k] = v
        elif type(v) is float and v == v and v not in (float('inf'), float('-inf')):
            out[k] = v
        elif type(v) is str and _META_STR_RE.fullmatch(v):
            out[k] = v
        else:
            return '{}'
    try:
        text = json.dumps(out, sort_keys=True, separators=_SEP, allow_nan=False)
    except (TypeError, ValueError):
        return '{}'
    return text if len(text) <= MAX_META_JSON else '{}'


class CodegenStore(CodegenStoreBase):
    @staticmethod
    def _nonce(nonce):
        # Must run before _load: _load treats nonce=None as an unauthenticated read.
        if type(nonce) is not str or not _NONCE_RE.fullmatch(nonce):
            raise reject(404, 'NOT_FOUND')
        return nonce

    def reserve(self, user, intent, key, provenance):
        if type(intent) is not CalcIntent:
            raise reject(400)
        if type(provenance) is not str or provenance not in PROVENANCES:
            raise reject(400)
        if type(user) is not str or not user:
            raise reject(404, 'NOT_FOUND')
        self._key(key)
        isha = intent_sha256(intent)
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            row = db.execute(
                'SELECT * FROM codegen_requests WHERE owner = ? AND generation_key = ?',
                (user, key)).fetchone()
            if row is not None:
                if row['intent_sha256'] != isha:
                    raise reject(409, 'CONFLICT')
                return self._view(db, row), None  # replay: read-only, no quota, no nonce
            now = self.bank.now()
            epoch = now.timestamp()
            used = db.execute(
                'SELECT COUNT(*) FROM codegen_requests WHERE owner = ? AND created_epoch > ?',
                (user, epoch - HOUR)).fetchone()[0]
            if used >= RATE_LIMIT:
                raise reject(429, 'RATE_LIMITED')
            gid = 'cgen_' + secrets.token_hex(16)
            nonce = secrets.token_hex(32)  # plaintext returned once; only its hash persists
            try:
                db.execute(
                    'INSERT INTO codegen_requests (id, owner, generation_key, intent_sha256, '
                    'arg_count, provenance, status, nonce_sha256, created_at, created_epoch, '
                    'updated_epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (gid, user, key, isha, len(intent.args), provenance, 'RESERVED',
                     _sha(nonce), now.isoformat(), epoch, epoch))
            except Exception as exc:
                if type(exc).__name__ == 'IntegrityError':
                    raise reject(409, 'CONFLICT') from None
                raise
            return self._view(db, self._load(db, user, gid, nonce)), nonce

    def mark_sent(self, user, gid, nonce):
        self._nonce(nonce)
        with self.bank.transaction() as db:
            self._load(db, user, gid, nonce)
            cur = db.execute(
                'UPDATE codegen_requests SET status = ?, updated_epoch = ? '
                'WHERE id = ? AND owner = ? AND status = ?',
                ('SENT', self._now(), gid, user, 'RESERVED'))
            return cur.rowcount == 1

    def save_generated(self, user, gid, nonce, generated):
        self._nonce(nonce)
        with self.bank.transaction() as db:
            row = self._load(db, user, gid, nonce)
            if row['status'] != 'SENT':
                return False
            if type(generated) is GeneratedWat:
                data = {'wat': generated.wat, 'arg_names': list(generated.arg_names)}
                meta = generated.meta
            else:
                data, meta = generated, None
            # Strict re-parse against the stored arg_count; raises ValueError (nothing written).
            parsed = parse_generated(data, row['arg_count'], meta if type(meta) is dict else None)
            cur = db.execute(
                'UPDATE codegen_requests SET status = ?, wat = ?, wat_sha256 = ?, '
                'arg_names_json = ?, private_meta_json = ?, updated_epoch = ?, '
                'wat_expires_epoch = ? WHERE id = ? AND owner = ? AND status = ?',
                ('GENERATED', parsed.wat, _sha(parsed.wat),
                 json.dumps(list(parsed.arg_names), separators=_SEP, allow_nan=False),
                 _meta_json(parsed.meta), self._now(), row['created_epoch'] + WEEK,
                 gid, user, 'SENT'))
            return cur.rowcount == 1

    def fail(self, user, gid, nonce, outcome, code, meta=None):
        if type(outcome) is not str or outcome not in OUTCOMES:
            raise reject(400)
        if type(code) is not str or not _CODE_RE.fullmatch(code):
            code = 'GENERATION_FAILED'
        self._nonce(nonce)
        with self.bank.transaction() as db:
            self._load(db, user, gid, nonce)
            # RUNNING and later are owned by settle; fail can never rewrite them.
            db.execute(
                'UPDATE codegen_requests SET status = ?, error_code = ?, '
                'private_meta_json = COALESCE(?, private_meta_json), updated_epoch = ? '
                'WHERE id = ? AND owner = ? AND status IN (?, ?, ?)',
                (outcome, code, None if meta is None else _meta_json(meta), self._now(),
                 gid, user, 'RESERVED', 'SENT', 'GENERATED'))
            return self._view(db, self._load(db, user, gid))

    def claim_run(self, user, gid, nonce, intent):
        if type(intent) is not CalcIntent:
            raise reject(400)
        isha = intent_sha256(intent)
        self._nonce(nonce)
        with self.bank.transaction() as db:
            row = self._load(db, user, gid, nonce)
            if row['intent_sha256'] != isha:
                raise reject(409, 'CONFLICT')
            if row['status'] != 'GENERATED':
                return None  # repeated or advanced: never re-execute
            wat, expires = row['wat'], row['wat_expires_epoch']
            if (wat is None or expires is None or expires <= self._now()
                    or _sha(wat) != row['wat_sha256']):
                return None
            args = list(intent.args)
            run_key = 'genrun_' + secrets.token_hex(16)
            # Same transaction: any failure here rolls back the sandbox reservation too.
            cap = self.sandbox.reserve_generated(db, user, wat, args, run_key, gid)
            cur = db.execute(
                'UPDATE codegen_requests SET status = ?, run_id = ?, updated_epoch = ? '
                'WHERE id = ? AND owner = ? AND status = ?',
                ('RUNNING', cap['run_id'], self._now(), gid, user, 'GENERATED'))
            if cur.rowcount != 1:
                raise reject(409, 'CONFLICT')
            return {'run_id': cap['run_id'], 'token': cap['token'], 'wat': wat,
                    'args': args, 'generation_id': gid}

    def settle(self, user, gid, nonce):
        self._nonce(nonce)
        with self.bank.transaction() as db:
            row = self._load(db, user, gid, nonce)
            if row['status'] == 'RUNNING':
                link = self._linked(db, row)
                if link is None:
                    status = 'UNKNOWN'  # no reliable link/run/source hash
                elif link['status'] in TERMINAL:
                    status = link['status']
                elif link['status'] == 'RUNNING':
                    status = None  # pending: keep RUNNING, never fake a terminal state
                else:
                    status = 'UNKNOWN'
                if status is not None:
                    code = {'SUCCEEDED': None, 'FAILED': 'EXECUTION_FAILED'}.get(
                        status, 'EXECUTION_UNKNOWN')
                    db.execute(
                        'UPDATE codegen_requests SET status = ?, error_code = ?, '
                        'updated_epoch = ? WHERE id = ? AND owner = ? AND status = ?',
                        (status, code, self._now(), gid, user, 'RUNNING'))
            return self._view(db, self._load(db, user, gid))

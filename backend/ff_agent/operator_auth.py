# Separate short-lived local operator sessions; they never authorize financial actions.
import hashlib
import hmac
import os
import secrets

from .bank import DomainError

ENV_NAME = 'FF_OPERATOR_ACCESS_TOKEN'
OPERATOR_ID = 'local-demo-operator'
LIFETIME = 1800
MIN_TOKEN_LENGTH = 32
MAX_FAILURES = 5
FAILURE_WINDOW = 60
FAILURE_SCOPE = 'service'


def _digest(value):
    # Unicode-safe fixed-length digest so compare_digest never sees raw or non-ASCII input.
    text = value if isinstance(value, str) else ''
    return hashlib.sha256(text.encode('utf-8', 'surrogatepass')).digest()


def _session_hash(token):
    return hashlib.sha256(b'ff-operator-session:' + _digest(token)).hexdigest()


def _fingerprint(token, configured):
    # Binds a session to the configured credential; useless without the session token itself.
    return hmac.new(_digest(token), b'ff-operator-config:' + _digest(configured), hashlib.sha256).hexdigest()


def _required():
    return DomainError('OPERATOR_SESSION_REQUIRED', '请先登录本机运营演示入口。', 401)


class OperatorAuth:
    def __init__(self, bank):
        self.bank = bank
        with bank.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS operator_sessions(token_hash TEXT PRIMARY KEY,'
                       'operator_id TEXT NOT NULL,csrf TEXT NOT NULL,expires_at REAL NOT NULL,config_fp TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS operator_login_failures(id INTEGER PRIMARY KEY AUTOINCREMENT,'
                       'scope TEXT NOT NULL,failed_at REAL NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS operator_login_failures_scope_at '
                       'ON operator_login_failures(scope,failed_at)')

    def _now(self):
        return self.bank.now().timestamp()

    @staticmethod
    def _configured():
        value = os.environ.get(ENV_NAME)
        if not isinstance(value, str) or len(value) < MIN_TOKEN_LENGTH:
            return None
        return value

    def login(self, access_token, previous_token=None):
        configured = self._configured()
        if configured is None:
            raise DomainError('OPERATOR_DISABLED', '本机运营演示入口未启用。', 503)
        now = self._now()
        outcome, token, csrf = None, None, None
        expires_at = now + LIFETIME
        with self.bank.transaction() as db:
            db.execute('DELETE FROM operator_login_failures WHERE failed_at<=?', (now - FAILURE_WINDOW,))
            db.execute('DELETE FROM operator_sessions WHERE expires_at<=?', (now,))
            failures = db.execute('SELECT COUNT(*) FROM operator_login_failures WHERE scope=? AND failed_at>?',
                                  (FAILURE_SCOPE, now - FAILURE_WINDOW)).fetchone()[0]
            if failures >= MAX_FAILURES:
                outcome = 'limited'
            elif not hmac.compare_digest(_digest(access_token), _digest(configured)):
                db.execute('INSERT INTO operator_login_failures(scope,failed_at) VALUES(?,?)', (FAILURE_SCOPE, now))
                outcome = 'rejected'
            else:
                if isinstance(previous_token, str) and previous_token:
                    db.execute('DELETE FROM operator_sessions WHERE token_hash=?', (_session_hash(previous_token),))
                token = secrets.token_urlsafe(32)
                csrf = secrets.token_urlsafe(24)
                db.execute('INSERT INTO operator_sessions(token_hash,operator_id,csrf,expires_at,config_fp) VALUES(?,?,?,?,?)',
                           (_session_hash(token), OPERATOR_ID, csrf, expires_at, _fingerprint(token, configured)))
                outcome = 'ok'
        # Raise only after commit so failure records persist.
        if outcome == 'limited':
            raise DomainError('OPERATOR_RATE_LIMITED', '登录尝试过多，请约60秒后再试。', 429)
        if outcome == 'rejected':
            raise DomainError('OPERATOR_CREDENTIAL_REJECTED', '运营演示凭据无效。', 401)
        return {'token': token, 'csrf': csrf, 'expires_at': expires_at,
                'operator_id': OPERATOR_ID, 'simulated': True}

    def session(self, token, csrf=None, write=False):
        configured = self._configured()
        if configured is None or not isinstance(token, str) or not token:
            raise _required()
        now = self._now()
        with self.bank.transaction() as db:
            row = db.execute('SELECT operator_id,csrf,expires_at,config_fp FROM operator_sessions '
                             'WHERE token_hash=? AND expires_at>?', (_session_hash(token), now)).fetchone()
        if row is None or not hmac.compare_digest(row['config_fp'], _fingerprint(token, configured)):
            raise _required()
        if write and not hmac.compare_digest(_digest(csrf), _digest(row['csrf'])):
            raise DomainError('CSRF_REJECTED', '操作凭据已失效，请刷新页面后重试。', 403)
        return {'operator_id': row['operator_id'], 'csrf': row['csrf'], 'expires_at': row['expires_at']}

    def logout(self, token, csrf):
        self.session(token, csrf, write=True)
        with self.bank.transaction() as db:
            db.execute('DELETE FROM operator_sessions WHERE token_hash=?', (_session_hash(token),))
        return {'revoked': True}

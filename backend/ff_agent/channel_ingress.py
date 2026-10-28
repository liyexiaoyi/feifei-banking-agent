"""Offline local demo channel ingress (FF_LOCAL_DEMO_V1).

Transport authentication only: a valid signature maps a server-side identity
and never authorizes any bank effect. Message text is always untrusted.
Only delivery metadata (keyed hashes) is persisted.
"""
import hashlib
import hmac
import json
import re

from .bank import DomainError

PROTOCOL = 'FF_LOCAL_DEMO_V1'
SIGN_PREFIX = b'FF_LOCAL_DEMO_V1\n'
MAX_RAW = 16384
MAX_TEXT = 2000
WINDOW = 60
FIELDS = frozenset({'protocol', 'channel', 'external_user', 'message_id', 'timestamp', 'text'})
MESSAGE_ID = re.compile(r'[A-Za-z0-9_-]{1,80}', re.ASCII)
SIGNATURE = re.compile(r'[0-9a-f]{64}', re.ASCII)

DDL = (
    'CREATE TABLE IF NOT EXISTS channel_deliveries ('
    'id INTEGER PRIMARY KEY AUTOINCREMENT,'
    'channel TEXT NOT NULL,'
    'message_id TEXT NOT NULL,'
    'body_hash TEXT NOT NULL,'
    'identity_hash TEXT NOT NULL,'
    'user_id TEXT NOT NULL,'
    'created_at TEXT NOT NULL,'
    'UNIQUE(channel, message_id))'
)


def _reject(code, message='invalid ingress frame', status=400):
    raise DomainError(code, message, status)


def _pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            _reject('INGRESS_DUPLICATE_KEY')
        obj[key] = value
    return obj


def _nonfinite(_):
    _reject('INGRESS_NONFINITE')


class ChannelIngress:
    def __init__(self, bank, keys, identities, enabled=False):
        if not isinstance(keys, dict) or not isinstance(identities, dict):
            _reject('INGRESS_CONFIG', 'invalid ingress configuration')
        for name, key in keys.items():
            if not isinstance(name, str) or not isinstance(key, bytes) or len(key) < 32:
                _reject('INGRESS_CONFIG', 'invalid ingress configuration')
        for ident, user in identities.items():
            if (not isinstance(ident, tuple) or len(ident) != 2
                    or not all(isinstance(x, str) for x in ident) or not isinstance(user, str)):
                _reject('INGRESS_CONFIG', 'invalid ingress configuration')
        self.bank = bank
        self._keys = dict(keys)
        self._identities = dict(identities)
        self.enabled = enabled is True
        # Owned metadata DDL runs in its own transaction, never inside accept.
        with bank.transaction() as db:
            db.execute(DDL)

    def __repr__(self):
        # Never expose keys or external identities.
        return f'<ChannelIngress enabled={self.enabled} channels={len(self._keys)}>'

    @staticmethod
    def _parse(raw):
        try:
            decoded = raw.decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            _reject('INGRESS_ENCODING')
        try:
            frame = json.loads(decoded, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        except DomainError:
            raise
        except (ValueError, RecursionError):
            _reject('INGRESS_JSON')
        if type(frame) is not dict or set(frame) != FIELDS:
            _reject('INGRESS_SHAPE')
        if frame['protocol'] != PROTOCOL or type(frame['protocol']) is not str:
            _reject('INGRESS_PROTOCOL')
        for name in ('channel', 'external_user', 'message_id', 'text'):
            if type(frame[name]) is not str:
                _reject('INGRESS_TYPE')
        if type(frame['timestamp']) is not int:
            _reject('INGRESS_TYPE')
        for name in ('channel', 'external_user', 'message_id', 'text'):
            try:
                frame[name].encode('utf-8')
            except UnicodeEncodeError:
                _reject('INGRESS_ENCODING')
        if not MESSAGE_ID.fullmatch(frame['message_id']):
            _reject('INGRESS_MESSAGE_ID')
        if not 1 <= len(frame['text']) <= MAX_TEXT:
            _reject('INGRESS_TEXT')
        return frame

    def accept(self, raw, signature, *, receiver=None):
        # receiver(db, frame, delivery_id, user) -> inbox_id is a trusted
        # server-side callback, invoked inside the same bank transaction.
        if receiver is not None and not callable(receiver):
            raise TypeError('receiver must be callable')
        if not self.enabled:
            _reject('INGRESS_DISABLED', 'ingress disabled', 403)
        if type(raw) is not bytes or len(raw) > MAX_RAW:
            _reject('INGRESS_SIZE')
        if type(signature) is not str or not SIGNATURE.fullmatch(signature):
            _reject('INGRESS_SIGNATURE', 'invalid signature', 401)
        frame = self._parse(raw)
        key = self._keys.get(frame['channel'])
        if key is None:
            _reject('INGRESS_CHANNEL', 'unknown channel', 403)
        expected = hmac.new(key, SIGN_PREFIX + raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            _reject('INGRESS_SIGNATURE', 'invalid signature', 401)
        # Owner comes only from the server mapping; the hidden message never echoes external_user.
        user = self._identities.get((frame['channel'], frame['external_user']))
        if user is None:
            _reject('INGRESS_IDENTITY', 'unknown identity', 403)
        canonical = json.dumps(
            {k: frame[k] for k in sorted(FIELDS)},
            ensure_ascii=False, separators=(',', ':'), sort_keys=True,
        ).encode('utf-8')
        body_hash = hmac.new(key, b'body\n' + canonical, hashlib.sha256).hexdigest()
        identity_hash = hmac.new(
            key, b'identity\n' + frame['external_user'].encode('utf-8'), hashlib.sha256
        ).hexdigest()
        with self.bank.transaction() as db:
            if not self.bank._user(db, user):
                _reject('INGRESS_IDENTITY', 'unknown identity', 403)
            row = db.execute(
                'SELECT id, body_hash, identity_hash, user_id FROM channel_deliveries '
                'WHERE channel=? AND message_id=?',
                (frame['channel'], frame['message_id']),
            ).fetchone()
            if row is not None:
                same = (hmac.compare_digest(row[1], body_hash)
                        and hmac.compare_digest(row[2], identity_hash)
                        and row[3] == user)
                if not same:
                    _reject('INGRESS_CONFLICT', 'message id conflict', 409)
                delivery_id = row[0]
                delivery = 'DUPLICATE'
            else:
                now = self.bank.now()
                if abs(frame['timestamp'] - int(now.timestamp())) > WINDOW:
                    _reject('INGRESS_STALE', 'timestamp outside window')
                cursor = db.execute(
                    'INSERT INTO channel_deliveries '
                    '(channel, message_id, body_hash, identity_hash, user_id, created_at) '
                    'VALUES (?,?,?,?,?,?)',
                    (frame['channel'], frame['message_id'], body_hash, identity_hash,
                     user, now.isoformat()),
                )
                delivery_id = cursor.lastrowid
                delivery = 'NEW'
            inbox_id = None
            if receiver is not None:
                # Same transaction: any exception rolls back delivery and inbox.
                # Also runs on DUPLICATE to backfill an earlier standalone accept.
                inbox_id = receiver(db, dict(frame), delivery_id, user)
        result = {
            'user': user,
            'text': frame['text'],
            'delivery': delivery,
            'effect_authorized': False,
            'untrusted_text': True,
            'simulated': True,
        }
        if receiver is not None:
            result['inbox_id'] = inbox_id
        return result

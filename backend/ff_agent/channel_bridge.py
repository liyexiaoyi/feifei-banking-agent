"""Owned local demo inbox for FF_LOCAL_DEMO_V1 channel deliveries.

Inbox text is untrusted and never authorizes any bank, chat or tool effect.
Raw content is stored only in the owner's channel_inbox row and is redacted
7 days after the original delivery.created_at (duplicates never renew).
"""
import uuid
from datetime import datetime, timedelta

from .bank import TZ, DomainError

LABEL = '本机演示渠道'
RETENTION = timedelta(days=7)
LIMIT = 50
MAX_TEXT = 2000

DDL = (
    'CREATE TABLE IF NOT EXISTS channel_inbox ('
    'id TEXT PRIMARY KEY,'
    'delivery_id INTEGER NOT NULL UNIQUE,'
    'user_id TEXT NOT NULL,'
    'body TEXT NOT NULL,'
    'received_at TEXT NOT NULL,'
    'expires_at REAL NOT NULL,'
    "state TEXT NOT NULL CHECK(state IN ('ACTIVE','REDACTED')))"
)


def _not_found():
    raise DomainError('NOT_FOUND', '未找到可访问的数据', 404)


def _public(row):
    received = datetime.fromisoformat(row[2]).astimezone(TZ)
    return {
        'id': row[0],
        'channel_label': LABEL,
        'text': row[1],
        'received_at': received.isoformat(),
        'expires_at': (received + RETENTION).isoformat(),
        'untrusted_text': True,
        'effect_authorized': False,
        'simulated': True,
    }


class ChannelBridge:
    def __init__(self, ingress):
        self.ingress = ingress
        self.bank = ingress.bank
        # Owned DDL runs in its own bank transaction, never inside a delivery.
        with self.bank.transaction() as db:
            db.execute(DDL)

    def __repr__(self):
        return '<ChannelBridge>'

    def _purge(self, db, user):
        # Scoped to one owner; clears body and keeps a single tombstone row.
        db.execute(
            "UPDATE channel_inbox SET body='', state='REDACTED' "
            "WHERE user_id=? AND state='ACTIVE' AND expires_at<=?",
            (user, self.bank.now().timestamp()),
        )

    def _store(self, db, frame, delivery_id, user):
        # Called by ChannelIngress.accept inside its own transaction; no nesting here.
        if getattr(db, 'in_transaction', True) is not True:
            raise RuntimeError('inbox store requires the delivery transaction')
        if type(delivery_id) is not int or type(user) is not str or not isinstance(frame, dict):
            raise RuntimeError('invalid inbox handoff')
        text = frame.get('text')
        if type(text) is not str or not 1 <= len(text) <= MAX_TEXT:
            raise RuntimeError('invalid inbox handoff')
        delivery = db.execute(
            'SELECT user_id, created_at FROM channel_deliveries WHERE id=?', (delivery_id,)
        ).fetchone()
        if delivery is None or delivery[0] != user:
            raise DomainError('INGRESS_CONFLICT', 'message id conflict', 409)
        self.bank._user(db, user)
        self._purge(db, user)
        existing = db.execute(
            'SELECT id, user_id FROM channel_inbox WHERE delivery_id=?', (delivery_id,)
        ).fetchone()
        if existing is not None:
            if existing[1] != user:
                raise DomainError('INGRESS_CONFLICT', 'message id conflict', 409)
            # Never update text/expiry and never resurrect a REDACTED tombstone.
            return existing[0]
        received = datetime.fromisoformat(delivery[1]).astimezone(TZ)
        expires = (received + RETENTION).timestamp()
        active = expires > self.bank.now().timestamp()
        inbox_id = 'inbox_' + uuid.uuid4().hex
        db.execute(
            'INSERT INTO channel_inbox '
            '(id, delivery_id, user_id, body, received_at, expires_at, state) '
            'VALUES (?,?,?,?,?,?,?)',
            (inbox_id, delivery_id, user, text if active else '', received.isoformat(),
             expires, 'ACTIVE' if active else 'REDACTED'),
        )
        return inbox_id

    def deliver(self, raw, signature):
        # Resolve self._store at call time so the handoff always uses the current receiver.
        result = self.ingress.accept(raw, signature, receiver=self._store)
        inbox_id = result.get('inbox_id')
        delivery = result.get('delivery')
        if type(inbox_id) is not str or not inbox_id.startswith('inbox_') \
                or delivery not in ('NEW', 'DUPLICATE'):
            raise RuntimeError('ingress receiver handoff missing')
        # Narrow receipt: no user, external identity, text or configuration echo.
        return {
            'inbox_id': inbox_id,
            'delivery': delivery,
            'untrusted_text': True,
            'effect_authorized': False,
            'simulated': True,
            'official_connected': False,
        }

    def list(self, user):
        if type(user) is not str:
            _not_found()
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            self._purge(db, user)
            rows = db.execute(
                'SELECT id, body, received_at FROM channel_inbox '
                "WHERE user_id=? AND state='ACTIVE' AND expires_at>? "
                'ORDER BY expires_at DESC, rowid DESC LIMIT ?',
                (user, self.bank.now().timestamp(), LIMIT),
            ).fetchall()
        return {'items': [_public(r) for r in rows], 'simulated': True, 'official_connected': False}

    def get(self, user, inbox_id):
        if type(user) is not str or type(inbox_id) is not str:
            _not_found()
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            self._purge(db, user)
            row = db.execute(
                'SELECT id, body, received_at FROM channel_inbox '
                "WHERE id=? AND user_id=? AND state='ACTIVE' AND expires_at>?",
                (inbox_id, user, self.bank.now().timestamp()),
            ).fetchone()
        if row is None:
            _not_found()
        return _public(row)

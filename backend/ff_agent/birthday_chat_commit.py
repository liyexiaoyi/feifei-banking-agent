"""Atomic chat confirmation of a summarized birthday slot.

One Bank transaction: every check runs before any write; any exception rolls back
flow, nodes, request, link, context and audit together. No money movement.
"""
import hashlib
import json
import math
import time

from .bank import DomainError
from .birthday_chat_slot import TTL_SECONDS, derive_chat_key, validate_birthday_pending

# Must stay identical to birthday_chat_parse.CONFIRM_WORDS.
CONFIRM_WORDS = frozenset(('启动', '确认启动', '确认启动生日安排', '开始安排'))

_LINKS_DDL = (
    'CREATE TABLE IF NOT EXISTS birthday_chat_links('
    'user_id TEXT NOT NULL, request_key TEXT NOT NULL, flow_id TEXT NOT NULL, '
    'input_hash TEXT NOT NULL, summary_hash TEXT NOT NULL, created_at REAL NOT NULL, '
    'PRIMARY KEY(user_id,request_key), UNIQUE(user_id,flow_id))')


def _session_required():
    return DomainError('SESSION_REQUIRED', '登录已失效，请重新登录。', 401)


def _superseded():
    return DomainError('REQUEST_SUPERSEDED', '该确认已失效，请重新确认生日安排。', 409)


def _key_conflict():
    return DomainError('KEY_CONFLICT', '请求键冲突', 409)


def _canon(v):
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _real(v):
    return type(v) in (int, float) and math.isfinite(v)


def bootstrap_birthday_chat(bank):
    with bank.transaction() as db:
        db.execute(_LINKS_DDL)


def confirm_birthday(bank, store, user, text, request_key, epoch, session_id,
                     request_order, expected_pending):
    if type(user) is not str or not user or type(session_id) is not str or not session_id:
        raise _session_required()
    if (type(text) is not str or text not in CONFIRM_WORDS or type(epoch) is not str
            or type(request_key) is not str or not 1 <= len(request_key) <= 100
            or type(request_order) is not int or request_order < 1
            or not isinstance(expected_pending, dict)):
        raise _superseded()
    key = derive_chat_key(request_key)
    with bank.transaction() as db:
        s = db.execute('SELECT 1 FROM sessions WHERE token_hash=? AND user_id=? AND expires_at>?',
                       (session_id, user, time.time())).fetchone()
        if not s:
            raise _session_required()
        req = db.execute('SELECT rowid,input,status FROM chat_requests WHERE user_id=? AND request_key=?',
                         (user, request_key)).fetchone()
        if not req or req[0] != request_order or req[1] != text or req[2] != 'PROCESSING':
            raise _superseded()
        ep = db.execute('SELECT epoch FROM chat_request_epochs WHERE user_id=? AND request_key=?',
                        (user, request_key)).fetchone()
        if not ep or ep[0] != epoch or bank._safety_epoch(db, user) != epoch:
            raise _superseded()
        bank._unlocked(db, user)
        bank._assert_transfer_allowed(db, user, epoch)
        latest = db.execute('SELECT MAX(rowid) FROM chat_requests WHERE user_id=?', (user,)).fetchone()[0]
        if latest != request_order:
            raise _superseded()
        prev = db.execute('SELECT MAX(rowid) FROM chat_requests WHERE user_id=? AND rowid<?',
                          (user, request_order)).fetchone()[0]
        row = db.execute('SELECT value FROM contexts WHERE user_id=?', (user,)).fetchone()
        try:
            stored = json.loads(row[0]) if row else None
        except (TypeError, ValueError):
            stored = None
        if not isinstance(stored, dict):
            raise _superseded()
        now_ts = bank.now().timestamp()
        wu = stored.get('working_updated_at')
        if (stored.get('session_id') != session_id or stored.get('safety_epoch') != epoch
                or not _real(wu) or not 0 <= now_ts - wu <= TTL_SECONDS):
            raise _superseded()
        p = validate_birthday_pending(db, user, stored, now_ts, epoch, session_id).get('pending_birthday')
        if (not p or p['stage'] != 'SUMMARIZED' or p['asked'] != 'confirm'
                or _canon(expected_pending) != _canon(p)
                or prev != p['summary_order'] or not request_order > p['summary_order']):
            raise _superseded()
        if db.execute('SELECT 1 FROM birthday_chat_links WHERE user_id=? AND request_key=?',
                      (user, request_key)).fetchone():
            raise _key_conflict()
        if db.execute('SELECT 1 FROM birthday_requests WHERE user_id=? AND request_key=?',
                      (user, key)).fetchone():
            raise _key_conflict()
        flow = store._create_locked(db, user, p['recipient_label'], p['event_date'],
                                    p['account_id'], p['budget_cents'], key)
        db.execute('INSERT INTO birthday_chat_links(user_id,request_key,flow_id,input_hash,summary_hash,created_at)'
                   ' VALUES(?,?,?,?,?,?)',
                   (user, request_key, flow['id'], hashlib.sha256(text.encode('utf-8')).hexdigest(),
                    p['summary_hash'], now_ts))
        stored.pop('pending_birthday', None)
        stored.update(working_updated_at=now_ts, safety_epoch=epoch, session_id=session_id,
                      request_order=request_order)
        db.execute('UPDATE contexts SET value=? WHERE user_id=?',
                   (json.dumps(stored, ensure_ascii=False), user))
        bank._event(db, user, None, 'BIRTHDAY_CHAT_STARTED', '已通过对话确认生日安排 / 本机模拟')
        return flow

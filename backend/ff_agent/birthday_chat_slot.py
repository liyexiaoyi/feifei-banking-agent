"""Validate the working birthday chat slot stored in a Memory context.

Pure read-only check: no DB writes, no cleanup, no financial calls.
Any malformed input yields {} instead of raising.
"""
from copy import deepcopy
from datetime import date
import hashlib
import importlib
import math
import re
import unicodedata

FIELDS = ('stage', 'recipient_label', 'event_date', 'account_id', 'budget_cents', 'asked',
          'session_id', 'epoch', 'started_at', 'origin_order', 'summary_order',
          'summary_hash', 'canonical_intent')
INTENT_FIELDS = ('recipient_label', 'event_date', 'account_id', 'budget_cents')
STAGES = ('COLLECTING', 'SUMMARIZED')
TTL_SECONDS = 600
MAX_BUDGET_CENTS = 100000000
MAX_LABEL = 40
_DATE_RE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
_HEX64_RE = re.compile(r'[0-9a-f]{64}')


def derive_chat_key(rk):
    if type(rk) is not str or not 1 <= len(rk) <= 100:
        raise ValueError('request_key must be a str of length 1..100')
    return 'chat-bday-' + hashlib.sha256(rk.encode('utf-8')).hexdigest()[:48]


def _is_int(v):
    return type(v) is int


def _is_real(v):
    return type(v) in (int, float) and math.isfinite(v)


def _label_ok(v):
    if type(v) is not str or not v.strip() or len(v) > MAX_LABEL:
        return False
    return not any(unicodedata.category(c) in ('Cc', 'Cf', 'Zl', 'Zp') for c in v)


def _date_ok(v):
    if type(v) is not str or not _DATE_RE.fullmatch(v):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


def _budget_ok(v):
    return _is_int(v) and 0 < v <= MAX_BUDGET_CENTS


def _account_owned(db, user, account_id):
    if type(account_id) is not str or not account_id:
        return False
    row = db.execute('SELECT id FROM accounts WHERE id=? AND user_id=?', (account_id, user)).fetchone()
    return row is not None


_CHECKS = {'recipient_label': _label_ok, 'event_date': _date_ok, 'budget_cents': _budget_ok}


def _check(db, user, stored, now_ts, epoch, session_id):
    if type(user) is not str or not user:
        return None
    if type(session_id) is not str or not session_id or type(epoch) is not str:
        return None
    if not _is_real(now_ts) or not isinstance(stored, dict):
        return None
    p = stored.get('pending_birthday')
    if not isinstance(p, dict) or set(p.keys()) != set(FIELDS):
        return None
    if p['stage'] not in STAGES or type(p['stage']) is not str:
        return None
    if type(p['session_id']) is not str or p['session_id'] != session_id:
        return None
    if type(p['epoch']) is not str or p['epoch'] != epoch:
        return None
    started = p['started_at']
    if not _is_real(started) or not 0 <= now_ts - started <= TTL_SECONDS:
        return None
    origin = p['origin_order']
    if not _is_int(origin) or origin < 1:
        return None
    for f, ok in _CHECKS.items():
        if p[f] is not None and not ok(p[f]):
            return None
    if p['account_id'] is not None and not _account_owned(db, user, p['account_id']):
        return None
    missing = [f for f in INTENT_FIELDS if p[f] is None]
    if p['stage'] == 'COLLECTING':
        if p['summary_order'] is not None or p['summary_hash'] is not None or p['canonical_intent'] is not None:
            return None
        if type(p['asked']) is not str or p['asked'] not in missing:
            return None
        return p
    if missing or p['asked'] != 'confirm':
        return None
    order = p['summary_order']
    if not _is_int(order) or order < origin:
        return None
    h = p['summary_hash']
    if type(h) is not str or not _HEX64_RE.fullmatch(h) or type(p['canonical_intent']) is not str:
        return None
    parse = importlib.import_module('ff_agent.birthday_chat_parse')
    canon = parse.canonical_intent(p)
    if type(canon) is not str or p['canonical_intent'] != canon:
        return None
    if h != parse.summary_hash(p):
        return None
    return p


def validate_birthday_pending(db, user, stored, now_ts, epoch, session_id):
    try:
        p = _check(db, user, stored, now_ts, epoch, session_id)
    except Exception:
        return {}
    if p is None:
        return {}
    return {'pending_birthday': deepcopy({f: p[f] for f in FIELDS})}

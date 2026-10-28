"""LOCAL SIMULATED demo card credential routes.

The POST route only prepares a RED operation; confirmation/verification use the
existing common operation endpoints. The body is parsed manually so validation
errors never echo submitted values. Request bodies are never logged.
"""
import json
import re

from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool

from .bank import DomainError, PENDING
from .db import connect

MAX_BODY = 4096
KEY_RE = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
REQUIRED = frozenset(('new_secret', 'expected_version', 'request_key'))
ALLOWED = REQUIRED | {'current_secret'}
MAX_VERSION = 2 ** 31 - 1


def _bad():
    return DomainError('INVALID_REQUEST', '请求内容无效，请检查后重新填写。', 400)


def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError
        out[k] = v
    return out


def _no_constant(_):
    raise ValueError


def _secret_ok(value):
    return (type(value) is str and 8 <= len(value) <= 64
            and all('!' <= ch <= '~' for ch in value))


def _key_ok(value):
    return type(value) is str and KEY_RE.fullmatch(value) is not None


def _reject_query(request):
    if request.query_params:
        raise _bad()


async def _read_body(request):
    ctype = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if ctype != 'application/json':
        raise _bad()
    size = 0
    chunks = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            raise _bad()
        chunks.append(chunk)
    raw = b''.join(chunks)
    try:
        text = raw.decode('utf-8')
        data = json.loads(text, object_pairs_hook=_no_dupes, parse_constant=_no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise _bad() from None
    if type(data) is not dict:
        raise _bad()
    keys = set(data)
    if not REQUIRED <= keys or not keys <= ALLOWED:
        raise _bad()
    new_secret = data['new_secret']
    current_secret = data.get('current_secret')
    expected_version = data['expected_version']
    request_key = data['request_key']
    if not _secret_ok(new_secret):
        raise _bad()
    if current_secret is not None and not _secret_ok(current_secret):
        raise _bad()
    if type(expected_version) is not int or not 0 <= expected_version <= MAX_VERSION:
        raise _bad()
    if not _key_ok(request_key):
        raise _bad()
    return new_secret, current_secret, expected_version, request_key


def register_card_credentials_api(app, bank, current_user, run_tool, record_operation):
    if not all(callable(f) for f in (current_user, run_tool, record_operation)):
        raise TypeError('card credential API requires existing callbacks')
    from .card_credentials import CardCredentials
    from .card_controls import _CARD_TYPES
    from .cards import CardService
    service = CardCredentials(bank)
    cards = CardService(bank)
    lookup_types = frozenset(_CARD_TYPES) | {'card_credential_change'}
    app.state.card_credentials = service

    @app.get('/api/cards/{card_id}/credential')
    def card_credential_view(card_id: str, request: Request, session=Depends(current_user)):
        _reject_query(request)
        return service.get(session['user_id'], card_id)

    @app.post('/api/cards/{card_id}/credential')
    async def card_credential_prepare_route(card_id: str, request: Request, session=Depends(current_user)):
        _reject_query(request)
        user = session['user_id']
        new_secret, current_secret, expected_version, request_key = await _read_body(request)
        def _prepare_and_record():
            # CPU-bound PBKDF2 and SQLite work runs off the event loop thread.
            operation = run_tool(user, 'cards', lambda: service.prepare(
                user, card_id, new_secret, current_secret, expected_version, request_key))
            record_operation(user, operation)
            return operation

        return await run_in_threadpool(_prepare_and_record)

    @app.get('/api/cards/{card_id}/requests/{request_key}')
    def card_request_lookup_route(card_id: str, request_key: str, request: Request, session=Depends(current_user)):
        _reject_query(request)
        if not _key_ok(request_key):
            raise DomainError('NOT_FOUND', '未找到本人可核对的操作记录。', 404)
        user = session['user_id']
        db = connect(bank.path)
        try:
            db.execute('PRAGMA query_only=ON')
            bank._user(db, user)
            cards._card(db, user, card_id)
            marks = ','.join('?' * len(lookup_types))
            rows = db.execute(
                'SELECT * FROM operations WHERE user_id=? AND idempotency_key=? AND type IN (' + marks + ')',
                (user, request_key, *sorted(lookup_types))).fetchall()
            match = None
            for row in rows:
                try:
                    payload = json.loads(row['payload'])
                except (TypeError, ValueError):
                    continue
                if type(payload) is dict and payload.get('card_id') == card_id:
                    match = row
                    break
            if match is None:
                raise DomainError('NOT_FOUND', '未找到本人可核对的操作记录。', 404)
            now = bank.now()
            status = match['status']
            observed = 'EXPIRED' if status in PENDING and match['expires_at'] <= now.timestamp() else status
            return {'operation': bank._view(match), 'recorded_status': status,
                    'observed_status': observed, 'read_only': True, 'simulated': True,
                    'checked_at': now.isoformat()}
        finally:
            db.close()

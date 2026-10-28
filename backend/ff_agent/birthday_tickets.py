"""Birthday capability tickets (Local Simulated, no network, nothing trained)."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
from dataclasses import dataclass, replace as _dc_replace

OP_TYPES = {0: 'birthday_budget_reserve', 4: 'birthday_order', 5: 'birthday_order'}
BUDGET_FIELDS = ('account_id', 'account_name', 'amount_cents', 'event_date', 'risk_epoch', 'simulated')
ORDER_FIELDS = ('budget_id', 'budget_version', 'budget_expires_at', 'account_id', 'account_name', 'gift',
                'item_id', 'item_name', 'quote_version', 'amount_cents', 'event_date', 'risk_epoch',
                'simulated', 'merchant_connected')
TICKET_TTL = 300
PHASES = ('confirm', 'verify', 'execute')
GUARD_STATES = ('RUNNING', 'WAITING_CONFIRMATION', 'WAITING_VERIFICATION')
PREFIX = 'bday-'


@dataclass(frozen=True)
class BirthdayBinding:
    ticket_id: int
    token: str
    effect_key: str


@dataclass(frozen=True)
class ClaimContext:
    flow: dict
    node: dict
    ticket: dict
    intent: dict


def _err(code, msg, status=400):
    from .bank import DomainError
    return DomainError(code, '生日流程暂时无法核对，请查看已有记录。', status=status)


def _canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _ts(v):
    return type(v) in (int, float) and math.isfinite(v)


def _now(bank):
    v = bank.now().timestamp()
    if not _ts(v):
        raise _err('bad_clock', 'invalid clock')
    return float(v)


def _uid(user):
    if type(user) is not str or not user:
        raise _err('bad_user', 'invalid user')
    return user


def _sint(v):
    return type(v) is int


def _tx(db):
    if not db.in_transaction:
        raise _err('tx_required', 'transaction required')


def _row(r):
    return dict(r) if r is not None else None


def _load(db, uid, flow_id, idx):
    from .birthday_store import load_flow, load_nodes
    flow = load_flow(db, uid, flow_id)
    node = load_nodes(db, uid, flow_id).get(idx)
    if flow is None or node is None:
        raise _err('not_found', 'flow or node not found', 404)
    return flow, node


def _ticket(db, uid, binding):
    if type(binding) is not BirthdayBinding or not _sint(binding.ticket_id) \
            or type(binding.token) is not str or type(binding.effect_key) is not str:
        raise _err('bad_binding', 'invalid binding')
    t = _row(db.execute('SELECT * FROM birthday_tickets WHERE id=? AND user_id=?',
                        (binding.ticket_id, uid)).fetchone())
    if t is None or not hmac.compare_digest(_sha(binding.token), t['token_hash']) \
            or not hmac.compare_digest(binding.effect_key, t['effect_key']):
        raise _err('bad_binding', 'invalid binding')
    return t


def issue_ticket(bank, db, user, flow_id, idx):
    _tx(db)
    from .birthday_provenance import make_intent
    uid = _uid(user)
    if not _sint(idx) or idx not in OP_TYPES or type(flow_id) is not str:
        raise _err('bad_node', 'not a financial node')
    flow, node = _load(db, uid, flow_id, idx)
    cv = node['claim_version']
    if flow['status'] != 'ACTIVE' or node['state'] != 'RUNNING' or node['operation_id'] is not None \
            or not _sint(cv) or cv < 1:
        raise _err('not_claimed', 'node not claimed')
    if db.execute('SELECT 1 FROM birthday_tickets WHERE user_id=? AND flow_id=? AND node_idx=?',
                  (uid, flow_id, idx)).fetchone():
        raise _err('ticket_exists', 'ticket already issued')
    key = '%s%s-%d-%d' % (PREFIX, flow_id, idx, cv)
    if len(key) > 120:
        raise _err('bad_key', 'effect key too long')
    if db.execute('SELECT 1 FROM operations WHERE idempotency_key=?', (key,)).fetchone():
        raise _err('key_taken', 'effect key already used')
    intent = make_intent(bank, db, uid, flow, node)
    if type(intent) is not dict or intent.get('flow_id') != flow_id or intent.get('node_idx') != idx \
            or intent.get('claim_version') != cv:
        raise _err('bad_intent', 'intent mismatch')
    now = _now(bank)
    exp = now + TICKET_TTL
    for f in ('quote_expires_at', 'budget_expires_at'):
        if f in intent:
            if not _ts(intent[f]) or intent[f] <= now:
                raise _err('expired', 'source expired')
            exp = min(exp, intent[f])
    token = secrets.token_hex(32)
    cur = db.execute(
        'INSERT INTO birthday_tickets (user_id,flow_id,node_idx,claim_version,token_hash,effect_key,'
        'op_type,intents_json,payload_hash,operation_id,created_at,expires_at) '
        'VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?)',
        (uid, flow_id, idx, cv, _sha(token), key, OP_TYPES[idx], _canon(intent), now, exp))
    return BirthdayBinding(ticket_id=cur.lastrowid, token=token, effect_key=key)


def assert_claim(bank, db, user, binding, key, kind):
    _tx(db)
    from .birthday_provenance import check_intent
    uid = _uid(user)
    t = _ticket(db, uid, binding)
    if type(key) is not str or key != t['effect_key'] or type(kind) is not str or kind != t['op_type'] \
            or OP_TYPES.get(t['node_idx']) != kind:
        raise _err('bad_binding', 'key or kind mismatch')
    if t['operation_id'] is not None or not _ts(t['expires_at']) or t['expires_at'] <= _now(bank):
        raise _err('ticket_unusable', 'ticket bound or expired')
    if db.execute('SELECT 1 FROM operations WHERE idempotency_key=?', (key,)).fetchone():
        raise _err('key_taken', 'effect key already used')
    flow, node = _load(db, uid, t['flow_id'], t['node_idx'])
    if flow['status'] != 'ACTIVE' or node['state'] != 'RUNNING' or node['operation_id'] is not None \
            or node['claim_version'] != t['claim_version']:
        raise _err('claim_stale', 'claim no longer valid')
    intent = check_intent(bank, db, uid, flow, node, t, None, current=True)
    return {'flow': flow, 'node': node, 'ticket': t, 'intent': intent}


def _expected_payload(intent, kind):
    if kind == 'birthday_budget_reserve':
        p = {f: intent[f] for f in BUDGET_FIELDS if f != 'simulated'}
        p['simulated'] = True
    elif kind == 'birthday_order':
        p = {f: intent[f] for f in ORDER_FIELDS
             if f not in ('amount_cents', 'simulated', 'merchant_connected')}
        p['amount_cents'] = intent['price_cents']
        p['simulated'] = True
        p['merchant_connected'] = False
    else:
        raise KeyError(kind)
    return p


def before_create(bank, db, user, kind, payload, key, binding):
    if type(key) is not str or not key.startswith(PREFIX) or type(binding) is not BirthdayBinding:
        raise _err('bad_binding', 'birthday binding required')
    ctx = assert_claim(bank, db, user, binding, key, kind)
    if type(payload) is not dict:
        raise _err('bad_payload', 'payload must be object')
    try:
        expected = _expected_payload(ctx['intent'], kind)
    except KeyError:
        raise _err('bad_intent', 'intent incomplete')
    if set(payload) != set(expected):
        raise _err('bad_payload', 'payload fields mismatch')
    for k in ('amount_cents', 'budget_version', 'quote_version'):
        if k in payload and not _sint(payload[k]):
            raise _err('bad_payload', 'strict integer required')
    if payload['amount_cents'] <= 0 or payload['simulated'] is not True \
            or ('merchant_connected' in payload and payload['merchant_connected'] is not False):
        raise _err('bad_payload', 'invalid amount or flags')
    if 'budget_expires_at' in payload and not _ts(payload['budget_expires_at']):
        raise _err('bad_payload', 'invalid timestamp')
    if json.dumps(payload, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise _err('payload_mismatch', 'payload differs from source')
    h = _sha(_canon(payload))
    _tx(db)
    cur = db.execute('UPDATE birthday_tickets SET payload_hash=? WHERE id=? AND user_id=? AND '
                     'operation_id IS NULL AND (payload_hash IS NULL OR payload_hash=?)',
                     (h, ctx['ticket']['id'], _uid(user), h))
    if cur.rowcount != 1:
        raise _err('ticket_conflict', 'ticket payload conflict')


def bind_created(bank, db, user, binding, opid):
    _tx(db)
    from .birthday_provenance import check_intent
    uid = _uid(user)
    t = _ticket(db, uid, binding)
    if type(opid) is not str or not opid or t['operation_id'] is not None or t['payload_hash'] is None \
            or not _ts(t['expires_at']) or t['expires_at'] <= _now(bank):
        raise _err('ticket_unusable', 'ticket not bindable')
    op = _row(db.execute('SELECT * FROM operations WHERE id=? AND user_id=?', (opid, uid)).fetchone())
    if op is None or op['idempotency_key'] != t['effect_key'] or op['type'] != t['op_type'] \
            or _sha(_canon(json.loads(op['payload']))) != t['payload_hash']:
        raise _err('op_mismatch', 'operation does not match ticket')
    if db.execute('SELECT 1 FROM birthday_tickets WHERE operation_id=?', (opid,)).fetchone() or \
            db.execute('SELECT 1 FROM birthday_nodes WHERE operation_id=?', (opid,)).fetchone():
        raise _err('op_bound', 'operation already bound')
    flow, node = _load(db, uid, t['flow_id'], t['node_idx'])
    if flow['status'] != 'ACTIVE' or node['state'] != 'RUNNING' or node['operation_id'] is not None \
            or node['claim_version'] != t['claim_version']:
        raise _err('claim_stale', 'claim no longer valid')
    intent = check_intent(bank, db, uid, flow, node, dict(t, operation_id=opid), op, current=True)
    exp = t['expires_at']
    for f in ('quote_expires_at', 'budget_expires_at'):
        if f in intent:
            if not _ts(intent[f]):
                raise _err('expired', 'source expired')
            exp = min(exp, intent[f])
    if op['expires_at'] is not None:
        if not _ts(op['expires_at']):
            raise _err('op_mismatch', 'invalid operation expiry')
        exp = min(exp, op['expires_at'])
    db.execute('UPDATE operations SET expires_at=? WHERE id=? AND user_id=?', (exp, opid, uid))
    c1 = db.execute('UPDATE birthday_tickets SET operation_id=? WHERE id=? AND user_id=? AND '
                    'operation_id IS NULL', (opid, t['id'], uid))
    c2 = db.execute("UPDATE birthday_nodes SET operation_id=?, state='WAITING_CONFIRMATION', "
                    "version=version+1 WHERE flow_id=? AND idx=? AND user_id=? AND version=? AND "
                    "state='RUNNING' AND operation_id IS NULL AND claim_version=?",
                    (opid, t['flow_id'], t['node_idx'], uid, node['version'], t['claim_version']))
    if c1.rowcount != 1 or c2.rowcount != 1:
        raise _err('bind_conflict', 'concurrent bind')


def guard_effect(bank, db, user, row, phase):
    from .birthday_provenance import check_intent
    uid = _uid(user)
    if phase not in PHASES:
        raise _err('bad_phase', 'invalid phase')
    row = _row(row)
    key = row.get('idempotency_key') or ''
    t = _row(db.execute('SELECT * FROM birthday_tickets WHERE operation_id=?', (row['id'],)).fetchone())
    if t is None and not str(key).startswith(PREFIX):
        return None
    _tx(db)
    if t is None or t['user_id'] != uid:
        raise _err('unbound_birthday_op', 'birthday operation not bound')
    op = _row(db.execute('SELECT * FROM operations WHERE id=? AND user_id=?', (row['id'], uid)).fetchone())
    if op is None or any(op[f] != row.get(f) for f in ('payload', 'type', 'idempotency_key')):
        raise _err('op_changed', 'operation changed')
    if op['idempotency_key'] != t['effect_key'] or op['type'] != t['op_type'] \
            or _sha(_canon(json.loads(op['payload']))) != t['payload_hash']:
        raise _err('op_mismatch', 'operation does not match ticket')
    flow, node = _load(db, uid, t['flow_id'], t['node_idx'])
    if flow['status'] != 'ACTIVE' or node['operation_id'] != op['id'] \
            or node['claim_version'] != t['claim_version'] or node['state'] not in GUARD_STATES:
        raise _err('effect_blocked', 'node not executable')
    if op['expires_at'] is not None and (not _ts(op['expires_at']) or op['expires_at'] <= _now(bank)):
        raise _err('expired', 'operation expired')
    check_intent(bank, db, uid, flow, node, t, op, current=True)
    return None


def observe_node(bank, db, user, flow, node):
    from .birthday_provenance import observe_node as _observe
    return _observe(bank, db, user, flow, node)

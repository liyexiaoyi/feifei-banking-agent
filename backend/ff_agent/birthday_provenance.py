"""Immutable provenance proofs for birthday financial nodes (local simulation only).

Pure reads: never writes, never calls birthday_tickets.observe_node, never resends.
"""
import hashlib
import json
import math
from datetime import date

from .bank import DomainError

FIN = {0: 'budget', 4: 'order', 5: 'order'}
SELECT = {4: 2, 5: 3}
ROOT = ('flow_id', 'node_idx', 'claim_version', 'recipient_label', 'event_date',
        'account_id', 'account_name', 'budget_cents', 'risk_epoch')
BUDGET_X = ('amount_cents',)
ORDER_X = ('gift', 'item_id', 'item_name', 'quote_version', 'price_cents',
           'quote_expires_at', 'budget_id', 'budget_version', 'budget_expires_at')
BUDGET_P = ('account_id', 'account_name', 'amount_cents', 'event_date', 'risk_epoch', 'simulated')
ORDER_P = ('budget_id', 'budget_version', 'budget_expires_at', 'account_id', 'account_name', 'gift',
           'item_id', 'item_name', 'quote_version', 'amount_cents', 'event_date', 'risk_epoch',
           'simulated', 'merchant_connected')
OPTYPE = {0: 'birthday_budget_reserve', 4: 'birthday_order', 5: 'birthday_order'}
GIFT = {4: 'flowers', 5: 'cake'}
UNRESOLVED = {'UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW'}
MAP = {'WAITING_CONFIRMATION': 'WAITING_CONFIRMATION', 'WAITING_VERIFICATION': 'WAITING_VERIFICATION',
       'READY': 'WAITING_VERIFICATION', 'CANCELLED': 'CANCELLED', 'EXPIRED': 'FAILED',
       'FAILED': 'FAILED', 'REJECTED': 'FAILED'}


def _bad(msg, code='PROVENANCE_INVALID'):
    raise DomainError(code, '生日流程暂时无法核对，请查看已有记录。', 400)


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode('utf-8')).hexdigest()


def _now(bank):
    return _ts(bank.now().timestamp())


def _ts(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        _bad('bad timestamp')
    return float(value)


def _date_ok(bank, value):
    if not isinstance(value, str):
        _bad('bad event date')
    try:
        d = date.fromisoformat(value)
    except ValueError:
        _bad('bad event date')
    if d < bank.now().date():
        _bad('event date passed')


def _int(v, lo=0):
    if type(v) is not int or v < lo:
        _bad('strict integer required')
    return v


def _eq(a, b, what):
    if type(a) is not type(b) or a != b:
        _bad(what + ' mismatch')


def _row(row):
    return dict(row) if row is not None else None


def _json(text):
    if not isinstance(text, str):
        _bad('stored json missing')
    try:
        val = json.loads(text)
    except ValueError:
        _bad('stored json malformed')
    if not isinstance(val, dict):
        _bad('stored json not object')
    return val


def _graph_ok(flow):
    from . import birthday_store
    if canonical(json.loads(flow['graph_json'])) != canonical(birthday_store.birthday_graph()):
        _bad('graph mismatch')


def _op(db, user, opid):
    r = db.execute('SELECT * FROM operations WHERE id=? AND user_id=?', (opid, user)).fetchone()
    if r is None:
        _bad('operation missing')
    return _row(r)


def _budget_proof(bank, db, user, flow, nodes):
    """Actual SUCCEEDED budget origin for node 0; returns budget row."""
    n0 = nodes.get(0)
    if not n0 or not n0['operation_id']:
        _bad('parent budget not confirmed')
    if observe_node(bank, db, user, flow, n0) != 'SUCCEEDED':
        _bad('parent budget not confirmed')
    op = _op(db, user, n0['operation_id'])
    if op['status'] != 'SUCCEEDED' or op['type'] != OPTYPE[0]:
        _bad('parent budget op not succeeded')
    bid = _json(op['result']).get('budget_id')
    if not isinstance(bid, str) or not bid:
        _bad('budget id missing')
    b = _row(db.execute('SELECT * FROM birthday_budgets WHERE id=? AND user_id=?', (bid, user)).fetchone())
    if b is None or b['origin_operation_id'] != op['id']:
        _bad('budget origin missing')
    for k, fk in (('account_id', 'account_id'), ('amount_cents', 'budget_cents'),
                  ('event_date', 'event_date'), ('risk_epoch', 'risk_epoch')):
        _eq(b[k], flow[fk], 'budget ' + k)
    _int(b['version'], 1)
    return b


def _selection(db, user, flow, nodes, idx):
    from .birthday_orders import CATALOG
    sel = nodes.get(SELECT[idx])
    if not sel or sel['state'] != 'SUCCEEDED':
        _bad('catalog selection incomplete')
    item = CATALOG.get(sel['item_id'])
    if not isinstance(item, dict) or item.get('gift') != GIFT[idx]:
        _bad('catalog item unknown')
    _int(sel['price_cents'], 1)
    _int(sel['quote_version'], 1)
    _eq(sel['price_cents'], item['price_cents'], 'price')
    _eq(sel['quote_version'], item['quote_version'], 'quote version')
    _ts(sel['quote_expires_at'])
    return sel, item


def _base(flow, node):
    idx = node['idx']
    if type(idx) is not int or idx not in FIN:
        _bad('not a financial node')
    if flow['user_id'] != node['user_id'] or flow['id'] != node['flow_id']:
        _bad('node ownership')
    _int(node['claim_version'], 1)
    _graph_ok(flow)
    return idx


def make_intent(bank, db, user, flow, node):
    """Live server-side snapshot for a claimed RUNNING financial node."""
    from .birthday_store import load_nodes
    idx = _base(flow, node)
    if flow['status'] != 'ACTIVE' or node['state'] != 'RUNNING':
        _bad('node not claimed', 'NOT_CLAIMED')
    _eq(flow['risk_epoch'], bank._safety_epoch(db, user), 'risk epoch')
    acct = db.execute('SELECT name FROM accounts WHERE id=? AND user_id=?',
                      (flow['account_id'], user)).fetchone()
    if acct is None:
        _bad('account missing')
    _eq(acct['name'], flow['account_name'], 'account name')
    now = _now(bank)
    _date_ok(bank, flow['event_date'])
    it = {k: flow[k] for k in ROOT[3:]}
    it.update(flow_id=flow['id'], node_idx=idx, claim_version=node['claim_version'])
    if FIN[idx] == 'budget':
        it['amount_cents'] = _int(flow['budget_cents'], 1)
        return it
    nodes = load_nodes(db, user, flow['id'])
    b = _budget_proof(bank, db, user, flow, nodes)
    if b['status'] != 'ACTIVE' or _ts(b['expires_at']) <= now:
        _bad('parent budget inactive')
    sel, item = _selection(db, user, flow, nodes, idx)
    qe = _ts(sel['quote_expires_at'])
    if not (now < qe <= now + 900.0):
        _bad('quote expired')
    it.update(gift=item['gift'], item_id=sel['item_id'], item_name=item['name'],
              quote_version=sel['quote_version'], price_cents=sel['price_cents'],
              quote_expires_at=sel['quote_expires_at'], budget_id=b['id'],
              budget_version=_int(b['version'], 1), budget_expires_at=b['expires_at'])
    return it


def _payload(intent, kind):
    p = {'account_id': intent['account_id'], 'account_name': intent['account_name'],
         'event_date': intent['event_date'], 'risk_epoch': intent['risk_epoch'], 'simulated': True}
    if kind == 'budget':
        p['amount_cents'] = intent['amount_cents']
        return p
    p.update(amount_cents=intent['price_cents'], merchant_connected=False,
             **{k: intent[k] for k in ('budget_id', 'budget_version', 'budget_expires_at', 'gift',
                                       'item_id', 'item_name', 'quote_version')})
    return p


def check_intent(bank, db, user, flow, node, ticket, op=None, current=False):
    """Verify immutable ticket intent; current=True re-checks live conditions."""
    idx = _base(flow, node)
    kind = FIN[idx]
    t = _row(ticket) if not isinstance(ticket, dict) else ticket
    for k, v in (('user_id', user), ('flow_id', flow['id']), ('node_idx', idx),
                 ('claim_version', node['claim_version'])):
        _eq(t[k], v, 'ticket ' + k)
    _eq(t['effect_key'], 'bday-%s-%s-%s' % (flow['id'], idx, node['claim_version']), 'effect key')
    _eq(t['op_type'], OPTYPE[idx], 'ticket op type')
    it = _json(t['intents_json'])
    keys = ROOT + (BUDGET_X if kind == 'budget' else ORDER_X)
    if set(it) != set(keys):
        _bad('intent schema')
    for k in ROOT[3:]:
        _eq(it[k], flow[k], 'intent ' + k)
    _eq(it['flow_id'], flow['id'], 'intent flow')
    _eq(it['node_idx'], idx, 'intent idx')
    _eq(it['claim_version'], node['claim_version'], 'intent claim')
    if kind == 'budget':
        _int(it['amount_cents'], 1)
        _eq(it['amount_cents'], flow['budget_cents'], 'budget amount')
    else:
        from .birthday_store import load_nodes
        nodes = load_nodes(db, user, flow['id'])
        b = _budget_proof(bank, db, user, flow, nodes)
        _eq(it['budget_id'], b['id'], 'budget id')
        _int(it['budget_version'], 1)
        _ts(it['budget_expires_at'])
        _eq(it['gift'], GIFT[idx], 'gift')
        sel = nodes.get(SELECT[idx]) or _bad('selection missing')
        for k in ('item_id', 'quote_version', 'price_cents', 'quote_expires_at'):
            _eq(it[k], sel[k], 'selection ' + k)
        _int(it['price_cents'], 1)
        _int(it['quote_version'], 1)
    o = None
    if op is not None:
        o = _row(op) if not isinstance(op, dict) else op
        _eq(o['user_id'], user, 'op owner')
        _eq(o['idempotency_key'], t['effect_key'], 'op key')
        _eq(o['type'], OPTYPE[idx], 'op type')
        if t['operation_id'] is None:
            _bad('op binding')
        _eq(o['id'], t['operation_id'], 'op binding')
        p = _json(o['payload'])
        want = _payload(it, kind)
        if set(p) != set(BUDGET_P if kind == 'budget' else ORDER_P) or canonical(p) != canonical(want):
            _bad('payload mismatch')
        if p['simulated'] is not True or p.get('merchant_connected', False) is not False:
            _bad('simulation flags')
        _int(p['amount_cents'], 1)
        if (o['payload'] != canonical(p) or not isinstance(t['payload_hash'], str)
                or t['payload_hash'] != digest(p)):
            _bad('payload hash')
    if current:
        now = _now(bank)
        if flow['status'] != 'ACTIVE':
            _bad('flow not active', 'FLOW_INACTIVE')
        _date_ok(bank, it['event_date'])
        if o is not None and 'expires_at' in o and o['expires_at'] is not None and _ts(o['expires_at']) <= now:
            _bad('operation expired', 'TICKET_EXPIRED')
        _eq(it['risk_epoch'], bank._safety_epoch(db, user), 'risk epoch')
        acct = db.execute('SELECT name FROM accounts WHERE id=? AND user_id=?',
                          (flow['account_id'], user)).fetchone()
        if acct is None or acct['name'] != it['account_name']:
            _bad('account name changed')
        if _ts(t['expires_at']) <= now:
            _bad('ticket expired', 'TICKET_EXPIRED')
        if kind == 'order':
            from .birthday_orders import CATALOG
            item = CATALOG.get(it['item_id']) or {}
            if item.get('price_cents') != it['price_cents'] or item.get('quote_version') != it['quote_version']:
                _bad('quote changed')
            if _ts(it['quote_expires_at']) <= now:
                _bad('quote expired')
            b = _row(db.execute('SELECT * FROM birthday_budgets WHERE id=? AND user_id=?',
                                (it['budget_id'], user)).fetchone())
            if (b is None or b['status'] != 'ACTIVE' or type(b['version']) is not int
                    or b['version'] != it['budget_version'] or _ts(b['expires_at']) <= now
                    or _ts(b['expires_at']) != _ts(it['budget_expires_at'])):
                _bad('parent budget changed')
    return it


def _succeeded(db, user, it, op, kind):
    res = _json(op['result'])
    if res.get('simulated') is not True or op['confirmed_at'] is None:
        return False
    if kind == 'budget':
        b = _row(db.execute('SELECT * FROM birthday_budgets WHERE id=? AND user_id=?',
                            (res.get('budget_id'), user)).fetchone())
        return bool(b and b['origin_operation_id'] == op['id'] and b['account_id'] == it['account_id']
                    and b['amount_cents'] == it['amount_cents'] and b['event_date'] == it['event_date']
                    and b['risk_epoch'] == it['risk_epoch'])
    led = _row(db.execute('SELECT * FROM birthday_order_ledger WHERE id=? AND user_id=? AND child_operation_id=?',
                          (res.get('order_id'), user, op['id'])).fetchone())
    tx = _row(db.execute('SELECT * FROM transactions WHERE id=? AND user_id=? AND operation_id=?',
                         (res.get('transaction_id'), user, op['id'])).fetchone())
    if not led or not tx:
        return False
    c = it['price_cents']
    if type(c) is not int or c <= 0 or tx['kind'] != 'EXPENSE' or op['confirmed_at'] is None:
        return False
    return (type(res.get('amount_cents')) is int and res.get('amount_cents') == c
            and type(led['amount_cents']) is int and led['amount_cents'] == c
            and type(tx['amount_cents']) is int and tx['amount_cents'] == c
            and led['transaction_id'] == tx['id'] and led['budget_id'] == it['budget_id']
            and led['account_id'] == it['account_id'] == tx['account_id'] and led['gift'] == it['gift']
            and led['item_id'] == it['item_id'] and led['quote_version'] == it['quote_version'])


def observe_node(bank, db, user, flow, node):
    """Pure read NodeState; any malformed or missing proof yields UNKNOWN."""
    if node['state'] == 'UNKNOWN':
        return 'UNKNOWN'
    if node['idx'] not in FIN:
        return node['state']
    try:
        opid = node['operation_id']
        if not opid:
            if node['state'] in ('PENDING', 'CANCELLED', 'FAILED'):
                return node['state']
            if node['state'] == 'SKIPPED' and node['idx'] == 5:
                from .birthday_store import load_nodes
                from .branch_plan import birthday_graph, evaluate_graph
                c = flow['cake_choice']
                nodes = load_nodes(db, user, flow['id'])
                _graph_ok(flow)
                if (type(c) is int and c == 0
                        and nodes[1]['state'] == 'SUCCEEDED'
                        and observe_node(bank, db, user, flow, nodes[0]) == 'SUCCEEDED'):
                    states = {i: n['state'] for i, n in nodes.items()}
                    states[5] = 'PENDING'
                    if 5 in evaluate_graph(birthday_graph(), states, {1: False})['skipped']:
                        return 'SKIPPED'
            return 'UNKNOWN'
        t = _row(db.execute('SELECT * FROM birthday_tickets WHERE user_id=? AND flow_id=? AND node_idx=?'
                            ' AND operation_id=?', (user, flow['id'], node['idx'], opid)).fetchone())
        if t is None:
            return 'UNKNOWN'
        op = _op(db, user, opid)
        it = check_intent(bank, db, user, flow, node, t, op, current=False)
        st = op['status']
        if st in UNRESOLVED:
            return 'UNKNOWN'
        if st == 'SUCCEEDED':
            return 'SUCCEEDED' if _succeeded(db, user, it, op, FIN[node['idx']]) else 'UNKNOWN'
        return MAP.get(st, 'UNKNOWN')
    except Exception:
        return 'UNKNOWN'

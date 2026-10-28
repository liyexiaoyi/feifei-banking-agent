"""Pure authoritative operation refs for conversation goals (read-only, caller transaction)."""
import json

MAX_STEPS = 64
MAX_IDS = 64
MAX_GOALS = 64
SUB_ACTIONS = ('subscription_query', 'subscription_cancel_prepare')
EXPECTED_TYPE = {'subscription_query': ('subscription_query',),
                 'subscription_cancel_prepare': ('subscription_cancel',)}
BDAY_FIN = (0, 4, 5)
UNRESOLVED = ('UNKNOWN', 'EXECUTING')
TERMINAL = ('FAILED', 'CANCELLED', 'EXPIRED', 'REJECTED')
PENDING = ('WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY')


def _has_table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _cols(db, name):
    return {r[1] for r in db.execute('PRAGMA table_info(%s)' % name)}


def _now_ts(bank):
    t = bank.now()
    return float(t.timestamp()) if hasattr(t, 'timestamp') else float(t)


def _get(row, key, default=None):
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _json(text):
    try:
        v = json.loads(text) if isinstance(text, str) else None
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def _classify(bank, db, op, state):
    """Fold one bound operation's status into state; returns False if unresolved."""
    st = op['status']
    if st in UNRESOLVED:
        state['unknown'] = True
        return False
    if st in TERMINAL:
        state['terminal'] = True
        return True
    if st == 'SUCCEEDED':
        return True
    if st not in PENDING:
        state['unknown'] = True  # fail closed: unrecognized status (e.g. MANUAL_REVIEW)
        return False
    try:
        if float(op['expires_at']) <= _now_ts(bank):
            state['terminal'] = True  # expired while pending; bookkeeping left to Bank
            return True
    except (TypeError, ValueError):
        state['unknown'] = True
        return False
    if state['vc'] and db.execute(
            "SELECT 1 FROM verification_challenges WHERE opid=? AND user_id=? AND status='UNKNOWN' LIMIT 1",
            (op['id'], op['user_id'])).fetchone():
        state['unknown'] = True
    return True


def _op(db, user, opid):
    if not isinstance(opid, str) or not 1 <= len(opid) <= 128:
        return None
    return db.execute('SELECT * FROM operations WHERE id=? AND user_id=?', (opid, user)).fetchone()


def _subs(bank, db, user, step, res, state):
    ids = res.get('operation_ids')
    if not isinstance(ids, list):
        state['unknown'] = True
        return
    for opid in ids[:MAX_IDS]:
        op = _op(db, user, opid)
        if (op is None or op['idempotency_key'] != step['effect_key']
                or op['type'] not in EXPECTED_TYPE[step['action']]):
            state['unknown'] = True  # forged/unbound: never adopt the id
            continue
        state['ids'].add(op['id'])
        _classify(bank, db, op, state)


def _birthday(bank, db, user, step, fid, state):
    if not isinstance(fid, str) or not _has_table(db, 'birthday_flows'):
        state['unknown'] = True
        return
    try:
        from .birthday_store import load_flow, load_nodes
        from .birthday_provenance import observe_node
        flow = load_flow(db, user, fid)
        if flow['create_key'] != step['effect_key'] or flow['user_id'] != user:
            state['unknown'] = True
            return
        nodes = load_nodes(db, user, fid)
    except Exception:
        state['unknown'] = True  # missing flow after reference
        return
    for idx in BDAY_FIN:
        node = nodes.get(idx)
        if node is None or not node['operation_id']:
            continue
        try:
            obs = observe_node(bank, db, user, flow, node)
        except Exception:
            obs = 'UNKNOWN'
        t = db.execute('SELECT op_type FROM birthday_tickets WHERE user_id=? AND flow_id=? AND node_idx=?'
                       ' AND operation_id=?', (user, fid, idx, node['operation_id'])).fetchone()
        op = _op(db, user, node['operation_id'])
        if t is not None and op is not None and op['type'] == t['op_type']:
            state['ids'].add(op['id'])
            _classify(bank, db, op, state)
        else:
            state['unknown'] = True
            continue
        if obs == 'UNKNOWN':
            state['unknown'] = True
        elif obs in TERMINAL:
            state['terminal'] = True


def inspect_goal(bank, db, goal_row):
    user = goal_row['user_id']
    state = {'ids': set(), 'unknown': goal_row['status'] == 'UNKNOWN', 'terminal': False,
             'vc': _has_table(db, 'verification_challenges')
             and {'opid', 'user_id', 'status'} <= _cols(db, 'verification_challenges')}
    steps = db.execute('SELECT action,effect_key,state,result FROM conversation_goal_steps WHERE goal_id=?'
                       ' ORDER BY seq LIMIT ?', (goal_row['id'], MAX_STEPS + 1)).fetchall()
    if len(steps) > MAX_STEPS:
        state['unknown'] = True
    for step in steps[:MAX_STEPS]:
        if step['state'] != 'OBSERVED':
            state['unknown'] = True  # STARTED/UNKNOWN; keep earlier known refs
            continue
        res = _json(step['result'])
        if step['action'] in SUB_ACTIONS:
            if res is None:
                state['unknown'] = True
            elif 'refused' not in res:
                _subs(bank, db, user, step, res, state)
        elif step['action'] == 'birthday_create':
            if res is None:
                state['unknown'] = True
            elif res.get('flow_id') is not None:
                _birthday(bank, db, user, step, res.get('flow_id'), state)
    return {'operation_ids': sorted(state['ids']), 'unknown': bool(state['unknown']),
            'terminal': bool(state['terminal'])}


def _owned_goals(db, user):
    """Yield ALL owned open/saved goals via keyset batches (bounded memory, no truncation)."""
    last = ''
    while True:
        batch = db.execute("SELECT * FROM conversation_goals WHERE user_id=? AND status IN ('ACTIVE','UNKNOWN')"
                           ' AND id>? ORDER BY id LIMIT ?', (user, last, MAX_GOALS)).fetchall()
        if not batch:
            return
        for g in batch:
            yield g
        last = batch[-1]['id']


def guard_goal_operation(bank, db, user, oprow, purpose):
    """Refuse confirm/verify of an op bound to a paused/unknown/drifted goal. No writes."""
    if not _has_table(db, 'conversation_goals') or not _has_table(db, 'conversation_goal_steps'):
        return
    opid = oprow['id']
    live = None
    for g in _owned_goals(db, user):
        f = inspect_goal(bank, db, g)
        if opid not in f['operation_ids']:
            continue
        from .bank import DomainError  # lazy: avoid bank import cycle
        if int(_get(g, 'paused', 0) or 0) == 1:
            raise DomainError('GOAL_PAUSED', '目标已暂停，恢复后才能继续确认。', 409)
        if g['status'] == 'UNKNOWN' or f['unknown']:
            raise DomainError('GOAL_UNKNOWN', '目标状态待核对，暂不能继续确认。', 409)
        if live is None:
            live = bank._safety_epoch(db, user)
        if g['epoch'] != live:
            raise DomainError('RISK_CHANGED', '风险状态已变化，请重新提出需求。', 409)

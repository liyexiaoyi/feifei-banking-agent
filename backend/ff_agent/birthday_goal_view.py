"""Pure birthday goal helpers: explicit slot patching, budget-filtered catalog, public goal view.

No writes, no service effects. Financial facts come only from supplied operations/flow.
"""
import re

from ff_agent.birthday_chat_fields import AccountOption, extract_birthday_fields

__all__ = ['extract_slots', 'catalog_choices', 'render_goal']

_WAIT = ('WAITING_CONFIRMATION', 'WAITING_VERIFICATION')
_TERMINAL = ('CANCELLED', 'FAILED', 'EXPIRED')
_STEP_STATES = {'PENDING', 'READY', 'RUNNING', 'OBSERVED', 'WAITING_CONFIRMATION', 'WAITING_VERIFICATION',
                'SUCCEEDED', 'FAILED', 'SKIPPED', 'PAUSED', 'CANCELLED', 'EXPIRED', 'BLOCKED', 'UNKNOWN'}
_LABELS = {'birthday_catalog': '读取花束目录', 'birthday_accounts': '读取账户', 'birthday_choice': '准备选项',
           'birthday_budget_reserve': '预留预算', 'birthday_order': '模拟下单'}
_CAKE_NO = re.compile(r'不要蛋糕|不需要蛋糕|不用蛋糕|别要蛋糕|只要花')
_CAKE_YES = re.compile(r'要蛋糕|加蛋糕|需要蛋糕|订蛋糕|买蛋糕')


def _now(bank):
    n = bank.now
    return n() if callable(n) else n


def _rows(bank, sql, args):
    # Thin integration with this repository's actual Bank transaction contract.
    with bank.transaction() as db:
        return [dict(row) for row in db.execute(sql, args).fetchall()]


def _mask(row):
    m = row.get('masked_number')
    if isinstance(m, str) and m:
        return m
    n = str(row.get('number') or '')
    return ('****' + n[-4:]) if n else ''


def _owned(bank, user):
    rows = _rows(bank, 'SELECT * FROM accounts WHERE user_id=? ORDER BY id', (user,))
    return tuple(AccountOption(str(r['id']), str(r.get('name') or ''), _mask(r)) for r in rows)


def _cake(text):
    t = text if isinstance(text, str) else ''
    no = bool(_CAKE_NO.search(t))
    yes = bool(_CAKE_YES.search(_CAKE_NO.sub('', t)))
    if no and yes:
        return 'conflict'
    return False if no else (True if yes else None)


def extract_slots(bank, user, text, slots):
    """Return a new slot dict: existing slots plus explicit fields from the current message only."""
    out = dict(slots or {})
    owned = _owned(bank, user)
    patch = extract_birthday_fields(text, {'asked': out.get('asked')}, owned, _now(bank))
    for k, v in patch.items():
        if v is not None:
            out[k] = v
        else:
            # Explicit None (e.g. ambiguous correction) clears the old value.
            out.pop(k, None)
            if k == 'account_id':
                out.pop('account_masked', None)
    acc = out.get('account_id')
    if 'account_id' in patch and acc is not None:
        match = [a for a in owned if a.id == acc]
        if match:
            out['account_masked'] = match[0].masked_number
        else:
            out.pop('account_id', None)
    c = _cake(text)
    if c == 'conflict':
        out['asked'] = 'cake'
    elif c is not None:
        out['cake'] = c
    return out


def _service(bank):
    s = getattr(bank, 'birthday', None)
    if s is not None and callable(getattr(s, 'catalog', None)):
        return s
    from ff_agent.birthday_service import BirthdayService
    return BirthdayService(bank)


def _cents(v):
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def catalog_choices(bank, budget_cents):
    if not _cents(budget_cents):
        return []
    out = []
    for item in _service(bank).catalog('flowers'):
        price = item.get('price_cents')
        if _cents(price) and price <= budget_cents:
            out.append({'id': item['item_id'], 'label': item['name'], 'price_cents': price,
                        'quote_version': item['quote_version']})
    return out


def _money(c):
    return f'{c // 100:,}.{c % 100:02d} 元'


def _summary(slots):
    s, out = slots or {}, []
    if isinstance(s.get('recipient_label'), str):
        out.append('收礼人：' + s['recipient_label'])
    if isinstance(s.get('event_date'), str):
        out.append('日期：' + s['event_date'])
    if _cents(s.get('budget_cents')):
        out.append('预算：' + _money(s['budget_cents']))
    if isinstance(s.get('cake'), bool):
        out.append('蛋糕：' + ('需要' if s['cake'] else '不要'))
    if isinstance(s.get('account_masked'), str) and s['account_masked']:
        out.append('付款账户：' + s['account_masked'])
    return out


def _steps(raw):
    out = []
    for st in raw or []:
        state = st.get('state') if st.get('state') in _STEP_STATES else 'UNKNOWN'
        out.append({'label': _LABELS.get(st.get('action'), '处理步骤'), 'state': state})
    return out


def _flow_states(flow):
    if not isinstance(flow, dict):
        return []
    nodes = flow.get('nodes') or flow.get('steps') or []
    out = [flow.get('status'), flow.get('observed_state')]
    for n in nodes:
        if isinstance(n, dict):
            out += [n.get('status') or n.get('state'), n.get('observed_state')]
    return out


_NODE_LABELS = (('order', '花束订单'), ('cake', '蛋糕'), ('flower', '花束'), ('choice', '选择花束'), ('budget', '预算预留'))


def _node_label(n):
    key = ' '.join(str(n.get(k) or '') for k in ('kind', 'type', 'action', 'node', 'name')).lower()
    for frag, label in _NODE_LABELS:
        if frag in key:
            return label
    return '流程节点'


def _flow_steps(flow):
    if not isinstance(flow, dict):
        return None
    nodes = [n for n in (flow.get('nodes') or []) if isinstance(n, dict)]
    if not nodes:
        return None
    out = []
    for n in nodes:
        st = n.get('observed_state') or n.get('status') or n.get('state')
        out.append({'label': _node_label(n), 'state': st if st in _STEP_STATES else 'UNKNOWN'})
    return out


def _int0(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _budget_ok(b):
    if not (isinstance(b, dict) and all(_int0(b.get(k)) for k in ('amount_cents', 'spent_cents', 'remaining_cents'))
            and b['remaining_cents'] == b['amount_cents'] - b['spent_cents'] and isinstance(b.get('status'), str)):
        return False
    if 'reserved_cents' in b:
        r = b['reserved_cents']
        return _int0(r) and r <= b['remaining_cents']
    return True


def _budget_lines(b):
    # Only the authority's reserved_cents proves funds are held; remaining balance alone does not.
    out = ['已用：' + _money(b['spent_cents'])]
    if b['remaining_cents'] > 0:
        out.append('未用：' + _money(b['remaining_cents']))
    reserved = b.get('reserved_cents')
    if _int0(reserved) and reserved > 0:
        out.append('仍预留：' + _money(reserved))
    return out


def render_goal(goal, flow=None, operations=None, budget=None):
    ops = [o for o in (operations or []) if isinstance(o, dict)]
    slots = goal.get('slots') or {}
    status, phase = goal.get('status'), goal.get('phase')
    steps = _steps(goal.get('steps'))
    if status in ('DONE', 'COMPLETED'):
        fs = _flow_steps(flow)
        if fs is not None:
            # Keep raw journal UNKNOWN steps so a completed flow projection cannot hide them.
            steps = fs + [s for s in steps if s['state'] == 'UNKNOWN']
    budget_bad = budget is not None and not _budget_ok(budget)
    op_states = [o.get('status') for o in ops]
    unknown = ('UNKNOWN' in op_states or 'UNKNOWN' in _flow_states(flow) or budget_bad
               or any(s['state'] == 'UNKNOWN' for s in steps))
    waiting = [o['status'] for o in ops if o.get('status') in _WAIT]
    budget_ok = any(o.get('type') == 'birthday_budget_reserve' and o.get('status') == 'SUCCEEDED' for o in ops)
    flow_done = isinstance(flow, dict) and flow.get('status') in ('COMPLETED', 'SUCCEEDED')
    choices, nxt = [], 'NONE'
    if status in ('DONE', 'COMPLETED'):
        if unknown or waiting or (flow is not None and not flow_done):
            status, nxt = 'UNKNOWN', 'CHECK'
        else:
            status, phase = 'COMPLETED', 'DONE'
    elif unknown or status in ('PAUSED', 'UNKNOWN'):
        status, nxt = ('UNKNOWN' if unknown else status), 'CHECK'
    elif status in _TERMINAL:
        nxt = 'NONE'
    elif any(s in _TERMINAL for s in op_states):
        status, nxt = 'BLOCKED', 'CHECK'
    elif waiting:
        phase, nxt = waiting[0], 'WAIT'
    elif phase in _WAIT and budget_ok:
        phase, nxt = 'READY', 'CONTINUE'
    elif phase in _WAIT or phase == 'COLLECTING':
        nxt = 'WAIT'
    elif phase == 'READY':
        nxt = 'CONTINUE'
    elif phase == 'CHOOSING':
        nxt = 'CHOOSE'
        for c in (goal.get('observations') or {}).get('choices') or []:
            if isinstance(c, dict) and isinstance(c.get('id'), str) and _cents(c.get('price_cents')):
                choices.append({'id': c['id'], 'label': str(c.get('label') or ''), 'price_cents': c['price_cents']})
    else:
        nxt = 'CHECK'
    recipient = slots.get('recipient_label')
    q = goal.get('question')
    return {'id': str(goal.get('id') or ''), 'version': int(goal.get('version') or 0),
            'kind': str(goal.get('kind') or ''), 'status': str(status or 'UNKNOWN'), 'phase': str(phase or ''),
            'title': (recipient + '生日安排') if isinstance(recipient, str) and recipient else '生日安排',
            'question': q if isinstance(q, str) and nxt != 'CHECK' else '',
            'summary': _summary(slots) + (_budget_lines(budget) if _budget_ok(budget) else []), 'choices': choices, 'steps': steps, 'next_action': nxt,
            'operation_ids': [str(o['id']) for o in ops if o.get('id') is not None]}

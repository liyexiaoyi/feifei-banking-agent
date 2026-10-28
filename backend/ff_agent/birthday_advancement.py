from __future__ import annotations

from typing import Any

__all__ = ['advance_locked']

_FIN = (0, 4, 5)
_ITEM_NODE = {4: 2, 5: 3}
_DONE = ('SUCCEEDED', 'SKIPPED')
_FAILED = ('FAILED', 'CANCELLED')


def _err(code: str, msg: str, status: int = 400):
    from .bank import DomainError
    return DomainError(code, msg, status)


def _eval(db: Any, user: str, flow: dict, nodes: dict) -> dict:
    from .birthday_nodes import _evaluate, _graph
    return _evaluate(_graph(flow), nodes, flow, db, user)


def _parent_budget(db: Any, user: str, nodes: dict):
    op_id = nodes[0].get('operation_id')
    if op_id is None or nodes[0]['state'] != 'SUCCEEDED':
        raise _err('budget_unproven', '预算未完成', 409)
    op = db.execute('SELECT status FROM operations WHERE id = ? AND user_id = ?',
                    (op_id, user)).fetchone()
    if op is None or op['status'] != 'SUCCEEDED':
        raise _err('budget_unproven', '预算未完成', 409)
    row = db.execute('SELECT * FROM birthday_budgets WHERE user_id = ? AND origin_operation_id = ?',
                     (user, op_id)).fetchone()
    if row is None or row['status'] != 'ACTIVE':
        raise _err('budget_unproven', '预算来源无效', 409)
    return row


def _prepare(bank: Any, db: Any, user: str, flow: dict, nodes: dict, idx: int) -> None:
    from .birthday_nodes import claim_node
    from .birthday_tickets import issue_ticket
    node = nodes[idx]
    claim_node(bank, db, user, flow['id'], idx, flow['version'], node['version'])
    cap = issue_ticket(bank, db, user, flow['id'], idx)
    if not cap or not cap.effect_key:
        raise _err('ticket_invalid', '票据无效', 409)
    if idx == 0:
        from .birthday_budget import BirthdayBudgets
        BirthdayBudgets(bank)._prepare_locked(
            db, user, flow['account_id'], flow['budget_cents'], flow['event_date'],
            cap.effect_key, binding=cap)
        return
    item_id = nodes[_ITEM_NODE[idx]].get('item_id')
    if type(item_id) is not str or not item_id:
        raise _err('item_missing', '未选择商品', 409)
    budget = _parent_budget(db, user, nodes)
    from .birthday_orders import BirthdayOrders
    BirthdayOrders(bank)._prepare_locked(
        db, user, budget['id'], item_id, budget['version'], cap.effect_key, binding=cap)


def advance_locked(bank: Any, db: Any, user: str, ctx: Any) -> Any:
    if not db.in_transaction:
        raise _err('no_transaction', '需要事务', 500)
    from .birthday_commands import assert_current, command_finish
    from .birthday_nodes import cas_node, sync_nodes
    from .birthday_store import load_flow, load_nodes
    flow_id = ctx['flow']['id']
    assert_current(bank, db, user, ctx['flow'])
    sync_nodes(bank, db, user, flow_id)
    flow = load_flow(db, user, flow_id)
    nodes = load_nodes(db, user, flow_id)
    if any(nodes[i]['state'] == 'UNKNOWN' for i in _FIN if i in nodes):
        return command_finish(bank, db, user, ctx, changes={'status': 'UNKNOWN'})
    if flow.get('status') == 'ACTIVE':
        for _ in range(7):
            skip = [i for i in _eval(db, user, flow, nodes).get('skipped', ())
                    if nodes[i]['state'] == 'PENDING']
            if not skip:
                break
            for i in sorted(skip):
                cas_node(db, user, flow_id, i, nodes[i]['version'], {'state': 'SKIPPED'})
            nodes = load_nodes(db, user, flow_id)
        orders = 0
        for _ in range(len(_FIN)):
            ready = [i for i in _FIN if i in _eval(db, user, flow, nodes)['ready']
                     and nodes[i]['state'] == 'PENDING' and nodes[i].get('operation_id') is None]
            if not ready or (ready[0] != 0 and orders >= 2):
                break
            idx = ready[0]
            _prepare(bank, db, user, flow, nodes, idx)
            if idx != 0:
                orders += 1
            nodes = load_nodes(db, user, flow_id)
        n6 = nodes.get(6)
        if n6 is not None and n6['state'] == 'PENDING' \
                and all(nodes[i]['state'] in _DONE for i in _FIN) \
                and 6 in _eval(db, user, flow, nodes)['ready']:
            cas_node(db, user, flow_id, 6, n6['version'], {'state': 'SUCCEEDED'})
            nodes = load_nodes(db, user, flow_id)
    states = [n['state'] for n in nodes.values()]
    if len(states) == 7 and all(s in _DONE for s in states):
        status = 'COMPLETED'
    elif any(s in _FAILED for s in states):
        status = 'BLOCKED'
    else:
        status = ctx['flow'].get('status') if ctx['flow'].get('status') != 'ACTIVE' else 'ACTIVE'
    return command_finish(bank, db, user, ctx, changes={'status': status})

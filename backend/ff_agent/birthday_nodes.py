"""生日流程节点锁内 CAS、领取与银行事实同步（本地模拟，不联网，不授权资金）。

调用方须已在 Bank.transaction（BEGIN IMMEDIATE）内；本模块不开启或提交事务。
非财务节点状态不代表财务批准；财务节点成功只来自 observe_node 的真实来源。
"""
from __future__ import annotations

import json
from typing import Any

__all__ = ['cas_node', 'claim_node', 'sync_nodes']

_FINANCIAL = frozenset({0, 4, 5})
_TERMINAL = frozenset({'SUCCEEDED', 'SKIPPED', 'FAILED', 'CANCELLED', 'UNKNOWN'})
_NODE_FIELDS = frozenset({'state', 'item_id', 'quote_version', 'price_cents',
                          'quote_expires_at', 'result_json'})
_NONFIN_TARGETS = frozenset({'SUCCEEDED', 'SKIPPED'})
_FIN_TARGETS = frozenset({'SKIPPED', 'CANCELLED'})
_SYNC_STATES = frozenset({'RUNNING', 'WAITING_CONFIRMATION', 'WAITING_VERIFICATION',
                          'SUCCEEDED', 'FAILED', 'CANCELLED', 'UNKNOWN'})
_MAX_RESULT = 4096
_MAX_ITEM = 64


def _err(code: str, msg: str, status: int = 400) -> Exception:
    from .birthday_store import DomainError
    return DomainError(code, msg, status)


def _strict_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise _err('invalid_' + name, '版本号无效')
    return value


def _require_txn(db: Any) -> None:
    if not getattr(db, 'in_transaction', False):
        raise _err('txn_required', '写操作必须在事务内执行', 500)


def _graph(flow: dict) -> list[dict]:
    from .branch_plan import GraphError, birthday_graph, validate_graph
    try:
        raw = json.loads(flow['graph_json'])
        graph = validate_graph(raw)
    except (GraphError, ValueError, TypeError, KeyError):
        raise _err('graph_invalid', '流程图无效', 409) from None
    if graph != validate_graph(birthday_graph()):
        raise _err('graph_invalid', '流程图无效', 409)
    return graph


def _decisions(flow: dict) -> dict[int, bool]:
    choice = flow.get('cake_choice')
    if type(choice) is int and choice in (0, 1):
        return {1: bool(choice)}
    return {}


def _observed_states(db: Any, user: Any, flow: dict, nodes: dict[int, dict]) -> dict[int, str]:
    from .birthday_tickets import observe_node
    out = {}
    for i, n in nodes.items():
        if i in _FINANCIAL:
            st = observe_node(None, db, user, flow, n)
            out[i] = st if isinstance(st, str) else 'UNKNOWN'
        else:
            out[i] = n['state']
    return out


def _evaluate(graph: list[dict], nodes: dict[int, dict], flow: dict, db: Any, user: Any) -> dict:
    from .branch_plan import GraphError, evaluate_graph
    states = _observed_states(db, user, flow, nodes)
    try:
        return evaluate_graph(graph, states, _decisions(flow))
    except GraphError:
        raise _err('graph_state_invalid', '节点状态无效', 409) from None


def _load(db: Any, user: Any, flow_id: Any, idx: Any) -> tuple[dict, dict, dict, list]:
    from .birthday_store import load_flow, load_nodes
    if type(idx) is not int or idx < 0:
        raise _err('invalid_idx', '节点索引无效')
    flow = load_flow(db, user, flow_id)
    nodes = load_nodes(db, user, flow_id)
    graph = _graph(flow)
    if set(nodes) != set(range(len(graph))) or idx not in nodes:
        raise _err('node_not_found', '节点不存在', 404)
    return flow, nodes, nodes[idx], graph


def _reload(db: Any, user: Any, flow_id: Any, idx: int) -> dict:
    from .birthday_store import load_nodes
    return load_nodes(db, user, flow_id)[idx]


def _check_result_json(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, str) or len(value) > _MAX_RESULT:
        raise _err('invalid_result', '结果数据无效')
    try:
        json.loads(value)
    except ValueError:
        raise _err('invalid_result', '结果数据无效') from None


def _check_catalog(merged: dict) -> None:
    item = merged.get('item_id')
    price = merged.get('price_cents')
    qv = merged.get('quote_version')
    exp = merged.get('quote_expires_at')
    if not isinstance(item, str) or not 1 <= len(item) <= _MAX_ITEM:
        raise _err('invalid_quote', '目录选择无效')
    if type(price) is not int or price < 1 or price > 100_000_000:
        raise _err('invalid_quote', '报价无效')
    if type(qv) is not int or qv < 1:
        raise _err('invalid_quote', '报价版本无效')
    if type(exp) not in (int, float) or exp != exp or exp in (float('inf'), float('-inf')):
        raise _err('invalid_quote', '报价期限无效')


def cas_node(db: Any, user: Any, flow_id: Any, idx: Any, expected_version: Any,
             changes: Any) -> dict:
    _require_txn(db)
    expected = _strict_int(expected_version, 'version')
    if not isinstance(changes, dict) or not changes or not set(changes) <= _NODE_FIELDS:
        raise _err('invalid_changes', '节点变更字段不允许')
    flow, nodes, node, graph = _load(db, user, flow_id, idx)
    if node['version'] != expected:
        raise _err('version_conflict', '节点版本冲突', 409)
    if node['state'] in _TERMINAL:
        raise _err('node_terminal', '节点已结束', 409)
    if flow.get('status') != 'ACTIVE':
        raise _err('flow_inactive', '流程不可修改', 409)
    kind = graph[idx]['action']['type']
    financial = idx in _FINANCIAL
    if 'result_json' in changes:
        _check_result_json(changes['result_json'])
    if financial and set(changes) - {'state'}:
        raise _err('invalid_changes', '资金节点字段不可直接修改')
    if kind != 'birthday_catalog' and set(changes) & {'item_id', 'quote_version', 'price_cents',
                                                       'quote_expires_at'}:
        raise _err('invalid_changes', '该节点不可设置目录字段')
    merged = {**node, **changes}
    target = changes.get('state')
    if target is not None:
        if node['state'] != 'PENDING':
            raise _err('node_not_pending', '节点不在待处理状态', 409)
        allowed = _FIN_TARGETS if financial else _NONFIN_TARGETS
        if target not in allowed:
            raise _err('state_denied', '不允许的节点状态变更', 409)
        ev = _evaluate(graph, nodes, flow, db, user)
        if target == 'SKIPPED' and idx not in ev['skipped']:
            raise _err('skip_denied', '节点不可跳过', 409)
        if target == 'SUCCEEDED':
            if idx not in ev['ready']:
                raise _err('not_ready', '节点依赖未完成', 409)
            if kind == 'birthday_choice':
                choice = flow.get('cake_choice')
                if type(choice) is not int or choice not in (0, 1):
                    raise _err('choice_missing', '尚未选择是否加蛋糕', 409)
            elif kind == 'birthday_catalog':
                _check_catalog(merged)
            elif kind == 'birthday_summary':
                obs = _observed_states(db, user, flow, nodes)
                if any(obs[d] not in ('SUCCEEDED', 'SKIPPED')
                       for d in graph[idx]['deps']):
                    raise _err('not_ready', '节点依赖未完成', 409)
    elif kind == 'birthday_catalog' and any(
            k in changes for k in ('item_id', 'price_cents', 'quote_version', 'quote_expires_at')):
        _check_catalog(merged)
    cols = sorted(changes)
    sets = ', '.join(f'{c} = ?' for c in cols)
    cur = db.execute(
        f'UPDATE birthday_nodes SET {sets}, version = version + 1 '
        'WHERE flow_id = ? AND user_id = ? AND idx = ? AND version = ?',
        [changes[c] for c in cols] + [flow_id, user, idx, expected])
    if cur.rowcount != 1:
        raise _err('version_conflict', '节点版本冲突', 409)
    return _reload(db, user, flow_id, idx)


def claim_node(bank: Any, db: Any, user: Any, flow_id: Any, idx: Any,
               expected_flow_version: Any, expected_node_version: Any) -> dict:
    _require_txn(db)
    fv = _strict_int(expected_flow_version, 'flow_version')
    nv = _strict_int(expected_node_version, 'node_version')
    if type(idx) is not int or idx not in _FINANCIAL:
        raise _err('not_financial', '节点不可领取')
    flow, nodes, node, graph = _load(db, user, flow_id, idx)
    if flow['version'] != fv:
        raise _err('version_conflict', '流程版本冲突', 409)
    if node['version'] != nv:
        raise _err('version_conflict', '节点版本冲突', 409)
    if flow.get('status') != 'ACTIVE':
        raise _err('flow_inactive', '流程不可执行', 409)
    if node['state'] != 'PENDING' or node.get('operation_id') is not None \
            or node.get('claim_version') != 0:
        raise _err('already_claimed', '节点已被领取', 409)
    if db.execute('SELECT 1 FROM birthday_tickets WHERE user_id = ? AND flow_id = ? '
                  'AND node_idx = ? LIMIT 1', (user, flow_id, idx)).fetchone():
        raise _err('already_claimed', '节点已存在票据', 409)
    if idx not in _evaluate(graph, nodes, flow, db, user)['ready']:
        raise _err('not_ready', '节点依赖未完成', 409)
    bank._user(db, user)
    bank._unlocked(db, user)
    epoch = bank._safety_epoch(db, user)
    bank._assert_transfer_allowed(db, user, epoch)
    acct = db.execute('SELECT name FROM accounts WHERE id = ? AND user_id = ?',
                      (flow.get('account_id'), user)).fetchone()
    if acct is None or flow.get('risk_epoch') != epoch \
            or flow.get('account_name') != acct['name']:
        raise _err('account_invalid', '账户或风险状态无效', 409)
    cur = db.execute(
        "UPDATE birthday_nodes SET state = 'RUNNING', version = version + 1, "
        'claim_version = version + 1 WHERE flow_id = ? AND user_id = ? AND idx = ? '
        "AND version = ? AND state = 'PENDING' AND operation_id IS NULL AND claim_version = 0",
        (flow_id, user, idx, nv))
    if cur.rowcount != 1:
        raise _err('version_conflict', '节点版本冲突', 409)
    return _reload(db, user, flow_id, idx)


def sync_nodes(bank: Any, db: Any, user: Any, flow_id: Any) -> None:
    _require_txn(db)
    from .birthday_store import load_flow, load_nodes
    from .birthday_tickets import observe_node
    flow = load_flow(db, user, flow_id)
    _graph(flow)
    nodes = load_nodes(db, user, flow_id)
    for idx in sorted(_FINANCIAL & set(nodes)):
        node = nodes[idx]
        if node['state'] == 'PENDING' or (node['state'] in _TERMINAL
                                          and node['state'] != 'SUCCEEDED'):
            continue
        observed = observe_node(bank, db, user, flow, node)
        if not isinstance(observed, str) or observed not in _SYNC_STATES:
            observed = 'UNKNOWN'
        if node['state'] == 'SUCCEEDED' and observed != 'SUCCEEDED':
            observed = 'UNKNOWN'
        if observed == node['state']:
            continue
        cur = db.execute(
            'UPDATE birthday_nodes SET state = ?, version = version + 1 '
            'WHERE flow_id = ? AND user_id = ? AND idx = ? AND version = ? '
            "AND state <> 'UNKNOWN'",
            (observed, flow_id, user, idx, node['version']))
        if cur.rowcount != 1:
            raise _err('version_conflict', '节点版本冲突', 409)

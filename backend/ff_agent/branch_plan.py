"""生日条件分支图：纯数据校验与候选评估。

本模块只使用标准库，不访问银行、数据库或网络，不执行工具，也不授权任何操作。
evaluate_graph 只返回候选索引；持久化执行方须在同一事务内以 CAS 应用跳过与领取，
资金类节点只有在银行事实确认后才能记为 SUCCEEDED。图校验通过不代表资金授权。
"""
from __future__ import annotations

from typing import Any

__all__ = ['GraphError', 'birthday_graph', 'validate_graph', 'evaluate_graph']

MAX_NODES = 12

NODE_STATES = frozenset({
    'PENDING', 'RUNNING', 'WAITING_CONFIRMATION', 'WAITING_VERIFICATION',
    'SUCCEEDED', 'FAILED', 'SKIPPED', 'UNKNOWN', 'CANCELLED',
})
_FAILED_STATES = frozenset({'FAILED', 'CANCELLED'})
_ACTION_TYPES = frozenset({
    'birthday_budget', 'birthday_choice', 'birthday_catalog',
    'birthday_order', 'birthday_summary',
})
_GIFT_ACTIONS = frozenset({'birthday_catalog', 'birthday_order'})
_GIFTS = frozenset({'flowers', 'cake'})
_CHOICE = 'birthday_choice'
_NODE_KEYS = frozenset({'idx', 'action', 'deps', 'when'})
_WHEN_KEYS = frozenset({'node', 'equals'})

_E_NODES = '分支图必须是包含1至12个节点的列表'
_E_NODE = '分支图节点字段无效'
_E_INDEX = '分支图节点索引必须是从0开始连续且唯一的整数'
_E_ACTION = '分支图动作未登记或参数无效'
_E_DEPS = '分支图依赖无效'
_E_CYCLE = '分支图存在环'
_E_WHEN = '分支图条件无效'
_E_STATES = '节点状态无效'
_E_DECISIONS = '选择决定无效'

# 评估时的内部投影状态
_SUCCEEDED = 'succeeded'
_SKIPPED = 'skipped'
_BLOCKED = 'blocked'
_WAITING = 'waiting'
_READY = 'ready'


class GraphError(ValueError):
    """分支图或评估输入不合法。消息为固定中文，不回显输入值。"""


def _is_int(value: Any) -> bool:
    return type(value) is int


def birthday_graph() -> list[dict[str, Any]]:
    """预算 -> 是否加蛋糕 -> 花与可选蛋糕目录 -> 各自本机订单 -> 汇总。每次返回新对象。"""
    return [
        {'idx': 0, 'action': {'type': 'birthday_budget'}, 'deps': [], 'when': None},
        {'idx': 1, 'action': {'type': 'birthday_choice'}, 'deps': [0], 'when': None},
        {'idx': 2, 'action': {'type': 'birthday_catalog', 'gift': 'flowers'}, 'deps': [1], 'when': None},
        {'idx': 3, 'action': {'type': 'birthday_catalog', 'gift': 'cake'}, 'deps': [1],
         'when': {'node': 1, 'equals': True}},
        {'idx': 4, 'action': {'type': 'birthday_order', 'gift': 'flowers'}, 'deps': [2], 'when': None},
        {'idx': 5, 'action': {'type': 'birthday_order', 'gift': 'cake'}, 'deps': [3],
         'when': {'node': 1, 'equals': True}},
        {'idx': 6, 'action': {'type': 'birthday_summary'}, 'deps': [4, 5], 'when': None},
    ]


def _norm_action(action: Any) -> dict[str, str]:
    if not isinstance(action, dict):
        raise GraphError(_E_ACTION)
    kind = action.get('type')
    if not isinstance(kind, str) or kind not in _ACTION_TYPES:
        raise GraphError(_E_ACTION)
    if kind in _GIFT_ACTIONS:
        if set(action) != {'type', 'gift'}:
            raise GraphError(_E_ACTION)
        gift = action['gift']
        if not isinstance(gift, str) or gift not in _GIFTS:
            raise GraphError(_E_ACTION)
        return {'type': kind, 'gift': gift}
    if set(action) != {'type'}:
        raise GraphError(_E_ACTION)
    return {'type': kind}


def _norm_deps(deps: Any, idx: int, count: int) -> list[int]:
    if not isinstance(deps, (list, tuple)):
        raise GraphError(_E_DEPS)
    seen: set[int] = set()
    for dep in deps:
        if not _is_int(dep) or not 0 <= dep < count or dep == idx or dep in seen:
            raise GraphError(_E_DEPS)
        seen.add(dep)
    return sorted(seen)


def _topological_order(nodes: list[dict[str, Any]]) -> list[int]:
    remaining = {node['idx']: set(node['deps']) for node in nodes}
    order: list[int] = []
    while remaining:
        free = sorted(i for i, pending in remaining.items() if not pending)
        if not free:
            raise GraphError(_E_CYCLE)
        for i in free:
            del remaining[i]
            order.append(i)
        for pending in remaining.values():
            pending.difference_update(free)
    return order


def _ancestors(nodes: list[dict[str, Any]], order: list[int]) -> dict[int, set[int]]:
    result: dict[int, set[int]] = {}
    for i in order:
        acc: set[int] = set()
        for dep in nodes[i]['deps']:
            acc.add(dep)
            acc |= result[dep]
        result[i] = acc
    return result


def _norm_when(when: Any, idx: int, nodes: list[dict[str, Any]],
               ancestors: dict[int, set[int]]) -> dict[str, Any] | None:
    if when is None:
        return None
    if not isinstance(when, dict) or set(when) != _WHEN_KEYS:
        raise GraphError(_E_WHEN)
    source = when['node']
    equals = when['equals']
    if not _is_int(source) or type(equals) is not bool:
        raise GraphError(_E_WHEN)
    if source not in ancestors[idx] or nodes[source]['action']['type'] != _CHOICE:
        raise GraphError(_E_WHEN)
    return {'node': source, 'equals': equals}


def validate_graph(nodes: Any) -> list[dict[str, Any]]:
    """返回按 idx 排序的规范化新图；不修改输入。校验通过不构成任何授权。"""
    if not isinstance(nodes, (list, tuple)) or not 1 <= len(nodes) <= MAX_NODES:
        raise GraphError(_E_NODES)
    count = len(nodes)
    by_idx: dict[int, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict) or set(node) != _NODE_KEYS:
            raise GraphError(_E_NODE)
        idx = node['idx']
        if not _is_int(idx) or not 0 <= idx < count or idx in by_idx:
            raise GraphError(_E_INDEX)
        by_idx[idx] = node
    normalized: list[dict[str, Any]] = []
    for idx in range(count):
        node = by_idx[idx]
        normalized.append({
            'idx': idx,
            'action': _norm_action(node['action']),
            'deps': _norm_deps(node['deps'], idx, count),
            'when': None,
        })
    order = _topological_order(normalized)
    ancestors = _ancestors(normalized, order)
    for idx in range(count):
        normalized[idx]['when'] = _norm_when(by_idx[idx]['when'], idx, normalized, ancestors)
    return normalized


def _check_states(states: Any, count: int) -> dict[int, str]:
    if not isinstance(states, dict) or len(states) != count:
        raise GraphError(_E_STATES)
    result: dict[int, str] = {}
    for key, value in states.items():
        if not _is_int(key) or not 0 <= key < count:
            raise GraphError(_E_STATES)
        if not isinstance(value, str) or value not in NODE_STATES:
            raise GraphError(_E_STATES)
        result[key] = value
    if len(result) != count:
        raise GraphError(_E_STATES)
    return result


def _check_decisions(decisions: Any, nodes: list[dict[str, Any]]) -> dict[int, bool]:
    if not isinstance(decisions, dict):
        raise GraphError(_E_DECISIONS)
    count = len(nodes)
    result: dict[int, bool] = {}
    for key, value in decisions.items():
        if not _is_int(key) or not 0 <= key < count:
            raise GraphError(_E_DECISIONS)
        if nodes[key]['action']['type'] != _CHOICE or type(value) is not bool:
            raise GraphError(_E_DECISIONS)
        result[key] = value
    return result


def _project(node: dict[str, Any], status: dict[int, str], states: dict[int, str],
             decisions: dict[int, bool]) -> str:
    dep_status = [status[dep] for dep in node['deps']]
    # 失败/取消的祖先永久阻断，即使存在跳过投影也不写成功。
    if _BLOCKED in dep_status:
        return _BLOCKED
    # 未决、执行中、待确认/验证或尚未完成的依赖优先于任何否定决定。
    if any(s in (_WAITING, _READY) for s in dep_status):
        return _WAITING
    if dep_status and all(s == _SKIPPED for s in dep_status):
        return _SKIPPED
    when = node['when']
    if when is not None:
        source = when['node']
        if states[source] != 'SUCCEEDED' or source not in decisions:
            return _WAITING
        if decisions[source] is not when['equals']:
            return _SKIPPED
    return _READY


def _check_persisted(node: dict[str, Any], state: str, status: dict[int, str],
                     states: dict[int, str], decisions: dict[int, bool]) -> None:
    """已记录的非 PENDING、非失败状态必须由已处理依赖与决定支撑，否则拒绝。"""
    projected = _project(node, status, states, decisions)
    if state == 'SKIPPED':
        ok = projected == _SKIPPED
    elif state == 'SUCCEEDED':
        ok = projected == _READY
    else:
        # 在途/未知：依赖须已完成、条件源须已决定；晚到的相反决定不改写它，保持等待。
        ok = projected == _READY or (
            projected == _SKIPPED and node['when'] is not None
            and not all(status[dep] == _SKIPPED for dep in node['deps']))
    if not ok:
        raise GraphError(_E_STATES)


def evaluate_graph(nodes: Any, states: Any, decisions: Any) -> dict[str, list[int]]:
    """只返回 PENDING 节点的候选分类；不执行、不授权、不修改输入。

    decisions 是可信持久业务数据或明确用户选择，不是银行批准。
    非 PENDING 节点（含 UNKNOWN）绝不会出现在 ready 或 skipped 中。
    """
    graph = validate_graph(nodes)
    current = _check_states(states, len(graph))
    choices = _check_decisions(decisions, graph)
    status: dict[int, str] = {}
    ready: list[int] = []
    skipped: list[int] = []
    blocked: list[int] = []
    for idx in _topological_order(graph):
        state = current[idx]
        if state != 'PENDING' and state not in _FAILED_STATES:
            _check_persisted(graph[idx], state, status, current, choices)
        if state == 'SUCCEEDED':
            status[idx] = _SUCCEEDED
        elif state == 'SKIPPED':
            status[idx] = _SKIPPED
        elif state in _FAILED_STATES:
            status[idx] = _BLOCKED
        elif state != 'PENDING':
            status[idx] = _WAITING
        else:
            projected = _project(graph[idx], status, current, choices)
            status[idx] = projected
            if projected == _READY:
                ready.append(idx)
            elif projected == _SKIPPED:
                skipped.append(idx)
            elif projected == _BLOCKED:
                blocked.append(idx)
    return {'ready': sorted(ready), 'skipped': sorted(skipped), 'blocked': sorted(blocked)}

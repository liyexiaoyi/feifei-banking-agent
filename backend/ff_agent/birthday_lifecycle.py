"""Birthday flow pause/resume/cancel. Runs inside the caller's single Bank transaction.

Local simulation only: no merchants, no network, no confirm/verify/execute.
"""
from .bank import DomainError
from .birthday_budget import BirthdayBudgets
from .birthday_commands import assert_current, command_finish
from .birthday_store import load_nodes, sync_nodes
from .birthday_provenance import observe_node

FINANCIAL_NODES = (0, 4, 5)
STOPPABLE = ('WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY')
CANCEL_ALLOWED = ('ACTIVE', 'PAUSED', 'BLOCKED', 'UNKNOWN', 'CANCELLED')


def _field(obj, name):
    # ctx/flow may be dict or attribute objects; never positional tuple access.
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _require_tx(db):
    if getattr(db, 'in_transaction', False) is not True:
        raise DomainError('TRANSACTION_REQUIRED', '生日任务写入须在银行事务内进行。', 409)


def _flow(ctx):
    flow = _field(ctx, 'flow')
    if not isinstance(flow, dict) or not isinstance(flow.get('id'), str):
        raise DomainError('INVALID_STATE', '生日任务数据无效，请刷新后重试。', 409)
    return flow


def _status_guard(flow, allowed):
    if flow.get('status') not in allowed:
        raise DomainError('INVALID_STATE', '当前生日任务状态不允许此操作。', 409)


def _confirmed(confirmed):
    if confirmed is not True:
        raise DomainError('CONFIRMATION_REQUIRED', '请明确确认后再操作。', 400)


def _fresh_nodes(bank, db, user, flow):
    sync_nodes(bank, db, user, flow['id'])
    nodes = load_nodes(db, user, flow['id'])
    if not isinstance(nodes, dict):
        raise DomainError('INVALID_STATE', '生日任务节点数据无效。', 409)
    return nodes


def _has_unknown(bank, db, user, flow, nodes):
    for idx in FINANCIAL_NODES:
        node = nodes.get(idx)
        if isinstance(node, dict):
            if observe_node(bank, db, user, flow, node) == 'UNKNOWN':
                return True
    return False


def pause_locked(bank, db, user, ctx):
    _require_tx(db)
    flow = _flow(ctx)
    _status_guard(flow, ('ACTIVE', 'PAUSED'))
    # Stop-type command: no assert_current, no epoch/unlock requirement, no prepare.
    nodes = _fresh_nodes(bank, db, user, flow)
    if _has_unknown(bank, db, user, flow, nodes):
        status = 'UNKNOWN'
    else:
        status = 'PAUSED'
        for idx in FINANCIAL_NODES:
            node = nodes.get(idx)
            if not isinstance(node, dict):
                continue
            observed = observe_node(bank, db, user, flow, node)
            if observed in ('FAILED', 'CANCELLED') or node.get('status') in ('FAILED', 'CANCELLED'):
                status = 'BLOCKED'
                break
    return command_finish(bank, db, user, ctx, {'status': status})


def resume_locked(bank, db, user, ctx, confirmed):
    _require_tx(db)
    _confirmed(confirmed)
    flow = _flow(ctx)
    _status_guard(flow, ('PAUSED',))
    assert_current(bank, db, user, flow)
    nodes = _fresh_nodes(bank, db, user, flow)
    if _has_unknown(bank, db, user, flow, nodes):
        raise DomainError('OPERATION_UNKNOWN', '存在结果未明的付款，不能继续，只能查看或取消未执行部分。', 409)
    terminal = ('FAILED', 'CANCELLED', 'EXPIRED', 'BLOCKED')
    for idx in FINANCIAL_NODES:
        node = nodes.get(idx)
        if not isinstance(node, dict):
            continue
        if (observe_node(bank, db, user, flow, node) in terminal
                or node.get('status') in terminal or node.get('state') in terminal):
            return command_finish(bank, db, user, ctx, {'status': 'BLOCKED'})
    return command_finish(bank, db, user, ctx, {'status': 'ACTIVE'})


def cancel_locked(bank, db, user, ctx, confirmed):
    _require_tx(db)
    _confirmed(confirmed)
    flow = _flow(ctx)
    _status_guard(flow, CANCEL_ALLOWED)
    flow_id = flow['id']
    now = bank.now().timestamp()
    nodes = load_nodes(db, user, flow_id)
    if not isinstance(nodes, dict):
        raise DomainError('INVALID_STATE', '生日任务节点数据无效。', 409)

    # 1. Stop only known unexecuted operations bound to this user's nodes.
    for idx in FINANCIAL_NODES:
        node = nodes.get(idx)
        if not isinstance(node, dict):
            continue
        op_id = node.get('operation_id')
        if not isinstance(op_id, str):
            continue
        # Read-only binding proof before any UPDATE; UNKNOWN rows are left untouched.
        observed = observe_node(bank, db, user, flow, node)
        if observed not in ('WAITING_CONFIRMATION', 'WAITING_VERIFICATION'):
            continue
        row = db.execute('SELECT id, status, expires_at FROM operations WHERE id = ? AND user_id = ?',
                         (op_id, user)).fetchone()
        if row is None or row['status'] not in STOPPABLE:
            continue
        expires = row['expires_at']
        if type(expires) not in (int, float):
            raise DomainError('INVALID_STATE', '操作期限数据无效。', 409)
        target = 'CANCELLED' if expires > now else 'EXPIRED'
        db.execute('UPDATE operations SET status = ? WHERE id = ? AND user_id = ? AND status = ?',
                   (target, op_id, user, row['status']))
        if target == 'EXPIRED':
            bank._event(db, user, op_id, 'OPERATION_EXPIRED', '确认已过期 / 生日任务已取消 / 本笔未执行')
        else:
            bank._event(db, user, op_id, 'OPERATION_CANCELLED', '生日任务已取消 / 本笔未执行')

    # 2. Sync financial nodes against actual operation state, then read fresh.
    nodes = _fresh_nodes(bank, db, user, flow)
    unknown = _has_unknown(bank, db, user, flow, nodes)

    # 3. Release unused budget only when the parent origin is provably SUCCEEDED.
    parent = nodes.get(0)
    parent_op = parent.get('operation_id') if isinstance(parent, dict) else None
    if isinstance(parent_op, str):
        op = db.execute('SELECT status FROM operations WHERE id = ? AND user_id = ?',
                        (parent_op, user)).fetchone()
        if (op is not None and op['status'] == 'SUCCEEDED'
                and observe_node(bank, db, user, flow, parent) == 'SUCCEEDED'):
            budget = db.execute('SELECT id, version, status FROM birthday_budgets '
                                'WHERE origin_operation_id = ? AND user_id = ?',
                                (parent_op, user)).fetchone()
            if budget is not None and budget['status'] == 'ACTIVE':
                # Unknown orders keep their hold; nothing is refunded or reversed.
                BirthdayBudgets(bank)._cancel_locked(db, user, budget['id'], budget['version'])

    # 4. Cancel only unbound PENDING nodes; never touch UNKNOWN/RUNNING/bound ones.
    db.execute("UPDATE birthday_nodes SET state = 'CANCELLED', version = version + 1 "
               "WHERE flow_id = ? AND user_id = ? AND state = 'PENDING' AND operation_id IS NULL",
               (flow_id, user))

    status = 'UNKNOWN' if unknown or flow.get('status') == 'UNKNOWN' else 'CANCELLED'
    return command_finish(bank, db, user, ctx, {'status': status})

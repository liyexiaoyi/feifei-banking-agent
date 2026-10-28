"""Read-only linked business facts; never execute or expire operations."""
import json
from .card_working import validate_card_slots
from .virtual_working import validate_virtual_pending
from .wealth_working import validate_wealth_pending


_PENDING_STATUSES = frozenset((
    'WAITING_CONFIRMATION',
    'WAITING_VERIFICATION',
    'READY',
))
_TERMINAL_STATUSES = frozenset(('HANDOFF', 'CANCELLED'))


def _nodes(db, user, plan):
    return db.execute('''SELECT n.idx,n.kind,n.effect_key, n.state, n.result
        FROM plan_store_nodes AS n
        JOIN plan_store_plans AS p ON p.id = n.plan_id
        WHERE p.id=? AND p.user=?''',
        (plan['id'], user)).fetchall()


def linked_operations(db, user, plan):
    return db.execute('''SELECT o.* FROM operations o
        JOIN operation_plan_links l ON l.operation_id=o.id
        JOIN plan_store_plans p ON p.id=l.plan_id
        WHERE p.id=? AND p.user=? AND l.user_id=? AND o.user_id=?
        ORDER BY o.created_at,o.id''', (plan['id'],user,user,user)).fetchall()


def _has_payees_card(node):
    effect_key = node['effect_key']
    if not isinstance(effect_key, str) or not effect_key:
        return False
    result = node['result']
    if isinstance(result, (str, bytes, bytearray)):
        try:
            result = json.loads(result)
        except (ValueError, UnicodeError):
            return False
    if not isinstance(result, dict):
        return False
    cards = result.get('cards')
    if not isinstance(cards, list):
        return False
    return any(
        isinstance(card, dict)
        and card.get('type') == 'payees'
        and card.get('selection_key') == effect_key
        for card in cards
    )


def business_summary(db, user, plan, now, epoch):
    """只读摘要；expires_at 与 now 使用数据库约定的同一可比较时间类型。

    用于控制决策时，调用方须持有原控制事务；本模块不提交事务。
    """
    stored_plan = db.execute('''SELECT * FROM plan_store_plans
        WHERE id=? AND user=?''', (plan['id'], user)).fetchone()
    if stored_plan is None:
        return {
            'pending_ops': 0,
            'awaiting_selection': False,
            'actionable': False,
            'attribution_incomplete': False,
        }
    nodes = _nodes(db, user, stored_plan)
    context_row = db.execute('SELECT value FROM contexts WHERE user_id=?', (user,)).fetchone()
    try:
        context = json.loads(context_row['value']) if context_row else {}
    except (ValueError, TypeError):
        context = {}
    updated = context.get('working_updated_at') if isinstance(context, dict) else None
    fresh = type(updated) in (int,float) and 0 <= now - updated <= 600
    card_pending = (validate_card_slots(db,user,context,now).get('pending_card',{})
                    if fresh and context.get('safety_epoch') == epoch else {})
    virtual_pending = (validate_virtual_pending(db,user,context,now).get('pending_virtual_card',{})
                       if fresh and context.get('safety_epoch') == epoch else {})
    wealth_pending = (validate_wealth_pending(db,user,context,now).get('pending_wealth',{})
                      if fresh and context.get('safety_epoch') == epoch else {})
    operations = linked_operations(db, user, stored_plan)
    pending_ops = sum(
        1 for operation in operations
        if operation['status'] in _PENDING_STATUSES
        and operation['expires_at'] is not None
        and operation['expires_at'] > now
    )
    # Existence only prevents reuse of an occupied selection key. It does not
    # attribute that operation to this plan, count it, or make it cancellable.
    existing_keys = {row['idempotency_key'] for row in db.execute(
        'SELECT idempotency_key FROM operations WHERE user_id=?',(user,))}
    awaiting_selection = (
        stored_plan['status'] in ('RUNNING', 'DONE')
        and isinstance(epoch, str)
        and stored_plan['risk_epoch'] == epoch
        and any(
            node['state'] == 'PREPARED'
            and ((_has_payees_card(node) and 'select-' + node['effect_key'] not in existing_keys)
                 or (node['kind'] in ('card_status','card_stop')
                     and card_pending.get('source_plan_id') == stored_plan['id']
                     and card_pending.get('source_plan_idx') == node['idx'])
                 or (node['kind']=='virtual_card_apply'
                     and virtual_pending.get('source_plan_id') == stored_plan['id']
                     and virtual_pending.get('source_plan_idx') == node['idx'])
                 or (node['kind']=='wealth_trade'
                     and wealth_pending.get('source_plan_id') == stored_plan['id']
                     and wealth_pending.get('source_plan_idx') == node['idx']))
            for node in nodes
        )
    )
    return {
        'pending_ops': pending_ops,
        'awaiting_selection': awaiting_selection,
        'attribution_incomplete': db.execute(
            'SELECT 1 FROM plan_link_issues WHERE user_id=? AND plan_id=? LIMIT 1',
            (user,stored_plan['id'])).fetchone() is not None,
        'actionable': (
            stored_plan['status'] not in _TERMINAL_STATUSES
            and (pending_ops > 0 or awaiting_selection)
        ),
    }

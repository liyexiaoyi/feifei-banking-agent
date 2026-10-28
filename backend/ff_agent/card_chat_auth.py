"""Bind a card dialogue step to its current server-owned plan claim."""
import json

from .bank import DomainError


def check_card_ticket(bank, db, user, ticket, action, key):
    if not isinstance(action, dict) or action.get('type') not in (
            'cards', 'card_status', 'card_stop', 'card_correct', 'virtual_card_apply'):
        raise DomainError('PLAN_OWNERSHIP_CONFLICT', '这不是可核对的卡片任务。', 409)
    bank._assert_plan_ticket(db, user, ticket, key)
    if ticket is None:
        return
    row = db.execute('SELECT action FROM plan_store_nodes WHERE plan_id=? AND idx=?',
                     (ticket['id'], ticket['idx'])).fetchone()
    try:
        matches = row is not None and json.loads(row['action']) == action
    except (ValueError, TypeError):
        matches = False
    if not matches:
        raise DomainError('PLAN_OWNERSHIP_CONFLICT', '卡片需求与当前任务步骤不一致，请重新核对。', 409)

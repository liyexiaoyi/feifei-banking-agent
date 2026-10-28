"""Explicit, caller-driven re-quote of a birthday catalog node (idx 2 flowers, 3 cake)."""

SUCC = 'SUCCEEDED'
_GIFT = {2: ('flowers', 4), 3: ('cake', 5)}


def _strict_int(v):
    return type(v) is int and v >= 1


def reselect_node(bank, db, user, flow_id, idx, expected_flow_version, expected_node_version, item_id):
    from .birthday_store import DomainError, load_flow, load_nodes
    from .birthday_provenance import observe_node
    from .birthday_orders import CATALOG

    def fail(code, msg, status=409):
        # Fixed user-facing message; code/status carry the technical reason.
        raise DomainError(code, '生日礼物选择暂时无法更新，请查看已有记录。', status)

    # Must run inside a caller-owned transaction; never auto-BEGIN/commit/nest.
    if not getattr(db, 'in_transaction', False):
        fail('TRANSACTION_REQUIRED', 'caller transaction required', 409)

    if not isinstance(user, str) or not isinstance(flow_id, str) or not isinstance(item_id, str):
        fail('bad_request', 'user, flow_id and item_id must be strings', 400)
    if type(idx) is not int or idx not in _GIFT:
        fail('bad_request', 'idx must be 2 or 3', 400)
    if not _strict_int(expected_flow_version) or not _strict_int(expected_node_version):
        fail('bad_request', 'versions must be integers >= 1', 400)

    bank._user(db, user)
    bank._unlocked(db, user)
    bank._assert_transfer_allowed(db, user)
    flow = load_flow(db, user, flow_id)
    if flow['status'] != 'ACTIVE' or flow['version'] != expected_flow_version:
        fail('stale_flow', 'flow not active or version mismatch')
    if bank._safety_epoch(db, user) != flow['risk_epoch']:
        fail('stale_epoch', 'risk epoch changed')
    acct = db.execute('SELECT name FROM accounts WHERE id=? AND user_id=?',
                      (flow['account_id'], user)).fetchone()
    if acct is None or acct[0] != flow['account_name']:
        fail('account_mismatch', 'account not owned or renamed')

    nodes = load_nodes(db, user, flow_id)
    node = nodes[idx]
    if node['version'] != expected_node_version or node['state'] not in ('PENDING', SUCC):
        fail('stale_node', 'node version mismatch or not reselectable')
    if nodes[1]['state'] != SUCC:
        fail('choice_pending', 'birthday choice not completed')
    if idx == 3 and flow['cake_choice'] != 1:
        fail('cake_not_chosen', 'cake only allowed for choice 1')

    gift, child_idx = _GIFT[idx]
    child = nodes[child_idx]
    if child['state'] != 'PENDING' or child['claim_version'] != 0 or child['operation_id'] is not None:
        fail('order_claimed', 'order already claimed; cannot reselect')
    if db.execute('SELECT 1 FROM birthday_tickets WHERE user_id=? AND flow_id=? AND node_idx=? LIMIT 1',
                  (user, flow_id, child_idx)).fetchone():
        fail('order_claimed', 'order has a ticket; cannot reselect')

    from datetime import date
    now_dt = bank.now()
    now = now_dt.timestamp()
    try:
        event_day = date.fromisoformat(flow['event_date'])
    except (TypeError, ValueError):
        fail('bad_event_date', 'event date invalid')
    if event_day < now_dt.date():
        fail('event_passed', 'event date already passed')
    parent = nodes[0]
    if observe_node(bank, db, user, flow, parent) != SUCC or not parent['operation_id']:
        fail('budget_unconfirmed', 'parent budget not confirmed')
    b = db.execute('SELECT status, expires_at, event_date, account_id, risk_epoch FROM birthday_budgets '
                   'WHERE origin_operation_id=? AND user_id=?', (parent['operation_id'], user)).fetchone()
    if (b is None or b[0] != 'ACTIVE' or not now < b[1] or b[2] != flow['event_date']
            or b[3] != flow['account_id'] or b[4] != flow['risk_epoch']):
        fail('budget_invalid', 'parent budget inactive, expired or mismatched')

    item = CATALOG.get(item_id)
    if not isinstance(item, dict) or item.get('gift') != gift:
        fail('bad_item', 'item not in catalog for this gift', 400)
    price, qv = item.get('price_cents'), item.get('quote_version')
    if type(price) is not int or not 1 <= price <= 100_000_000 or not _strict_int(qv):
        fail('bad_item', 'catalog price or quote version invalid', 500)
    expires = min(now + 300.0, float(b[1]))

    cur = db.execute(
        "UPDATE birthday_nodes SET item_id=?, price_cents=?, quote_version=?, quote_expires_at=?, "
        "state=?, version=version+1 WHERE flow_id=? AND user_id=? AND idx=? AND version=? "
        "AND state IN ('PENDING', ?) AND EXISTS (SELECT 1 FROM birthday_flows WHERE id=? AND user_id=? "
        "AND status='ACTIVE' AND version=?)",
        (item_id, price, qv, expires, SUCC, flow_id, user, idx, expected_node_version, SUCC,
         flow_id, user, expected_flow_version))
    if cur.rowcount != 1:
        fail('stale_node', 'concurrent update')
    return load_nodes(db, user, flow_id)[idx]

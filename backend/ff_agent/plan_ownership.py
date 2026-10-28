"""Explicit immutable operation ownership and conservative legacy evidence migration."""
import json


class OwnershipConflict(Exception):
    pass


def _reject_constant(value):
    raise ValueError('Non-JSON constant: ' + value)


def _load(value):
    return json.loads(value, parse_constant=_reject_constant)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def owner_for(db, user, operation_id):
    row = db.execute(
        'SELECT user_id, plan_id, node_idx FROM operation_plan_links '
        'WHERE operation_id = ?', (operation_id,)
    ).fetchone()
    if row is None:
        return None
    if row['user_id'] != user:
        raise OwnershipConflict('Operation link belongs to another user')
    return row['plan_id'], row['node_idx']


def bind_operation(db, user, operation_id, owner, origin, source_id=None):
    if origin not in {'ticket', 'select', 'resume', 'migration'}:
        raise OwnershipConflict('Invalid ownership origin')
    op = db.execute(
        'SELECT user_id FROM operations WHERE id = ?', (operation_id,)
    ).fetchone()
    if op is None or op['user_id'] != user:
        raise OwnershipConflict('Operation missing or user mismatch')
    existing = owner_for(db, user, operation_id)
    if owner is None:
        if existing is not None:
            raise OwnershipConflict('Linked operation requires its owner')
        return
    if not isinstance(owner, tuple) or len(owner) != 2:
        raise OwnershipConflict('Owner must be a (plan_id, node_idx) tuple')
    plan_id, node_idx = owner
    if not isinstance(plan_id, str) or type(node_idx) is not int:
        raise OwnershipConflict('Invalid owner coordinates')
    node = db.execute(
        'SELECT 1 FROM plan_store_plans p JOIN plan_store_nodes n '
        'ON n.plan_id = p.id WHERE p.id = ? AND p.user = ? AND n.idx = ?',
        (plan_id, user, node_idx)
    ).fetchone()
    if node is None:
        raise OwnershipConflict('Plan user mismatch or node missing')
    if existing is not None:
        if existing != owner:
            raise OwnershipConflict('Operation cannot be reassigned')
        return
    db.execute(
        'INSERT INTO operation_plan_links '
        '(operation_id, user_id, plan_id, node_idx, origin, source_id) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (operation_id, user, plan_id, node_idx, origin, source_id)
    )


def migrate_legacy(db):
    stats = {'links_added': 0, 'issues_added': 0, 'conflicts': 0}
    allowed = {'transfer': {'transfer'}, 'subscriptions': {'subscription_query'},
               'cancel_subscription': {'subscription_query', 'subscription_cancel'}}
    bad = object()
    payloads, candidates, legacy_users, key_owners = {}, {}, {}, {}

    def issue(plan_id, user, code):
        cursor = db.execute(
            'INSERT OR IGNORE INTO plan_link_issues (plan_id, user_id, code) '
            'VALUES (?, ?, ?)', (plan_id, user, code)
        )
        stats['issues_added'] += cursor.rowcount

    def payload(op, plan_id, user):
        op_id = op['id']
        if op_id not in payloads:
            try:
                payloads[op_id] = _canonical(_load(op['payload']))
            except (ValueError, TypeError, RecursionError):
                payloads[op_id] = bad
        if payloads[op_id] is bad:
            issue(plan_id, user, 'invalid_json')
        return payloads[op_id]

    operations = {r['id']: r for r in db.execute(
        'SELECT id, user_id, idempotency_key, type, payload FROM operations')}
    links = {r['operation_id']: r for r in db.execute(
        'SELECT operation_id, user_id, plan_id, node_idx FROM operation_plan_links')}
    nodes = db.execute(
        'SELECT n.plan_id, n.idx, n.kind, n.effect_key, n.result, p.user '
        'FROM plan_store_nodes n JOIN plan_store_plans p ON p.id = n.plan_id '
        "WHERE n.effect_key IS NOT ('task-' || p.id || '-' || n.idx)"
    ).fetchall()
    for node in nodes:
        plan_id, user, idx = node['plan_id'], node['user'], node['idx']
        key = node['effect_key']
        legacy_users.setdefault(user, set()).add(plan_id)
        if not isinstance(key, str) or not key:
            issue(plan_id, user, 'invalid_legacy_key')
            continue
        key_owners.setdefault((user, key), set()).add(plan_id)
        if node['result'] is None:
            continue
        try:
            result = _load(node['result'])
        except (ValueError, TypeError, RecursionError):
            issue(plan_id, user, 'invalid_json')
            continue
        if not isinstance(result, dict) or not isinstance(result.get('cards', []), list):
            issue(plan_id, user, 'invalid_legacy_result')
            continue
        for card in result.get('cards', []):
            if not isinstance(card, dict):
                issue(plan_id, user, 'invalid_legacy_card')
                continue
            if card.get('type') != 'operation':
                continue
            saved = card.get('operation')
            fields = {'id', 'type', 'payload'}
            if not isinstance(saved, dict) or not fields.issubset(saved):
                issue(plan_id, user, 'invalid_legacy_card')
                continue
            op = operations.get(saved['id']) if isinstance(saved['id'], str) else None
            if (op is None or op['user_id'] != user
                    or op['idempotency_key'] != key
                    or saved['type'] != op['type']
                    or op['type'] not in allowed.get(node['kind'], set())):
                issue(plan_id, user, 'legacy_card_mismatch')
                continue
            stored = payload(op, plan_id, user)
            try:
                matches = stored is not bad and stored == _canonical(saved['payload'])
            except (ValueError, TypeError, RecursionError):
                issue(plan_id, user, 'invalid_json')
                continue
            if not matches:
                issue(plan_id, user, 'legacy_card_mismatch')
                continue
            candidates.setdefault(op['id'], set()).add((plan_id, user, idx))

    linked = set(links)
    for op_id, owners in candidates.items():
        previous = links.get(op_id)
        related = set(owners)
        if previous is not None:
            related.add((previous['plan_id'], previous['user_id'], previous['node_idx']))
        if len(related) != 1:
            stats['conflicts'] += 1
            for plan_id, user, idx in related:
                issue(plan_id, user, 'ownership_conflict')
            continue
        if previous is None:
            plan_id, user, idx = next(iter(owners))
            bind_operation(db, user, op_id, (plan_id, idx), 'migration')
            linked.add(op_id)
            stats['links_added'] += 1

    for op_id, op in operations.items():
        user, key = op['user_id'], op['idempotency_key']
        related_plans = set(key_owners.get((user, key), ()))
        followup = isinstance(key, str) and key.startswith(('select-', 'resume-'))
        if isinstance(key, str) and key.startswith('select-'):
            related_plans.update(key_owners.get((user, key[7:]), ()))
        if isinstance(key, str) and key.startswith('resume-'):
            parent = operations.get(key[7:])
            if parent is not None and parent['user_id'] == user:
                related_plans.update(key_owners.get((user, parent['idempotency_key']), ()))
        for plan_id in related_plans:
            payload(op, plan_id, user)
            if op_id not in linked:
                code = 'unlinked_legacy_followup' if followup else 'missing_operation_link'
                issue(plan_id, user, code)
    return stats

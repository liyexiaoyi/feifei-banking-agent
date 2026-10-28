"""Read-only validation of card working-memory slots."""

import json
import math


def _cell(row, name, index):
    return row[index] if isinstance(row, (tuple, list)) else row[name]


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _fresh(value, now):
    return _finite_number(value) and 0 <= now - value <= 600


def validate_card_slots(db, user, value, now):
    """Return fresh slot dictionaries; caller must enforce Memory gates."""
    result = {}
    if not isinstance(value, dict) or not _finite_number(now):
        return result

    pending = value.get("pending_card")
    if isinstance(pending, dict):
        mode = pending.get("mode")
        action = pending.get("action")
        started = pending.get("started_at")
        valid = (
            isinstance(mode, str) and mode in ("prepare", "stop")
            and isinstance(action, str)
            and (action in ("report_lost", "restore")
                 or (mode == "stop" and action == "any"))
            and _fresh(started, now)
        )
        has_watermark = "operation_watermark" in pending
        watermark = pending.get("operation_watermark")
        if valid and has_watermark:
            valid = type(watermark) is int and watermark >= 0
            if valid:
                row = db.execute(
                    "SELECT COALESCE(MAX(rowid), 0) AS max_rowid, "
                    "COALESCE(MAX(CASE WHEN type IN "
                    "('card_report_lost', 'card_restore') THEN rowid END), 0) "
                    "AS max_card_rowid FROM operations WHERE user_id = ?",
                    (user,),
                ).fetchone()
                valid = (
                    watermark <= _cell(row, "max_rowid", 0)
                    and _cell(row, "max_card_rowid", 1) <= watermark
                )
        has_plan = 'source_plan_id' in pending or 'source_plan_idx' in pending
        if valid and has_plan:
            plan_id, idx = pending.get('source_plan_id'), pending.get('source_plan_idx')
            valid = (has_watermark and isinstance(plan_id, str) and bool(plan_id)
                     and type(idx) is int and idx >= 0)
            if valid:
                row = db.execute('''SELECT p.status AS plan_status,n.state,n.action,p.risk_epoch
                    FROM plan_store_plans p JOIN plan_store_nodes n ON p.id=n.plan_id
                    WHERE p.id=? AND p.user=? AND n.idx=?''', (plan_id, user, idx)).fetchone()
                valid = (row is not None and _cell(row,'plan_status',0) in ('RUNNING','DONE')
                         and _cell(row,'state',1) in ('RUNNING','PREPARED')
                         and _cell(row,'risk_epoch',3) == value.get('safety_epoch',''))
                if valid:
                    try:
                        planned = json.loads(_cell(row,'action',2))
                    except (ValueError, TypeError):
                        planned = None
                    valid = (isinstance(planned, dict)
                             and planned.get('type') == ('card_status' if mode == 'prepare' else 'card_stop')
                             and planned.get('action') == action)
                if valid:
                    filled = db.execute('''SELECT 1 FROM operation_plan_links l
                        JOIN operations o ON o.id=l.operation_id
                        WHERE l.user_id=? AND o.user_id=? AND l.plan_id=? AND l.node_idx=? LIMIT 1''',
                        (user,user,plan_id,idx)).fetchone()
                    valid = filled is None
        if valid:
            clean = {"mode": mode, "action": action, "started_at": started}
            if has_watermark:
                clean["operation_watermark"] = watermark
            if has_plan:
                clean.update(source_plan_id=plan_id, source_plan_idx=idx)
            result["pending_card"] = clean

    recent = value.get("recent_card")
    if not isinstance(recent, dict):
        return result
    operation_id = recent.get("operation_id")
    if not isinstance(operation_id, str):
        return result
    row = db.execute(
        "SELECT payload, created_at FROM operations WHERE id = ? "
        "AND user_id = ? AND type IN ('card_report_lost', 'card_restore')",
        (operation_id, user),
    ).fetchone()
    if row is None:
        return result
    payload, created = _cell(row, "payload", 0), _cell(row, "created_at", 1)
    if not _fresh(created, now) or not isinstance(payload, (str, bytes, bytearray)):
        return result
    try:
        payload = json.loads(payload)
    except (ValueError, TypeError, RecursionError):
        return result
    if not isinstance(payload, dict) or not isinstance(payload.get("card_id"), str):
        return result
    owned = db.execute(
        "SELECT 1 FROM bank_all_cards c JOIN accounts a "
        "ON a.id = c.account_id AND a.user_id = c.user_id "
        "WHERE c.id = ? AND c.user_id = ? LIMIT 1",
        (payload["card_id"], user),
    ).fetchone()
    if owned is not None:
        result["recent_card"] = {"operation_id": operation_id, "started_at": created}
    return result

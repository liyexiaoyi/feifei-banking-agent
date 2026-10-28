import json
import math

_MISSING = object()


def _column(row, name, index):
    try:
        return row[name]
    except (IndexError, KeyError, TypeError):
        try:
            return row[index]
        except (IndexError, KeyError, TypeError):
            return _MISSING


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def validate_wealth_pending(db, user, value, now) -> dict:
    if not isinstance(value, dict) or not _finite_number(now):
        return {}
    pending = value.get("pending_wealth")
    if not isinstance(pending, dict):
        return {}

    trade_action = pending.get("action")
    product = pending.get("product")
    account = pending.get("account")
    amount_cents = pending.get("amount_cents")
    started_at = pending.get("started_at")
    watermark = pending.get("operation_watermark")
    source_plan_id = pending.get("source_plan_id")
    source_plan_idx = pending.get("source_plan_idx")

    if trade_action not in ("purchase", "redeem"):
        return {}
    for field in (product, account):
        if not isinstance(field, str) or not field or len(field) > 300:
            return {}
    if amount_cents is not None and (
        type(amount_cents) is not int or not 1 <= amount_cents <= 100_000_000
    ):
        return {}
    if not _finite_number(started_at):
        return {}
    age = now - started_at
    if not _finite_number(age) or not 0 <= age <= 600:
        return {}
    if type(watermark) is not int or watermark < 0:
        return {}
    if not isinstance(source_plan_id, str) or not source_plan_id:
        return {}
    if type(source_plan_idx) is not int or source_plan_idx < 0:
        return {}
    safety_epoch = value.get("safety_epoch", _MISSING)
    if safety_epoch is _MISSING:
        return {}

    row = db.execute(
        "SELECT p.status,p.risk_epoch,n.state,n.action "
        "FROM plan_store_plans p JOIN plan_store_nodes n ON p.id=n.plan_id "
        "WHERE p.user=? AND p.id=? AND n.idx=?",
        (user, source_plan_id, source_plan_idx),
    ).fetchone()
    if row is None:
        return {}
    if _column(row, "status", 0) not in ("RUNNING", "DONE"):
        return {}
    if _column(row, "risk_epoch", 1) != safety_epoch:
        return {}
    if _column(row, "state", 2) not in ("RUNNING", "PREPARED"):
        return {}

    raw_action = _column(row, "action", 3)
    if not isinstance(raw_action, str):
        return {}
    try:
        frozen = json.loads(raw_action)
    except (TypeError, ValueError):
        return {}
    expected_keys = {"type", "action", "product", "account", "amount_cents"}
    if not isinstance(frozen, dict) or set(frozen) != expected_keys:
        return {}
    frozen_amount = frozen["amount_cents"]
    if frozen_amount is not None and type(frozen_amount) is not int:
        return {}
    if (
        frozen["type"] != "wealth_trade"
        or frozen["action"] != trade_action
        or frozen["product"] != product
        or frozen["account"] != account
        or frozen_amount != amount_cents
    ):
        return {}

    row = db.execute(
        "SELECT COALESCE(MAX(rowid),0) AS latest_rowid FROM operations WHERE user_id=?",
        (user,),
    ).fetchone()
    latest = _column(row, "latest_rowid", 0) if row is not None else _MISSING
    if type(latest) is not int or watermark != latest:
        return {}

    row = db.execute(
        "SELECT 1 FROM operation_plan_links l "
        "JOIN operations o ON o.id=l.operation_id "
        "WHERE o.user_id=? AND l.user_id=? AND l.plan_id=? AND l.node_idx=? LIMIT 1",
        (user, user, source_plan_id, source_plan_idx),
    ).fetchone()
    if row is not None:
        return {}

    return {
        "pending_wealth": {
            "action": trade_action,
            "product": product,
            "account": account,
            "amount_cents": amount_cents,
            "started_at": started_at,
            "operation_watermark": watermark,
            "source_plan_id": source_plan_id,
            "source_plan_idx": source_plan_idx,
        }
    }

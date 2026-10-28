"""在调用方持有的 SQLite 事务内认领恢复请求。"""

import sqlite3
from typing import Any, Mapping

from .task_plan import PlanError


def claim_resume(
    db: sqlite3.Connection,
    user: str,
    plan: Mapping[str, Any],
    current_epoch: str,
) -> dict:
    """认领原请求；事务提交及失败回滚均由调用方负责。"""
    if not db.in_transaction:
        raise PlanError("PLAN_CONFLICT", "恢复需要在原子事务内认领。", 409)
    if current_epoch != plan["risk_epoch"]:
        raise PlanError("REQUEST_SUPERSEDED", "计划对应的风险版本已失效", 409)

    request_key = plan["request_key"]
    try:
        original = db.execute(
            """
            SELECT rowid AS request_rowid,
                   request_key, input, status, created_at
              FROM chat_requests
             WHERE user_id = ? AND request_key = ?
            """,
            (user, request_key),
        ).fetchone()
    except sqlite3.Error:
        raise PlanError("PLAN_CONFLICT", "读取原请求失败", 409) from None

    if original is None:
        raise PlanError("ORIGINAL_REQUEST_MISSING", "原请求不存在", 404)

    status = original["status"]
    if status == "PROCESSING":
        raise PlanError("REQUEST_PENDING", "原请求仍在处理中", 409)
    if status not in ("DONE", "FAILED"):
        raise PlanError("PLAN_CONFLICT", "原请求状态不允许恢复", 409)

    try:
        cursor = db.execute(
            """
            UPDATE chat_requests
               SET status = 'FAILED', response = NULL
             WHERE rowid = ?
               AND user_id = ?
               AND request_key = ?
               AND status = ?
               AND created_at IS ?
            """,
            (
                original["request_rowid"],
                user,
                request_key,
                status,
                original["created_at"],
            ),
        )
    except sqlite3.Error:
        raise PlanError("PLAN_CONFLICT", "认领原请求失败", 409) from None

    if cursor.rowcount != 1:
        raise PlanError("PLAN_CONFLICT", "原请求已发生变化，无法恢复", 409)

    return {"text": original["input"], "request_key": original["request_key"]}

"""Local demonstration handoff progress; never changes financial state."""

import hashlib
import json
import re
import uuid

from .bank import DomainError


class HandoffProgress:
    def __init__(self, bank):
        self.bank = bank
        with bank.transaction() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS handoff_progress (
                    user_id TEXT NOT NULL,
                    handoff_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, handoff_id)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS handoff_events (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    handoff_id TEXT NOT NULL,
                    request_key TEXT NOT NULL,
                    body_hash TEXT NOT NULL,
                    from_status TEXT NOT NULL,
                    to_status TEXT NOT NULL,
                    from_version INTEGER NOT NULL,
                    to_version INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE (user_id, request_key)
                )
            """)

    @staticmethod
    def _fail(code, message, status):
        raise DomainError(code, message, status)

    def _owned(self, db, user, plan_id):
        row = db.execute("""
            SELECT h.id, h.plan_id, h.created_at
            FROM local_handoffs AS h
            JOIN plan_store_plans AS p
              ON p.id = h.plan_id AND p.user = h.user_id
            WHERE h.user_id = ? AND p.user = ? AND p.id = ?
            ORDER BY h.created_at DESC, h.id DESC
            LIMIT 1
        """, (user, user, plan_id)).fetchone()
        if row is None:
            self._fail("NOT_FOUND", "未找到可访问的本地核查记录。", 404)
        return row

    @staticmethod
    def _state(db, user, handoff):
        row = db.execute("""
            SELECT status, version, updated_at FROM handoff_progress
            WHERE user_id = ? AND handoff_id = ?
        """, (user, handoff["id"])).fetchone()
        if row is None:
            return {"status": "OPEN", "version": 0,
                    "updated_at": handoff["created_at"]}
        return dict(row)

    def _view(self, db, user, handoff, receipt=None, replayed=False):
        state = self._state(db, user, handoff)
        operations = db.execute("""
            SELECT DISTINCT o.id, o.type, o.status
            FROM plan_store_plans AS p
            JOIN operation_plan_links AS l
              ON l.plan_id = p.id AND l.user_id = p.user
            JOIN operations AS o
              ON o.id = l.operation_id AND o.user_id = l.user_id
            WHERE p.user = ? AND p.id = ?
            ORDER BY o.created_at, o.id
        """, (user, handoff["plan_id"])).fetchall()
        events = db.execute("""
            SELECT id, from_status, to_status, from_version,
                   to_version, created_at
            FROM handoff_events WHERE user_id = ? AND handoff_id = ?
            ORDER BY to_version, id
        """, (user, handoff["id"])).fetchall()
        return {
            "handoff_id": handoff["id"], "plan_id": handoff["plan_id"],
            **state, "simulated": True, "contacted_human": False,
            "operations": [dict(row) for row in operations],
            "events": [dict(row) for row in events],
            "receipt": receipt, "replayed": replayed,
        }

    @staticmethod
    def _receipt(event):
        return {
            "event_id": event["id"],
            **{key: event[key] for key in (
                "from_status", "to_status", "from_version", "to_version",
                "created_at",
            )},
        }

    def get(self, user, plan_id):
        with self.bank.transaction() as db:
            return self._view(db, user, self._owned(db, user, plan_id))

    def update(self, user, plan_id, expected_version, status, request_key):
        if (type(expected_version) is not int or expected_version < 0
                or type(status) is not str
                or status not in ("IN_REVIEW", "CLOSED")
                or type(request_key) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_key) is None):
            self._fail("INVALID_INPUT", "本地进度输入不正确，请刷新后重试。", 422)
        with self.bank.transaction() as db:
            handoff = self._owned(db, user, plan_id)
            body = {"handoff_id": handoff["id"],
                    "expected_version": expected_version, "status": status}
            body_hash = hashlib.sha256(json.dumps(
                body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode("utf-8")).hexdigest()
            previous = db.execute("""
                SELECT * FROM handoff_events
                WHERE user_id = ? AND request_key = ?
            """, (user, request_key)).fetchone()
            if previous is not None:
                if previous["body_hash"] != body_hash:
                    self._fail("IDEMPOTENCY_MISMATCH", "这次请求与原记录不一致，请先刷新核对。", 409)
                return self._view(db, user, handoff,
                                  self._receipt(previous), True)
            current = self._state(db, user, handoff)
            if current["version"] != expected_version:
                self._fail("VERSION_CONFLICT", "记录已被更新，请刷新后再试。", 409)
            allowed = {"OPEN": ("IN_REVIEW", "CLOSED"),
                       "IN_REVIEW": ("CLOSED",), "CLOSED": ()}
            if status not in allowed.get(current["status"], ()):
                self._fail("INVALID_TRANSITION", "当前本地记录不能这样更新，请刷新核对。", 409)
            now = self.bank.now().timestamp()
            event = {
                "id": str(uuid.uuid4()), "from_status": current["status"],
                "to_status": status, "from_version": current["version"],
                "to_version": current["version"] + 1, "created_at": now,
            }
            changed = db.execute("""
                INSERT INTO handoff_progress
                    (user_id, handoff_id, status, version, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, handoff_id) DO UPDATE SET
                    status = excluded.status, version = excluded.version,
                    updated_at = excluded.updated_at
                WHERE handoff_progress.version = ?
            """, (user, handoff["id"], status, event["to_version"], now,
                  expected_version))
            if changed.rowcount != 1:
                self._fail("VERSION_CONFLICT", "记录已被更新，请刷新后再试。", 409)
            db.execute("""
                INSERT INTO handoff_events
                    (id, user_id, handoff_id, request_key, body_hash,
                     from_status, to_status, from_version, to_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (event["id"], user, handoff["id"], request_key, body_hash,
                  event["from_status"], status, event["from_version"],
                  event["to_version"], now))
            receipt = self._receipt(event)
            self.bank._event(db, user, None, "HANDOFF_PROGRESS_UPDATED", json.dumps({
                "handoff_id": handoff["id"], "plan_id": handoff["plan_id"],
                **receipt, "simulated": True, "contacted_human": False,
            }, ensure_ascii=False))
            return self._view(db, user, handoff, receipt)

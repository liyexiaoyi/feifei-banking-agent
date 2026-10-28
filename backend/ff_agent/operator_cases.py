"""Local demonstration operator cases; never changes financial state."""

import hashlib
import json
import re
import secrets
import uuid

from .bank import DomainError

_KEY = re.compile(r"[A-Za-z0-9_-]{8,64}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_OPERATOR = "operator:local-demo-operator"


def _fail(code, message, status):
    raise DomainError(code, message, status)


def _check(expected_version, request_key):
    if (type(expected_version) is not int or expected_version < 0
            or type(request_key) is not str
            or _KEY.fullmatch(request_key) is None):
        _fail("INVALID_INPUT", "输入不正确，请刷新后重试。", 422)


def _hash(body):
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")).hexdigest()


def _mask_phone(value):
    digits = re.sub(r"\D", "", value)
    return digits[:3] + "****" + digits[-4:] if len(digits) >= 7 else "****"


def _mask_number(value):
    digits = re.sub(r"\D", "", value)
    return "****" + digits[-4:] if len(digits) >= 4 else "****"


def _operation(row):
    try:
        payload = json.loads(row["payload"])
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    item = {"id": row["id"], "type": row["type"], "status": row["status"]}
    amount = payload.get("amount_cents")
    if type(amount) is int:
        item["amount_cents"] = amount
    for key in ("payee_name", "product_name"):
        if type(payload.get(key)) is str:
            item[key] = _CONTROL.sub("", payload[key])[:64]
    if type(payload.get("payee_phone")) is str:
        item["payee_phone"] = _mask_phone(payload["payee_phone"])
    if type(payload.get("masked_number")) is str:
        item["masked_number"] = _mask_number(payload["masked_number"])
    return item


class OperatorCases:
    def __init__(self, bank):
        self.bank = bank
        with bank.transaction() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS operator_cases (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL, handoff_id TEXT NOT NULL,
                    status TEXT NOT NULL, version INTEGER NOT NULL,
                    snapshot TEXT NOT NULL, reply TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    UNIQUE (user_id, handoff_id)
                )""")
            db.execute("""
                CREATE TABLE IF NOT EXISTS operator_case_events (
                    id TEXT PRIMARY KEY, actor TEXT NOT NULL,
                    request_key TEXT NOT NULL, body_hash TEXT NOT NULL,
                    case_id TEXT NOT NULL, action TEXT NOT NULL,
                    from_version INTEGER NOT NULL, to_version INTEGER NOT NULL,
                    created_at REAL NOT NULL, UNIQUE (actor, request_key)
                )""")
            db.execute("""CREATE INDEX IF NOT EXISTS operator_cases_user
                ON operator_cases(user_id, plan_id)""")

    def _facts(self, db, user, plan_id):
        plan = db.execute("SELECT version FROM plan_store_plans WHERE id = ? AND user = ?",
                          (plan_id, user)).fetchone()
        if plan is None:
            return None
        rows = db.execute("""
            SELECT DISTINCT o.id, o.type, o.status, o.payload, o.created_at
            FROM plan_store_plans AS p
            JOIN operation_plan_links AS l ON l.plan_id = p.id AND l.user_id = p.user
            JOIN operations AS o ON o.id = l.operation_id AND o.user_id = l.user_id
            WHERE p.user = ? AND p.id = ?
            ORDER BY o.created_at, o.id""", (user, plan_id)).fetchall()
        return {"plan_id": plan_id, "plan_version": plan["version"],
                "observed_at": self.bank.now().timestamp(),
                "operations": [_operation(row) for row in rows]}

    def _view(self, db, row, replayed=False):
        cancelled = row["status"] == "CANCELLED"
        snapshot = None if cancelled else json.loads(row["snapshot"])
        current = None if cancelled else self._facts(db, row["user_id"], row["plan_id"])
        if current is not None:
            shared_ids = {item["id"] for item in snapshot["operations"]}
            current["operations"] = [
                {key: item[key] for key in ("id", "type", "status")}
                for item in current["operations"] if item["id"] in shared_ids
            ]
        return {
            "id": row["id"], "plan_id": row["plan_id"], "handoff_id": row["handoff_id"],
            "status": row["status"], "version": row["version"], "simulated": True,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "reply": row["reply"] or "",
            "snapshot": snapshot,
            "current_facts": current,
            "replayed": replayed,
        }

    @staticmethod
    def _load(db, case_id, user=None):
        if type(case_id) is not str:
            _fail("NOT_FOUND", "未找到可访问的核查请求。", 404)
        if user is None:
            row = db.execute("SELECT * FROM operator_cases WHERE id = ?", (case_id,)).fetchone()
        else:
            row = db.execute("SELECT * FROM operator_cases WHERE id = ? AND user_id = ?",
                             (case_id, user)).fetchone()
        if row is None or (user is None and row["status"] == "CANCELLED"):
            _fail("NOT_FOUND", "未找到可访问的核查请求。", 404)
        return row

    @staticmethod
    def _previous(db, actor, request_key, body_hash):
        row = db.execute("SELECT * FROM operator_case_events WHERE actor = ? AND request_key = ?",
                         (actor, request_key)).fetchone()
        if row is not None and row["body_hash"] != body_hash:
            _fail("IDEMPOTENCY_MISMATCH", "这次请求与原记录不一致，请先刷新核对。", 409)
        return row

    def _record(self, db, user, actor, key, body_hash, case_id, action, from_v, to_v, now):
        db.execute("""
            INSERT INTO operator_case_events (id, actor, request_key, body_hash, case_id,
                action, from_version, to_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), actor, key, body_hash, case_id, action, from_v, to_v, now))
        self.bank._event(db, user, None, "OPERATOR_CASE_" + action.upper(), json.dumps({
            "case_id": case_id, "action": action, "from_version": from_v,
            "to_version": to_v, "simulated": True}, ensure_ascii=True))

    def _transition(self, db, row, actor, key, body_hash, action, to_status, reply=None):
        now = self.bank.now().timestamp()
        changed = db.execute("""
            UPDATE operator_cases SET status = ?, version = version + 1,
                reply = COALESCE(?, reply), updated_at = ?
            WHERE id = ? AND version = ? AND status = ?""",
            (to_status, reply, now, row["id"], row["version"], row["status"]))
        if changed.rowcount != 1:
            _fail("VERSION_CONFLICT", "记录已被更新，请刷新后再试。", 409)
        self._record(db, row["user_id"], actor, key, body_hash, row["id"], action,
                     row["version"], row["version"] + 1, now)
        return self._view(db, self._load(db, row["id"], row["user_id"]))

    def share(self, user, plan_id, expected_version, request_key, confirmed=False):
        if confirmed is not True:
            _fail("CONSENT_REQUIRED", "需要明确确认后才能共享核查信息。", 422)
        _check(expected_version, request_key)
        if type(plan_id) is not str:
            _fail("NOT_FOUND", "未找到可访问的本地核查记录。", 404)
        actor = "user:" + user
        with self.bank.transaction() as db:
            owned = db.execute("""
                SELECT h.id AS handoff_id, p.id AS plan_id, p.version AS plan_version
                FROM local_handoffs AS h
                JOIN plan_store_plans AS p ON p.id = h.plan_id AND p.user = h.user_id
                WHERE h.user_id = ? AND p.user = ? AND p.id = ?
                ORDER BY h.created_at DESC, h.id DESC LIMIT 1""", (user, user, plan_id)).fetchone()
            if owned is None:
                _fail("NOT_FOUND", "未找到可访问的本地核查记录。", 404)
            body_hash = _hash({"action": "share", "plan_id": plan_id,
                               "expected_version": expected_version, "confirmed": True})
            previous = self._previous(db, actor, request_key, body_hash)
            if previous is not None:
                return self._view(db, self._load(db, previous["case_id"], user), True)
            if owned["plan_version"] != expected_version:
                _fail("VERSION_CONFLICT", "计划已更新，请刷新后再试。", 409)
            existing = db.execute("""
                SELECT id,status FROM operator_cases
                WHERE user_id = ? AND (handoff_id = ? OR plan_id = ?) LIMIT 1""",
                (user, owned["handoff_id"], plan_id)).fetchone()
            if existing is not None:
                if existing["status"] == "CANCELLED":
                    _fail("CASE_WITHDRAWN", "分享已撤回，本机演示不支持再次分享这份记录。", 409)
                _fail("CASE_EXISTS", "这份记录已提交过核查请求，请刷新核对。", 409)
            snapshot = self._facts(db, user, plan_id)
            now = self.bank.now().timestamp()
            case_id = "case_" + secrets.token_hex(12)
            db.execute("""
                INSERT INTO operator_cases (id, user_id, plan_id, handoff_id, status,
                    version, snapshot, reply, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'QUEUED', 1, ?, '', ?, ?)""",
                (case_id, user, plan_id, owned["handoff_id"],
                 json.dumps(snapshot, ensure_ascii=False, sort_keys=True), now, now))
            self._record(db, user, actor, request_key, body_hash, case_id, "share", 0, 1, now)
            return self._view(db, self._load(db, case_id, user))

    def list_user(self, user):
        with self.bank.transaction() as db:
            rows = db.execute("""SELECT * FROM operator_cases WHERE user_id = ?
                ORDER BY created_at DESC, id DESC LIMIT 50""", (user,)).fetchall()
            return [self._view(db, row) for row in rows]

    def get_for_plan(self, user, plan_id):
        with self.bank.transaction() as db:
            if db.execute("SELECT 1 FROM plan_store_plans WHERE id=? AND user=?", (plan_id, user)).fetchone() is None:
                _fail("NOT_FOUND", "未找到可访问的计划。", 404)
            row = db.execute("SELECT * FROM operator_cases WHERE plan_id=? AND user_id=?", (plan_id, user)).fetchone()
            return self._view(db, row) if row is not None else None

    def preview(self, user, plan_id):
        with self.bank.transaction() as db:
            owned = db.execute("""SELECT 1 FROM local_handoffs h
                JOIN plan_store_plans p ON p.id=h.plan_id AND p.user=h.user_id
                WHERE h.user_id=? AND p.user=? AND p.id=?""", (user, user, plan_id)).fetchone()
            if owned is None:
                _fail("NOT_FOUND", "未找到可分享的本人核查记录。", 404)
            return self._facts(db, user, plan_id)

    def get_user(self, user, case_id):
        with self.bank.transaction() as db:
            return self._view(db, self._load(db, case_id, user))

    def withdraw(self, user, case_id, expected_version, request_key):
        _check(expected_version, request_key)
        with self.bank.transaction() as db:
            row = self._load(db, case_id, user)
            body_hash = _hash({"action": "withdraw", "case_id": row["id"],
                               "expected_version": expected_version})
            if self._previous(db, "user:" + user, request_key, body_hash) is not None:
                return self._view(db, row, True)
            if row["version"] != expected_version:
                _fail("VERSION_CONFLICT", "记录已被更新，请刷新后再试。", 409)
            if row["status"] not in ("QUEUED", "IN_REVIEW", "REPLIED"):
                _fail("INVALID_TRANSITION", "当前请求不能撤回，请刷新核对。", 409)
            return self._transition(db, row, "user:" + user, request_key, body_hash,
                                    "withdraw", "CANCELLED")

    def list_operator(self):
        with self.bank.transaction() as db:
            rows = db.execute("""SELECT * FROM operator_cases WHERE status != 'CANCELLED'
                ORDER BY created_at, id LIMIT 100""").fetchall()
            return [self._view(db, row) for row in rows]

    def get_operator(self, case_id):
        with self.bank.transaction() as db:
            return self._view(db, self._load(db, case_id))

    def act_operator(self, case_id, expected_version, action, reply, request_key):
        _check(expected_version, request_key)
        if action == "claim":
            if reply not in (None, ""):
                _fail("INVALID_INPUT", "认领请求不能附带回复。", 422)
            text = None
        elif action == "reply":
            text = reply.strip() if type(reply) is str else ""
            if not text or len(text) > 500 or _CONTROL.search(text):
                _fail("INVALID_INPUT", "回复须为 1 到 500 字的文本。", 422)
        else:
            _fail("INVALID_INPUT", "不支持的操作。", 422)
        with self.bank.transaction() as db:
            row = self._load(db, case_id)
            body_hash = _hash({"action": action, "case_id": row["id"],
                               "expected_version": expected_version, "reply": text})
            if self._previous(db, _OPERATOR, request_key, body_hash) is not None:
                return self._view(db, row, True)
            if row["version"] != expected_version:
                _fail("VERSION_CONFLICT", "记录已被更新，请刷新后再试。", 409)
            source, target = (("QUEUED", "IN_REVIEW") if action == "claim"
                              else ("IN_REVIEW", "REPLIED"))
            if row["status"] != source:
                _fail("INVALID_TRANSITION", "当前请求不能这样处理，请刷新核对。", 409)
            return self._transition(db, row, _OPERATOR, request_key, body_hash,
                                    action, target, text)

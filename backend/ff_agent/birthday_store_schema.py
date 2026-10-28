import json
import time
from .bank import DomainError
from .branch_plan import birthday_graph

FLOW_STATUSES = {"ACTIVE", "PAUSED", "CANCELLED", "COMPLETED", "BLOCKED", "UNKNOWN"}
TERMINAL = {"CANCELLED", "COMPLETED", "UNKNOWN"}
FINANCIAL = {0, 4, 5}

DDL = (
    """CREATE TABLE IF NOT EXISTS birthday_flows(
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, create_key TEXT NOT NULL, request_hash TEXT NOT NULL,
    recipient_label TEXT NOT NULL, event_date TEXT NOT NULL, account_id TEXT NOT NULL, account_name TEXT NOT NULL,
    budget_cents INTEGER NOT NULL, risk_epoch TEXT NOT NULL DEFAULT '', graph_json TEXT NOT NULL,
    cake_choice INTEGER NULL CHECK(cake_choice IS NULL OR cake_choice IN (0,1)),
    status TEXT NOT NULL DEFAULT 'ACTIVE', version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL, updated_at REAL NOT NULL,
    UNIQUE(user_id, create_key), UNIQUE(id, user_id))""",
    """CREATE TABLE IF NOT EXISTS birthday_nodes(
    flow_id TEXT NOT NULL, user_id TEXT NOT NULL, idx INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'PENDING', version INTEGER NOT NULL DEFAULT 1,
    claim_version INTEGER NOT NULL DEFAULT 0, operation_id TEXT NULL UNIQUE,
    item_id TEXT NULL, quote_version INTEGER NULL, price_cents INTEGER NULL,
    quote_expires_at REAL NULL, result_json TEXT NULL,
    PRIMARY KEY(flow_id, idx),
    FOREIGN KEY(flow_id, user_id) REFERENCES birthday_flows(id, user_id))""",
    """CREATE TABLE IF NOT EXISTS birthday_tickets(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, flow_id TEXT NOT NULL,
    node_idx INTEGER NOT NULL, claim_version INTEGER NOT NULL, token_hash TEXT NOT NULL,
    effect_key TEXT NOT NULL UNIQUE, op_type TEXT NOT NULL, intents_json TEXT NOT NULL,
    payload_hash TEXT NULL, operation_id TEXT NULL UNIQUE,
    created_at REAL NOT NULL, expires_at REAL NOT NULL,
    FOREIGN KEY(flow_id, user_id) REFERENCES birthday_flows(id, user_id))""",
    """CREATE TABLE IF NOT EXISTS birthday_requests(
    user_id TEXT NOT NULL, request_key TEXT NOT NULL, action TEXT NOT NULL,
    args_hash TEXT NOT NULL, flow_id TEXT NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY(user_id, request_key),
    FOREIGN KEY(flow_id, user_id) REFERENCES birthday_flows(id, user_id))""",
)

FLOW_COLS = ("id", "user_id", "create_key", "request_hash", "recipient_label", "event_date", "account_id",
             "account_name", "budget_cents", "risk_epoch", "graph_json", "cake_choice", "status", "version",
             "created_at", "updated_at")
NODE_COLS = ("flow_id", "user_id", "idx", "state", "version", "claim_version", "operation_id", "item_id",
             "quote_version", "price_cents", "quote_expires_at", "result_json")


def bootstrap_birthday_store(bank):
    with bank.transaction() as db:
        for stmt in DDL:
            db.execute(stmt)


def _strict_int(v):
    return type(v) is int


def _need_tx(db):
    if not db.in_transaction:
        raise DomainError("TX_REQUIRED", "写操作必须在事务内执行", 500)


def _graph_nodes(graph):
    return graph.get("nodes", []) if isinstance(graph, dict) else list(graph)


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def load_flow(db, user, flow_id):
    if not isinstance(user, str) or not isinstance(flow_id, str):
        raise DomainError("NOT_FOUND", "生日流程不存在", 404)
    row = db.execute("SELECT " + ",".join(FLOW_COLS) + " FROM birthday_flows WHERE id=? AND user_id=?",
                     (flow_id, user)).fetchone()
    if row is None:
        raise DomainError("NOT_FOUND", "生日流程不存在", 404)
    return dict(zip(FLOW_COLS, tuple(row)))


def load_nodes(db, user, flow_id):
    rows = db.execute("SELECT " + ",".join(NODE_COLS) + " FROM birthday_nodes WHERE flow_id=? AND user_id=? ORDER BY idx",
                      (flow_id, user)).fetchall()
    return {r["idx"]: r for r in (dict(zip(NODE_COLS, tuple(x))) for x in rows)}


def _item_name(bank, item_id):
    if item_id is None:
        return None
    from .birthday_orders import CATALOG
    item = CATALOG.get(item_id) if isinstance(CATALOG, dict) else None
    return item.get("name") if isinstance(item, dict) else None


def view_flow(bank, db, user, flow):
    if flow.get("user_id") != user:
        raise DomainError("NOT_FOUND", "生日流程不存在", 404)
    try:
        graph = json.loads(flow["graph_json"])
    except (TypeError, ValueError):
        raise DomainError("BAD_GRAPH", "生日流程图无效", 500)
    if _canon(graph) != _canon(json.loads(_canon(birthday_graph()))):
        raise DomainError("BAD_GRAPH", "生日流程图无效", 500)
    from . import birthday_tickets
    gnodes = _graph_nodes(graph)
    nodes = load_nodes(db, user, flow["id"])
    out = []
    for idx, n in sorted(nodes.items()):
        g = gnodes[idx] if 0 <= idx < len(gnodes) and isinstance(gnodes[idx], dict) else {}
        observed = n["state"]
        if idx in FINANCIAL:
            try:
                observed = birthday_tickets.observe_node(bank, db, user, flow, n)
            except DomainError:
                observed = "UNKNOWN"
            if not isinstance(observed, str):
                observed = "UNKNOWN"
        action = g.get("action") if isinstance(g.get("action"), dict) else {}
        ntype = action.get("type")
        if not isinstance(ntype, str):
            raise DomainError("BAD_GRAPH", "生日流程图无效", 500)
        d = {"idx": idx, "type": ntype, "state": n["state"], "observed_state": observed,
             "version": n["version"], "operation_id": n["operation_id"]}
        if action.get("gift") is not None:
            d["gift"] = action.get("gift")
        name = _item_name(bank, n["item_id"]) if idx in (2, 3) else None
        if name is not None:
            d["item_name"] = name
            d["price_cents"] = n["price_cents"]
        out.append(d)
    cc = flow["cake_choice"]
    return {"id": flow["id"], "recipient_label": flow["recipient_label"], "event_date": flow["event_date"],
            "account_id": flow["account_id"], "budget_cents": flow["budget_cents"],
            "cake_choice": None if cc is None else bool(cc), "status": flow["status"], "version": flow["version"],
            "simulated": True, "merchant_connected": False, "nodes": out}


def _choice_succeeded(db, user, flow):
    gnodes = _graph_nodes(json.loads(flow["graph_json"]))
    if len(gnodes) < 2 or not isinstance(gnodes[1], dict):
        raise DomainError("BAD_GRAPH", "生日流程图无效", 500)
    action = gnodes[1].get("action")
    if not isinstance(action, dict) or action.get("type") != "birthday_choice":
        raise DomainError("BAD_GRAPH", "生日流程图无效", 500)
    nodes = load_nodes(db, user, flow["id"])
    return nodes.get(1, {}).get("state") == "SUCCEEDED"


def cas_flow(db, user, flow_id, expected_version, changes):
    _need_tx(db)
    if not _strict_int(expected_version) or expected_version < 1:
        raise DomainError("INVALID_VERSION", "版本无效", 400)
    if not isinstance(changes, dict) or not changes or set(changes) - {"status", "cake_choice"}:
        raise DomainError("INVALID_FIELD", "不允许修改该字段", 400)
    flow = load_flow(db, user, flow_id)
    if flow["version"] != expected_version:
        raise DomainError("VERSION_CONFLICT", "版本冲突", 409)
    if "status" in changes:
        st = changes["status"]
        if not isinstance(st, str) or st not in FLOW_STATUSES:
            raise DomainError("INVALID_STATUS", "状态无效", 400)
        if flow["status"] in TERMINAL and st != flow["status"]:
            raise DomainError("TERMINAL_STATE", "终止状态不可恢复", 409)
        if st == "COMPLETED" and flow["status"] != "COMPLETED":
            ns = load_nodes(db, user, flow_id)
            if (len(ns) != 7 or any(v["state"] not in ("SUCCEEDED", "SKIPPED") for v in ns.values())
                    or ns.get(6, {}).get("state") != "SUCCEEDED"):
                raise DomainError("FLOW_INCOMPLETE", "流程尚未完成", 409)
            from . import birthday_tickets
            for i in sorted(FINANCIAL):
                try:
                    obs = birthday_tickets.observe_node(None, db, user, flow, ns[i])
                except Exception:
                    obs = "UNKNOWN"
                if obs not in ("SUCCEEDED", "SKIPPED"):
                    raise DomainError("FLOW_INCOMPLETE", "流程尚未完成", 409)
    if "cake_choice" in changes:
        cc = changes["cake_choice"]
        if cc is not None and not (_strict_int(cc) and cc in (0, 1)):
            raise DomainError("INVALID_CHOICE", "蛋糕选择无效", 400)
        if cc != flow["cake_choice"] and _choice_succeeded(db, user, flow):
            raise DomainError("CHOICE_LOCKED", "选择已完成不可更改", 409)
    cols = sorted(changes)
    now = time.time()
    sets = ",".join(c + "=?" for c in cols)
    cur = db.execute("UPDATE birthday_flows SET " + sets + ",version=version+1,updated_at=? WHERE id=? AND user_id=? AND version=?",
                     [changes[c] for c in cols] + [now, flow_id, user, expected_version])
    if cur.rowcount != 1:
        raise DomainError("VERSION_CONFLICT", "版本冲突", 409)
    return load_flow(db, user, flow_id)

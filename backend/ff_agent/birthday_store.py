import hashlib
import json
import re
import uuid
from datetime import date, timedelta

from .bank import DomainError
from .birthday_store_schema import (
    bootstrap_birthday_store, load_flow, load_nodes, view_flow, cas_flow, birthday_graph,
)
from .birthday_nodes import cas_node, claim_node, sync_nodes
from .birthday_reselection import reselect_node

__all__ = [
    "BirthdayStore", "DomainError", "bootstrap_birthday_store", "load_flow", "load_nodes",
    "view_flow", "cas_flow", "birthday_graph", "cas_node", "claim_node", "sync_nodes",
]

_KEY = re.compile(r"[A-Za-z0-9_.:-]{8,64}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_FLOW_COLS = (
    "id", "user_id", "create_key", "request_hash", "recipient_label", "event_date",
    "account_id", "account_name", "budget_cents", "risk_epoch", "graph_json",
    "cake_choice", "status", "version", "created_at", "updated_at",
)


def _hash(args):
    raw = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _graph_nodes(graph):
    nodes = graph["nodes"] if isinstance(graph, dict) else graph
    if not isinstance(nodes, list) or len(nodes) != 7:
        raise DomainError("BAD_GRAPH", "生日流程图无效", 500)
    return nodes


class BirthdayStore:
    def __init__(self, bank):
        self.bank = bank

    def _validate(self, user, recipient_label, event_date, account_id, budget_cents, key):
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise DomainError("INVALID_KEY", "请求键无效", 400)
        if not isinstance(recipient_label, str):
            raise DomainError("INVALID_LABEL", "收礼人称呼无效", 400)
        label = recipient_label.strip()
        if not 1 <= len(label) <= 40:
            raise DomainError("INVALID_LABEL", "收礼人称呼无效", 400)
        if type(budget_cents) is not int or not 1 <= budget_cents <= 100_000_000:
            raise DomainError("INVALID_BUDGET", "预算无效", 400)
        if not isinstance(event_date, str) or not _DATE.fullmatch(event_date):
            raise DomainError("INVALID_DATE", "日期无效", 400)
        try:
            date.fromisoformat(event_date)
        except ValueError:
            raise DomainError("INVALID_DATE", "日期无效", 400)
        if not isinstance(account_id, str) or not account_id:
            raise DomainError("INVALID_ACCOUNT", "账户无效", 400)
        return {"action": "create", "recipient_label": label, "event_date": event_date,
                "account_id": account_id, "budget_cents": budget_cents}

    def create(self, user, recipient_label, event_date, account_id, budget_cents, key):
        with self.bank.transaction() as db:
            return self._create_locked(db, user, recipient_label, event_date, account_id,
                                       budget_cents, key)

    def _create_locked(self, db, user, recipient_label, event_date, account_id, budget_cents, key):
        if not db.in_transaction:
            raise DomainError("TX_REQUIRED", "写操作必须在事务内执行", 500)
        args = self._validate(user, recipient_label, event_date, account_id, budget_cents, key)
        h = _hash(args)
        self.bank._user(db, user)
        req = db.execute(
            "SELECT action,args_hash,flow_id FROM birthday_requests WHERE user_id=? AND request_key=?",
            (user, key)).fetchone()
        if req:
            if req[0] != "create" or req[1] != h:
                raise DomainError("KEY_CONFLICT", "请求键冲突", 409)
            return view_flow(self.bank, db, user, load_flow(db, user, req[2]))
        now = self.bank.now()
        d = date.fromisoformat(event_date)
        if not now.date() <= d <= now.date() + timedelta(days=366):
            raise DomainError("DATE_OUT_OF_RANGE", "日期超出范围", 400)
        acct = db.execute("SELECT name FROM accounts WHERE id=? AND user_id=?",
                          (account_id, user)).fetchone()
        if not acct:
            raise DomainError("NOT_FOUND", "未找到可访问的数据", 404)
        epoch = self.bank._safety_epoch(db, user)
        if not isinstance(epoch, str):
            raise DomainError("BAD_EPOCH", "安全状态无效", 500)
        graph = birthday_graph()
        nodes = _graph_nodes(graph)
        ts = now.timestamp()
        fid = uuid.uuid4().hex
        vals = (fid, user, key, h, args["recipient_label"], event_date, account_id,
                str(acct[0]), budget_cents, epoch,
                json.dumps(graph, sort_keys=True, separators=(",", ":")),
                None, "ACTIVE", 1, ts, ts)
        db.execute("INSERT INTO birthday_flows (%s) VALUES (%s)" % (
            ",".join(_FLOW_COLS), ",".join("?" * len(_FLOW_COLS))), vals)
        db.executemany("INSERT INTO birthday_nodes (flow_id,user_id,idx) VALUES (?,?,?)",
                       [(fid, user, i) for i in range(len(nodes))])
        db.execute(
            "INSERT INTO birthday_requests (user_id,request_key,action,args_hash,flow_id,created_at)"
            " VALUES (?,?,?,?,?,?)", (user, key, "create", h, fid, ts))
        self.bank._event(db, user, None, "BIRTHDAY_FLOW_CREATED", f"生日流程 {fid} 已建立 / 本机模拟")
        return view_flow(self.bank, db, user, load_flow(db, user, fid))

    def get(self, user, flow_id):
        if not isinstance(flow_id, str) or not flow_id:
            raise DomainError("NOT_FOUND", "生日流程不存在", 404)
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            return view_flow(self.bank, db, user, load_flow(db, user, flow_id))

    def list(self, user):
        with self.bank.transaction() as db:
            self.bank._user(db, user)
            ids = [r[0] for r in db.execute(
                "SELECT id FROM birthday_flows WHERE user_id=? ORDER BY created_at DESC, id",
                (user,)).fetchall()]
            return [view_flow(self.bank, db, user, load_flow(db, user, i)) for i in ids]

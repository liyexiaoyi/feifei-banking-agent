from .bank import DomainError,PENDING
from .card_chat_auth import check_card_ticket
from .card_query import normalize_card_text


class VirtualDialogue:
    def __init__(self, bank):
        self.bank = bank

    def handle(self, user, account, key, context, plan_ticket=None):
        action = {"type": "virtual_card_apply", "account": account}
        try:
            with self.bank.transaction() as db:
                owner = self.bank._user(db, user)
                check_card_ticket(self.bank, db, user, plan_ticket, action, key)
                watermark = db.execute(
                    "SELECT COALESCE(MAX(rowid), 0) FROM operations WHERE user_id = ?",
                    (owner["id"],),
                ).fetchone()[0]
            owned = self.bank.snapshot(user).get("accounts", [])
            normalized = normalize_card_text(account) if isinstance(account, str) else ""
            aliases = {
                normalize_card_text("日常"): "日常账户",
                normalize_card_text("日常账户"): "日常账户",
                normalize_card_text("储蓄"): "储蓄账户",
                normalize_card_text("储蓄账户"): "储蓄账户",
            }
            canonical = aliases.get(normalized)
            if canonical:
                matches = [a for a in owned if a.get("name") == canonical]
            else:
                matches = [a for a in owned if isinstance(account, str) and a.get("name") == account]
            if len(matches) != 1:
                ctx = context if isinstance(context, dict) else {}
                old = ctx.get("pending_virtual_card")
                old = old if isinstance(old, dict) else {}
                now = self.bank.now().timestamp()
                pending = {
                    "account": account,
                    "started_at": old.get("started_at", now),
                    "operation_watermark": old.get("operation_watermark", watermark),
                }
                return {
                    "cards": [{"type": "virtual_accounts", "accounts": owned, "simulated": True}],
                    "lines": ["关联哪个账户？请提供名称或从下方选择。（本地演示每个账户限 1 张卡。）"],
                    "state": "WAITING_USER",
                    "working": {"pending_virtual_card": pending},
                }
            op = self.bank.prepare_virtual_card(user, matches[0]["id"], key, plan_ticket)
            status = op.get("status")
            if status in PENDING:
                line, state = "申请已准备，确认后才会生成虚拟演示卡", "WAITING_USER"
            elif status == "SUCCEEDED":
                line, state = "虚拟演示卡已生成。", "COMPLETED"
            else:
                line, state = "申请未能完成。", "BLOCKED"
            return {"cards": [{"type":"operation","operation":op}], "lines": [line], "state": state, "working": {}}
        except DomainError as exc:
            return {"cards": [], "lines": [exc.message], "state": "BLOCKED", "working": {}}

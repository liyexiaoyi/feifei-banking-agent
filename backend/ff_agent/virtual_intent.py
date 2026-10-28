import re

from .card_query import normalize_card_text


def _clarify(message):
    return [{"type": "clarify", "message": message}]


def _apply(account):
    return [{"type": "virtual_card_apply", "account": account}]


def parse_virtual_intent(text, context=None) -> list[dict] | None:
    if not isinstance(text, str):
        return None
    normalized = normalize_card_text(text).strip()
    if not normalized:
        return None

    cue = re.search(r"虚拟\s*(?:银行\s*)?卡", normalized)
    if not cue:
        alias = re.fullmatch(r"(日常|储蓄)(?:账户)?", normalized)
        if not alias or not isinstance(context, dict):
            return None
        pending = context.get("pending_virtual_card")
        if not isinstance(pending, dict):
            return None
        account = pending.get("account")
        if not isinstance(account, str) or not account.strip():
            return None
        if any(context.get(key) for key in (
            "pending_transfer", "pending_card", "pending_wealth"
        )):
            return _clarify("你是在选择申请虚拟卡的账户吗？")
        return _apply(alias.group(1) + "账户")

    if re.search(r"游戏|公交|显卡", normalized):
        return None
    if re.search(r"(?<!\d)\d{12,}(?!\d)", text + "\n" + normalized):
        return _clarify("你想了解虚拟卡的什么信息？")

    action = r"(?:申请|开通|办理|办(?!法))"
    negative = r"(?:不要|不用|无需|别|先不|不需要|不想|暂时不|不)"
    if re.search(r"取消|撤回", normalized) or re.search(negative + r"[^。！？!?]*?" + action, normalized):
        return _clarify("你需要了解虚拟卡的相关信息吗？")
    if re.search(r"怎么|如何|要不要|能不能|吗|什么资料|退款|退钱|是否", normalized):
        return _clarify("你想了解虚拟卡的哪方面信息？")
    if re.search(
        r"已(?:经)?(?:申请|开通|办理)|申请了|开通了|办好了|刚才.*成功|状态",
        normalized,
    ):
        return _clarify("你是想查询虚拟卡的申请状态吗？")

    accounts = [name + "账户" for name in ("日常", "储蓄") if name in normalized]
    if len(accounts) > 1:
        return _clarify("你想用日常账户还是储蓄账户申请虚拟卡？")
    if re.search(action, normalized):
        return _apply(accounts[0] if accounts else "未指定")
    return None

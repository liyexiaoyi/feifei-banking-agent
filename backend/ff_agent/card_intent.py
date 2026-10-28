import re

from .card_query import normalize_card_text, resolve_card_query

_NONBANK = re.compile(r"(?:显卡|卡通|门禁卡|公交卡|sim卡)", re.I)
_PAN = re.compile(r"(?<!\d)(?:\d[ -]?){11,}\d(?!\d)")
_QUESTION = re.compile(r"怎么|如何|怎样|要不要|会不会|能不能|是不是|是否|吗|成功了没|费用|收费|多少钱|退款")
_CARD_CUE = re.compile(r"银行卡|信用卡|借记卡|储蓄卡|日常账户|储蓄账户|账户|卡|挂失|解挂")
_DOUBLE_NEG = re.compile(r"(?:不是不|不能不|不可以不).{0,6}(?:挂失|解挂|解除挂失)")
_RESTORE = re.compile(r"解除挂失|解挂")
_STOP_RESTORE = re.compile(r"(?:不要|别(?:再)?|撤回本次|取消本次)(?:解除挂失|解挂)")
_STOP_LOST = re.compile(r"先\s*不要挂失|不要挂失|别(?:再)?挂失|撤回本次挂失|取消本次挂失")
_UNCLEAR = re.compile(r"哪(?:一)?张|某张|另一张|其他那张|其中一张|第[一二三四五六七八九\d]+张|尾号\s*\d{1,3}(?!\d)")


def _clarify(message):
    return {"type": "clarify", "message": message[:300]}


def _pending_value(pending, key):
    return pending.get(key) if isinstance(pending, dict) else getattr(pending, key, None)


def _pending_result(context, card):
    if isinstance(context, dict):
        pending = context.get("pending_card")
    else:
        pending = getattr(context, "pending_card", None) if context is not None else None
    if pending is None:
        return None
    mode = _pending_value(pending, "mode")
    action = _pending_value(pending, "action")
    if mode == "prepare" and action in ("report_lost", "restore"):
        return {"type": "card_status", "card": card, "action": action}
    if mode == "stop" and action in ("report_lost", "restore", "any"):
        return {"type": "card_stop", "card": card, "action": action}
    return None


def _target(s):
    digits = re.findall(r"(?<!\d)\d{4}(?!\d)", s)
    if len(digits) > 1:
        return None, True
    found = []
    if digits:
        found.append(digits[0])
    if re.search(r"日常(?:账户|卡|那张)", s):
        found.append("日常账户")
    if re.search(r"储蓄(?:账户|卡|那张)", s):
        found.append("储蓄账户")
    if (not found or re.search(r"(?:或者|或是|或|还是|以及|还有|和)\s*(?:刚才|之前|上一|这|那)", s)) and re.search(r"刚才那张|(?:上一|之前)那张|那张(?:卡)?|这张(?:卡)?", s):
        found.append("刚才那张")
    if re.search(r"未指定", s):
        found.append("未指定")
    if re.search(r"全部|所有卡", s):
        found.append("全部")
    if _UNCLEAR.search(s):
        return None, True
    unique = set(found)
    if len(unique) > 1:
        return None, True
    return (next(iter(unique)) if unique else "未指定"), False


def _parse_card_intent(text, context=None):
    s = normalize_card_text(text).strip()
    if not s:
        return None
    if _NONBANK.search(s):
        return None
    if _PAN.search(s):
        return _clarify("请勿发送完整卡号，请改用卡片别名或末四位。")
    if _QUESTION.search(s) and _CARD_CUE.search(s):
        return _clarify("请说明您是要办理挂失或解除挂失，还是查询处理状态。")
    if _DOUBLE_NEG.search(s):
        return _clarify("请确认您是否要办理挂失或解除挂失。")
    if re.search(r"没说.{0,4}(?:挂失|解挂)", s) or (_RESTORE.search(s) and "挂失" in _RESTORE.sub("", s)):
        return _clarify("请说明您这次要办理的卡片操作。")

    correction = re.fullmatch(
        r"(?:不是那张[，,\s]*(?:是|改成|换成)|改成|换成)\s*"
        r"(尾号\s*\d{4}(?:的)?(?:那张)?(?:银行卡|卡)?|"
        r"(?:日常|储蓄)(?:账户|那张|卡)(?:银行卡|卡)?)[吧。！!]*", s)
    if correction:
        card, unclear = _target(correction[1])
        return _clarify("要改成哪张卡？请只说一张卡的名称或末四位。") if unclear else {"type": "card_correct", "card": card}

    tail = re.fullmatch(r"(?:尾号)?(\d{4})", s)
    if tail:
        return _pending_result(context, tail[1])
    alias = re.fullmatch(r"就(?:选|用)?(日常|储蓄)(?:账户|卡)?那张(?:卡)?", s)
    if alias:
        return _pending_result(context, alias[1] + "账户")

    if re.search(r"查[^，,。；;！!？?]{0,24}(?:挂失|解挂)|(?:挂失|解挂)[^，,。；;！!？?]{0,24}(?:状态|进度|结果)", s):
        return _clarify("请确认您要查询的卡片及挂失或解挂申请。")
    if re.search(r"取消挂失", s) and not re.search(r"取消本次挂失", s):
        return _clarify("您是要停止本次挂失申请，还是解除已办理的挂失？")
    if _STOP_RESTORE.search(s):
        action = "restore"
        kind = "card_stop"
    elif _STOP_LOST.search(s):
        action = "report_lost"
        kind = "card_stop"
    elif re.search(r"不|别|无需|取消|撤回", s) and re.search(r"挂失|解挂", s):
        return _clarify("请确认您想办理的卡片操作。")
    else:
        action = "restore" if _RESTORE.search(s) else ("report_lost" if re.search(r"挂失", s) else None)
        kind = "card_status"

    if action:
        card, unclear = _target(s)
        if unclear or (card == "全部" and kind != "card_stop"):
            return _clarify("请明确指定一张卡片或提供单个末四位。")
        return {"type": kind, "card": card, "action": action}

    if re.search(r"(?:银行卡|卡片|我的卡|卡).{0,8}(?:丢了|不见了|找不到)|(?:丢了|不见了|找不到).{0,8}(?:银行卡|卡片|卡)", s):
        return {"type": "cards"}
    if re.search(r"(?:看看|看一下|看下|查看|查一下|查询|列出).{0,12}(?:我的)?(?:银行卡|卡片|卡)|(?:有哪些|有几张).{0,8}(?:银行卡|卡)", s):
        return {"type": "cards"}
    return None


def parse_card_intent(text, context=None):
    """Return bounded action suggestions; never authorize or perform banking."""
    action = _parse_card_intent(text, context)
    return [action] if action is not None else None

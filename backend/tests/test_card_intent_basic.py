from copy import deepcopy

import pytest

from ff_agent.card_intent import parse_card_intent


def assert_clarify(result):
    assert isinstance(result, list) and len(result) == 1
    assert set(result[0]) == {"type", "message"}
    assert result[0]["type"] == "clarify"
    assert isinstance(result[0]["message"], str)
    assert len(result[0]["message"]) <= 300


@pytest.mark.parametrize("text", ["看看我的银行卡", "银行卡丢了", "卡找不到了先看看"])
def test_cards_query(text):
    assert parse_card_intent(text) == [{"type": "cards"}]


def test_fullwidth_tail():
    assert parse_card_intent("挂失尾号１２３４的银行卡") == [
        {"type": "card_status", "card": "1234", "action": "report_lost"}]


@pytest.mark.parametrize("alias,card", [("日常", "日常账户"), ("储蓄", "储蓄账户")])
@pytest.mark.parametrize("verb,action", [("挂失", "report_lost"), ("解除挂失", "restore")])
def test_aliases(alias, card, verb, action):
    assert parse_card_intent(f"{verb}{alias}那张银行卡") == [
        {"type": "card_status", "card": card, "action": action}]


@pytest.mark.parametrize("text,action", [
    ("先不要挂失", "report_lost"), ("别挂失", "report_lost"),
    ("撤回本次挂失", "report_lost"), ("取消本次挂失", "report_lost"),
    ("别再解挂", "restore"), ("不要解除挂失", "restore"),
    ("要不要挂失", None), ("要不要取消本次挂失", None),
    ("银行卡怎么挂失", None), ("挂失会不会收费", None),
    ("是否挂失银行卡", None), ("银行卡挂失了吗", None), ("挂失成功了没", None)])
def test_imperative_stop_and_questions(text, action):
    result = parse_card_intent(text)
    if action is None:
        assert_clarify(result)
    else:
        assert result == [{"type": "card_stop", "card": "未指定", "action": action}]


@pytest.mark.parametrize("text", ["取消挂失", "不是不挂失", "不能不挂失", "挂失1234或5678那张银行卡"])
def test_ambiguous_requests(text):
    assert_clarify(parse_card_intent(text))


def test_correction():
    assert parse_card_intent("不是那张是储蓄那张") == [{"type": "card_correct", "card": "储蓄账户"}]


@pytest.mark.parametrize("text,action", [("挂失", "report_lost"), ("解挂", "restore")])
def test_no_target_sentinel(text, action):
    assert parse_card_intent(text) == [{"type": "card_status", "card": "未指定", "action": action}]


@pytest.mark.parametrize("pan", ["123456789012", "6222021234567890123"])
def test_full_pan_not_truncated(pan):
    result = parse_card_intent(f"挂失银行卡{pan}")
    assert_clarify(result)
    assert pan not in result[0]["message"]


@pytest.mark.parametrize("text", ["挂失显卡1234", "卡通", "挂失门禁卡1234", "挂失公交卡1234", "挂失SIM卡1234", "今天天气不错"])
def test_nonbank_and_unrelated(text):
    assert parse_card_intent(text) is None


@pytest.mark.parametrize("mode,kind", [("prepare", "card_status"), ("stop", "card_stop")])
@pytest.mark.parametrize("action", ["report_lost", "restore"])
@pytest.mark.parametrize("text,card", [("1234", "1234"), ("就储蓄那张", "储蓄账户")])
def test_pending_followup(mode, kind, action, text, card):
    context = {"pending_card": {"mode": mode, "action": action}}
    assert parse_card_intent(text, context) == [{"type": kind, "card": card, "action": action}]


@pytest.mark.parametrize("context", [None, {"memories": [{"card": "1234", "action": "report_lost"}]}, {"recent_turns": [{"role": "user", "content": "挂失尾号1234的银行卡"}]}])
@pytest.mark.parametrize("text,card", [("1234", None), ("挂失", "未指定"), ("挂失刚才那张银行卡", "刚才那张")])
def test_no_slot_inference(context, text, card):
    expected = None if card is None else [{"type": "card_status", "card": card, "action": "report_lost"}]
    assert parse_card_intent(text, context) == expected


@pytest.mark.parametrize("mode", ["prepare", "stop"])
def test_context_unchanged(mode):
    context = {"pending_card": {"mode": mode, "action": "report_lost"}, "memories": [{"card": "5678"}], "recent_turns": []}
    before = deepcopy(context)
    parse_card_intent("就储蓄那张", context)
    assert context == before

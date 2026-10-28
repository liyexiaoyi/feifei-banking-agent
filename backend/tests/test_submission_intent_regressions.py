"""Synthetic utterances from the final source review, not fraud accuracy data."""
import pytest

from ff_agent.card_intent import parse_card_intent
from ff_agent.clarification import render_clarify
from ff_agent.safety import assess_request
from ff_agent.virtual_intent import parse_virtual_intent
from ff_agent.wealth_amount import wealth_amount
from ff_agent.wealth_intent import parse_wealth_intent


@pytest.mark.parametrize('text', [
    '不要把日常卡挂失', '别把储蓄卡解挂',
    '查询日常卡挂失状态', '查看储蓄卡解除挂失结果',
])
def test_card_negation_and_status_question_never_prepare(text):
    result = parse_card_intent(text)
    assert result and result[0]['type'] in ('clarify', 'cards', 'card_stop')
    assert all(a['type'] != 'card_status' for a in result)


@pytest.mark.parametrize('text', ['取消申请虚拟卡', '撤回日常账户虚拟卡申请'])
def test_withdrawing_virtual_application_never_creates_one(text):
    result = parse_virtual_intent(text)
    assert result and result[0]['type'] == 'clarify'


@pytest.mark.parametrize('text', ['取消申购演示产品甲', '撤回赎回演示产品甲'])
def test_withdrawing_wealth_request_never_creates_a_trade(text):
    result = parse_wealth_intent(text)
    assert result and result[0]['type'] == 'clarify'


@pytest.mark.parametrize('text', ['给妈妈转账1e3元', '给妈妈转账1/2元'])
def test_unsupported_amount_notation_cannot_be_rephrased_as_its_tail(text):
    message, mode = render_clarify('', text, {}, ['clarify'])
    assert mode == 'template'
    assert '转账3元' not in message and '转账2元' not in message
    assert '明确' in message


@pytest.mark.parametrize('text', ['五元五毛', '买演示产品甲五元五毛', '五元五'])
def test_fractional_colloquial_amount_is_not_silently_truncated(text):
    assert wealth_amount(text) is None


@pytest.mark.parametrize('prefix', ['我要', '我想', '我打算', '我决定'])
def test_separate_affirmative_risk_instruction_survives_prior_negation(prefix):
    result = assess_request('不要共享屏幕' + prefix + '转300元到安全账户')
    assert result and result['hold'] is True and 'SAFE_ACCOUNT' in result['codes']


def test_incomplete_pending_wealth_amount_is_not_a_crash():
    pending = {'action': 'purchase', 'product': '演示产品甲', 'account': '未指定'}
    result = parse_wealth_intent('日常账户', {'pending_wealth': pending})
    assert result == [{'type': 'wealth_trade', 'action': 'purchase',
                       'product': '演示产品甲', 'account': '日常账户', 'amount_cents': None}]


def test_positive_requests_and_explicit_withdrawal_keep_their_original_meaning():
    assert parse_card_intent('挂失日常卡')[0]['type'] == 'card_status'
    assert parse_card_intent('不要挂失日常卡')[0]['type'] == 'card_stop'
    assert parse_virtual_intent('日常账户申请虚拟卡')[0]['type'] == 'virtual_card_apply'
    assert parse_wealth_intent('用日常账户买演示产品甲五元')[0]['amount_cents'] == 500
    assert assess_request('不要转300元到安全账户') is None

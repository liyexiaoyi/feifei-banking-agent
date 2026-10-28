from copy import deepcopy

import pytest

from ff_agent.virtual_intent import parse_virtual_intent


@pytest.mark.parametrize('text,account', [('给我申请张虚拟卡','未指定'),('给储蓄账户申请虚拟卡','储蓄账户'),
    ('给日常账户办虚拟卡','日常账户')])
def test_virtual_application_is_only_a_bounded_prepare_suggestion(text,account):
    assert parse_virtual_intent(text)==[{'type':'virtual_card_apply','account':account}]


@pytest.mark.parametrize('text', ['不要申请虚拟卡','先不申请虚拟卡','别给我办虚拟卡',
    '虚拟卡怎么申请','要不要申请虚拟卡','虚拟卡能退款吗','虚拟卡申请成功了吗',
    '虚拟卡已经申请了','日常或储蓄账户申请虚拟卡'])
def test_questions_negation_and_ambiguous_accounts_do_not_prepare(text):
    result=parse_virtual_intent(text)
    assert result and result[0]['type']=='clarify'


@pytest.mark.parametrize('text', ['先看看有哪些卡','今天天气怎样','游戏虚拟卡申请','公交虚拟卡开通'])
def test_nonbank_and_other_topics_are_not_virtual_application(text):
    assert parse_virtual_intent(text) is None


@pytest.mark.parametrize('account', ['日常账户','储蓄账户'])
def test_only_verified_virtual_pending_slot_supplies_application_purpose(account):
    context={'pending_virtual_card':{'account':'未指定'},'memories':[{'account':'日常账户'}]}
    original=deepcopy(context)
    assert parse_virtual_intent(account,context)==[{'type':'virtual_card_apply','account':account}]
    assert context==original
    assert parse_virtual_intent(account,{'memories':context['memories'],'recent_turns':[{'role':'user','content':'申请虚拟卡'}]}) is None


def test_conflicting_legacy_business_slots_require_clarification():
    context={'pending_virtual_card':{'account':'未指定'},'pending_transfer':{'payee':'妈妈'}}
    assert parse_virtual_intent('储蓄账户',context)[0]['type']=='clarify'


def test_full_card_number_is_never_returned_as_an_account_selector():
    number='6222000011112222333'
    result=parse_virtual_intent('给'+number+'申请虚拟卡')
    assert result[0]['type']=='clarify' and number not in str(result)

@pytest.mark.parametrize('text',['不用申请虚拟卡','不办虚拟卡','不申请日常账户虚拟卡','无需开通虚拟卡'])
def test_common_negative_wording_never_proposes_an_application(text):
    assert parse_virtual_intent(text)[0]['type']=='clarify'

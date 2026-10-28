from copy import deepcopy
import pytest
from ff_agent.wealth_intent import parse_wealth_intent

def action(amount=30000,product='演示产品甲',account='日常账户',trade='purchase'):
    return {'type':'wealth_trade','action':trade,'product':product,'account':account,'amount_cents':amount}

@pytest.mark.parametrize('text',['查询理财持仓和收益','看看有哪些演示产品','查询模拟理财'])
def test_wealth_query_does_not_prepare_a_trade(text):
    assert parse_wealth_intent(text)==[{'type':'wealth'}]

@pytest.mark.parametrize('text',['用日常账户买演示产品甲300元','日常账户申购演示产品甲三百元','日常账户购买演示产品甲３００．００元'])
def test_explicit_integer_cents_and_names_are_bounded_suggestions(text):
    assert parse_wealth_intent(text)==[action()]

def test_redemption_is_distinct_from_purchase():
    assert parse_wealth_intent('日常账户赎回演示产品甲300元')==[action(trade='redeem')]

@pytest.mark.parametrize('text',['不要买演示产品甲300元','不用申购演示产品甲','先不赎回演示产品甲','能不能买演示产品甲','申购演示产品甲会退款吗','要不要买理财'])
def test_negative_question_or_refund_never_prepares_a_trade(text):
    result=parse_wealth_intent(text);assert result and result[0]['type']=='clarify'

@pytest.mark.parametrize('amount',['一千二元','二三百元','大概300元','300元左右','300多元','1,000元','100.123元','-300元','0元','1000001元','几百元'])
def test_ambiguous_or_invalid_amount_is_missing_never_guessed(amount):
    result=parse_wealth_intent('日常账户买演示产品甲'+amount)
    assert result and result[0]['type']=='wealth_trade' and result[0]['amount_cents'] is None

@pytest.mark.parametrize('text',['演示产品甲','日常账户','300元'])
def test_only_verified_pending_wealth_can_give_a_bare_followup_purpose(text):
    pending=action(amount=None,product='未指定',account='未指定');pending.pop('type')
    context={'pending_wealth':pending};old=deepcopy(context)
    result=parse_wealth_intent(text,context);assert result and result[0]['type']=='wealth_trade'
    assert context==old
    assert parse_wealth_intent(text,{'memories':[pending],'recent_turns':[{'content':'买理财'}]}) is None

def test_new_trade_cannot_take_money_from_old_pending_or_notes():
    context={'pending_wealth':{k:v for k,v in action().items() if k!='type'}}
    result=parse_wealth_intent('申购演示产品乙',context)[0]
    assert result['amount_cents'] is None and result['account']=='未指定'

def test_conflicting_pending_business_must_clarify():
    context={'pending_wealth':{k:v for k,v in action().items() if k!='type'},'pending_card':{'mode':'prepare','action':'report_lost'}}
    assert parse_wealth_intent('300元',context)[0]['type']=='clarify'

@pytest.mark.parametrize('text',['买菜花300元','天气怎么样','你好'])
def test_unrelated_messages_do_not_become_wealth_business(text):
    assert parse_wealth_intent(text) is None

@pytest.mark.parametrize('text',['不要买理财','能否申购演示产品甲','先看看申购流程','赎回有什么规则'])
def test_clarification_has_a_bounded_message_and_information_questions_do_not_start_trade(text):
    result=parse_wealth_intent(text)[0]
    assert result['type']=='clarify'
    assert isinstance(result.get('message'),str) and 1<=len(result['message'])<=300

@pytest.mark.parametrize('text',['算了','不用了'])
def test_short_stop_cannot_continue_a_wealth_pending(text):
    ctx={'pending_wealth':{k:v for k,v in action(amount=None).items() if k!='type'}}
    assert parse_wealth_intent(text,ctx)[0]['type']=='clarify'

def test_bare_kuaiqian_is_a_money_followup_for_valid_pending():
    ctx={'pending_wealth':{k:v for k,v in action(amount=None).items() if k!='type'}}
    assert parse_wealth_intent('300块钱',ctx)==[action()]

@pytest.mark.parametrize('text,context',[('300元',{'pending_transfer':{'payee':'妈妈'}}),('储蓄账户',{'pending_virtual_card':{'account':'未指定'}})])
def test_other_business_without_a_wealth_pending_is_left_to_its_parser(text,context):
    assert parse_wealth_intent(text,context) is None


def test_card_refund_question_is_left_to_card_rules_without_wealth_clarification():
    assert parse_wealth_intent('挂失会退钱吗',{}) is None

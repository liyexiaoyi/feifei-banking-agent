import json
import httpx
import pytest
from ff_agent.planner import Planner,PlannerError,validate_plan

def action(amount=30000):
    return {'type':'wealth_trade','action':'purchase','product':'演示产品甲','account':'日常账户','amount_cents':amount}

def test_wealth_action_fields_and_missing_amount_are_strictly_bounded():
    for amount in [30000,None]:assert validate_plan({'actions':[action(amount)]})==[action(amount)]
    for extra in [{'confirmed':True},{'product_id':'demo_cash'},{'quote_version':1},{'risk':'GREEN'},{'amount_cents':True},{'amount_cents':0},{'action':'transfer'}]:
        with pytest.raises(PlannerError):validate_plan({'actions':[{**action(),**extra}]})

@pytest.mark.parametrize('mode',['rules','model'])
def test_explicit_trade_and_pending_money_followup_are_local(monkeypatch,mode):
    monkeypatch.setenv('FF_PLANNER_MODE',mode)
    planner=Planner()
    assert planner.plan('日常账户买演示产品甲300元',{})==[action()]
    assert planner.plan('300块钱',{'pending_wealth':{k:v for k,v in action(None).items() if k!='type'}})==[action()]

def test_old_history_does_not_supply_a_wealth_purpose_to_bare_amount():
    assert Planner().plan('300元',{'memories':[{'content':'买日常账户演示产品甲'}]})[0]['type']=='clarify'

def test_ambiguous_money_is_not_a_confirmable_wealth_operation():
    actions=Planner().plan('日常账户买演示产品甲三百块五',{})
    assert actions==[action(None)]

def test_green_query_and_negative_process_question_are_local_without_authorizing():
    assert Planner().plan('查询理财持仓和收益',{})==[{'type':'wealth'}]
    assert Planner().plan('先看看申购流程',{})[0]['type']=='clarify'

def test_model_only_receives_business_fields_not_pending_authority(monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','model');monkeypatch.setenv('FF_MODEL_API_KEY','test');monkeypatch.setenv('FF_MODEL_NAME','test');monkeypatch.setenv('FF_MODEL_BASE_URL','https://example.invalid/v1')
    seen=[]
    def reply(request):
        seen.append(json.loads(request.content));return httpx.Response(200,json={'choices':[{'message':{'content':'{"actions":[{"type":"clarify","message":"你好"}]}'}}]})
    pending={k:v for k,v in action(None).items() if k!='type'}
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        Planner(client=client).plan('你好',{'pending_wealth':{**pending,'source_plan_id':'private-source','operation_watermark':7,'started_at':99}})
    assert json.loads(seen[0]['messages'][1]['content'])['context']['pending_wealth']==pending
    assert 'private-source' not in json.dumps(seen)


def test_wealth_and_card_pending_collision_does_not_also_prepare_card():
    context={'pending_wealth':{k:v for k,v in action(None).items() if k!='type'},'pending_card':{'mode':'prepare','action':'report_lost'}}
    result=Planner().plan('0826',context)
    assert len(result)==1 and result[0]['type']=='clarify'

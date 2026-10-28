import pytest

from ff_agent.planner import Planner,PlannerError,validate_plan


def test_virtual_action_has_only_account_and_never_authority_fields():
    action={'type':'virtual_card_apply','account':'储蓄账户'}
    assert validate_plan({'actions':[action]})==[action]
    for extra in ({'confirmed':True},{'account_id':'a_saving'},{'risk':'GREEN'}):
        with pytest.raises(PlannerError):validate_plan({'actions':[{**action,**extra}]})


@pytest.mark.parametrize('mode',['rules','model'])
def test_explicit_virtual_apply_and_valid_account_followup_are_local_bounded_plans(monkeypatch,mode):
    monkeypatch.setenv('FF_PLANNER_MODE',mode)
    planner=Planner()
    assert planner.plan('给储蓄账户申请虚拟卡',{})==[{'type':'virtual_card_apply','account':'储蓄账户'}]
    context={'pending_virtual_card':{'account':'未指定'}}
    assert planner.plan('储蓄账户',context)==[{'type':'virtual_card_apply','account':'储蓄账户'}]


def test_expired_virtual_history_cannot_supply_purpose_to_a_bare_account(monkeypatch):
    monkeypatch.setenv('FF_PLANNER_MODE','model')
    context={'recent_turns':[{'role':'user','content':'申请虚拟卡'}]}
    assert Planner().plan('储蓄账户',context)[0]['type']=='clarify'


def test_virtual_process_question_is_one_clarification_not_duplicate_card_and_virtual_tasks():
    actions=Planner().plan('虚拟卡怎么申请',{})
    assert len(actions)==1 and actions[0]['type']=='clarify'


def test_compound_virtual_application_keeps_explicit_balance_query():
    actions=Planner().plan('查询余额，给日常账户申请虚拟卡',{})
    assert {'type':'balance'} in actions
    assert {'type':'virtual_card_apply','account':'日常账户'} in actions

from datetime import timedelta
from copy import deepcopy

from ff_agent.memory import Memory
from test_wealth_working import environment


def save(memory,value,order=1,session='first'):
    memory.save_working('demo',value,'',session,order)


def test_wealth_slot_persists_only_for_the_same_session_and_inputs_remain_unchanged(environment):
    bank,_,_,_,value=environment
    memory=Memory(bank)
    original=deepcopy(value)
    save(memory,value)
    assert memory.context('demo','储蓄账户','first')['pending_wealth']==value['pending_wealth']
    assert 'pending_wealth' not in memory.context('demo','储蓄账户','second')
    assert value==original


def test_wealth_context_resave_cannot_extend_its_independent_lifetime(environment):
    bank,_,_,now,value=environment
    memory=Memory(bank);save(memory,value)
    context=memory.context('demo','储蓄账户','first')
    assert 'pending_wealth' in context
    now[0]+=timedelta(seconds=601)
    save(memory,context,2)
    assert 'pending_wealth' not in memory.context('demo','储蓄账户','first')


def test_late_reply_or_new_risk_epoch_cannot_restore_an_old_wealth_intent(environment):
    bank,_,_,_,value=environment
    memory=Memory(bank);save(memory,value,2)
    assert 'pending_wealth' in memory.context('demo','储蓄账户','first')
    save(memory,{},3);save(memory,value,2)
    assert 'pending_wealth' not in memory.context('demo','储蓄账户','first')
    save(memory,value,4)
    bank.activate_safety_hold('demo',{'codes':['SAFE_ACCOUNT'],'reasons':['合成风险测试'],'advice':'先核对'},'hold')
    assert 'pending_wealth' not in memory.context('demo','储蓄账户','first')


def test_wealth_source_node_with_an_operation_cannot_become_a_selector_again(assessed_environment):
    environment=assessed_environment
    bank,plans,plan,_,value=environment
    memory=Memory(bank);save(memory,value)
    assert 'pending_wealth' in memory.context('demo','储蓄账户','first')
    with bank.transaction() as db:
        db.execute("UPDATE plan_store_plans SET status='RUNNING' WHERE id=?",(plan['id'],))
        db.execute("UPDATE plan_store_nodes SET state='RUNNING',token='test-claim' WHERE plan_id=?",(plan['id'],))
    key=plan['nodes'][0]['effect_key']
    bank.prepare_wealth('demo','a_daily','demo_cash','purchase',100,key,plan_ticket={'id':plan['id'],'idx':0,'token':'test-claim'})
    assert 'pending_wealth' not in memory.context('demo','储蓄账户','first')


# Explicit prerequisite for existing financial tests; never an application default.
from demo_assessment import consent_test_profile as _consent_test_profile
import pytest as _pytest_assessment

@_pytest_assessment.fixture
def assessed_environment(environment):
    _consent_test_profile(environment[0])
    return environment

from datetime import timedelta
from copy import deepcopy

from ff_agent.memory import Memory
from test_virtual_working import environment


def save(memory,value,order=1,session='first'):
    memory.save_working('demo',value,'',session,order)


def test_virtual_slot_persists_only_for_the_same_session_and_inputs_remain_unchanged(environment):
    bank,_,_,_,value=environment
    memory=Memory(bank)
    original=deepcopy(value)
    save(memory,value)
    assert memory.context('demo','储蓄账户','first')['pending_virtual_card']==value['pending_virtual_card']
    assert 'pending_virtual_card' not in memory.context('demo','储蓄账户','second')
    assert value==original


def test_virtual_context_resave_cannot_extend_its_independent_lifetime(environment):
    bank,_,_,now,value=environment
    memory=Memory(bank);save(memory,value)
    context=memory.context('demo','储蓄账户','first')
    assert 'pending_virtual_card' in context
    now[0]+=timedelta(seconds=601)
    save(memory,context,2)
    assert 'pending_virtual_card' not in memory.context('demo','储蓄账户','first')


def test_late_reply_or_new_risk_epoch_cannot_restore_an_old_virtual_intent(environment):
    bank,_,_,_,value=environment
    memory=Memory(bank);save(memory,value,2)
    assert 'pending_virtual_card' in memory.context('demo','储蓄账户','first')
    save(memory,{},3);save(memory,value,2)
    assert 'pending_virtual_card' not in memory.context('demo','储蓄账户','first')
    save(memory,value,4)
    bank.activate_safety_hold('demo',{'codes':['SAFE_ACCOUNT'],'reasons':['合成风险测试'],'advice':'先核对'},'hold')
    assert 'pending_virtual_card' not in memory.context('demo','储蓄账户','first')


def test_virtual_source_node_with_an_operation_cannot_become_a_selector_again(environment):
    bank,plans,plan,_,value=environment
    memory=Memory(bank);save(memory,value)
    assert 'pending_virtual_card' in memory.context('demo','储蓄账户','first')
    with bank.transaction() as db:
        db.execute("UPDATE plan_store_plans SET status='RUNNING' WHERE id=?",(plan['id'],))
        db.execute("UPDATE plan_store_nodes SET state='RUNNING',token='test-claim' WHERE plan_id=?",(plan['id'],))
    key=plan['nodes'][0]['effect_key']
    bank.prepare_virtual_card('demo','a_daily',key,{'id':plan['id'],'idx':0,'token':'test-claim'})
    assert 'pending_virtual_card' not in memory.context('demo','储蓄账户','first')

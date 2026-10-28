from copy import deepcopy
from datetime import datetime,timedelta

import pytest

from ff_agent.bank import Bank,TZ
from ff_agent.task_plan import PlanStore
from ff_agent.virtual_working import validate_virtual_pending


@pytest.fixture
def environment(tmp_path):
    now=[datetime(2026,10,27,3,tzinfo=TZ)]
    bank=Bank(tmp_path/'virtual-working.db',clock=lambda:now[0])
    plans=PlanStore(bank.path,clock=lambda:bank.now().timestamp())
    plan,_=plans.reserve('demo','virtual-clarify','申请虚拟卡','')
    action={'type':'virtual_card_apply','account':'未指定'}
    plan=plans.freeze('demo',plan['id'],plan['version'],[{'idx':0,'action':action,'deps':[]}])
    plan,token=plans.claim('demo',plan['id'],0,plan['version'],'')
    plans.settle('demo',plan['id'],0,token,'PREPARED',{'cards':[]})
    value={'safety_epoch':'','pending_virtual_card':{'account':'未指定','started_at':bank.now().timestamp(),
        'operation_watermark':0,'source_plan_id':plan['id'],'source_plan_idx':0}}
    yield bank,plans,plan,now,value
    plans.close()


def check(bank,value,user='demo',tuple_rows=False):
    with bank.transaction() as db:
        if tuple_rows:db.row_factory=None
        db.execute('PRAGMA query_only=ON')
        before=db.total_changes
        result=validate_virtual_pending(db,user,value,bank.now().timestamp())
        assert db.total_changes==before and db.execute('SELECT 1').fetchone()[0]==1
        return result


@pytest.mark.parametrize('tuple_rows',[False,True])
def test_valid_slot_is_read_only_cloned_and_does_not_preserve_extra_data(environment,tuple_rows):
    bank,_,_,_,value=environment
    original=deepcopy(value)
    value['pending_virtual_card']['not_allowed']='drop'
    result=check(bank,value,tuple_rows=tuple_rows)
    assert result=={'pending_virtual_card':original['pending_virtual_card']}
    assert result['pending_virtual_card'] is not value['pending_virtual_card']
    assert value['pending_virtual_card']['not_allowed']=='drop'


@pytest.mark.parametrize('change',[{'started_at':True},{'started_at':float('nan')},
    {'operation_watermark':True},{'operation_watermark':-1},{'operation_watermark':99},
    {'source_plan_id':'other'},{'source_plan_idx':True},{'account':'储蓄账户'}])
def test_invalid_schema_future_watermark_or_unmatched_frozen_action_is_rejected(environment,change):
    bank,_,_,_,value=environment
    value['pending_virtual_card'].update(change)
    assert check(bank,value)=={}


@pytest.mark.parametrize('seconds,valid',[(600,True),(600.001,False),(-1,False)])
def test_pending_deadline_is_independent_and_rejects_clock_rollback(environment,seconds,valid):
    bank,_,_,now,value=environment
    now[0]+=timedelta(seconds=seconds)
    assert ('pending_virtual_card' in check(bank,value)) is valid


def test_cross_user_and_stopped_source_plan_cannot_supply_a_virtual_application(environment):
    bank,_,plan,_,value=environment
    assert check(bank,value,'guest')=={}
    with bank.transaction() as db:
        db.execute("UPDATE plan_store_plans SET status='CANCELLED' WHERE id=?",(plan['id'],))
    assert check(bank,value)=={}


def test_new_owned_operation_supersedes_but_other_user_operation_does_not(environment):
    bank,_,_,_,value=environment
    bank.prepare_card_status('guest','card_b_daily','report_lost','guest-op',1)
    assert 'pending_virtual_card' in check(bank,value)
    bank.prepare_transfer('demo','p_mom',100,'new-own-intent')
    assert check(bank,value)=={}


def test_notes_and_history_cannot_supply_virtual_slots(environment):
    bank,_,_,_,value=environment
    assert check(bank,{'memories':[value],'recent_turns':[{'content':'申请虚拟卡'}]})=={}

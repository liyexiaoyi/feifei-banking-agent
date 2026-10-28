"""Scheduled mandates must authorize exact future cash, never reminders or replayed payments."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
import importlib
import importlib.util
import json

import pytest

from ff_agent.bank import Bank, DomainError


@pytest.fixture
def env(tmp_path):
    clock = [datetime.fromisoformat('2026-10-28T12:00:00+08:00')]
    return Bank(tmp_path/'schedule.db', clock=lambda: clock[0]), clock


def svc(env):
    assert importlib.util.find_spec('ff_agent.scheduled_transfers'), 'scheduled mandates not implemented'
    return importlib.import_module('ff_agent.scheduled_transfers').ScheduledTransfers(env[0])


def drive(env, user='demo', **kwargs):
    assert importlib.util.find_spec('ff_agent.scheduled_driver'), 'actual due execution not implemented'
    return importlib.import_module('ff_agent.scheduled_driver').ScheduledDriver(env[0]).run_due(user, **kwargs)


def prepare(env, amount=100, key='schedule-first', due=None, user='demo', payee='p_mom'):
    return svc(env).prepare(user, payee, amount, due or (env[1][0]+timedelta(hours=1)).isoformat(), key)


def authorize(env, item):
    op=item['operation']
    assert env[0].confirm('demo', op['id'])['status']=='WAITING_VERIFICATION'
    assert env[0].verify('demo', op['id'], '246810')['status']=='SUCCEEDED'
    return svc(env).get('demo',item['schedule']['id'])


def assets(env):
    with env[0].transaction() as db:
        return {t:[tuple(x) for x in db.execute('SELECT * FROM '+t+' ORDER BY id')] for t in ('accounts','transactions')}


def cash(env):
    return env[0].snapshot('demo')['accounts'][0]


def op_rows(env):
    with env[0].transaction() as db:
        return [dict(x) for x in db.execute('SELECT * FROM operations ORDER BY id')]


def due(env):
    env[1][0]+=timedelta(hours=1)


def test_prepare_exact_red_mandate_no_cash_hold_and_bound_authorization(env):
    old=assets(env); item=prepare(env,12345)
    s,op=item['schedule'],item['operation']
    assert s['status']=='AUTHORIZING' and s['version']==1
    assert op['type']=='scheduled_transfer_authorize' and op['risk']=='RED'
    assert op['payload']['amount_cents']==12345 and op['payload']['payee_name']=='妈妈'
    assert op['payload']['due_at_iso']==s['due_at']
    assert assets(env)==old and cash(env)['reserved_cents']==0
    with pytest.raises(DomainError):env[0].verify('demo',op['id'],'246810')
    assert drive(env)['processed']==[]
    ready=authorize(env,item)
    assert ready['status']=='ACTIVE' and ready['version']==2
    assert assets(env)==old and cash(env)['reserved_cents']==0
    assert svc(env).lookup_child('demo',op['id']) is None


def test_due_low_risk_uses_original_transfer_ledger_once_and_restart(env):
    item=prepare(env,12345); authorize(env,item); old=assets(env); start=cash(env)['balance_cents']
    assert drive(env)['processed']==[] and assets(env)==old
    due(env); out=drive(env); s=svc(env).get('demo',item['schedule']['id'])
    assert out['processed'][0]['action']=='EXECUTED' and s['status']=='SUCCEEDED'
    child=env[0].receipt('demo',s['child_operation_id'])['operation']
    assert child['type']=='transfer' and child['status']=='SUCCEEDED'
    assert set(child['payload'])=={'account_id','account_name','payee_id','payee_name','payee_phone','amount_cents','fee_cents'}
    assert cash(env)['balance_cents']==start-12345 and cash(env)['reserved_cents']==0
    with env[0].transaction() as db:
        tx=db.execute('SELECT * FROM transactions WHERE operation_id=?',(child['id'],)).fetchall()
        assert len(tx)==1 and tx[0]['amount_cents']==12345
    stable=assets(env); drive(env)
    reopened=Bank(env[0].path,clock=lambda:env[1][0]); env2=(reopened,env[1]);drive(env2)
    assert assets(env)==stable


def test_due_high_risk_requires_new_child_confirmation_and_verification(env):
    item=prepare(env,100001); authorize(env,item);old=assets(env);due(env);drive(env)
    s=svc(env).get('demo',item['schedule']['id']); cid=s['child_operation_id']
    assert s['status']=='CHILD_PENDING' and s['child_mode']=='MANUAL'
    assert env[0].receipt('demo',cid)['operation']['status']=='WAITING_CONFIRMATION'
    assert assets(env)==old and cash(env)['reserved_cents']==100001
    with pytest.raises(DomainError):env[0].verify('demo',cid,'246810')
    assert env[0].confirm('demo',cid)['status']=='WAITING_VERIFICATION'
    with pytest.raises(DomainError):env[0].verify('demo',cid,'000000')
    assert assets(env)==old
    assert env[0].verify('demo',cid,'246810')['status']=='SUCCEEDED'
    drive(env);assert svc(env).get('demo',s['id'])['status']=='SUCCEEDED'


def test_due_current_cross_business_risk_beats_initial_small_amount(env):
    item=prepare(env,100); authorize(env,item)
    other=env[0].prepare_transfer('demo','p_dad',100000,'before-due-other')
    assert env[0].confirm('demo',other['id'])['status']=='SUCCEEDED'
    due(env);old=assets(env);drive(env)
    s=svc(env).get('demo',item['schedule']['id'])
    assert s['child_mode']=='MANUAL' and s['child_status']=='WAITING_CONFIRMATION'
    assert assets(env)==old


@pytest.mark.parametrize('amount',[True,1.0,'100',0,-1,100000001])
def test_strict_amount_never_creates_or_moves_money(env,amount):
    old=assets(env)
    with pytest.raises(DomainError):prepare(env,amount)
    assert op_rows(env)==[] and assets(env)==old


@pytest.mark.parametrize('text',['2026-10-28T13:00:00','2026-10-28','not-time','2026-10-28T13:00:00.123+08:00','2026-10-28T12:00:59+08:00','2026-11-28T12:00:00+08:00'])
def test_explicit_bounded_due_time_required(env,text):
    with pytest.raises(DomainError):prepare(env,due=text)
    assert op_rows(env)==[]


def test_due_offset_normalization_and_replay_after_time_passed(env):
    item=prepare(env,due='2026-10-28T05:00:00+00:00')
    assert item['schedule']['due_at']=='2026-10-28T13:00:00+08:00'
    env[1][0]+=timedelta(days=3)
    replay=prepare(env,due='2026-10-28T13:00:00+08:00')
    assert replay['schedule']['id']==item['schedule']['id'] and replay['schedule']['replayed'] is True
    assert len(op_rows(env))==1
    with pytest.raises(DomainError):prepare(env,amount=101,due='2026-10-28T13:00:00+08:00')


@pytest.mark.parametrize('key',['short','task-forged-key','sched-forged-key','select-forged-key','invalid key'])
def test_reserved_or_invalid_request_key_rejected(env,key):
    with pytest.raises(DomainError):prepare(env,key=key)
    assert op_rows(env)==[]


def test_reads_no_expiry_or_automatic_drive_private_fields_hidden(env):
    item=prepare(env); env[1][0]+=timedelta(hours=2)
    before=op_rows(env)
    s=svc(env).get('demo',item['schedule']['id'])
    assert s['observed_status']=='AUTH_EXPIRED' and s['status']=='AUTHORIZING'
    assert len(svc(env).list('demo'))==1 and svc(env).list('guest')==[]
    with pytest.raises(DomainError):svc(env).get('guest',s['id'])
    assert op_rows(env)==before
    assert not any(x in s for x in ('user_id','request_key','request_hash','safety_epoch','payee_phone_digest'))
    assert s['payee_phone']=='138****0001' and s['simulated'] is True


def test_cancel_explicit_cas_and_replay_cannot_cancel_after_execution(env):
    item=prepare(env);s=authorize(env,item);old=assets(env)
    with pytest.raises(DomainError):svc(env).cancel('demo',s['id'],s['version'],'cancel-first',False)
    with pytest.raises(DomainError):svc(env).cancel('demo',s['id'],True,'cancel-first',True)
    with pytest.raises(DomainError):svc(env).cancel('demo',s['id'],1,'cancel-first',True)
    done=svc(env).cancel('demo',s['id'],s['version'],'cancel-first',True)
    assert done['status']=='CANCELLED'
    assert svc(env).cancel('demo',s['id'],s['version'],'cancel-first',True)['version']==done['version']
    due(env);drive(env);assert assets(env)==old and len(op_rows(env))==1


def test_cancel_pending_child_releases_only_normal_reservation(env):
    item=prepare(env,100001);authorize(env,item);due(env);drive(env)
    s=svc(env).get('demo',item['schedule']['id']);old=assets(env)
    done=svc(env).cancel('demo',s['id'],s['version'],'cancel-child',True)
    assert done['status']=='CANCELLED' and cash(env)['reserved_cents']==0 and assets(env)==old
    with pytest.raises(DomainError):env[0].confirm('demo',s['child_operation_id'])


@pytest.mark.parametrize('state',['UNKNOWN','EXECUTING','MANUAL_REVIEW'])
def test_unknown_child_never_resent_expired_or_freed_on_restart(env,state):
    item=prepare(env,100001);authorize(env,item);due(env);drive(env)
    s=svc(env).get('demo',item['schedule']['id']);cid=s['child_operation_id']
    with env[0].transaction() as db:db.execute('UPDATE operations SET status=? WHERE id=?',(state,cid))
    before=assets(env); env[1][0]+=timedelta(days=3)
    drive(env);s=svc(env).get('demo',s['id'])
    assert s['status']=='CHILD_IN_FLIGHT' and s['child_operation_id']==cid
    assert cash(env)['reserved_cents']==100001
    with pytest.raises(DomainError):svc(env).cancel('demo',s['id'],s['version'],'cancel-unknown',True)
    drive((Bank(env[0].path,clock=lambda:env[1][0]),env[1]))
    assert assets(env)==before and len(op_rows(env))==2


@pytest.mark.parametrize('change',['payee_name','phone','account_name','epoch','funds','grace'])
def test_due_rechecks_current_facts_and_never_silently_retries(env,change):
    item=prepare(env,100);authorize(env,item)
    with env[0].transaction() as db:
        if change=='payee_name':db.execute("UPDATE payees SET name='different' WHERE id='p_mom'")
        if change=='phone':db.execute("UPDATE payees SET phone='13899990001' WHERE id='p_mom'")
        if change=='account_name':db.execute("UPDATE accounts SET name='different' WHERE id='a_daily'")
        if change=='funds':db.execute("UPDATE accounts SET balance_cents=0 WHERE id='a_daily'")
    if change=='epoch':
        hold=env[0].activate_safety_hold('demo',{'codes':['TEST'],'reasons':['fixture'],'advice':'核对'},'risk-sched')
        env[0].end_safety_hold('demo',hold['id'])
    due(env)
    if change=='grace':env[1][0]+=timedelta(days=1,seconds=1)
    old=assets(env);drive(env)
    s=svc(env).get('demo',item['schedule']['id'])
    assert s['status']=='NOT_PAID' and s['child_operation_id'] is None
    assert assets(env)==old and len(op_rows(env))==1
    with env[0].transaction() as db:db.execute("UPDATE accounts SET balance_cents=2000000 WHERE id='a_daily'")
    drive(env);assert len(op_rows(env))==1


def test_parallel_due_runs_one_payment_and_one_child(env):
    item=prepare(env,11);authorize(env,item);start=cash(env)['balance_cents'];due(env)
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:drive(env),range(4)))
    assert cash(env)['balance_cents']==start-11 and len(op_rows(env))==2


def test_public_transfer_cannot_claim_child_prefix_or_replace_schedule(env):
    with pytest.raises(DomainError):env[0].prepare_transfer('demo','p_mom',1,'sched-forged')
    assert op_rows(env)==[]


def test_preoccupied_identical_child_key_is_not_adopted(env):
    item=prepare(env,100);s=authorize(env,item);due(env)
    with env[0].transaction() as db:
        from ff_agent.scheduled_rules import canonical_transfer
        row=db.execute('SELECT * FROM scheduled_transfers WHERE id=?',(s['id'],)).fetchone()
        forged=env[0]._create(db,'demo','transfer',canonical_transfer(row),'YELLOW','sched-'+s['id'])
    before=assets(env);drive(env)
    actual=svc(env).get('demo',s['id'])
    assert actual['status']=='NOT_PAID' and actual['outcome_code']=='CHILD_KEY_CONFLICT'
    assert actual['child_operation_id'] is None and assets(env)==before
    with pytest.raises(DomainError):env[0].confirm('demo',forged['id'])


def test_due_failure_rolls_back_actual_debit_and_child_creation(env):
    item=prepare(env,100);s=authorize(env,item);due(env);old=assets(env)
    with env[0].transaction() as db:
        db.execute("CREATE TRIGGER fail_scheduled_tx BEFORE INSERT ON transactions WHEN NEW.operation_id IS NOT NULL BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(Exception):drive(env)
    assert assets(env)==old and len(op_rows(env))==1
    with env[0].transaction() as db:db.execute('DROP TRIGGER fail_scheduled_tx')
    drive(env);assert svc(env).get('demo',s['id'])['status']=='SUCCEEDED'


def test_child_payload_tamper_and_due_tamper_block_common_paths(env):
    item=prepare(env,100001);authorize(env,item);due(env);drive(env)
    s=svc(env).get('demo',item['schedule']['id']);cid=s['child_operation_id'];before=assets(env)
    with env[0].transaction() as db:
        p=json.loads(db.execute('SELECT payload FROM operations WHERE id=?',(cid,)).fetchone()[0]);p['amount_cents']=1
        db.execute('UPDATE operations SET payload=? WHERE id=?',(json.dumps(p),cid))
    with pytest.raises(DomainError):env[0].confirm('demo',cid)
    assert assets(env)==before


@pytest.mark.parametrize('limit',[True,0,21,'2'])
def test_driver_limit_is_strict_and_bounded(env,limit):
    with pytest.raises(DomainError):drive(env,limit=limit)


@pytest.mark.parametrize('field,value',[('due_at',0),('grace_seconds',172800),('amount_cents',True),('due_at_iso','2026-10-28T13:00:00'),('version',1.5)])
def test_malformed_mandate_cannot_authorize_or_be_exposed_as_valid(env,field,value):
    item=prepare(env)
    # A bool stored by SQLite becomes integer1; rebind the actual auth payload/hash
    # so the type counterexample is instead placed directly in its JSON payload.
    with env[0].transaction() as db:
        if field=='amount_cents':
            op=item['operation'];p=dict(op['payload']);p['amount_cents']=True
            db.execute('UPDATE operations SET payload=? WHERE id=?',(json.dumps(p),op['id']))
        else:db.execute('UPDATE scheduled_transfers SET '+field+'=? WHERE id=?',(value,item['schedule']['id']))
    with pytest.raises(DomainError):env[0].confirm('demo',item['operation']['id'])


@pytest.mark.parametrize('field,value',[('due_at',0),('grace_seconds',172800),('version',1.5)])
def test_immutable_context_validation_has_no_disabled_condition(env,field,value):
    item=prepare(env)
    from ff_agent.scheduled_rules import check_context
    with env[0].transaction() as db:
        db.execute('UPDATE scheduled_transfers SET '+field+'=? WHERE id=?',(value,item['schedule']['id']))
        row=dict(db.execute('SELECT * FROM scheduled_transfers WHERE id=?',(item['schedule']['id'],)).fetchone())
        with pytest.raises(DomainError):check_context(env[0],db,'demo',row)


def test_manual_child_reconfirm_never_removes_verification_and_success_not_reversed(env):
    item=prepare(env,100001);authorize(env,item);due(env);drive(env)
    s=svc(env).get('demo',item['schedule']['id']);cid=s['child_operation_id']
    first=env[0].confirm('demo',cid)
    assert first['status']=='WAITING_VERIFICATION'
    assert env[0].confirm('demo',cid)['status']=='WAITING_VERIFICATION'
    assert env[0].verify('demo',cid,'246810')['status']=='SUCCEEDED'
    with pytest.raises(DomainError):svc(env).cancel('demo',s['id'],s['version'],'cancel-after-paid',True)


def test_child_expiration_is_read_only_until_drive_and_never_recreates(env):
    item=prepare(env,100001);authorize(env,item);due(env);drive(env)
    s=svc(env).get('demo',item['schedule']['id']);old=assets(env)
    env[1][0]+=timedelta(seconds=301)
    before=op_rows(env);svc(env).get('demo',s['id']);assert op_rows(env)==before
    drive(env);done=svc(env).get('demo',s['id'])
    assert done['status']=='NOT_PAID' and done['child_operation_id']==s['child_operation_id']
    drive(env);assert len(op_rows(env))==2 and assets(env)==old


def test_existing_unknown_cash_hold_blocks_due_new_payment(env):
    item=prepare(env,100);authorize(env,item)
    other=env[0].prepare_transfer('demo','p_dad',2000000,'funds-held-other')
    with env[0].transaction() as db:db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?",(other['id'],))
    old=assets(env);due(env);drive(env)
    s=svc(env).get('demo',item['schedule']['id'])
    assert s['status']=='NOT_PAID' and s['outcome_code']=='INSUFFICIENT_FUNDS'
    assert cash(env)['reserved_cents']==2000000 and assets(env)==old


def test_verification_lock_defers_only_while_bounded_window_and_cancel_remains_available(env):
    item=prepare(env,100);s=authorize(env,item)
    with env[0].transaction() as db:db.execute('UPDATE users SET locked_until=? WHERE id=?',((env[1][0]+timedelta(hours=2)).timestamp(),'demo'))
    due(env);old=assets(env)
    assert drive(env)['processed'][0]['action']=='DEFERRED'
    assert svc(env).get('demo',s['id'])['status']=='ACTIVE'
    assert svc(env).cancel('demo',s['id'],s['version'],'cancel-while-locked',True)['status']=='CANCELLED'
    assert assets(env)==old


def test_direct_authorization_execute_without_bound_verification_rejected(env):
    item=prepare(env);old=assets(env)
    with env[0].transaction() as db:
        row=dict(db.execute('SELECT * FROM operations WHERE id=?',(item['operation']['id'],)).fetchone())
        with pytest.raises(DomainError):env[0]._execute(db,'demo',row)
    assert svc(env).get('demo',item['schedule']['id'])['status']=='AUTHORIZING' and assets(env)==old


def test_safety_hold_cancels_pending_authorization_but_not_unknown(env):
    item=prepare(env)
    env[0].activate_safety_hold('demo',{'codes':['FIXTURE'],'reasons':['fixture'],'advice':'核对'},'new-risk-hold')
    assert env[0].receipt('demo',item['operation']['id'])['operation']['status']=='CANCELLED'
    with pytest.raises(DomainError):env[0].confirm('demo',item['operation']['id'])


def test_twenty_waiting_children_cannot_starve_next_due_schedule(env):
    with env[0].transaction() as db:db.execute("UPDATE accounts SET balance_cents=20000000 WHERE id='a_daily'")
    items=[prepare(env,100001,key=f'waiting-schedule-{i}') for i in range(20)]
    for item in items:authorize(env,item)
    due(env);drive(env)
    assert all(svc(env).get('demo',x['schedule']['id'])['child_operation_id'] for x in items)
    extra=prepare(env,1,key='extra-due-after-waiters',due=(env[1][0]+timedelta(minutes=1)).isoformat())
    authorize(env,extra);env[1][0]+=timedelta(minutes=1);drive(env)
    assert svc(env).get('demo',extra['schedule']['id'])['child_operation_id'] is not None


def test_post_debit_business_failure_rolls_back_whole_child_and_payment(env,monkeypatch):
    item=prepare(env);authorize(env,item);due(env);old=assets(env);original=env[0]._event
    def fail(db,user,opid,event,detail):
        if event=='TRANSFER_SUCCEEDED':raise DomainError('TEST_AFTER_DEBIT','isolated fixture',409)
        return original(db,user,opid,event,detail)
    monkeypatch.setattr(env[0],'_event',fail)
    drive(env);s=svc(env).get('demo',item['schedule']['id'])
    assert s['status']=='NOT_PAID' and s['outcome_code']=='TEST_AFTER_DEBIT'
    assert len(op_rows(env))==1 and assets(env)==old


def test_unknown_driver_user_rejected(env):
    with pytest.raises(DomainError):drive(env,user='not-a-user')


@pytest.mark.parametrize('status',['CANCELLED','EXPIRED','UNKNOWN','EXECUTING','MANUAL_REVIEW'])
def test_authorization_outcome_is_observed_without_rewriting_schedule(env,status):
    item=prepare(env)
    with env[0].transaction() as db:db.execute('UPDATE operations SET status=? WHERE id=?',(status,item['operation']['id']))
    before=op_rows(env);s=svc(env).get('demo',item['schedule']['id'])
    assert s['status']=='AUTHORIZING' and s['observed_status']==status
    assert op_rows(env)==before

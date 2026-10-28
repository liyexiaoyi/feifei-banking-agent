"""Birthday holds share real simulated Bank availability; never book a merchant."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
import importlib
import importlib.util

import pytest
from ff_agent.bank import Bank,DomainError


@pytest.fixture
def env(tmp_path):
    now=[datetime.fromisoformat('2026-10-27T12:00:00+08:00')]
    return Bank(tmp_path/'birthday.db',clock=lambda:now[0]),now


def service(env):
    assert importlib.util.find_spec('ff_agent.birthday_budget') is not None,'birthday budget service not implemented'
    return importlib.import_module('ff_agent.birthday_budget').BirthdayBudgets(env[0])


def prepare(env,amount=100000,key='birthday-1',account='a_daily',event='2026-10-28'):
    return service(env).prepare('demo',account,amount,event,key)


def finish(env,op):
    bank=env[0];view=bank.confirm('demo',op['id'])
    if view['status']=='WAITING_VERIFICATION':view=bank.verify('demo',op['id'],'246810')
    assert view['status']=='SUCCEEDED'
    return view['result']['budget_id']


def balance(env):
    bank=env[0]
    with bank.transaction() as db:
        return [tuple(r) for r in db.execute('SELECT * FROM accounts ORDER BY id')],[tuple(r) for r in db.execute('SELECT * FROM transactions ORDER BY id')]


def account(env):return next(a for a in env[0].snapshot('demo')['accounts'] if a['id']=='a_daily')


def child(env,bid,amount=20000,status='UNKNOWN',key='child-unknown'):
    bank=env[0]
    with bank.transaction() as db:
        op=bank._create(db,'demo','birthday_order',{'budget_id':bid,'account_id':'a_daily','amount_cents':amount},'YELLOW',key)
        db.execute('UPDATE operations SET status=? WHERE id=?',(status,op['id']))
    return op['id']


def test_prepare_is_not_a_hold_until_explicit_confirmation_and_never_debits(env):
    s=service(env);before=balance(env);op=prepare(env)
    assert op['type']=='birthday_budget_reserve' and op['risk']=='YELLOW'
    assert account(env)['reserved_cents']==0
    bid=finish(env,op);view=s.get('demo',bid)
    assert view['amount_cents']==view['remaining_cents']==view['reserved_cents']==100000
    assert view['status']=='ACTIVE' and view['simulated'] is True
    assert account(env)['reserved_cents']==100000
    assert balance(env)==before


def test_large_hold_requires_bound_verification(env):
    s=service(env);op=prepare(env,100001)
    confirmed=env[0].confirm('demo',op['id']);assert confirmed['status']=='WAITING_VERIFICATION'
    assert account(env)['reserved_cents']==0
    with pytest.raises(DomainError):env[0].verify('guest',op['id'],'246810')
    assert finish(env,op) in {b['id'] for b in s.list('demo')}


@pytest.mark.parametrize('amount',[True,0,-1,1.0,100000001])
def test_invalid_amount_cannot_prepare(env,amount):
    with pytest.raises(DomainError):prepare(env,amount)


@pytest.mark.parametrize('event',['2026-02-30','2026-10-26','2027-10-29','明天'])
def test_dates_are_explicit_valid_and_bounded(env,event):
    with pytest.raises(DomainError):prepare(env,event=event)


def test_budget_and_transfer_compete_for_same_available_money(env):
    service(env);finish(env,prepare(env,1500000))
    with pytest.raises(DomainError) as e:env[0].prepare_transfer('demo','p_mom',600000,'compete')
    assert e.value.code=='INSUFFICIENT_FUNDS'


def test_transfer_reservation_is_rechecked_when_budget_is_confirmed(env):
    service(env);op=prepare(env,1500000)
    env[0].prepare_transfer('demo','p_mom',600000,'first-transfer')
    with pytest.raises(DomainError):finish(env,op)
    assert account(env)['reserved_cents']==600000


def test_repeat_prepare_reuses_original_without_second_hold(env):
    s=service(env);op=prepare(env);bid=finish(env,op)
    assert prepare(env)['id']==op['id']
    assert s.get('demo',bid)['reserved_cents']==100000 and account(env)['reserved_cents']==100000
    with pytest.raises(DomainError) as e:prepare(env,99999)
    assert e.value.code=='IDEMPOTENCY_CONFLICT'


def test_expiry_read_is_query_only_and_releases_only_definite_unallocated_hold(env):
    s=service(env);bid=finish(env,prepare(env));before=balance(env)
    with env[0].transaction() as db:stored=tuple(db.execute('SELECT * FROM birthday_budgets WHERE id=?',(bid,)).fetchone())
    env[1][0]+=timedelta(seconds=1801)
    assert s.get('demo',bid)['status']=='EXPIRED'
    assert account(env)['reserved_cents']==0 and balance(env)==before
    with env[0].transaction() as db:assert tuple(db.execute('SELECT * FROM birthday_budgets WHERE id=?',(bid,)).fetchone())==stored


def test_cancel_keeps_unknown_allocations_and_does_not_refund(env):
    s=service(env);bid=finish(env,prepare(env));opid=child(env,bid);before=balance(env)
    view=s.cancel('demo',bid,1)
    assert view['status']=='CANCELLED' and view['reserved_cents']==20000
    assert account(env)['reserved_cents']==20000 and balance(env)==before
    assert env[0].receipt('demo',opid)['recorded_status']=='UNKNOWN'
    with pytest.raises(DomainError):s.cancel('demo',bid,1)


def test_expired_budget_keeps_unknown_even_across_midnight(env):
    s=service(env);bid=finish(env,prepare(env));child(env,bid)
    env[1][0]+=timedelta(days=1)
    assert s.get('demo',bid)['status']=='EXPIRED'
    assert account(env)['reserved_cents']==20000


def test_cancellation_stops_known_unexecuted_children_only(env):
    s=service(env);bid=finish(env,prepare(env));op=child(env,bid,status='WAITING_CONFIRMATION',key='known-child')
    s.cancel('demo',bid,1)
    assert env[0].operation('demo',op)['status']=='CANCELLED'
    assert account(env)['reserved_cents']==0


def test_owner_and_account_ownership_are_not_client_claims(env):
    s=service(env);bid=finish(env,prepare(env))
    with pytest.raises(DomainError) as e:s.get('guest',bid)
    assert e.value.status==404
    with pytest.raises(DomainError):s.cancel('guest',bid,1)
    with pytest.raises(DomainError):prepare(env,account='b_daily',key='foreign')


def test_budget_risk_epoch_cannot_cross_new_safety_hold(env):
    service(env);op=prepare(env)
    env[0].activate_safety_hold('demo',{'codes':['SCAM'],'reasons':['测试'],'advice':'请核对'},'risk')
    with pytest.raises(DomainError):finish(env,op)
    assert account(env)['reserved_cents']==0


def test_concurrent_two_large_holds_cannot_reserve_more_than_balance(env):
    service(env);ops=[prepare(env,1500000,key='parallel-'+str(i)) for i in range(2)]
    def run(op):
        try:finish(env,op);return True
        except DomainError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(run,ops))
    assert sorted(result)==[False,True] and account(env)['reserved_cents']==1500000


def test_only_bound_child_amount_is_excluded_not_entire_budget(env):
    service(env);bid=finish(env,prepare(env));opid=child(env,bid,status='WAITING_CONFIRMATION')
    with env[0].transaction() as db:
        assert env[0]._reserved_amount(db,'demo','a_daily',opid)==80000
        assert env[0]._reserved_amount(db,'guest','b_daily',opid)==0


@pytest.mark.parametrize('state',['ACTIVE','CANCELLED','EXPIRED'])
def test_unknown_child_is_never_exempted_from_reservation_even_by_its_id(env,state):
    s=service(env);bid=finish(env,prepare(env));opid=child(env,bid)
    if state=='CANCELLED':s.cancel('demo',bid,1)
    if state=='EXPIRED':env[1][0]+=timedelta(seconds=1801)
    with env[0].transaction() as db:
        assert env[0]._reserved_amount(db,'demo','a_daily',opid)==(100000 if state=='ACTIVE' else 20000)


def test_replay_after_event_date_passes_returns_original_not_a_new_request(env):
    s=service(env);op=prepare(env);bid=finish(env,op);env[1][0]+=timedelta(days=2)
    assert prepare(env)['id']==op['id']
    assert s.get('demo',bid)['status']=='EXPIRED' and account(env)['reserved_cents']==0


def test_execute_outside_bank_transaction_cannot_create_a_ghost_hold(env):
    from ff_agent.db import connect
    s=service(env);op=prepare(env,100001);env[0].confirm('demo',op['id'])
    db=connect(env[0].path)
    try:
        assert not db.in_transaction
        row=dict(db.execute('SELECT * FROM operations WHERE id=?',(op['id'],)).fetchone())
        with pytest.raises(DomainError):s.execute(db,'demo',row)
        assert db.execute('SELECT count(*) FROM birthday_budgets').fetchone()[0]==0
    finally:db.close()


def test_confirmation_is_bound_to_account_identity(env):
    s=service(env);op=prepare(env,100001)
    with env[0].transaction() as db:db.execute("UPDATE accounts SET name='已更改名称' WHERE id='a_daily'")
    with pytest.raises(DomainError):finish(env,op)
    assert account(env)['reserved_cents']==0


def test_budget_views_are_select_only_even_after_expiry(env):
    s=service(env);bid=finish(env,prepare(env));env[1][0]+=timedelta(seconds=1801)
    with env[0].transaction() as db:before=db.execute('SELECT count(*) FROM audit').fetchone()[0]
    assert s.get('demo',bid)['status']=='EXPIRED'
    assert s.list('demo')[0]['status']=='EXPIRED'
    with env[0].transaction() as db:assert db.execute('SELECT count(*) FROM audit').fetchone()[0]==before


@pytest.mark.parametrize('amount',[True,100001])
def test_invalid_unknown_allocation_fails_closed_instead_of_releasing_hold(env,amount):
    service(env);bid=finish(env,prepare(env));child(env,bid,amount=amount)
    with pytest.raises(DomainError):account(env)


def test_cancel_does_not_relabel_an_already_expired_child_as_user_cancelled(env):
    s=service(env);bid=finish(env,prepare(env));cid=child(env,bid,status='WAITING_CONFIRMATION')
    env[1][0]+=timedelta(seconds=601)
    s.cancel('demo',bid,1)
    with env[0].transaction() as db:
        assert db.execute('SELECT status FROM operations WHERE id=?',(cid,)).fetchone()[0]=='EXPIRED'


def test_cancel_without_unknown_does_not_claim_unknown_money_is_retained(env):
    s=service(env);bid=finish(env,prepare(env));s.cancel('demo',bid,1)
    with env[0].transaction() as db:
        details=[r[0] for r in db.execute("SELECT detail FROM audit WHERE event='BIRTHDAY_BUDGET_CANCELLED'")]
    assert len(details)==1 and '未决订单金额仍保留' not in details[0]

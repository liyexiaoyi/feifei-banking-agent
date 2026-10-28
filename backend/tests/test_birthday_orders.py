"""Real simulated ledger effects: budget allocation is not another cash hold."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from copy import deepcopy
import importlib,importlib.util,json

import pytest
from ff_agent.bank import Bank,DomainError
from ff_agent.birthday_budget import BirthdayBudgets


@pytest.fixture
def env(tmp_path):
    clock=[datetime.fromisoformat('2026-10-27T12:00:00+08:00')]
    return Bank(tmp_path/'orders.db',clock=lambda:clock[0]),clock


def orders(env):
    assert importlib.util.find_spec('ff_agent.birthday_orders') is not None,'birthday orders not implemented'
    return importlib.import_module('ff_agent.birthday_orders').BirthdayOrders(env[0])


def complete(env,op):
    result=env[0].confirm('demo',op['id'])
    if result['status']=='WAITING_VERIFICATION':result=env[0].verify('demo',op['id'],'246810')
    assert result['status']=='SUCCEEDED'
    return result


def budget(env,amount=100000):
    op=BirthdayBudgets(env[0]).prepare('demo','a_daily',amount,'2026-10-28','budget-origin')
    return complete(env,op)['result']['budget_id']


def prepare(env,bid,item='flowers_simple',key='order-1',version=1):
    return orders(env).prepare('demo',bid,item,version,key)


def account(env):return next(x for x in env[0].snapshot('demo')['accounts'] if x['id']=='a_daily')


def assets(env):
    with env[0].transaction() as db:
        return [[tuple(x) for x in db.execute('SELECT * FROM '+t+' ORDER BY id')] for t in ('accounts','transactions')]


def test_catalog_is_server_priced_simulated_and_returns_independent_copies(env):
    s=orders(env);items=s.catalog();by={i['item_id']:i for i in items}
    assert {k:v['price_cents'] for k,v in by.items()}=={'flowers_simple':30000,'flowers_large':80000,'cake_small':20000,'cake_large':80000}
    assert all(i['simulated'] is True and i['merchant_connected'] is False for i in items)
    items[0]['price_cents']=1;assert s.catalog()[0]['price_cents']!=1
    assert {i['gift'] for i in s.catalog('cake')}=={'cake'}


def test_prepare_neither_debits_nor_double_reserves_and_amount_is_not_client_controlled(env):
    s=orders(env);bid=budget(env);before=assets(env);op=prepare(env,bid)
    assert op['type']=='birthday_order' and op['payload']['amount_cents']==30000
    assert op['status']=='WAITING_CONFIRMATION' and account(env)['reserved_cents']==100000
    assert assets(env)==before and s.list('demo',bid)==[]
    with pytest.raises(TypeError):s.prepare('demo',bid,'cake_small',1,'bad-amount',amount_cents=1)


def test_success_is_single_ledger_debit_and_conserves_other_available_money(env):
    s=orders(env);bid=budget(env);before=account(env);op=prepare(env,bid);done=complete(env,op)
    after=account(env);b=BirthdayBudgets(env[0]).get('demo',bid)
    assert before['balance_cents']-after['balance_cents']==30000 and b['spent_cents']==30000
    assert b['remaining_cents']==after['reserved_cents']==70000
    assert after['available_cents']==before['available_cents']
    assert done['result']['simulated'] is True and done['result']['merchant_connected'] is False
    ledger=s.list('demo',bid);assert len(ledger)==1
    assert s.get('demo',ledger[0]['id'])['child_operation_id']==op['id']
    with env[0].transaction() as db:
        tx=db.execute('SELECT * FROM transactions WHERE operation_id=?',(op['id'],)).fetchall()
        assert len(tx)==1 and tx[0]['kind']=='EXPENSE' and tx[0]['amount_cents']==30000


def test_repeated_confirmation_verification_and_restart_do_not_debit_twice(env):
    s=orders(env);bid=budget(env);op=prepare(env,bid);done=complete(env,op);before=assets(env)
    assert env[0].confirm('demo',op['id'])==done
    assert env[0].verify('demo',op['id'],'wrong-but-already-success')==done
    new=Bank(env[0].path,clock=lambda:env[1][0]);assert new.confirm('demo',op['id'])==done
    assert assets(env)==before and len(s.list('demo',bid))==1


def test_two_independent_gifts_share_budget_without_spending_it_twice(env):
    s=orders(env);bid=budget(env);ops=[prepare(env,bid,'flowers_large','flowers'),prepare(env,bid,'cake_small','cake')]
    before=account(env)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda op:complete(env,op),ops))
    assert all(x['status']=='SUCCEEDED' for x in results)
    b=BirthdayBudgets(env[0]).get('demo',bid)
    assert b['spent_cents']==100000 and b['reserved_cents']==0 and len(s.list('demo',bid))==2
    assert account(env)['available_cents']==before['available_cents']


def test_parallel_prepares_cannot_overallocate_budget(env):
    orders(env);bid=budget(env)
    def run(item):
        try:return prepare(env,bid,item,item)
        except DomainError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:ops=list(pool.map(run,['flowers_large','cake_large']))
    assert sum(x is not None for x in ops)==1 and account(env)['reserved_cents']==100000


def test_same_gift_cannot_have_two_live_or_successful_orders(env):
    orders(env);bid=budget(env);op=prepare(env,bid)
    with pytest.raises(DomainError):prepare(env,bid,'flowers_large','duplicate-gift')
    complete(env,op)
    with pytest.raises(DomainError):prepare(env,bid,'flowers_simple','duplicate-success')


def test_cancelled_known_order_can_be_prepared_as_new_explicit_request(env):
    orders(env);bid=budget(env);op=prepare(env,bid);env[0].cancel('demo',op['id'])
    assert prepare(env,bid,key='new-explicit')['id']!=op['id']


def test_replay_old_key_returns_original_after_expiry_and_quote_change(env,monkeypatch):
    s=orders(env);bid=budget(env);op=prepare(env,bid);env[1][0]+=timedelta(days=2)
    m=importlib.import_module('ff_agent.birthday_orders');changed=deepcopy(m.CATALOG)
    changed['flowers_simple']['price_cents']=31000;changed['flowers_simple']['quote_version']=2;monkeypatch.setattr(m,'CATALOG',changed)
    assert prepare(env,bid)['id']==op['id']
    with pytest.raises(DomainError):prepare(env,bid,'cake_small')
    assert s.list('demo',bid)==[]


@pytest.mark.parametrize('change',['cancel','expire_parent','version','epoch','account_name','quote'])
def test_changed_bound_state_rejects_without_debit(env,monkeypatch,change):
    s=orders(env);bid=budget(env);op=prepare(env,bid)
    if change=='cancel':BirthdayBudgets(env[0]).cancel('demo',bid,1)
    if change=='expire_parent':
        with env[0].transaction() as db:db.execute('UPDATE birthday_budgets SET expires_at=? WHERE id=?',(env[1][0].timestamp()-1,bid))
    if change=='version':
        with env[0].transaction() as db:db.execute('UPDATE birthday_budgets SET version=version+1 WHERE id=?',(bid,))
    if change=='epoch':env[0].activate_safety_hold('demo',{'codes':['SCAM'],'reasons':['测试'],'advice':'核对'},'new-risk')
    if change=='account_name':
        with env[0].transaction() as db:db.execute("UPDATE accounts SET name='改名账户' WHERE id='a_daily'")
    if change=='quote':
        m=importlib.import_module('ff_agent.birthday_orders');changed=deepcopy(m.CATALOG);changed['flowers_simple']['quote_version']=2;monkeypatch.setattr(m,'CATALOG',changed)
    before=assets(env)
    with pytest.raises(DomainError):complete(env,op)
    assert assets(env)==before and s.list('demo',bid)==[]


def test_pending_order_cannot_outlive_budget_even_when_own_confirmation_is_fresh(env):
    orders(env);bid=budget(env);env[1][0]+=timedelta(seconds=1790);op=prepare(env,bid)
    env[1][0]+=timedelta(seconds=11);before=assets(env)
    with pytest.raises(DomainError):complete(env,op)
    assert assets(env)==before


def test_cancel_budget_keeps_success_and_unknown_allocations_without_refund(env):
    s=orders(env);bid=budget(env);complete(env,prepare(env,bid));unknown=prepare(env,bid,'cake_small','unknown-cake')
    with env[0].transaction() as db:db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?",(unknown['id'],))
    before=assets(env);b=BirthdayBudgets(env[0]).cancel('demo',bid,1)
    assert b['spent_cents']==30000 and b['reserved_cents']==20000 and assets(env)==before
    with pytest.raises(DomainError):complete(env,unknown)
    with env[0].transaction() as db:assert env[0]._reserved_amount(db,'demo','a_daily',unknown['id'])==20000
    assert len(s.list('demo',bid))==1


@pytest.mark.parametrize('direction',['transfer_then_order','order_then_transfer','pending_order_then_transfer'])
def test_daily_risk_includes_actual_or_pending_orders_in_both_directions(env,direction):
    orders(env)
    if direction=='transfer_then_order':
        complete(env,env[0].prepare_transfer('demo','p_mom',100000,'first-transfer'))
        bid=budget(env);op=prepare(env,bid,'cake_small')
    else:
        bid=budget(env);order=prepare(env,bid,'flowers_large')
        if direction=='order_then_transfer':complete(env,order)
        op=env[0].prepare_transfer('demo','p_mom',30000,'second-transfer')
    assert op['risk']=='RED'
    before=assets(env);assert env[0].confirm('demo',op['id'])['status']=='WAITING_VERIFICATION'
    assert assets(env)==before


def test_risk_recomputed_at_confirmation_and_not_lowered_for_gift(env):
    orders(env);bid=budget(env);op=prepare(env,bid,'flowers_large')
    assert op['risk']=='YELLOW'
    transfer=env[0].prepare_transfer('demo','p_mom',30000,'other-pending')
    assert transfer['risk']=='RED'
    assert env[0].confirm('demo',op['id'])['status']=='WAITING_VERIFICATION'


def test_ownership_strict_versions_and_graph_ticket_rejection(env):
    s=orders(env);bid=budget(env)
    with pytest.raises(DomainError):s.prepare('guest',bid,'cake_small',1,'foreign')
    with pytest.raises(DomainError):prepare(env,bid,version=True)
    with pytest.raises(DomainError):s.prepare('demo',bid,'cake_small',1,'graph',plan_ticket={'id':'forged'})
    with pytest.raises(DomainError):prepare(env,bid,key='task-forged')
    with pytest.raises(DomainError):prepare(env,bid,item='unknown-item')
    op=prepare(env,bid);complete(env,op);oid=s.list('demo',bid)[0]['id']
    assert s.list('guest')==[]
    with pytest.raises(DomainError):s.get('guest',oid)


def test_ready_order_still_allocates_budget_so_second_order_cannot_overbook(env):
    orders(env);bid=budget(env);op=prepare(env,bid,'flowers_large')
    with env[0].transaction() as db:db.execute("UPDATE operations SET status='READY' WHERE id=?",(op['id'],))
    with pytest.raises(DomainError):prepare(env,bid,'cake_large','overbook-after-ready')


def test_ready_order_counts_toward_daily_transfer_risk(env):
    orders(env);bid=budget(env);op=prepare(env,bid,'flowers_large')
    with env[0].transaction() as db:db.execute("UPDATE operations SET status='READY' WHERE id=?",(op['id'],))
    transfer=env[0].prepare_transfer('demo','p_mom',30000,'after-ready')
    assert transfer['risk']=='RED'


def test_ready_sibling_still_blocks_duplicate_gift_even_with_spare_budget(env):
    orders(env);bid=budget(env,200000);op=prepare(env,bid,'flowers_large')
    with env[0].transaction() as db:db.execute("UPDATE operations SET status='READY' WHERE id=?",(op['id'],))
    with pytest.raises(DomainError) as error:prepare(env,bid,'flowers_simple','another-flower')
    assert error.value.code=='GIFT_ALREADY_ORDERED'


def test_order_expense_keeps_iso_date_and_appears_in_bill_report(env):
    orders(env);bid=budget(env);op=prepare(env,bid);complete(env,op)
    report=env[0].analyze('demo','2026-10')
    assert any(x['name']=='生日礼物' and x['amount_cents']==30000 for x in report['categories'])
    with env[0].transaction() as db:
        assert db.execute('SELECT occurred_at FROM transactions WHERE operation_id=?',(op['id'],)).fetchone()[0]==env[1][0].isoformat()


def test_each_actual_order_has_authoritative_audit_event(env):
    orders(env);bid=budget(env);op=prepare(env,bid);done=complete(env,op)
    with env[0].transaction() as db:
        rows=db.execute("SELECT detail FROM audit WHERE operation_id=? AND event='BIRTHDAY_ORDER_SUCCEEDED'",(op['id'],)).fetchall()
    assert len(rows)==1 and done['result']['transaction_id'] in rows[0][0] and '未连接商户' in rows[0][0]

from datetime import datetime,timedelta
from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from ff_agent.bank import Bank,DomainError,TZ

@pytest.fixture
def env(tmp_path):
    now=[datetime(2026,10,27,3,20,tzinfo=TZ)]
    return Bank(tmp_path/'wealth.db',clock=lambda:now[0]),now

def prepare(bank,action='purchase',amount=10000,key='wealth',account='a_daily',product='demo_cash'):
    return bank.prepare_wealth('demo',account,product,action,amount,key)

def execute(bank,op):
    assert bank.confirm('demo',op['id'])['status']=='WAITING_VERIFICATION'
    return bank.verify('demo',op['id'],'246810')

def position(bank,account='a_daily',product='demo_cash'):
    return next(p for p in bank.wealth('demo')['positions'] if p['account_id']==account and p['product_id']==product)

def balance(bank):
    return next(a for a in bank.snapshot('demo')['accounts'] if a['id']=='a_daily')

def test_wealth_query_is_green_owned_and_explicitly_simulated(env):
    bank,_=env
    result=bank.wealth('demo')
    assert result['simulated'] is True and result['valuation_note']
    assert len(result['products'])==2
    assert all(p['simulated'] is True and p['nav_cents']==100 for p in result['products'])
    assert result['positions']==[]
    assert bank.wealth('guest')['positions']==[]
    assert bank.snapshot('demo')['operations']==[]

def test_purchase_requires_red_confirmation_and_verification_then_replays_once(assessed_env):
    env=assessed_env
    bank,_=env;before=balance(bank)['balance_cents'];bill=bank.analyze('demo','2026-10')
    op=prepare(bank)
    assert op['risk']=='RED' and op['type']=='wealth_purchase'
    assert balance(bank)['reserved_cents']==10000 and balance(bank)['balance_cents']==before
    assert bank.wealth('demo')['positions']==[]
    with pytest.raises(DomainError):bank.verify('demo',op['id'],'246810')
    done=execute(bank,op)
    assert done['status']=='SUCCEEDED' and done['result']['simulated'] is True
    assert balance(bank)['balance_cents']==before-10000 and balance(bank)['reserved_cents']==0
    p=position(bank);assert p['units_1e4']==1000000 and p['cost_cents']==p['value_cents']==10000 and p['gain_cents']==0
    assert bank.wealth('guest')['positions']==[]
    assert prepare(bank)['id']==op['id']
    assert bank.verify('demo',op['id'],'246810')['result']==done['result']
    assert balance(bank)['balance_cents']==before-10000
    after=bank.analyze('demo','2026-10')
    for k in ['expense_cents','income_cents','refund_cents','net_expense_cents']:assert after[k]==bill[k]
    assert len([t for t in after['transactions'] if t['kind']=='INVESTMENT_PURCHASE'])==1

def test_redemption_reduces_position_and_returns_cash_once(assessed_env):
    env=assessed_env
    bank,_=env;before=balance(bank)['balance_cents'];execute(bank,prepare(bank))
    op=prepare(bank,'redeem',4000,'redeem')
    assert op['risk']=='RED' and op['type']=='wealth_redemption'
    p=position(bank);assert p['reserved_units_1e4']==400000 and p['available_units_1e4']==600000
    execute(bank,op)
    p=position(bank);assert p['units_1e4']==600000 and p['cost_cents']==6000 and p['gain_cents']==0
    execute(bank,prepare(bank,'redeem',6000,'redeem-all'))
    p=position(bank);assert p['units_1e4']==p['cost_cents']==0 and balance(bank)['balance_cents']==before
    with pytest.raises(DomainError):prepare(bank,'redeem',1,'oversell')

def test_wealth_and_transfer_reserve_same_cash_without_changing_transfer_risk(assessed_env):
    env=assessed_env
    bank,_=env
    prepare(bank,amount=1900000)
    with pytest.raises(DomainError):bank.prepare_transfer('demo','p_mom',200000,'too-much')
    transfer=bank.prepare_transfer('demo','p_mom',50000,'within')
    assert transfer['risk']=='YELLOW'
    assert balance(bank)['reserved_cents']==1950000
    with pytest.raises(DomainError):prepare(bank,amount=100000,key='again')

@pytest.mark.parametrize('state',['UNKNOWN','EXECUTING','MANUAL_REVIEW'])
def test_unresolved_wealth_reservation_survives_ttl_and_blocks_replay(assessed_env,state):
    env=assessed_env
    bank,now=env;op=prepare(bank,amount=1900000)
    with bank.transaction() as db:db.execute('UPDATE operations SET status=? WHERE id=?',(state,op['id']))
    now[0]+=timedelta(seconds=601)
    assert balance(bank)['reserved_cents']==1900000
    with pytest.raises(DomainError):prepare(bank,amount=1900000)
    with pytest.raises(DomainError):bank.prepare_transfer('demo','p_mom',200000,'blocked')
    try:bank.cancel('demo',op['id'])
    except DomainError:pass
    assert balance(bank)['reserved_cents']==1900000

def test_known_pending_expiry_releases_cash(assessed_env):
    env=assessed_env
    bank,now=env;prepare(bank,amount=1900000);now[0]+=timedelta(seconds=301)
    assert balance(bank)['reserved_cents']==0
    assert prepare(bank,amount=1900000,key='new')['status']=='WAITING_CONFIRMATION'

@pytest.mark.parametrize('amount',[True,False,0,-1,1.5,100000001,'100'])
def test_wealth_rejects_non_integer_or_out_of_range_amount(env,amount):
    bank,_=env
    with pytest.raises(DomainError):prepare(bank,amount=amount)
    assert bank.snapshot('demo')['operations']==[]

def test_owner_product_input_and_key_binding_are_strict(assessed_env):
    env=assessed_env
    bank,_=env
    for kw in [{'account':'b_daily'},{'product':'real-product'},{'action':'transfer'}]:
        with pytest.raises(DomainError):prepare(bank,**kw)
    op=prepare(bank)
    with pytest.raises(DomainError):prepare(bank,amount=10001)
    with pytest.raises(DomainError):bank.confirm('guest',op['id'])

def test_quote_change_before_verification_does_not_mutate_assets(assessed_env):
    env=assessed_env
    bank,_=env;before=balance(bank)['balance_cents'];op=prepare(bank);bank.confirm('demo',op['id'])
    with bank.transaction() as db:db.execute('UPDATE wealth_products SET nav_cents=101,quote_version=2 WHERE id=?',('demo_cash',))
    with pytest.raises(DomainError):bank.verify('demo',op['id'],'246810')
    assert balance(bank)['balance_cents']==before and bank.wealth('demo')['positions']==[]

def test_concurrent_new_purchase_keys_cannot_overdraw(assessed_env):
    env=assessed_env
    bank,_=env
    def attempt(index):
        try:return prepare(bank,amount=1500000,key=str(index))['id']
        except DomainError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
    assert sum(r is not None for r in results)==1
    assert balance(bank)['reserved_cents']==1500000

def test_safety_hold_cancels_known_wealth_and_new_requests_need_current_epoch(assessed_env):
    env=assessed_env
    bank,_=env;op=prepare(bank)
    finding={'codes':['SAFE_ACCOUNT'],'reasons':['测试风险'],'advice':'先核实','hold':True}
    hold=bank.activate_safety_hold('demo',finding,'hold')
    assert bank.operation('demo',op['id'])['status']=='CANCELLED'
    with pytest.raises(DomainError):prepare(bank,key='under-hold')
    bank.end_safety_hold('demo',hold['id'])
    with pytest.raises(DomainError):bank.prepare_wealth('demo','a_daily','demo_cash','purchase',100,'old-epoch',expected_epoch='')
    assert prepare(bank,key='fresh')['risk']=='RED'


def test_same_key_after_quote_change_keeps_original_request_and_expiry(assessed_env):
    env=assessed_env
    bank,now=env;op=prepare(bank)
    with bank.transaction() as db:db.execute('UPDATE wealth_products SET nav_cents=101,quote_version=2 WHERE id=?',('demo_cash',))
    cached=prepare(bank)
    assert cached['id']==op['id'] and cached['payload']['nav_cents']==100
    # The immutable quote is checked before confirmation as well as execution.
    with pytest.raises(DomainError) as error:bank.confirm('demo',op['id'])
    assert error.value.code=='QUOTE_CHANGED'
    assert bank.operation('demo',op['id'])['status']=='WAITING_CONFIRMATION'
    with pytest.raises(DomainError) as unconfirmed:bank.verify('demo',op['id'],'246810')
    assert unconfirmed.value.code=='CONFIRMATION_REQUIRED'
    now[0]+=timedelta(seconds=301)
    assert balance(bank)['reserved_cents']==0

def test_non_divisible_demo_quote_does_not_round_units(assessed_env):
    env=assessed_env
    bank,_=env
    with bank.transaction() as db:db.execute('UPDATE wealth_products SET nav_cents=101,quote_version=2 WHERE id=?',('demo_cash',))
    with pytest.raises(DomainError):prepare(bank,amount=100)
    op=prepare(bank,amount=101,key='exact')
    assert op['payload']['units_1e4']==10000

@pytest.mark.parametrize('state',['UNKNOWN','EXECUTING','MANUAL_REVIEW'])
def test_unresolved_redemption_keeps_units_and_new_request_cannot_sell_them(assessed_env,state):
    env=assessed_env
    bank,now=env;execute(bank,prepare(bank));op=prepare(bank,'redeem',8000,'redeem')
    with bank.transaction() as db:db.execute('UPDATE operations SET status=? WHERE id=?',(state,op['id']))
    now[0]+=timedelta(seconds=601)
    p=position(bank);assert p['reserved_units_1e4']==800000 and p['available_units_1e4']==200000
    with pytest.raises(DomainError):prepare(bank,'redeem',1000,'another')
    with pytest.raises(DomainError):bank.verify('demo',op['id'],'246810')
    assert position(bank)['units_1e4']==1000000

def test_two_redemptions_cannot_reserve_more_than_position(assessed_env):
    env=assessed_env
    bank,_=env;execute(bank,prepare(bank));prepare(bank,'redeem',7000,'one')
    with pytest.raises(DomainError):prepare(bank,'redeem',4000,'two')
    assert position(bank)['reserved_units_1e4']==700000

def test_same_key_concurrent_purchase_verification_changes_money_once(assessed_env):
    env=assessed_env
    bank,_=env;before=balance(bank)['balance_cents'];op=prepare(bank);bank.confirm('demo',op['id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:bank.verify('demo',op['id'],'246810'),range(2)))
    assert all(r['status']=='SUCCEEDED' for r in results)
    assert results[0]['result']==results[1]['result']
    assert balance(bank)['balance_cents']==before-10000 and position(bank)['units_1e4']==1000000

def test_prepared_trade_rejects_deleted_or_old_epoch_without_asset_change(assessed_env):
    env=assessed_env
    bank,_=env;op=prepare(bank);bank.confirm('demo',op['id']);before=balance(bank)['balance_cents']
    with bank.transaction() as db:db.execute('UPDATE wealth_operation_epochs SET epoch=? WHERE operation_id=?',('obsolete',op['id']))
    with pytest.raises(DomainError):bank.verify('demo',op['id'],'246810')
    assert balance(bank)['balance_cents']==before and bank.wealth('demo')['positions']==[]

def test_transfer_already_reserved_cash_blocks_new_purchase_and_unknown_transfer_keeps_it(env):
    bank,now=env;op=bank.prepare_transfer('demo','p_mom',1900000,'transfer')
    with pytest.raises(DomainError):prepare(bank,amount=200000)
    with bank.transaction() as db:db.execute("UPDATE operations SET status='EXECUTING' WHERE id=?",(op['id'],))
    now[0]+=timedelta(seconds=601)
    assert balance(bank)['reserved_cents']==1900000
    with pytest.raises(DomainError):prepare(bank,amount=200000)


def test_restart_preserves_position_quote_and_no_second_ledger(assessed_env):
    env=assessed_env
    bank,now=env;execute(bank,prepare(bank))
    with bank.transaction() as db:db.execute('UPDATE wealth_products SET nav_cents=105,quote_version=2 WHERE id=?',('demo_cash',))
    before=position(bank);reopened=Bank(bank.path,clock=lambda:now[0])
    assert position(reopened)==before
    product=next(p for p in reopened.wealth('demo')['products'] if p['id']=='demo_cash')
    assert product['nav_cents']==105 and product['quote_version']==2

def test_prepared_sibling_cannot_execute_when_original_trade_becomes_unknown(assessed_env):
    env=assessed_env
    bank,_=env;one=prepare(bank,key='one');two=prepare(bank,key='two')
    bank.confirm('demo',two['id'])
    with bank.transaction() as db:db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?",(one['id'],))
    before=balance(bank)['balance_cents']
    with pytest.raises(DomainError) as error:bank.verify('demo',two['id'],'246810')
    assert error.value.code=='RESULT_PENDING' and balance(bank)['balance_cents']==before
    assert bank.wealth('demo')['positions']==[]


# Explicit prerequisite for existing financial tests; never an application default.
from demo_assessment import consent_test_profile as _consent_test_profile
import pytest as _pytest_assessment

@_pytest_assessment.fixture
def assessed_env(env):
    _consent_test_profile(env[0])
    return env

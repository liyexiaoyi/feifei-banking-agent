"""Card controls must constrain actual simulated cash, not just metadata."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import importlib
import importlib.util
import json
import sqlite3

import pytest
from ff_agent.bank import Bank, DomainError
from ff_agent.card_controls import CardControls
from ff_agent.cards import CardService
from ff_agent.birthday_budget import BirthdayBudgets


@pytest.fixture
def env(tmp_path):
    clock = [datetime.fromisoformat('2026-10-28T12:00:00+08:00')]
    return Bank(tmp_path/'spend.db', clock=lambda: clock[0]), clock


def service(env):
    assert importlib.util.find_spec('ff_agent.card_spend') is not None, 'actual card consumption not implemented'
    return importlib.import_module('ff_agent.card_spend').CardSpend(env[0])


def prepare(env, amount=100, key='spend-first', card='card_a_daily', user='demo', cv=None, vv=None, merchant='demo_market'):
    b = env[0]
    c = next((x for x in b.list_cards(user) if x['id'] == card), {'version': 1})
    v = CardControls(b).get_controls(user, card)['control_version'] if cv is None else cv
    return service(env).prepare(user, card, merchant, amount, c['version'] if vv is None else vv, v, key)


def finish(env, op):
    result = env[0].confirm('demo', op['id'])
    if result['status'] == 'WAITING_VERIFICATION':
        result = env[0].verify('demo', op['id'], '246810')
    assert result['status'] == 'SUCCEEDED'
    return result


def control(env, action, value, key, card='card_a_daily'):
    s = CardControls(env[0])
    return finish(env, s.prepare_control('demo', card, action, value, s.get_controls('demo', card)['control_version'], key))


def account(env, aid='a_daily'):
    return next(x for x in env[0].snapshot('demo')['accounts'] if x['id'] == aid)


def assets(env):
    with env[0].transaction() as db:
        return {t: [tuple(x) for x in db.execute('SELECT * FROM '+t+' ORDER BY id')] for t in ('accounts','transactions')}


def unknown(env, op, state='UNKNOWN'):
    with env[0].transaction() as db:
        db.execute('UPDATE operations SET status=? WHERE id=?', (state, op['id']))


def test_merchant_catalog_is_independent_explicit_mock(env):
    s = service(env)
    values = s.merchants()
    assert {x['id']: x['name'] for x in values} == {'demo_market':'演示超市','demo_cafe':'演示咖啡店'}
    assert all(x['simulated'] is True and x['merchant_connected'] is False for x in values)
    values[0]['name'] = 'tampered'
    assert all(x['name'] != 'tampered' for x in s.merchants())


def test_prepare_only_holds_then_exactly_one_real_mock_debit(env):
    before = assets(env); start = account(env)
    op = prepare(env, 12345)
    assert op['type'] == 'card_spend' and op['risk'] == 'YELLOW' and op['status'] == 'WAITING_CONFIRMATION'
    assert assets(env) == before
    assert account(env)['reserved_cents'] == 12345
    assert account(env)['available_cents'] == start['balance_cents']-12345
    result = finish(env, op)
    assert result['result']['amount_cents'] == 12345
    assert result['result']['simulated'] is True and result['result']['merchant_connected'] is False
    assert account(env)['balance_cents'] == start['balance_cents']-12345
    assert account(env)['reserved_cents'] == 0
    with env[0].transaction() as db:
        rows = db.execute('SELECT * FROM transactions WHERE operation_id=?',(op['id'],)).fetchall()
        assert len(rows) == 1 and rows[0]['kind'] == 'EXPENSE' and rows[0]['amount_cents'] == 12345
        assert rows[0]['account_id'] == 'a_daily' and rows[0]['merchant'] == '演示超市'
    stable = assets(env)
    assert finish(env,op)['id'] == op['id']
    assert prepare(env,12345)['id'] == op['id']
    assert assets(env) == stable
    assert CardControls(env[0]).get_controls('demo','card_a_daily')['spent_today_cents'] == 12345


def test_red_spend_requires_explicit_confirmation_and_bound_verification(env):
    before = assets(env); op = prepare(env,100001)
    assert op['risk'] == 'RED'
    with pytest.raises(DomainError): env[0].verify('demo',op['id'],'246810')
    assert env[0].confirm('demo',op['id'])['status'] == 'WAITING_VERIFICATION'
    with pytest.raises(DomainError): env[0].verify('demo',op['id'],'000000')
    assert assets(env) == before
    assert finish(env,op)['status'] == 'SUCCEEDED'


@pytest.mark.parametrize('amount',[True,1.0,0,-1,100000001,'100'])
def test_invalid_cents_no_operation_or_debit(env,amount):
    before = assets(env)
    with pytest.raises(DomainError): prepare(env,amount)
    with env[0].transaction() as db: assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 0
    assert assets(env) == before


@pytest.mark.parametrize('changes',[{'merchant':'external_bank'},{'vv':True},{'cv':True},{'vv':2},{'cv':2}])
def test_invalid_merchant_versions_do_not_create_operation(env,changes):
    with pytest.raises(DomainError): prepare(env,**changes)
    with env[0].transaction() as db: assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 0


def test_foreign_card_hidden_and_plan_provenance_not_invented(env):
    s = service(env)
    with pytest.raises(DomainError) as exc: s.prepare('guest','card_a_daily','demo_market',10,1,1,'foreign-spend')
    assert exc.value.status == 404
    for key,ticket in [('task-fake-0',None),('plain-plan-key',{'plan_id':'fake'})]:
        with pytest.raises(DomainError): s.prepare('demo','card_a_daily','demo_market',10,1,1,key,plan_ticket=ticket)


def test_lock_and_lost_are_independent_and_neither_can_be_bypassed(env):
    before = assets(env)
    control(env,'lock',True,'lock-first')
    with pytest.raises(DomainError): prepare(env)
    lost = CardService(env[0]).prepare_status('demo','card_a_daily','report_lost','report-lost',1)
    finish(env,lost)
    control(env,'unlock',False,'unlock-second')
    assert CardControls(env[0]).get_controls('demo','card_a_daily')['locked'] is False
    assert env[0].list_cards('demo')[0]['status'] == 'LOST'
    with pytest.raises(DomainError): prepare(env)
    assert assets(env) == before


@pytest.mark.parametrize('change',['control','card','account','risk_epoch'])
def test_execution_rechecks_bound_state_and_never_debits_stale_request(env,change):
    op = prepare(env,200)
    if change == 'control': control(env,'set_single_limit',100000,'limit-new')
    elif change == 'card': finish(env,CardService(env[0]).prepare_status('demo','card_a_daily','report_lost','lost-new',1))
    elif change == 'account':
        with env[0].transaction() as db: db.execute("UPDATE accounts SET name='改名账户' WHERE id='a_daily'")
        # The immutable payload binds card metadata; account name is not a payload field.
        with env[0].transaction() as db: db.execute("UPDATE bank_cards SET name='改名卡' WHERE id='card_a_daily'")
    else:
        hold = env[0].activate_safety_hold('demo',{'codes':['test-fixture'],'reasons':['测试夹具'],'advice':'停止操作并核对'},'risk-epoch-new')
        env[0].end_safety_hold('demo',hold['id'])
    before = assets(env)
    with pytest.raises(DomainError): finish(env,op)
    assert assets(env) == before


def test_single_and_daily_limits_include_pending_and_settled_exact_cents(env):
    control(env,'set_single_limit',40000,'single-40k')
    control(env,'set_daily_limit',50000,'daily-50k')
    with pytest.raises(DomainError): prepare(env,40001,'single-plus-one')
    a = prepare(env,40000,'first-40k')
    with pytest.raises(DomainError): prepare(env,10001,'pending-plus-one')
    finish(env,a)
    with pytest.raises(DomainError): prepare(env,10001,'settled-plus-one')
    finish(env,prepare(env,10000,'exact-daily'))
    assert CardControls(env[0]).get_controls('demo','card_a_daily')['spent_today_cents'] == 50000
    with pytest.raises(DomainError): prepare(env,1,'daily-over-one')
    env[1][0] += timedelta(days=1)
    finish(env,prepare(env,1,'new-day-one'))
    assert CardControls(env[0]).get_controls('demo','card_a_daily')['spent_today_cents'] == 1


def test_transfers_and_card_spends_share_funds_in_both_directions(env):
    op = prepare(env,1500000)
    with pytest.raises(DomainError): env[0].prepare_transfer('demo','p_mom',500001,'transfer-over-hold')
    env[0].cancel('demo',op['id'])
    env[0].prepare_transfer('demo','p_mom',1500000,'transfer-held')
    with pytest.raises(DomainError): prepare(env,500001,'spend-over-transfer')
    assert account(env)['reserved_cents'] == 1500000


def test_wealth_pending_and_birthday_budget_also_reduce_card_available_cash(env):
    # Explicit minimal own pending wealth fixture; reservation parsing contract is existing production code.
    with env[0].transaction() as db:
        op = env[0]._create(db,'demo','wealth_purchase',{'account_id':'a_daily','amount_cents':1200000},'RED','wealth-held-fixture')
    with pytest.raises(DomainError): prepare(env,800001,'over-wealth')
    env[0].cancel('demo',op['id'])
    budget = BirthdayBudgets(env[0]).prepare('demo','a_daily',1200000,'2026-10-29','budget-held')
    finish(env,budget)
    with pytest.raises(DomainError): prepare(env,800001,'over-budget')
    assert account(env)['reserved_cents'] == 1200000


@pytest.mark.parametrize('settled',[False,True])
def test_card_and_transfer_daily_risk_in_both_directions(env,settled):
    a = prepare(env,60000,'card-risk-first')
    if settled: finish(env,a)
    t = env[0].prepare_transfer('demo','p_mom',40001,'transfer-risk-second')
    assert t['risk'] == 'RED'
    env[0].cancel('demo',t['id'])
    if not settled: env[0].cancel('demo',a['id'])
    else: env[1][0] += timedelta(days=1)
    t = env[0].prepare_transfer('demo','p_mom',60000,'transfer-other-first')
    if settled: finish(env,t)
    assert prepare(env,40001,'card-other-second')['risk'] == 'RED'


def test_yellow_confirm_upgrades_when_other_card_adds_daily_exposure(env):
    a = prepare(env,60000,'risk-current')
    b = prepare(env,40001,'risk-other',card='card_a_saving')
    assert a['risk'] == 'YELLOW' and b['risk'] == 'RED'
    before = assets(env)
    assert env[0].confirm('demo',a['id'])['status'] == 'WAITING_VERIFICATION'
    assert assets(env) == before
    assert finish(env,a)['status'] == 'SUCCEEDED'
    assert account(env,'a_saving')['reserved_cents'] == 40001


@pytest.mark.parametrize('state',['UNKNOWN','EXECUTING','MANUAL_REVIEW'])
def test_unknown_survives_day_expiry_self_exclude_restart_and_is_never_resent(env,state):
    op = prepare(env,60000)
    unknown(env,op,state)
    env[1][0] += timedelta(days=2)
    before = assets(env)
    rules = importlib.import_module('ff_agent.card_spend_rules')
    with env[0].transaction() as db:
        assert rules.reserved_cash(db,'demo',env[1][0],'a_daily',op['id']) == 60000
        assert rules.daily_exposure(db,'demo',env[1][0],op['id']) == 60000
    assert prepare(env,60000)['status'] == state
    for fn in [lambda:env[0].confirm('demo',op['id']),lambda:prepare(env,1,'same-card-new')]:
        with pytest.raises(DomainError): fn()
    if state == 'MANUAL_REVIEW':
        # Existing cancel is a read-only no-op for this state; never becomes CANCELLED or releases cash.
        assert env[0].cancel('demo',op['id'])['status'] == state
    else:
        with pytest.raises(DomainError): env[0].cancel('demo',op['id'])
    assert prepare(env,40001,'other-card-new',card='card_a_saving')['risk'] == 'RED'
    restored = Bank(env[0].path,clock=lambda:env[1][0])
    assert next(x for x in restored.snapshot('demo')['accounts'] if x['id']=='a_daily')['reserved_cents'] == 60000
    assert assets(env) == before


def test_cancel_and_known_expiry_release_only_their_own_holds(env):
    a = prepare(env,200,'cancel-first'); b = prepare(env,300,'expire-second')
    env[0].cancel('demo',a['id'])
    assert account(env)['reserved_cents'] == 300
    env[1][0] += timedelta(minutes=6)
    assert account(env)['reserved_cents'] == 0
    with pytest.raises(DomainError): env[0].confirm('demo',b['id'])


def test_same_key_changed_intent_conflicts_and_concurrent_confirm_debits_once(env):
    op = prepare(env,200)
    for changes in [{'amount':201},{'merchant':'demo_cafe'},{'card':'card_a_saving'}]:
        with pytest.raises(DomainError): prepare(env,**({'amount':200}|changes))
    before = account(env)['balance_cents']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _:env[0].confirm('demo',op['id']),range(2)))
    assert all(x['status']=='SUCCEEDED' for x in results)
    assert account(env)['balance_cents'] == before-200
    with env[0].transaction() as db: assert db.execute('SELECT count(*) FROM transactions WHERE operation_id=?',(op['id'],)).fetchone()[0] == 1


def test_concurrent_preparations_cannot_over_reserve_same_account(env):
    s = service(env)
    def request(i):
        try: return s.prepare('demo','card_a_daily','demo_market',1500000,1,1,'parallel-'+str(i))
        except DomainError: return None
    with ThreadPoolExecutor(max_workers=2) as pool: values=list(pool.map(request,range(2)))
    assert sum(v is not None for v in values)==1
    assert account(env)['reserved_cents']==1500000


@pytest.mark.parametrize('field,value',[('merchant_name','假商户'),('card_name','假卡'),('masked_number','OTHER'),('storage_kind','virtual'),('amount_cents',True),('risk_epoch','fake')])
def test_tampered_payload_rejected_at_execution_without_financial_changes(env,field,value):
    op = prepare(env,200)
    p = dict(op['payload']);p[field]=value
    with env[0].transaction() as db: db.execute('UPDATE operations SET payload=? WHERE id=?',(json.dumps(p,ensure_ascii=False,sort_keys=True),op['id']))
    before = assets(env)
    with pytest.raises(DomainError): finish(env,op)
    assert assets(env) == before


def test_transaction_failure_rolls_back_account_and_operation(env):
    op=prepare(env,200); before=assets(env)
    with env[0].transaction() as db:
        db.execute("CREATE TRIGGER reject_spend BEFORE INSERT ON transactions WHEN NEW.category='卡片消费' BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError): env[0].confirm('demo',op['id'])
    assert assets(env)==before and env[0].receipt('demo',op['id'])['observed_status']=='WAITING_CONFIRMATION'


def test_virtual_card_uses_own_controls_but_same_account_funds(env):
    finish(env,env[0].prepare_virtual_card('demo','a_daily','virtual-spend-card'))
    card=next(c for c in env[0].list_cards('demo') if c['id'].startswith('vcard_'))
    a=prepare(env,1500000,card=card['id'])
    with pytest.raises(DomainError): prepare(env,500001,'physical-over-shared')
    finish(env,a)
    assert account(env)['balance_cents']==500000
    control(env,'lock',True,'lock-virtual',card=card['id'])
    with pytest.raises(DomainError): prepare(env,1,'locked-virtual-new',card=card['id'])
    finish(env,prepare(env,1,'physical-still-works'))


def test_malformed_owned_unresolved_row_fails_closed_not_silently_ignored(env):
    op=prepare(env,100); unknown(env,op)
    with env[0].transaction() as db: db.execute("UPDATE operations SET payload='{}' WHERE id=?",(op['id'],))
    with pytest.raises(DomainError): account(env)


def test_successful_key_replay_uses_original_intent_after_later_lock_and_lost(env):
    s=service(env);op=prepare(env,200);finish(env,op)
    control(env,'lock',True,'replay-later-lock')
    finish(env,env[0].prepare_card_status('demo','card_a_daily','report_lost','replay-later-lost',1))
    before=assets(env)
    assert s.prepare('demo','card_a_daily','demo_market',200,1,1,'spend-first')['id']==op['id']
    assert assets(env)==before


@pytest.mark.parametrize('state,risk',[('READY','YELLOW'),('WAITING_CONFIRMATION','RED'),('WAITING_VERIFICATION','YELLOW')])
def test_only_exact_confirmed_state_risk_pairs_can_enter_card_executor(env,state,risk):
    op=prepare(env,200);before=assets(env)
    with env[0].transaction() as db:
        db.execute('UPDATE operations SET status=?,risk=?,confirmed_at=? WHERE id=?',(state,risk,env[1][0].timestamp(),op['id']))
        row=db.execute('SELECT * FROM operations WHERE id=?',(op['id'],)).fetchone()
        with pytest.raises(DomainError): service(env).execute(db,'demo',row)
    assert assets(env)==before


def test_executor_refuses_autocommit_connection_before_any_debit(env):
    from ff_agent.db import connect
    op=prepare(env,200)
    with env[0].transaction() as db:
        db.execute('UPDATE operations SET confirmed_at=? WHERE id=?',(env[1][0].timestamp(),op['id']))
    before=assets(env);db=connect(env[0].path)
    try:
        row=db.execute('SELECT * FROM operations WHERE id=?',(op['id'],)).fetchone()
        assert not db.in_transaction
        with pytest.raises(DomainError): service(env).execute(db,'demo',row)
    finally: db.close()
    assert assets(env)==before

"""Real-bank goal loop: observations drive continuation, never financial approval."""
from datetime import datetime
import importlib
import importlib.util
import pytest
from ff_agent.bank import Bank, DomainError
from ff_agent.conversation_goal import GoalStore
from ff_agent.birthday_service import BirthdayService

TEXT = '给妈妈安排明天生日，预算500，用日常账户，只要花不要蛋糕'


def make(tmp_path):
    assert importlib.util.find_spec('ff_agent.conversation_goal_loop'), 'conversation goal loop not implemented'
    bank=Bank(tmp_path/'birthday-goal.db',clock=lambda:datetime.fromisoformat('2026-10-28T12:00:00+08:00'))
    store=GoalStore(bank); loop=importlib.import_module('ff_agent.conversation_goal_loop').GoalLoop(bank,store)
    return bank,store,loop


def talk(e,text=TEXT,key='birthday-goal-first'):
    z=e[2].handle('demo','room-a','',text,key)
    assert isinstance(z,dict) and isinstance(z.get('message'),str)
    assert isinstance(z.get('cards'),list) and isinstance(z.get('goal'),dict)
    return z


def counts(e):
    with e[0].transaction() as db:return {n:db.execute('SELECT count(*) FROM '+n).fetchone()[0] for n in ('operations','birthday_flows','birthday_budgets','birthday_order_ledger')}


def prepare(e):
    first=talk(e);chosen=talk(e,'第一个','birthday-goal-choice')
    assert chosen['goal']['id']==first['goal']['id']
    ops=[c['operation'] for c in chosen['cards'] if c.get('type')=='operation']
    assert len(ops)==1 and ops[0]['type']=='birthday_budget_reserve'
    assert ops[0]['status']=='WAITING_CONFIRMATION'
    return chosen['goal'],ops[0]


def pay(e,op):
    done=e[0].confirm('demo',op['id'])
    if done['status']=='WAITING_VERIFICATION':done=e[0].verify('demo',op['id'],'246810')
    assert done['status']=='SUCCEEDED'


def cont(e,g,key):return e[2].continue_goal('demo','room-a','',g['id'],g['version'],key)


def test_first_turn_reads_actual_affordable_catalog_and_never_reserves_funds(tmp_path):
    e=make(tmp_path);z=talk(e);g=z['goal']
    assert g['phase']=='CHOOSING' and g['question'].count('？')==1
    assert g['choices'] and all(c['price_cents']<=50000 for c in g['choices'])
    assert [c['id'] for c in g['choices']]==['flowers_simple']
    assert all(v==0 for v in counts(e).values())
    assert e[1].current('demo','room-a','')['id']==g['id']


def test_nonexistent_second_choice_is_not_invented_or_silently_substituted(tmp_path):
    e=make(tmp_path);talk(e);z=talk(e,'第二个','birthday-goal-invalid-choice')
    assert z['goal']['phase']=='CHOOSING' and counts(e)['operations']==0
    assert '800' not in z['message'] and '已付款' not in z['message']


def test_choice_only_prepares_budget_and_plain_chat_confirmation_never_pays(tmp_path):
    e=make(tmp_path);g,op=prepare(e)
    assert op['payload']['amount_cents']==50000
    assert counts(e)=={'operations':1,'birthday_flows':1,'birthday_budgets':0,'birthday_order_ledger':0}
    again=talk(e,'好的确认','birthday-goal-chat-confirm')
    assert e[0].operation('demo',op['id'])['status']=='WAITING_CONFIRMATION'
    assert counts(e)['operations']==1 and again['goal']['id']==g['id']


def test_paid_budget_observation_then_choice_and_order_are_separate_steps(tmp_path):
    e=make(tmp_path);g,op=prepare(e);pay(e,op)
    n=cont(e,e[2].view('demo','room-a','',g['id']),'birthday-goal-after-budget')
    f=BirthdayService(e[0]).list('demo')[0]
    assert f['cake_choice'] is False
    with e[0].transaction() as db:
        assert db.execute('SELECT item_id FROM birthday_nodes WHERE flow_id=? AND idx=2',(f['id'],)).fetchone()[0]=='flowers_simple'
    orders=[c['operation'] for c in n['cards'] if c.get('type')=='operation' and c['operation']['type']=='birthday_order']
    assert len(orders)==1 and orders[0]['payload']['amount_cents']==30000
    assert orders[0]['status']=='WAITING_CONFIRMATION' and counts(e)['birthday_order_ledger']==0
    pay(e,orders[0]);latest=e[2].view('demo','room-a','',g['id']);done=cont(e,latest,'birthday-goal-after-order')
    assert done['goal']['status']=='COMPLETED' and counts(e)['birthday_order_ledger']==1
    assert '真实配送' not in done['message']


def test_unchanged_pending_operation_is_observed_without_duplicate_preparation(tmp_path):
    e=make(tmp_path);g,op=prepare(e);before=counts(e)
    for i in range(3):
        latest=e[2].view('demo','room-a','',g['id']);cont(e,latest,'birthday-goal-check-'+str(i))
    assert counts(e)==before and e[0].operation('demo',op['id'])['status']=='WAITING_CONFIRMATION'


def test_read_only_balance_interruption_preserves_goal_and_choice(tmp_path):
    e=make(tmp_path);first=talk(e);before=e[1].current('demo','room-a','')
    assert e[2].handle('demo','room-a','','查询余额','birthday-goal-balance-side') is None
    assert e[1].current('demo','room-a','')==before
    assert talk(e,'第一个','birthday-goal-after-balance')['goal']['id']==first['goal']['id']


def test_completed_goal_discloses_actual_unspent_reservation_without_auto_release(tmp_path):
    e=make(tmp_path);g,budget=prepare(e);pay(e,budget)
    n=cont(e,e[2].view('demo','room-a','',g['id']),'display-after-budget')
    order=next(c['operation'] for c in n['cards'] if c['operation']['type']=='birthday_order')
    pay(e,order)
    done=cont(e,e[2].view('demo','room-a','',g['id']),'display-after-order')
    summary=' '.join(done['goal']['summary'])
    assert '已用' in summary and '300.00' in summary
    assert '预留' in summary and '200.00' in summary
    assert any(s['state']=='SUCCEEDED' and ('花' in s['label'] or '订单' in s['label']) for s in done['goal']['steps'])
    with e[0].transaction() as db:
        assert db.execute('SELECT amount_cents-spent_cents FROM birthday_budgets').fetchone()[0]==20000


def test_unverifiable_budget_blocks_continuation_before_preparing_any_order(tmp_path,monkeypatch):
    from ff_agent.birthday_budget import BirthdayBudgets
    e=make(tmp_path);g,op=prepare(e);pay(e,op);before=counts(e)
    def missing(*args):raise DomainError('BUDGET_UNAVAILABLE','预算记录暂时无法核对。',503)
    monkeypatch.setattr(BirthdayBudgets,'get',missing)
    view=e[2].view('demo','room-a','',g['id']);assert view['status']=='UNKNOWN'
    cont(e,view,'do-not-advance-unverifiable-budget')
    assert counts(e)==before


def test_session_owner_and_stale_version_cannot_continue_goal(tmp_path):
    e=make(tmp_path);g,_=prepare(e)
    for user,room in [('guest','room-a'),('demo','room-b')]:
        with pytest.raises(DomainError):e[2].continue_goal(user,room,'',g['id'],g['version'],'birthday-goal-foreign')
    with pytest.raises(DomainError):e[2].continue_goal('demo','room-a','',g['id'],g['version']-1,'birthday-goal-stale')
    assert counts(e)['operations']==1


def test_unknown_budget_never_retries_cancels_or_releases(tmp_path,monkeypatch):
    e=make(tmp_path);g,op=prepare(e)
    with e[0].transaction() as db:db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?",(op['id'],))
    before=counts(e)
    def forbidden(*a,**k):pytest.fail('unknown must not mutate financial flow')
    monkeypatch.setattr(e[0],'cancel',forbidden);monkeypatch.setattr(BirthdayService,'advance',forbidden)
    n=cont(e,e[2].view('demo','room-a','',g['id']),'birthday-goal-unknown-check')
    assert n['goal']['status']=='UNKNOWN' and counts(e)==before
    assert e[0].operation('demo',op['id'])['status']=='UNKNOWN'


def test_new_loop_instance_resumes_existing_goal_without_a_second_budget(tmp_path):
    e=make(tmp_path);g,op=prepare(e);fresh=type(e[2])(e[0],GoalStore(e[0]))
    assert fresh.view('demo','room-a','',g['id'])['id']==g['id']
    z=fresh.handle('demo','room-a','','核对进度','birthday-goal-reopen-check')
    assert z['goal']['id']==g['id'] and counts(e)['operations']==1


def test_metadata_or_financial_confirmation_is_never_taken_from_notes(tmp_path):
    e=make(tmp_path);from ff_agent.memory import Memory
    Memory(e[0]).remember('demo','生日用日常账户，预算500元，不要蛋糕','birthday-goal-memory')
    z=talk(e,'给妈妈安排明天生日','birthday-goal-missing-fields')
    assert counts(e)['operations']==0 and z['goal']['phase']=='COLLECTING'
    assert '预算' in z['message'] and not z['goal']['choices']


def test_negative_selection_never_prepares_the_rejected_item(tmp_path):
    e=make(tmp_path);talk(e);z=talk(e,'不要第一个','birthday-negative-choice')
    assert z['goal']['phase']=='CHOOSING' and counts(e)['operations']==counts(e)['birthday_flows']==0


def test_informational_question_about_assistant_birthday_is_not_a_banking_goal(tmp_path):
    e=make(tmp_path);assert e[2].handle('demo','room-a','','你的生日是哪天','birthday-faq') is None
    assert e[1].current('demo','room-a','') is None


def test_requoted_choice_reuses_original_paid_budget_and_flow(tmp_path,monkeypatch):
    e=make(tmp_path);g,op=prepare(e);pay(e,op)
    import ff_agent.birthday_orders as orders
    monkeypatch.setattr(orders,'CATALOG',{k:dict(v) for k,v in orders.CATALOG.items()})
    orders.CATALOG['flowers_simple']['price_cents']=32000;orders.CATALOG['flowers_simple']['quote_version']=2
    z=cont(e,e[2].view('demo','room-a','',g['id']),'birthday-quote-change')
    assert z['goal']['phase']=='CHOOSING'
    talk(e,'第一个','birthday-quote-new-choice')
    assert counts(e)['birthday_flows']==1
    with e[0].transaction() as db:assert db.execute("SELECT count(*) FROM operations WHERE type='birthday_budget_reserve'").fetchone()[0]==1


def test_journalled_metadata_survives_missing_flow_pointer_without_second_flow(tmp_path,monkeypatch):
    e=make(tmp_path);talk(e);original=e[1].update;failed=[False]
    def lose_pointer(*a,**k):
        patch=a[-1]
        if not failed[0] and patch.get('observations',{}).get('flow_id'):
            failed[0]=True;raise RuntimeError('test loss after journalled metadata')
        return original(*a,**k)
    monkeypatch.setattr(e[1],'update',lose_pointer)
    with pytest.raises(RuntimeError,match='journalled metadata'):talk(e,'第一个','birthday-pointer-loss')
    assert counts(e)['birthday_flows']==1 and counts(e)['operations']==0
    monkeypatch.setattr(e[1],'update',original)
    talk(e,'第一个','birthday-pointer-recovery')
    assert counts(e)['birthday_flows']==1 and counts(e)['operations']==1


def test_known_selection_refusal_does_not_replay_completed_cake_decision(tmp_path,monkeypatch):
    e=make(tmp_path);g,op=prepare(e);pay(e,op);original=e[2].svc.select_item
    def refused(*a,**k):raise DomainError('TEMPORARY_REFUSAL','暂不能准备商品',409)
    monkeypatch.setattr(e[2].svc,'select_item',refused)
    cont(e,e[2].view('demo','room-a','',g['id']),'birthday-known-refusal')
    monkeypatch.setattr(e[2].svc,'select_item',original)
    cont(e,e[2].view('demo','room-a','',g['id']),'birthday-explicit-next-try')
    with e[0].transaction() as db:assert db.execute("SELECT count(*) FROM operations WHERE type='birthday_order'").fetchone()[0]==1


def test_cake_request_asks_real_item_then_prepares_two_independent_order_cards(tmp_path):
    e=make(tmp_path);talk(e,TEXT.replace('只要花不要蛋糕','也要蛋糕'),'birthday-cake-first')
    z=talk(e,'第一个','birthday-cake-flower');g=z['goal'];op=next(c['operation'] for c in z['cards'] if c['type']=='operation')
    pay(e,op);z=cont(e,e[2].view('demo','room-a','',g['id']),'birthday-cake-budget-done')
    assert z['goal']['phase']=='CHOOSING' and [c['id'] for c in z['goal']['choices']]==['cake_small']
    talk(e,'第一个','birthday-cake-choose-item');latest=e[2].view('demo','room-a','',g['id'])
    if latest['phase']=='READY':cont(e,latest,'birthday-cake-prepare-orders')
    with e[0].transaction() as db:
        rows=db.execute("SELECT status,payload FROM operations WHERE type='birthday_order'").fetchall()
    assert len(rows)==2 and all(row[0]=='WAITING_CONFIRMATION' for row in rows)
    assert counts(e)['birthday_flows']==1 and counts(e)['birthday_order_ledger']==0


def test_missing_receipt_never_emits_a_fabricated_operation_card(tmp_path,monkeypatch):
    e=make(tmp_path);g,_=prepare(e)
    def missing(*a,**k):raise DomainError('NOT_FOUND','原回执无法核对',404)
    monkeypatch.setattr(e[0],'receipt',missing)
    z=talk(e,'核对进度','birthday-missing-receipt')
    assert z['goal']['status']=='UNKNOWN'
    assert all(isinstance(c.get('operation',{}).get('payload'),dict) for c in z['cards'] if c.get('type')=='operation')

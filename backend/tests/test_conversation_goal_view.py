"""Tools and public view remain grounded in real bank data."""
from datetime import datetime
import importlib,importlib.util
import pytest
from ff_agent.bank import Bank


def make(tmp_path):
    assert importlib.util.find_spec('ff_agent.birthday_goal_view'),'birthday goal view helpers not implemented'
    return Bank(tmp_path/'view.db',clock=lambda:datetime.fromisoformat('2026-10-28T12:00:00+08:00')),importlib.import_module('ff_agent.birthday_goal_view')


def test_fields_extract_only_current_explicit_intent(tmp_path):
    b,m=make(tmp_path);s=m.extract_slots(b,'demo','给妈妈安排明天生日，预算500，用日常账户，只要花不要蛋糕',{})
    assert s['recipient_label']=='妈妈' and s['event_date']=='2026-10-29'
    assert s['budget_cents']==50000 and s['account_id']=='a_daily' and s['cake'] is False
    assert 'budget_cents' not in m.extract_slots(b,'demo','给妈妈安排明天生日',{})


def test_choices_come_from_real_budget_filtered_catalog(tmp_path):
    b,m=make(tmp_path);c=m.catalog_choices(b,50000)
    assert [(x['id'],x['price_cents']) for x in c]==[('flowers_simple',30000)]
    assert c[0]['quote_version']==1


def base():return {'id':'owned','kind':'birthday','status':'ACTIVE','phase':'CHOOSING','version':2,'slots':{'recipient_label':'妈妈','budget_cents':50000,'cake':False},'question':'选哪束花？','observations':{'choices':[{'id':'flowers_simple','label':'简约花束','price_cents':30000,'quote_version':1}]},'steps':[]}


def test_public_view_never_exposes_journal_keys_claims_or_private_args(tmp_path):
    _,m=make(tmp_path);g=base();g['steps']=[{'seq':1,'action':'birthday_catalog','args':{'private':'hidden'},'effect_key':'not-for-browser','state':'OBSERVED','result':{}}]
    z=m.render_goal(g);assert z['choices']==[{'id':'flowers_simple','label':'简约花束','price_cents':30000}]
    assert set(z['steps'][0])=={'label','state'} and z['next_action']=='CHOOSE'
    assert 'hidden' not in str(z) and 'not-for-browser' not in str(z)


def test_unknown_journal_is_not_hidden_by_a_completed_flow_projection(tmp_path):
    _,m=make(tmp_path);g=base();g.update(status='DONE',phase='DONE')
    g['steps']=[{'action':'birthday_advance','state':'UNKNOWN'}]
    flow={'status':'COMPLETED','nodes':[{'type':'flowers','observed_state':'SUCCEEDED'}]}
    out=m.render_goal(g,flow)
    assert out['status']=='UNKNOWN' and out['next_action']=='CHECK'


def test_budget_summary_uses_actual_reserved_amount_instead_of_remaining_balance(tmp_path):
    _,m=make(tmp_path)
    budget={'amount_cents':50000,'spent_cents':30000,'remaining_cents':20000,'reserved_cents':15000,'status':'ACTIVE'}
    out=m.render_goal(base(),budget=budget)
    reserved=[s for s in out['summary'] if '仍预留' in s]
    assert len(reserved)==1 and '150.00' in reserved[0] and '200.00' not in reserved[0]


def test_unknown_operation_overrides_saved_done_without_offering_continuation(tmp_path):
    _,m=make(tmp_path);g=base();g['status']='DONE';g['phase']='DONE'
    z=m.render_goal(g,operations=[{'id':'own-op','type':'birthday_order','status':'UNKNOWN'}])
    assert z['status']=='UNKNOWN' and z['next_action']=='CHECK'


def test_waiting_budget_is_not_payment_or_goal_completion(tmp_path):
    _,m=make(tmp_path);g=base();g['phase']='WAITING_CONFIRMATION';g['observations']['choices']=[]
    z=m.render_goal(g,operations=[{'id':'own-op','type':'birthday_budget_reserve','status':'WAITING_CONFIRMATION'}])
    assert z['phase']=='WAITING_CONFIRMATION' and z['status']!='COMPLETED' and z['next_action']=='WAIT'


def test_successful_budget_is_ready_for_bounded_continuation_not_final_success(tmp_path):
    _,m=make(tmp_path);g=base();g['phase']='WAITING_CONFIRMATION';g['observations']['choices']=[]
    z=m.render_goal(g,operations=[{'id':'own-op','type':'birthday_budget_reserve','status':'SUCCEEDED'}])
    assert z['phase']=='READY' and z['next_action']=='CONTINUE' and z['status']=='ACTIVE'


def test_explicit_ambiguous_correction_clears_old_budget_and_account(tmp_path):
    b,m=make(tmp_path);old={'recipient_label':'妈妈','budget_cents':50000,'account_id':'a_daily'}
    z=m.extract_slots(b,'demo','预算改成几百元，用朋友的账户',old)
    assert z.get('budget_cents') is None and z.get('account_id') is None


def test_unknown_observation_overrides_recorded_node_success(tmp_path):
    _,m=make(tmp_path);g=base();f={'status':'ACTIVE','nodes':[{'idx':0,'state':'SUCCEEDED','observed_state':'UNKNOWN'}]}
    z=m.render_goal(g,flow=f);assert z['status']=='UNKNOWN' and z['next_action']=='CHECK'


def test_failed_order_cannot_inherit_ready_from_successful_budget(tmp_path):
    _,m=make(tmp_path);g=base();g['phase']='WAITING_CONFIRMATION'
    z=m.render_goal(g,operations=[{'id':'b','type':'birthday_budget_reserve','status':'SUCCEEDED'},{'id':'o','type':'birthday_order','status':'FAILED'}])
    assert z['status']=='BLOCKED' and z['next_action']=='CHECK'

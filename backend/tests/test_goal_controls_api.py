"""Goal controls use existing session, CSRF and deployment boundaries."""
import pytest
from test_conversation_goal_api import env,prepared,current,chat

def post(e,g,action,key='api-control-key',**extra):
    return e[1].post('/api/goal-controls/'+g['id']+'/'+action,json={'expected_version':g['version'],'request_key':key,'confirmed':True,**extra})
def financial(e):
    with e[0].state.bank.transaction() as db:return {t:[tuple(x) for x in db.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in ('accounts','operations','transactions','verification_challenges','birthday_budgets')}

def test_pause_and_resume_are_explicit_and_keep_same_financial_request(env):
    g,op=prepared(env);before=financial(env)
    z=post(env,g,'pause');assert z.status_code==200,z.text
    assert z.json()['command']['recorded'] is True
    assert z.json()['goal']['status']=='PAUSED'
    g=current(env);assert g['paused'] is True and g['pause_gen']==1
    assert post(env,g,'resume','api-resume-01',confirmed=False).status_code in (400,422)
    assert post(env,g,'resume','api-resume-02').status_code==200
    assert current(env)['status']=='ACTIVE' and financial(env)==before
    assert env[1].get('/api/operations/'+op['id']).json()['status']=='WAITING_CONFIRMATION'

def test_control_post_cannot_bypass_csrf_or_strict_shape(env):
    g,_=prepared(env);token=env[1].headers.pop('X-CSRF-Token')
    assert post(env,g,'pause').status_code==403
    env[1].headers['X-CSRF-Token']=token
    for extra in [{'expected_version':True},{'confirmed':'true'},{'user_id':'guest'}]:
        assert post(env,g,'pause',**extra).status_code==422
    assert current(env)['status']=='ACTIVE'

def test_switch_clears_focus_without_releasing_original_budget_request(env):
    g,_=prepared(env);before=financial(env)
    z=post(env,g,'switch','api-switch-birthday');assert z.status_code==200,z.text
    assert z.json()['goal'] is None and current(env) is None
    saved=env[1].get('/api/goal-controls/paused');assert saved.status_code==200,saved.text
    one=saved.json()['goals'][0];assert one['id']==g['id'] and one['status']=='PAUSED'
    assert not ({'slots','observations','session_hash','epoch','intent_hash'}&set(one))
    assert financial(env)==before
    assert chat(env,'看看这个月花哪儿了，再帮我处理不需要的续费','api-second-goal').status_code==200
    newer=current(env);assert newer['id']!=g['id']
    assert post(env,one,'resume','blocked-original-resume').status_code==409
    assert post(env,newer,'switch','save-second-for-original').status_code==200
    assert post(env,one,'resume','restore-original-saved').status_code==200
    assert current(env)['id']==g['id']

def test_lookup_is_scoped_and_reads_never_dispatch_or_change_data(env):
    g,_=prepared(env);assert post(env,g,'pause','api-lookup-original').status_code==200
    with env[0].state.bank.transaction() as db:before=list(db.iterdump())
    found=env[1].get('/api/goal-controls/commands/api-lookup-original');assert found.status_code==200,found.text
    assert found.json()['command']['goal_id']==g['id']
    assert env[1].get('/api/goal-controls/commands/no-such-command').status_code==404
    assert env[1].get('/api/goal-controls/paused').status_code==200
    assert current(env)['status']=='PAUSED'
    with env[0].state.bank.transaction() as db:assert list(db.iterdump())==before
    z=env[1].post('/api/session',json={'user':'guest'});env[1].headers['X-CSRF-Token']=z.json()['csrf']
    assert env[1].get('/api/goal-controls/commands/api-lookup-original').status_code==404
    assert post(env,g,'resume','guest-cannot-adopt').status_code==404

def test_same_control_key_replay_does_not_increment_version_or_forge_old_status(env):
    g,_=prepared(env);assert post(env,g,'pause','api-once-control').status_code==200
    once=current(env)
    assert post(env,g,'pause','api-once-control').status_code==200
    assert current(env)['version']==once['version']
    assert post(env,once,'pause','api-once-control').status_code==409

@pytest.mark.parametrize('action',['pause','resume','switch'])
def test_unknown_financial_request_cannot_be_paused_or_switched_away(env,action):
    g,op=prepared(env)
    with env[0].state.bank.transaction() as db:db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?",(op['id'],))
    before=financial(env)
    assert post(env,g,action,'api-unknown-'+action).status_code==409
    assert current(env)['status']=='UNKNOWN'
    assert financial(env)==before

def test_explicit_stop_can_save_known_old_risk_goal_without_restoring_authorization(env):
    g,_=prepared(env)
    env[0].state.bank.activate_safety_hold('demo',{'codes':['SAFE_ACCOUNT'],'reasons':['fixture'],'advice':'先核实','hold':True},'controls-risk-stop-fixture')
    paused=current(env);assert paused['status']=='PAUSED' and paused['reason']=='RISK_CHANGED'
    before=financial(env);z=post(env,paused,'switch','explicit-risk-stop')
    assert z.status_code==200,z.text
    assert current(env) is None and financial(env)==before

@pytest.mark.parametrize('first,next_text',[
 ('给妈妈安排明天生日，预算500，用日常账户，只要花不要蛋糕','看本月账单，帮我整理不需要的自动续费'),
 ('看看这个月花哪儿了，再帮我处理不需要的续费','给爸爸安排明天生日，预算600，用日常账户，只要花不要蛋糕'),
])
def test_paused_goal_requires_explicit_switch_before_starting_another_multiturn_goal(env,first,next_text):
    assert chat(env,first,'paused-new-first').status_code==200
    g=current(env);assert post(env,g,'pause','paused-new-stop').status_code==200
    before=financial(env);reply=chat(env,next_text,'paused-new-other')
    assert reply.status_code==200,reply.text
    assert '切换' in reply.json()['message']
    assert financial(env)==before
    assert current(env)['id']==g['id']

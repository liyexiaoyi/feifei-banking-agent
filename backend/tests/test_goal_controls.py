"""Atomic explicit goal controls never authorize financial operations."""
import importlib.util,json
from concurrent.futures import ThreadPoolExecutor
import pytest
from ff_agent.bank import DomainError
from test_goal_controls_foundation import env,create,row,query_goal,birthday_pending

def control(bank,s):
    assert importlib.util.find_spec('ff_agent.conversation_goal_control'),'goal control service missing'
    from ff_agent.conversation_goal_control import GoalControls
    return GoalControls(bank,s)
def command(c,g,action,key,confirmed=True,version=None):
    return c.command('demo','session-control','',g['id'],action,g['version'] if version is None else version,key,confirmed)
def get(s,g):return s.get('demo','session-control','',g['id'])
def assets(bank):
    with bank.transaction() as db:return {t:[tuple(x) for x in db.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in ('accounts','operations','transactions','verification_challenges','birthday_budgets')}

def test_pause_resume_preserves_original_ops_and_never_confirms(tmp_path):
    bank,s=env(tmp_path);c=control(bank,s);g,op=query_goal(bank,s);before=assets(bank)
    command(c,g,'pause','pause-query-goal');g=get(s,g)
    assert g['status']=='PAUSED' and assets(bank)==before
    assert s.current('demo','session-control','')['id']==g['id']
    command(c,g,'resume','resume-query-goal');g=get(s,g)
    assert g['status']=='ACTIVE' and assets(bank)==before
    assert bank.operation('demo',op['id'])['status']=='WAITING_CONFIRMATION'
    assert bank.confirm('demo',op['id'])['status']=='SUCCEEDED'

def test_explicit_switch_saves_then_original_goal_can_resume(tmp_path):
    bank,s=env(tmp_path);c=control(bank,s);g,op=query_goal(bank,s);before=assets(bank)
    command(c,g,'switch','switch-original-goal');assert s.current('demo','session-control','') is None
    saved=c.list_paused('demo','session-control','');assert [x['id'] for x in saved]==[g['id']]
    other=create(s,'second-goal-after-switch')
    with pytest.raises(DomainError):command(c,get(s,g),'resume','cannot-auto-pause-other')
    command(c,other,'switch','save-second-goal')
    command(c,get(s,g),'resume','resume-original-goal');assert s.current('demo','session-control','')['id']==g['id']
    assert assets(bank)==before

@pytest.mark.parametrize('action',['pause','switch','resume'])
def test_unknown_is_not_closed_to_free_a_slot(tmp_path,action):
    bank,s=env(tmp_path);c=control(bank,s);g,op=query_goal(bank,s)
    with bank.transaction() as db:db.execute("UPDATE operations SET status='UNKNOWN' WHERE id=?",(op['id'],))
    before=assets(bank)
    with pytest.raises(DomainError):command(c,g,action,'unknown-'+action)
    assert s.current('demo','session-control','')['id']==g['id']
    assert assets(bank)==before
    with pytest.raises(DomainError):create(s,'unknown-new-goal')

@pytest.mark.parametrize('bad',[False,1,'true',None])
def test_control_requires_exact_explicit_confirmation(tmp_path,bad):
    bank,s=env(tmp_path);c=control(bank,s);g=create(s)
    with pytest.raises(DomainError):command(c,g,'switch','bad-confirm',confirmed=bad)
    assert get(s,g)['status']=='ACTIVE'

def test_control_key_binds_the_full_payload_and_replay_does_not_reapply(tmp_path):
    bank,s=env(tmp_path);c=control(bank,s);g=create(s)
    first=command(c,g,'pause','control-repeat-key');v=get(s,g)['version']
    assert command(c,g,'pause','control-repeat-key')==first
    assert get(s,g)['version']==v
    with pytest.raises(DomainError) as e:command(c,g,'pause','control-repeat-key',version=v)
    assert e.value.code=='IDEMPOTENCY_CONFLICT'
    with pytest.raises(DomainError):command(c,g,'switch','control-repeat-key')
    assert c.lookup('demo','session-control','','control-repeat-key')==first

@pytest.mark.parametrize('who,sid',[('guest','session-control'),('demo','different-session')])
def test_foreign_scope_cannot_control_or_lookup(tmp_path,who,sid):
    bank,s=env(tmp_path);c=control(bank,s);g=create(s)
    command(c,g,'pause','own-key')
    with pytest.raises(DomainError):c.command(who,sid,'',g['id'],'resume',get(s,g)['version'],'foreign-key',True)
    with pytest.raises(DomainError):c.lookup(who,sid,'','own-key')
    assert c.list_paused(who,sid,'')==[]

def test_resume_expired_or_unknown_challenge_is_refused(tmp_path):
    bank,s=env(tmp_path);c=control(bank,s);g,op=birthday_pending(bank,s)
    assert bank.confirm('demo',op['id'])['status']=='WAITING_VERIFICATION'
    before=assets(bank);command(c,g,'pause','pause-verified-birthday');assert assets(bank)==before
    g=get(s,g)
    with bank.transaction() as db:db.execute("UPDATE verification_challenges SET status='UNKNOWN' WHERE opid=?",(op['id'],))
    with pytest.raises(DomainError):command(c,g,'resume','unknown-challenge-no-resume')
    assert get(s,g)['status']=='PAUSED'

def test_resume_never_rebinds_existing_verification_challenge(tmp_path):
    bank,s=env(tmp_path);c=control(bank,s);g,op=birthday_pending(bank,s)
    assert bank.confirm('demo',op['id'])['status']=='WAITING_VERIFICATION'
    before=assets(bank);command(c,g,'pause','pause-red');g=get(s,g)
    with pytest.raises(DomainError):bank.verify('demo',op['id'],'246810')
    command(c,g,'resume','resume-red');assert assets(bank)==before
    assert bank.verify('demo',op['id'],'246810')['status']=='SUCCEEDED'

def test_version_race_has_only_one_successful_control(tmp_path):
    bank,s=env(tmp_path);c=control(bank,s);g=create(s)
    def run(i):
        try:command(c,g,'pause','race-'+str(i));return True
        except DomainError:return False
    with ThreadPoolExecutor(max_workers=2) as ex:assert sorted(ex.map(run,[1,2]))==[False,True]
    assert get(s,g)['version']==g['version']+1

def test_control_gets_are_pure_and_missing_key_not_proof_of_failure(tmp_path):
    bank,s=env(tmp_path);c=control(bank,s);g=create(s);command(c,g,'pause','pure-read-key')
    with bank.transaction() as db:before=list(db.iterdump())
    assert c.list_paused('demo','session-control','')[0]['id']==g['id']
    assert c.lookup('demo','session-control','','pure-read-key')['recorded'] is True
    with pytest.raises(DomainError) as e:c.lookup('demo','session-control','','missing-control-key')
    assert e.value.status==404
    with bank.transaction() as db:assert list(db.iterdump())==before

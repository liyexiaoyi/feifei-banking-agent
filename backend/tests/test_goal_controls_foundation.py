"""Goal controls additive migration and authoritative Bank gate regressions."""
import importlib.util,json,sqlite3
from datetime import datetime
import pytest
from ff_agent.bank import Bank,DomainError
from ff_agent.conversation_goal import GoalStore
NOW=datetime.fromisoformat('2026-10-28T12:00:00+08:00')

def env(tmp_path):
    bank=Bank(tmp_path/'controls.db',clock=lambda:NOW);return bank,GoalStore(bank)
def create(s,key='control-goal-one',kind='spending_subscriptions'):
    return s.create('demo','session-control','',kind,{'period':'2026-10'},key)
def row(bank,g):
    with bank.transaction() as db:return db.execute('SELECT * FROM conversation_goals WHERE id=?',(g['id'],)).fetchone()
def pause_fixture(bank,g,clear=False):
    with bank.transaction() as db:
        db.execute('UPDATE conversation_goals SET paused=1,pause_gen=pause_gen+1 WHERE id=?',(g['id'],))
        if clear:db.execute('UPDATE conversation_goal_focus SET goal_id=NULL WHERE goal_id=?',(g['id'],))
def refs_module():
    assert importlib.util.find_spec('ff_agent.goal_control_refs'),'authoritative goal control refs missing'
    return __import__('ff_agent.goal_control_refs',fromlist=['inspect_goal'])
def query_goal(bank,s):
    g=create(s);g,token=s.start_step('demo','session-control','',g['id'],g['version'],'subscription_query',{})
    op=bank.prepare_subscription_query('demo',g['steps'][-1]['effect_key'])
    g=s.finish_step('demo','session-control','',g['id'],token,'OBSERVED',{'operation_ids':[op['id']]})
    return g,op

def test_additive_schema_and_initial_focus(tmp_path):
    bank,s=env(tmp_path);g=create(s)
    with bank.transaction() as db:
        cols={x['name'] for x in db.execute('PRAGMA table_info(conversation_goals)')}
        assert {'paused','pause_gen'}<=cols
        assert db.execute('SELECT goal_id FROM conversation_goal_focus').fetchone()[0]==g['id']
    assert s.current('demo','session-control','')['id']==g['id']

def test_multiple_saved_pauses_restart_and_explicit_empty_focus(tmp_path):
    bank,s=env(tmp_path);a=create(s);pause_fixture(bank,a,True)
    b=create(s,'control-second-goal');pause_fixture(bank,b,True)
    for _ in range(2):
        s=GoalStore(Bank(bank.path,clock=lambda:NOW))
        assert s.current('demo','session-control','') is None
        assert s.get('demo','session-control','',a['id'])['status']=='PAUSED'
        assert s.get('demo','session-control','',b['id'])['status']=='PAUSED'
    c=create(s,'control-third-goal');assert s.current('demo','session-control','')['id']==c['id']

def test_focused_pause_requires_explicit_switch_before_create(tmp_path):
    bank,s=env(tmp_path);g=create(s);pause_fixture(bank,g)
    assert s.current('demo','session-control','')['status']=='PAUSED'
    with pytest.raises(DomainError):create(s,'control-cannot-implicitly-switch')

@pytest.mark.parametrize('action',['update','start'])
def test_paused_goal_cannot_dispatch_or_change_slots(tmp_path,action):
    bank,s=env(tmp_path);g=create(s);pause_fixture(bank,g)
    with pytest.raises(DomainError) as e:
        if action=='update':s.update('demo','session-control','',g['id'],g['version'],{'phase':'READY'})
        else:s.start_step('demo','session-control','',g['id'],g['version'],'bill',{'period':'2026-10'})
    assert e.value.code=='GOAL_PAUSED'

def test_goal_done_focus_does_not_automatically_restore_saved_goal(tmp_path):
    bank,s=env(tmp_path);a=create(s);pause_fixture(bank,a,True);b=create(s,'control-completing')
    s.update('demo','session-control','',b['id'],b['version'],{'status':'DONE'})
    assert s.current('demo','session-control','') is None

def test_refs_are_exact_journal_effect_key_bound(tmp_path):
    m=refs_module();bank,s=env(tmp_path);g,op=query_goal(bank,s)
    raw=row(bank,g)
    with bank.transaction() as db:f=m.inspect_goal(bank,db,raw)
    assert f['operation_ids']==[op['id']] and not f['unknown'] and not f['terminal']

def test_refs_do_not_adopt_an_unrelated_owned_operation(tmp_path):
    m=refs_module();bank,s=env(tmp_path);g=create(s)
    op=bank.prepare_subscription_query('demo','unrelated-direct-query')
    g,t=s.start_step('demo','session-control','',g['id'],g['version'],'subscription_query',{})
    g=s.finish_step('demo','session-control','',g['id'],t,'OBSERVED',{'operation_ids':[op['id']]})
    raw=row(bank,g)
    with bank.transaction() as db:f=m.inspect_goal(bank,db,raw)
    assert f['unknown'] and op['id'] not in f['operation_ids']

@pytest.mark.parametrize('state',['paused','unknown'])
def test_bank_confirm_cannot_bypass_goal_state(tmp_path,state):
    refs_module();bank,s=env(tmp_path);g,op=query_goal(bank,s)
    with bank.transaction() as db:
        if state=='paused':db.execute('UPDATE conversation_goals SET paused=1 WHERE id=?',(g['id'],))
        else:db.execute("UPDATE conversation_goals SET status='UNKNOWN' WHERE id=?",(g['id'],))
        before=[tuple(x) for x in db.execute('SELECT * FROM operations')]
    with pytest.raises(DomainError) as e:bank.confirm('demo',op['id'])
    assert e.value.code in ('GOAL_PAUSED','GOAL_UNKNOWN')
    with bank.transaction() as db:assert [tuple(x) for x in db.execute('SELECT * FROM operations')]==before

def test_successful_replay_remains_readonly_after_pause(tmp_path):
    refs_module();bank,s=env(tmp_path);g,op=query_goal(bank,s);done=bank.confirm('demo',op['id']);pause_fixture(bank,g)
    with bank.transaction() as db:before=list(db.iterdump())
    assert bank.confirm('demo',op['id'])==done
    with bank.transaction() as db:assert list(db.iterdump())==before

def test_model_slots_do_not_create_operation_bindings(tmp_path):
    refs_module();bank,s=env(tmp_path);g=create(s);op=bank.prepare_subscription_query('demo','ordinary-query')
    g=s.update('demo','session-control','',g['id'],g['version'],{'observations':{'operation_ids':[op['id']]}});pause_fixture(bank,g)
    assert bank.confirm('demo',op['id'])['status']=='SUCCEEDED'

OLD_GOAL_DDL="CREATE TABLE IF NOT EXISTS conversation_goals(id TEXT PRIMARY KEY, user_id TEXT NOT NULL,\n    session_hash TEXT NOT NULL, epoch TEXT NOT NULL, request_key TEXT NOT NULL,\n    intent_hash TEXT NOT NULL, kind TEXT NOT NULL,\n    status TEXT NOT NULL CHECK(status IN ('ACTIVE','UNKNOWN','DONE','CANCELLED')),\n    phase TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>=1), slots TEXT NOT NULL,\n    observations TEXT NOT NULL, question TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,\n    UNIQUE(user_id,session_hash,request_key))"

def test_real_legacy_schema_is_migrated_without_losing_goal(tmp_path):
    import hashlib
    bank=Bank(tmp_path/'legacy.db',clock=lambda:NOW)
    sh=hashlib.sha256(b'ff-goal-session:session-control').hexdigest()
    with bank.transaction() as db:
        db.execute(OLD_GOAL_DDL)
        db.execute("CREATE UNIQUE INDEX conversation_goal_one_open ON conversation_goals(user_id,session_hash) WHERE status IN ('ACTIVE','UNKNOWN')")
        db.execute('INSERT INTO conversation_goals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('legacy-goal','demo',sh,'','legacy-key','legacy-intent','spending_subscriptions','ACTIVE','COLLECTING',1,'{"period":"2026-10"}','{}',None,NOW.isoformat(),NOW.isoformat()))
    s=GoalStore(bank);g=s.current('demo','session-control','')
    assert g['id']=='legacy-goal' and g['status']=='ACTIVE'
    with bank.transaction() as db:
        assert {'paused','pause_gen'}<={x['name'] for x in db.execute('PRAGMA table_info(conversation_goals)')}
    pause_fixture(bank,g,True)
    assert GoalStore(Bank(bank.path,clock=lambda:NOW)).current('demo','session-control','') is None


def birthday_pending(bank,s):
    from ff_agent.conversation_goal_loop import GoalLoop
    loop=GoalLoop(bank,s)
    loop.handle('demo','session-control','','给妈妈安排明天生日，预算1500，用日常账户，只要花不要蛋糕','control-birthday-first')
    reply=loop.handle('demo','session-control','','第一个','control-birthday-choice')
    op=next(c['operation'] for c in reply['cards'] if c['type']=='operation')
    return s.current('demo','session-control',''),op

def test_unknown_bound_verification_blocks_goal_controls_inspection(tmp_path):
    m=refs_module();bank,s=env(tmp_path);g,op=birthday_pending(bank,s)
    assert bank.confirm('demo',op['id'])['status']=='WAITING_VERIFICATION'
    with bank.transaction() as db:db.execute("UPDATE verification_challenges SET status='UNKNOWN' WHERE opid=?",(op['id'],))
    raw=row(bank,g)
    with bank.transaction() as db:f=m.inspect_goal(bank,db,raw)
    assert op['id'] in f['operation_ids'] and f['unknown'],f

def test_guard_does_not_silently_skip_a_goal_after_64_saved_goals(tmp_path):
    refs_module();bank,s=env(tmp_path);g,op=query_goal(bank,s)
    with bank.transaction() as db:
        original=dict(db.execute('SELECT * FROM conversation_goals WHERE id=?',(g['id'],)).fetchone())
        for i in range(70):
            value={**original,'id':('0'*30)+format(i,'02x'),'request_key':'saved-before-guard-'+str(i),'paused':1,'pause_gen':1,'phase':'COLLECTING','observations':'{}'}
            cols=list(value);db.execute('INSERT INTO conversation_goals('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',tuple(value[k] for k in cols))
        db.execute('UPDATE conversation_goals SET paused=1 WHERE id=?',(g['id'],))
    with pytest.raises(DomainError) as e:bank.confirm('demo',op['id'])
    assert e.value.code=='GOAL_PAUSED'

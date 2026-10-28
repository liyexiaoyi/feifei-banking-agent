"""Demo secrets belong to a dedicated local credential flow, never the agent prompt."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
import importlib,importlib.util,json,hashlib

import pytest
from ff_agent.bank import Bank,DomainError
from ff_agent.card_controls import CardControls

OLD='OnlyDemo_old-472891'
NEW='OnlyDemo_new-583902'


@pytest.fixture
def env(tmp_path):
    clock=[datetime.fromisoformat('2026-10-28T12:00:00+08:00')]
    return Bank(tmp_path/'credentials.db',clock=lambda:clock[0]),clock


def service(env):
    assert importlib.util.find_spec('ff_agent.card_credentials') is not None,'dedicated demo credential flow missing'
    return importlib.import_module('ff_agent.card_credentials').CardCredentials(env[0])


def prepare(env,secret=OLD,current=None,version=0,key='credential-first',card='card_a_daily',user='demo'):
    return service(env).prepare(user,card,secret,current,version,key)


def finish(env,op):
    assert env[0].confirm('demo',op['id'])['status']=='WAITING_VERIFICATION'
    return env[0].verify('demo',op['id'],'246810')


def assets(env):
    with env[0].transaction() as db:return {t:[tuple(x) for x in db.execute('SELECT * FROM '+t+' ORDER BY id')] for t in ('accounts','transactions')}


def public(env,op):
    return json.dumps({'view':service(env).get('demo','card_a_daily'),'op':env[0].receipt('demo',op['id']),'audit':env[0].audit('demo')},ensure_ascii=False)


def test_read_missing_returns_unconfigured_without_lazy_creation(env):
    s=service(env)
    assert s.get('demo','card_a_daily')=={'card_id':'card_a_daily','configured':False,'credential_version':0,'simulated':True,'bank_connected':False}
    with env[0].transaction() as db:assert db.execute('SELECT count(*) FROM card_credentials').fetchone()[0]==0
    with pytest.raises(DomainError) as e:s.get('guest','card_a_daily')
    assert e.value.status==404


def test_initial_setting_requires_bound_red_confirmation_and_verification_without_funds_change(env):
    before=assets(env);s=service(env);op=prepare(env)
    assert op['type']=='card_credential_change' and op['risk']=='RED'
    assert s.get('demo','card_a_daily')['configured'] is False
    with pytest.raises(DomainError):env[0].verify('demo',op['id'],'246810')
    env[0].confirm('demo',op['id'])
    assert s.get('demo','card_a_daily')['configured'] is False
    with pytest.raises(DomainError):env[0].verify('demo',op['id'],'000000')
    done=env[0].verify('demo',op['id'],'246810')
    assert done['status']=='SUCCEEDED' and done['result']['credential_version']==1
    assert assets(env)==before
    assert s.get('demo','card_a_daily')['configured'] is True
    with env[0].transaction() as db:
        live=db.execute("SELECT * FROM card_credentials WHERE card_id='card_a_daily'").fetchone()
        assert len(bytes.fromhex(live['salt']))==16 and len(bytes.fromhex(live['verifier']))==32
        assert live['verifier']==hashlib.pbkdf2_hmac('sha256',OLD.encode(),bytes.fromhex(live['salt']),600000,32).hex()
        candidate=db.execute('SELECT * FROM card_credential_challenges WHERE operation_id=?',(op['id'],)).fetchone()
        assert candidate['consumed_at'] is not None and candidate['salt'] is None and candidate['verifier'] is None
        for t in ('operations','audit','messages','card_credentials','card_credential_challenges'):
            assert OLD not in str([tuple(x) for x in db.execute('SELECT * FROM '+t)])
    text=public(env,op)
    assert OLD not in text and live['salt'] not in text and live['verifier'] not in text


def test_current_secret_required_for_change_and_replayed_success_never_changes_twice(env):
    first=prepare(env);finish(env,first)
    with pytest.raises(DomainError):prepare(env,NEW,None,1,'change-no-current')
    with pytest.raises(DomainError):prepare(env,NEW,'Incorrect-demo-secret',1,'change-wrong-current')
    second=prepare(env,NEW,OLD,1,'change-valid-current');finish(env,second)
    assert env[0].confirm('demo',second['id'])['result']['credential_version']==2
    with pytest.raises(DomainError):prepare(env,NEW,OLD,1,'change-valid-current')
    with env[0].transaction() as db:
        live=db.execute("SELECT * FROM card_credentials WHERE card_id='card_a_daily'").fetchone()
        assert live['version']==2 and live['verifier']==hashlib.pbkdf2_hmac('sha256',NEW.encode(),bytes.fromhex(live['salt']),600000,32).hex()
    assert OLD not in public(env,second) and NEW not in public(env,second)


@pytest.mark.parametrize('value',[True,12345678,'short','bad secret with spaces','x'*65,'密码不是演示ASCII'])
def test_invalid_secret_never_stages_candidate_or_echoes_input(env,value):
    s=service(env)
    with pytest.raises(DomainError) as e:prepare(env,value)
    assert str(value) not in str(e.value)
    with env[0].transaction() as db:
        assert db.execute('SELECT count(*) FROM card_credential_challenges').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==0


def test_two_candidates_cas_only_one_can_install(env):
    a=prepare(env);b=prepare(env,NEW,key='credential-parallel-second')
    finish(env,a)
    with pytest.raises(DomainError):finish(env,b)
    assert service(env).get('demo','card_a_daily')['credential_version']==1


def test_concurrent_verification_installs_once(env):
    op=prepare(env);env[0].confirm('demo',op['id'])
    with ThreadPoolExecutor(max_workers=2) as p:
        values=list(p.map(lambda _:env[0].verify('demo',op['id'],'246810'),range(2)))
    assert all(x['status']=='SUCCEEDED' for x in values)
    assert service(env).get('demo','card_a_daily')['credential_version']==1


@pytest.mark.parametrize('action',['cancel','expire'])
def test_cancel_or_expire_discards_staged_hashes_but_not_live_secret(env,action):
    op=prepare(env)
    if action=='cancel':env[0].cancel('demo',op['id'])
    else:
        env[1][0]+=timedelta(minutes=6);env[0].snapshot('demo')
    with env[0].transaction() as db:
        candidate=db.execute('SELECT * FROM card_credential_challenges WHERE operation_id=?',(op['id'],)).fetchone()
        assert candidate['salt'] is None and candidate['verifier'] is None
    assert service(env).get('demo','card_a_daily')['configured'] is False
    with pytest.raises(DomainError):env[0].verify('demo',op['id'],'246810')


@pytest.mark.parametrize('state',['UNKNOWN','EXECUTING','MANUAL_REVIEW'])
def test_unresolved_credential_blocks_new_card_mutations_and_is_not_resent(env,state):
    op=prepare(env)
    with env[0].transaction() as db:db.execute('UPDATE operations SET status=? WHERE id=?',(state,op['id']))
    before=assets(env)
    with pytest.raises(DomainError):prepare(env,NEW,key='after-unknown-new')
    with pytest.raises(DomainError):CardControls(env[0]).prepare_control('demo','card_a_daily','lock',True,1,'after-unknown-lock')
    with pytest.raises(DomainError):env[0].confirm('demo',op['id'])
    env[1][0]+=timedelta(days=2);env[0].snapshot('demo')
    assert env[0].receipt('demo',op['id'])['observed_status']==state and assets(env)==before
    assert service(env).get('demo','card_a_daily')['configured'] is False


def test_three_wrong_current_attempts_share_sensitive_lock_but_reads_stay_available(env):
    finish(env,prepare(env))
    for i in range(3):
        with pytest.raises(DomainError):prepare(env,NEW,'Wrong-demo-password',1,'wrong-current-'+str(i))
    assert env[0].snapshot('demo')['locked'] is True
    with pytest.raises(DomainError) as e:prepare(env,NEW,OLD,1,'after-lock-valid')
    assert e.value.status==423
    assert service(env).get('demo','card_a_daily')['credential_version']==1
    env[1][0]+=timedelta(minutes=11)
    assert finish(env,prepare(env,NEW,OLD,1,'after-lock-expired'))['result']['credential_version']==2


def test_lost_or_foreign_card_and_stale_version_cannot_prepare(env):
    with pytest.raises(DomainError) as e:prepare(env,user='guest')
    assert e.value.status==404
    with pytest.raises(DomainError):prepare(env,version=True)
    with pytest.raises(DomainError):prepare(env,version=1)
    lost=env[0].prepare_card_status('demo','card_a_daily','report_lost','credential-card-lost',1);finish(env,lost)
    with pytest.raises(DomainError):prepare(env)


def test_candidate_cannot_be_substituted_or_reused_after_card_change(env):
    a=prepare(env);b=prepare(env,NEW,key='credential-other-candidate')
    with env[0].transaction() as db:
        p=dict(a['payload']);p['challenge_id']=b['payload']['challenge_id']
        db.execute('UPDATE operations SET payload=? WHERE id=?',(json.dumps(p,ensure_ascii=False,sort_keys=True),a['id']))
    with pytest.raises(DomainError):finish(env,a)
    assert service(env).get('demo','card_a_daily')['configured'] is False


def test_restart_retains_only_derived_live_credential(env):
    finish(env,prepare(env));restored=Bank(env[0].path,clock=lambda:env[1][0])
    assert importlib.import_module('ff_agent.card_credentials').CardCredentials(restored).get('demo','card_a_daily')['credential_version']==1
    assert finish((restored,env[1]),prepare((restored,env[1]),NEW,OLD,1,'restart-change'))['status']=='SUCCEEDED'


@pytest.mark.parametrize('key',['key.with.punctuation','credential+plus','credential key'])
def test_domain_request_key_matches_strict_public_key_contract(env,key):
    with pytest.raises(DomainError):prepare(env,key=key)


def test_cancelled_update_preserves_old_secret_and_does_not_unlock_card(env):
    finish(env,prepare(env))
    change=prepare(env,NEW,OLD,1,'candidate-then-cancel');env[0].cancel('demo',change['id'])
    lock=CardControls(env[0]).prepare_control('demo','card_a_daily','lock',True,1,'secret-while-locked');finish(env,lock)
    final=prepare(env,NEW,OLD,1,'old-still-valid');finish(env,final)
    assert CardControls(env[0]).get_controls('demo','card_a_daily')['locked'] is True
    assert service(env).get('demo','card_a_daily')['credential_version']==2


def test_old_secret_and_operation_code_failures_share_same_streak(env):
    finish(env,prepare(env))
    pending=prepare(env,NEW,OLD,1,'mixed-failure-pending');env[0].confirm('demo',pending['id'])
    with pytest.raises(DomainError):env[0].verify('demo',pending['id'],'000000')
    for i in range(2):
        with pytest.raises(DomainError):prepare(env,NEW,'WrongDemo_secret-123',1,'mixed-failure-'+str(i))
    assert env[0].snapshot('demo')['locked'] is True


def test_candidate_is_invalidated_by_card_version_and_risk_epoch_changes(env):
    op=prepare(env)
    lost=env[0].prepare_card_status('demo','card_a_daily','report_lost','credential-lost-later',1);finish(env,lost)
    with pytest.raises(DomainError):finish(env,op)
    assert service(env).get('demo','card_a_daily')['configured'] is False
    restore=env[0].prepare_card_status('demo','card_a_daily','restore','credential-restore-later',2);finish(env,restore)
    second=prepare(env,key='credential-risk-later')
    hold=env[0].activate_safety_hold('demo',{'codes':['fixture'],'reasons':['测试'],'advice':'独立核对'},'credential-risk-fixture')
    env[0].end_safety_hold('demo',hold['id'])
    with pytest.raises(DomainError):finish(env,second)
    assert service(env).get('demo','card_a_daily')['configured'] is False


def test_virtual_and_physical_credentials_are_independent(env):
    op=env[0].prepare_virtual_card('demo','a_daily','credential-virtual-card')
    card=env[0].confirm('demo',op['id'])['result']['card_id']
    finish(env,prepare(env,card=card))
    assert service(env).get('demo',card)['credential_version']==1
    assert service(env).get('demo','card_a_daily')['configured'] is False


def test_expired_candidates_purged_on_next_credential_prepare(env):
    first=prepare(env)
    env[1][0]+=timedelta(minutes=6)
    prepare(env,NEW,key='credential-after-expiry')
    with env[0].transaction() as db:
        old=db.execute('SELECT * FROM card_credential_challenges WHERE operation_id=?',(first['id'],)).fetchone()
        assert old['salt'] is None and old['verifier'] is None

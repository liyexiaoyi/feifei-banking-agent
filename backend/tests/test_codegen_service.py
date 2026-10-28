"""NL computation generation never grants financial authority or paid replay."""
import importlib
import importlib.util
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from ff_agent.bank import Bank, DomainError
from ff_agent.sandbox_service import SandboxService
from ff_agent.tool_guard import ToolGuard
from ff_agent.codegen_types import CalcIntent, GeneratedWat, GenerationError

WAT='(module (func (export "run") (param i64) (result i64) local.get 0))'
INTENT={'kind':'calc','description':'返回第一个整数参数，不要转账 private-intent-canary','args':[713]}

class Runner:
    def __init__(self): self.calls=0; self.error=None
    def run(self,wat,args):
        self.calls+=1
        if self.error: raise self.error
        return {'ok':True,'result':args[0] if args else 0,'fuel_used':2}

class Generator:
    provenance='fixture'; available=True
    def __init__(self): self.calls=0; self.error=None; self.value=GeneratedWat(WAT,('value',),{})
    def generate(self,intent,timeout_s=20):
        self.calls+=1
        if self.error: raise self.error
        return self.value

@pytest.fixture
def env(tmp_path):
    if not importlib.util.find_spec('ff_agent.codegen_service'):
        yield None
        return
    m=importlib.import_module('ff_agent.codegen_service')
    bank=Bank(tmp_path/'gen.db'); runner=Runner(); sandbox=SandboxService(bank,runner)
    guard=ToolGuard(bank.path,clock=lambda:bank.now().timestamp()); gen=Generator()
    try: yield bank,runner,gen,guard,m.CodegenService(bank,sandbox,gen,guard,allow_fixture=True)
    finally: guard.close()

def test_fixture_requires_explicit_trusted_server_opt_in(tmp_path):
    assert importlib.util.find_spec('ff_agent.codegen_service'), 'persistent codegen service missing'
    m=importlib.import_module('ff_agent.codegen_service'); bank=Bank(tmp_path/'off.db')
    sandbox=SandboxService(bank,Runner())
    with pytest.raises(ValueError):m.CodegenService(bank,sandbox,Generator())
    dishonest=Generator(); dishonest.provenance='model'
    with pytest.raises(ValueError):m.CodegenService(bank,sandbox,dishonest,allow_fixture=True)
    off=m.CodegenService(bank,sandbox)
    assert not off.available
    with pytest.raises(DomainError) as err: off.generate_and_run('demo',INTENT,'generation-off-key')
    assert err.value.status==503

def test_actual_generated_program_executes_once_and_is_not_financial_authority(env):
    assert env is not None, 'persistent codegen service missing'
    bank,runner,gen,guard,svc=env
    def finance():
        with bank.transaction() as db:return {t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in ('accounts','operations','transactions')}
    before=finance(); a=svc.generate_and_run('demo',INTENT,'generation-success-01')
    assert a['generation']['status']=='SUCCEEDED' and a['run']['status']=='SUCCEEDED'
    assert a['generation']['provenance']=='fixture' and a['run']['result']['result_text']=='713'
    assert a['generation']['wat_sha256']==a['run']['source_sha256'] and a['simulated'] is True
    assert svc.generate_and_run('demo',INTENT,'generation-success-01')==a
    assert svc.store.by_key('demo','generation-success-01')==a
    assert gen.calls==runner.calls==1 and finance()==before
    public=json.dumps(a)
    for secret in ('private-intent-canary', 'nonce', 'private_meta', 'wat"', 'configured_model'):
        assert secret not in public
    with bank.transaction() as db:
        row=db.execute('SELECT * FROM codegen_requests').fetchone()
        assert 'private-intent-canary' not in str(tuple(row))
        assert row['wat']==WAT and row['arg_count']==1

def test_same_key_changed_arguments_conflict_and_other_owner_cannot_read(env):
    assert env is not None, 'persistent codegen service missing'
    _,runner,gen,_,svc=env
    a=svc.generate_and_run('demo',INTENT,'generation-isolated-01')
    with pytest.raises(DomainError) as err:svc.generate_and_run('demo',{**INTENT,'args':[714]},'generation-isolated-01')
    assert err.value.status==409
    with pytest.raises(DomainError) as err:svc.store.get('guest',a['generation']['id'])
    assert err.value.status==404 and svc.store.list('guest')==[] and gen.calls==runner.calls==1

@pytest.mark.parametrize('outcome',['FAILED','SENT_UNKNOWN'])
def test_failed_or_unknown_generation_replay_has_no_paid_or_worker_retry(env,outcome):
    assert env is not None, 'persistent codegen service missing'
    _,runner,gen,_,svc=env;gen.error=GenerationError(outcome,'MODEL_FAILURE',{'billing_status':'unknown'})
    a=svc.generate_and_run('demo',INTENT,'generation-unknown-01')
    assert a['generation']['status']==outcome and a['run'] is None
    gen.error=None;gen.available=False
    assert svc.generate_and_run('demo',INTENT,'generation-unknown-01')==a
    assert gen.calls==1 and runner.calls==0

def test_unexpected_model_error_is_generic_delivery_unknown_and_no_repeat(env):
    assert env is not None, 'persistent codegen service missing'
    _,runner,gen,_,svc=env;gen.error=RuntimeError('provider-private-error-canary')
    a=svc.generate_and_run('demo',INTENT,'generation-error-001')
    assert a['generation']['status']=='SENT_UNKNOWN' and 'provider-private-error-canary' not in str(a)
    assert svc.generate_and_run('demo',INTENT,'generation-error-001')==a
    assert gen.calls==1 and runner.calls==0

def test_invalid_generated_envelope_never_reaches_worker(env):
    assert env is not None, 'persistent codegen service missing'
    _,runner,gen,_,svc=env;gen.value={'wat':WAT,'arg_names':['value'],'provenance':'model'}
    a=svc.generate_and_run('demo',INTENT,'generation-invalid-1')
    assert a['generation']['status']=='FAILED' and runner.calls==0 and gen.calls==1

def test_only_five_new_reservations_per_hour_replay_free_and_other_owner_independent(env):
    assert env is not None, 'persistent codegen service missing'
    _,runner,gen,_,svc=env
    for i in range(5):assert svc.generate_and_run('demo',INTENT,f'generation-quota-{i}')['generation']['status']=='SUCCEEDED'
    assert svc.generate_and_run('demo',INTENT,'generation-quota-0')['generation']['status']=='SUCCEEDED'
    with pytest.raises(DomainError) as err:svc.generate_and_run('demo',INTENT,'generation-quota-5')
    assert err.value.status==429 and gen.calls==runner.calls==5
    assert svc.generate_and_run('guest',INTENT,'generation-quota-5')['generation']['status']=='SUCCEEDED'

def test_codegen_pause_does_not_pause_balance_or_planner_and_sends_zero_more_calls(env):
    assert env is not None, 'persistent codegen service missing'
    _,runner,gen,guard,svc=env;gen.error=GenerationError('FAILED','BAD_OUTPUT')
    for i in range(3):svc.generate_and_run('demo',INTENT,f'generation-breaker-{i}')
    a=svc.generate_and_run('demo',INTENT,'generation-breaker-3')
    assert a['generation']['status']=='FAILED' and gen.calls==3 and runner.calls==0
    by={x['group']:x for x in guard.snapshot('demo')}
    assert by['codegen']['state']=='PAUSED' and by['bill']['state']==by['planner']['state']=='READY'

def test_same_key_inflight_read_is_nonblocking_and_cannot_start_second_generation(env):
    assert env is not None, 'persistent codegen service missing'
    _,runner,gen,_,svc=env;entered=threading.Event();release=threading.Event();original=gen.generate
    def slow(intent,timeout_s=20):
        entered.set();assert release.wait(3)
        return original(intent,timeout_s)
    gen.generate=slow
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(svc.generate_and_run,'demo',INTENT,'generation-parallel-1');assert entered.wait(2)
        try:
            second=pool.submit(svc.generate_and_run,'demo',INTENT,'generation-parallel-1').result(1)
            assert second['generation']['status']=='SENT' and second['run'] is None
        finally:release.set()
        assert first.result(2)['generation']['status']=='SUCCEEDED'
    assert gen.calls==runner.calls==1

def test_invalid_intent_rejected_before_reservation_or_generator(env):
    assert env is not None, 'persistent codegen service missing'
    bank,runner,gen,_,svc=env
    with pytest.raises((ValueError,GenerationError,DomainError)):
        svc.generate_and_run('demo',{**INTENT,'amount_cents':100},'generation-invalid-2')
    with bank.transaction() as db:assert db.execute('SELECT COUNT(*) FROM codegen_requests').fetchone()[0]==0
    assert gen.calls==runner.calls==0

def test_generation_run_reservation_is_atomic_and_source_capability_remains_internal(env):
    assert env is not None, 'persistent codegen service missing'
    bank,runner,gen,_,svc=env; store=svc.store
    intent=CalcIntent(INTENT['description'],(713,))
    view,nonce=store.reserve('demo',intent,'generation-store-01','fixture');id=view['generation']['id']
    assert nonce and store.mark_sent('demo',id,nonce)
    assert store.save_generated('demo',id,nonce,gen.value)
    old=svc.sandbox.reserve_generated
    def fail_after_insert(db,*args):
        old(db,*args);raise RuntimeError('force atomic rollback')
    svc.sandbox.reserve_generated=fail_after_insert
    with pytest.raises(RuntimeError):store.claim_run('demo',id,nonce,intent)
    with bank.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM sandbox_runs').fetchone()[0]==0
        assert db.execute('SELECT status FROM codegen_requests WHERE id=?',(id,)).fetchone()[0]=='GENERATED'
    svc.sandbox.reserve_generated=old
    cap=store.claim_run('demo',id,nonce,intent)
    assert cap['generation_id']==id and store.claim_run('demo',id,nonce,intent) is None
    assert not store.mark_sent('demo',id,nonce)
    assert runner.calls==0

def test_nonce_and_full_intent_protect_run_reservation(env):
    assert env is not None, 'persistent codegen service missing'
    bank,runner,gen,_,svc=env; store=svc.store; intent=CalcIntent(INTENT['description'],(713,))
    view,nonce=store.reserve('demo',intent,'generation-store-02','fixture');id=view['generation']['id']
    for bad in ('wrong',None):
        with pytest.raises((DomainError,ValueError)):store.mark_sent('demo',id,bad)
    store.mark_sent('demo',id,nonce);store.save_generated('demo',id,nonce,gen.value)
    with pytest.raises((DomainError,ValueError)):store.claim_run('demo',id,nonce,CalcIntent(INTENT['description'],(714,)))
    assert runner.calls==gen.calls==0
    with bank.transaction() as db:assert db.execute('SELECT COUNT(*) FROM sandbox_runs').fetchone()[0]==0

def test_incomplete_generated_request_never_auto_resumes_on_same_key(env):
    assert env is not None, 'persistent codegen service missing'
    _,runner,gen,_,svc=env; intent=CalcIntent(INTENT['description'],(713,));store=svc.store
    view,nonce=store.reserve('demo',intent,'generation-unfinished','fixture');id=view['generation']['id']
    store.mark_sent('demo',id,nonce);store.save_generated('demo',id,nonce,gen.value)
    a=svc.generate_and_run('demo',INTENT,'generation-unfinished')
    assert a['generation']['status']=='GENERATED' and a['run'] is None and runner.calls==gen.calls==0

def test_wat_expires_on_access_without_regenerating_or_erasing_receipt(env):
    assert env is not None, 'persistent codegen service missing'
    bank,runner,gen,_,svc=env; a=svc.generate_and_run('demo',INTENT,'generation-retain-01')
    bank.clock=lambda: __import__('datetime').datetime.now(__import__('datetime').timezone.utc)+timedelta(days=8)
    b=svc.store.by_key('demo','generation-retain-01')
    assert not b['generation']['source_available'] and b['generation']['wat_sha256']==a['generation']['wat_sha256']
    assert b['run']['result']['result_text']=='713'
    with bank.transaction() as db:assert db.execute('SELECT wat FROM codegen_requests').fetchone()[0] is None
    assert svc.generate_and_run('demo',INTENT,'generation-retain-01')['run']==b['run'] and gen.calls==runner.calls==1

@pytest.mark.parametrize('key',['invalid.key-01','invalid:key-01','invalid/key-01'])
def test_generation_key_matches_existing_opaque_key_contract_before_model(env,key):
    assert env is not None, 'persistent codegen service missing'
    _,runner,gen,_,svc=env
    with pytest.raises(DomainError) as error:svc.generate_and_run('demo',INTENT,key)
    assert error.value.status==400 and gen.calls==runner.calls==0

@pytest.mark.parametrize('key',['_generation-key','-generation-key'])
def test_valid_key_can_start_with_underscore_or_dash(env,key):
    assert env is not None, 'persistent codegen service missing'
    _,runner,gen,_,svc=env
    assert svc.generate_and_run('demo',INTENT,key)['generation']['status']=='SUCCEEDED'

def test_missing_origin_link_cannot_keep_a_success_label(env):
    assert env is not None, 'persistent codegen service missing'
    bank,runner,gen,_,svc=env
    a=svc.generate_and_run('demo',INTENT,'generation-damage-01')
    assert a['generation']['status']=='SUCCEEDED'
    with bank.transaction() as db:db.execute('DELETE FROM sandbox_generation_links')
    b=svc.store.get('demo',a['generation']['id'])
    assert b['generation']['status']=='UNKNOWN' and b['run'] is None
    assert gen.calls==runner.calls==1

def test_sanitized_provider_usage_is_kept_privately_without_raw_response(env):
    assert env is not None, 'persistent codegen service missing'
    bank,runner,gen,_,svc=env
    meta={'http_status':200,'usage':{'input_tokens':1234,'output_tokens':56},'requested_effort':'low'}
    gen.value=GeneratedWat(WAT,('value',),meta)
    result=svc.generate_and_run('demo',INTENT,'generation-meta-01')
    assert result['generation']['status']=='SUCCEEDED'
    with bank.transaction() as db:
        saved=json.loads(db.execute('SELECT private_meta_json FROM codegen_requests').fetchone()[0])
    assert saved==meta and 'usage' not in str(result) and 'input_tokens' not in str(result)

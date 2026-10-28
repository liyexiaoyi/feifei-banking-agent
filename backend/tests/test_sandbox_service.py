"""Owner-only computation receipts never become bank authority or automatic replay."""
import json,threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from ff_agent.bank import Bank,DomainError
from ff_agent.sandbox_process import SandboxError

WAT='(module (func (export "run") (result i64) i64.const 713))'

class FakeRunner:
    def __init__(self):self.calls=0;self.error=None
    def run(self,wat,args):
        self.calls+=1
        if self.error:raise self.error
        return {'ok':True,'result':713,'fuel_used':2,'simulated':True,'source_sha256':'a'*64,'elapsed_ms':1,'cpu_user_ms':1,'limits':{}}

@pytest.fixture
def env(tmp_path):
    import importlib.util,importlib
    assert importlib.util.find_spec('ff_agent.sandbox_service') is not None,'sandbox service not implemented'
    mod=importlib.import_module('ff_agent.sandbox_service');bank=Bank(tmp_path/'sandbox.db');runner=FakeRunner()
    return bank,runner,mod.SandboxService(bank,runner)


def test_success_owner_receipt_replay_does_not_compute_twice(env):
    bank,runner,service=env
    a=service.run('demo',WAT,[],'sandbox-key-01');b=service.run('demo',WAT,[],'sandbox-key-01')
    assert a==b and a['status']=='SUCCEEDED' and a['result']['result']==713 and a['simulated'] is True
    assert service.get('demo',a['id'])==a and runner.calls==1
    with pytest.raises(DomainError) as e:service.get('guest',a['id'])
    assert e.value.status==404


def test_same_key_different_payload_conflict(env):
    bank,runner,service=env;service.run('demo',WAT,[],'sandbox-key-02')
    with pytest.raises(DomainError) as e:service.run('demo',WAT+' ',[],'sandbox-key-02')
    assert e.value.status==409 and runner.calls==1


def test_known_failure_is_stored_and_not_retried(env):
    bank,runner,service=env;runner.error=SandboxError('IMPORT_DENIED')
    a=service.run('demo',WAT,[],'sandbox-key-03')
    assert a['status']=='FAILED' and a['result'] is None and a['error']['code']=='IMPORT_DENIED'
    assert service.run('demo',WAT,[],'sandbox-key-03')==a and runner.calls==1


def test_unknown_failure_is_read_only_and_never_raw_error(env):
    bank,runner,service=env;runner.error=RuntimeError('synthetic-private-canary')
    a=service.run('demo',WAT,[],'sandbox-key-04')
    assert a['status']=='UNKNOWN' and a['result'] is None
    assert 'synthetic-private-canary' not in json.dumps(a)
    assert service.run('demo',WAT,[],'sandbox-key-04')==a and runner.calls==1


def test_concurrent_same_key_does_not_hold_db_during_compute(env):
    bank,runner,service=env;entered=threading.Event();release=threading.Event();old=runner.run
    def slow(wat,args):entered.set();assert release.wait(3);return old(wat,args)
    runner.run=slow
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(service.run,'demo',WAT,[],'sandbox-key-05');assert entered.wait(2)
        try:
            second=pool.submit(service.run,'demo',WAT,[],'sandbox-key-05').result(1)
            assert second['status']=='RUNNING' and second['result'] is None
        finally:release.set()
        assert first.result(2)['status']=='SUCCEEDED'
    assert runner.calls==1


def test_compute_does_not_change_financial_rows_or_store_code(env):
    bank,runner,service=env
    def rows():
        with bank.transaction() as db:return {t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in ('accounts','operations','transactions')}
    before=rows();service.run('demo',WAT,[],'sandbox-key-06');assert rows()==before
    with bank.transaction() as db:
        dumped=''.join(str(tuple(r)) for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'sandbox%'") )
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'sandbox%'")]
        assert tables
        for table in tables:dumped+=''.join(str(tuple(r)) for r in db.execute('SELECT * FROM '+table))
        dumped+=''.join(str(tuple(r)) for r in db.execute("SELECT * FROM audit WHERE event LIKE 'sandbox%'") )
    assert WAT not in dumped and 'i64.const 713' not in dumped


@pytest.mark.parametrize('wat,args,key',[(WAT,[True],'sandbox-key-07'),(WAT,[],'x'),('a'*32769,[],'sandbox-key-08'),(WAT,{},'sandbox-key-09')],ids=['bool','short-key','large','bad-list'])
def test_invalid_input_does_not_reach_worker(env,wat,args,key):
    bank,runner,service=env
    with pytest.raises((DomainError,SandboxError)):service.run('demo',wat,args,key)
    assert runner.calls==0



def test_domain_errors_have_stable_machine_codes(env):
    bank,runner,service=env
    with pytest.raises(DomainError) as e:service.run('demo',WAT,[],'x')
    assert e.value.code=='INVALID_INPUT' and 'request_key' not in e.value.message


@pytest.mark.parametrize('bad',[{}, {'ok':True,'result':True}, {'ok':1,'result':1}, {'ok':False}],ids=['empty','bool-result','integer-ok','not-ok'])
def test_bad_runner_result_cannot_be_stored_as_success(env,bad):
    bank,runner,service=env;runner.run=lambda wat,args:bad
    result=service.run('demo',WAT,[],'invalid-result-key')
    assert result['status']=='UNKNOWN' and result['result'] is None


def test_hourly_budget_replay_and_owner_isolation(env):
    bank,runner,service=env
    for i in range(30):service.run('demo',WAT,[],f'hourly-key-{i:03}')
    assert service.run('demo',WAT,[],'hourly-key-000')['status']=='SUCCEEDED'
    with pytest.raises(DomainError) as e:service.run('demo',WAT,[],'hourly-key-031')
    assert e.value.status==429 and e.value.code=='RATE_LIMITED' and runner.calls==30
    assert service.run('guest',WAT,[],'hourly-key-031')['status']=='SUCCEEDED'


def test_readonly_lookup_by_key_and_list_never_execute(env):
    bank,runner,service=env;a=service.run('demo',WAT,[],'sandbox-key-lookup')
    assert service.by_key('demo','sandbox-key-lookup')==a and a in service.list('demo')
    assert service.list('guest')==[] and runner.calls==1
    with pytest.raises(DomainError) as e:service.by_key('guest','sandbox-key-lookup')
    assert e.value.status==404 and runner.calls==1



def test_unverified_cleanup_receipt_is_unknown_not_definite_failure(env):
    bank,runner,service=env;runner.error=SandboxError('CLEANUP_UNVERIFIED')
    a=service.run('demo',WAT,[],'cleanup-unknown-key')
    assert a['status']=='UNKNOWN' and a['result'] is None
    assert service.run('demo',WAT,[],'cleanup-unknown-key')==a and runner.calls==1


def test_stale_running_is_readonly_unknown_not_claimed_stopped(env):
    bank,runner,service=env;a=service.run('demo',WAT,[],'stale-running-key')
    with bank.transaction() as db:
        db.execute("UPDATE sandbox_runs SET status='RUNNING',result_json=NULL,created_epoch=? WHERE id=?",(bank.now().timestamp()-120,a['id']))
        audit_before=db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]
    for view in [service.by_key('demo','stale-running-key'),service.get('demo',a['id']),service.list('demo')[0],service.run('demo',WAT,[],'stale-running-key')]:
        assert view['status']=='UNKNOWN' and view['error']['code']=='UNKNOWN_OUTCOME' and view['result'] is None
    assert runner.calls==1
    with bank.transaction() as db:
        assert db.execute('SELECT status FROM sandbox_runs WHERE id=?',(a['id'],)).fetchone()[0]=='RUNNING'
        assert db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]==audit_before
        db.execute('UPDATE sandbox_runs SET created_epoch=? WHERE id=?',(bank.now().timestamp()-1,a['id']))
    assert service.get('demo',a['id'])['status']=='RUNNING'

"""Real Windows/Wasm integration; all generated input goes through the Job broker."""
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import pytest

ROOT=Path(__file__).resolve().parents[2]
ADD='(module (func (export "run") (param i64 i64) (result i64) local.get 0 local.get 1 i64.add))'
ONE='(module (func (export "run") (result i64) i64.const 1))'


@pytest.fixture
def runner(tmp_path):
    def build():
        assert importlib.util.find_spec('ff_agent.sandbox_process') is not None,'sandbox process module is not implemented'
        module=importlib.import_module('ff_agent.sandbox_process')
        python=Path(os.environ.get('FF_SANDBOX_TEST_PYTHON',sys.executable))
        worker=ROOT/'backend/ff_agent/sandbox_worker.py'
        assert worker.is_file(),'trusted Wasm worker is not implemented'
        return module,module.SandboxRunner(Path(getattr(sys,'_base_executable',sys.executable)),worker,tmp_path/'jobs',runtime_site=python.parent.parent/'Lib/site-packages')
    return build


def test_actual_integer_compute_has_server_owned_limits(runner):
    module,service=runner()
    result=service.run(ADD,[3,4])
    assert result['ok'] is True and result['simulated'] is True and result['result']==7
    assert type(result['fuel_used']) is int and 0<result['fuel_used']<=100000
    assert result['source_sha256']==hashlib.sha256(ADD.encode()).hexdigest()
    assert result['limits']=={'linear_memory_bytes':1048576,'process_memory_bytes':268435456,'cpu_seconds':2,'wall_seconds':5,'output_bytes':4096}


@pytest.mark.parametrize('wat,args',[(None,[]),(123,[]),('a'*32769,[]),(ONE,[True]),(ONE,['1']),(ONE,[1.5]),(ONE,[0]*9),(ONE,{}),(ADD,[2**63,0])],ids=['null','not-text','too-large','boolean','string','float','too-many','not-list','i64-overflow'])
def test_invalid_input_never_becomes_host_code(runner,wat,args):
    module,service=runner()
    with pytest.raises(module.SandboxError) as error:service.run(wat,args)
    assert error.value.code=='INVALID_INPUT'


@pytest.mark.parametrize('namespace,function',[('env','open'),('env','fetch'),('env','bank_transfer'),('wasi_snapshot_preview1','path_open')])
def test_any_host_capability_import_is_rejected_before_execution(runner,namespace,function):
    module,service=runner()
    wat=f'(module (import "{namespace}" "{function}" (func)) (func (export "run") (result i64) i64.const 1))'
    with pytest.raises(module.SandboxError) as error:service.run(wat,[])
    assert error.value.code=='IMPORT_DENIED'
    assert service.run(ONE,[])['result']==1


@pytest.mark.parametrize('wat',[
 '(module (func (export "run") (result i64) (loop $again br $again) i64.const 1))',
 '(module (func $begin (loop $again br $again)) (start $begin) (func (export "run") (result i64) i64.const 1))',
])
def test_runtime_and_start_loops_are_fuel_bounded_and_next_run_recovers(runner,wat):
    module,service=runner();start=time.monotonic()
    with pytest.raises(module.SandboxError) as error:service.run(wat,[])
    assert error.value.code=='FUEL_EXHAUSTED'
    assert time.monotonic()-start<8
    assert service.run(ONE,[])['result']==1


def test_linear_memory_initial_and_growth_limits(runner):
    module,service=runner()
    wat='(module (memory 16) (func (export "run") (result i32) i32.const 1 memory.grow))'
    assert service.run(wat,[])['result']==-1
    with pytest.raises(module.SandboxError) as error:service.run('(module (memory 17) (func (export "run") (result i64) i64.const 1))',[])
    assert error.value.code in ('MEMORY_LIMIT','INVALID_MODULE')


@pytest.mark.parametrize('wat',[
 '(module (func (export "run") (result f64) f64.const 1))',
 '(module (func (export "other") (result i64) i64.const 1))',
 'print("this must never be Python exec")',
])
def test_no_float_missing_entry_or_host_language_fallback(runner,wat):
    module,service=runner()
    with pytest.raises(module.SandboxError) as error:service.run(wat,[])
    assert error.value.code in ('INVALID_EXPORT','INVALID_MODULE')


def fake_runner(runner,tmp_path,code):
    module,_=runner();worker=tmp_path/'trusted-test-worker.py';worker.write_text(code,encoding='utf8')
    runtime=Path(os.environ.get('FF_SANDBOX_TEST_PYTHON',sys.executable));return module,module.SandboxRunner(Path(getattr(sys,'_base_executable',sys.executable)),worker,tmp_path/'fake-jobs',runtime_site=runtime.parent.parent/'Lib/site-packages')


PREFIX='import sys,json,time,os\nprint(json.dumps({"ready":True,"protocol":1}),flush=True)\npayload=sys.stdin.readline()\nif not payload:sys.exit(0)\n'


def test_total_timeout_terminates_blocked_worker(runner,tmp_path):
    module,service=fake_runner(runner,tmp_path,PREFIX+'time.sleep(60)\n')
    start=time.monotonic()
    with pytest.raises(module.SandboxError) as error:service.run(ONE,[])
    assert error.value.code=='TIMEOUT'
    assert time.monotonic()-start<8
    assert runner()[1].run(ONE,[])['result']==1


def test_output_is_bounded_without_unbounded_communicate(runner,tmp_path):
    module,service=fake_runner(runner,tmp_path,PREFIX+'sys.stdout.write("x"*8192);sys.stdout.flush();time.sleep(60)\n')
    with pytest.raises(module.SandboxError) as error:service.run(ONE,[])
    assert error.value.code=='OUTPUT_LIMIT'


def test_child_environment_has_no_application_secret(runner,tmp_path,monkeypatch):
    monkeypatch.setenv('FF_SANDBOX_CANARY','synthetic-test-secret')
    module,service=fake_runner(runner,tmp_path,PREFIX+'print(json.dumps({"ok":True,"result":int("FF_SANDBOX_CANARY" in os.environ),"fuel_used":1}),flush=True)\n')
    assert service.run(ONE,[])['result']==0


def test_invalid_worker_output_cannot_supply_limits_or_success(runner,tmp_path):
    module,service=fake_runner(runner,tmp_path,PREFIX+'print(json.dumps({"ok":True,"result":True,"fuel_used":1,"limits":{"bank_access":True}}),flush=True)\n')
    with pytest.raises(module.SandboxError) as error:service.run(ONE,[])
    assert error.value.code in ('INVALID_OUTPUT','PROCESS_FAILED')


def test_job_assignment_failure_sends_no_generated_input(runner,tmp_path,monkeypatch):
    module,_=runner()
    marker=tmp_path/'received.txt'
    module,service=fake_runner(runner,tmp_path,PREFIX+f'open({str(marker)!r},"w").write("received")\n')
    def fail(self,pid):raise module.JobError('synthetic assignment failure')
    monkeypatch.setattr(module.WindowsJob,'assign_process',fail)
    with pytest.raises(module.SandboxError) as error:service.run(ONE,[])
    assert error.value.code=='RUNTIME_UNAVAILABLE'
    assert not marker.exists()


def test_real_job_denies_process_memory_above_cap(runner,tmp_path):
    code=PREFIX+'try:\n    data=bytearray(300*1024*1024)\n    result=0\nexcept MemoryError:\n    result=1\nprint(json.dumps({"ok":True,"result":result,"fuel_used":1}),flush=True)\n'
    module,service=fake_runner(runner,tmp_path,code)
    assert service.run(ONE,[])['result']==1


def test_real_job_stops_cpu_spin_before_wall_timeout(runner,tmp_path):
    module,service=fake_runner(runner,tmp_path,PREFIX+'while True:pass\n')
    with pytest.raises(module.SandboxError) as error:service.run(ONE,[])
    assert error.value.code=='PROCESS_FAILED'


def test_real_job_blocks_additional_child_process(runner,tmp_path):
    code=PREFIX+'import subprocess\ntry:\n    child=subprocess.Popen([sys.executable,"-I","-c","pass"],creationflags=subprocess.CREATE_NO_WINDOW)\n    child.wait();result=1\nexcept OSError:\n    result=0\nprint(json.dumps({"ok":True,"result":result,"fuel_used":1}),flush=True)\n'
    module,service=fake_runner(runner,tmp_path,code)
    assert service.run(ONE,[])['result']==0


@pytest.mark.parametrize('terminal',[
    '{"ok":true,"result":1,"result":2,"fuel_used":1}',
    '{"ok":true,"result":1,"fuel_used":true}',
    '{"ok":true,"result":NaN,"fuel_used":1}',
    '{"ok":true,"result":1,"fuel_used":100001}',
    '{"ok":false,"error":{"code":"bank_success","message":"fake"}}',
],ids=['duplicate','boolean-fuel','nan','over-fuel','unknown-code'])
def test_terminal_schema_cannot_forge_result(runner,tmp_path,terminal):
    module,service=fake_runner(runner,tmp_path,PREFIX+f'print({terminal!r},flush=True)\n')
    with pytest.raises(module.SandboxError) as error:service.run(ONE,[])
    assert error.value.code in ('INVALID_OUTPUT','RUNTIME_UNAVAILABLE')


def test_blocked_input_writer_is_killed_and_reaped(runner,tmp_path,monkeypatch):
    module,service=fake_runner(runner,tmp_path,'import json,time\nprint(json.dumps({"ready":True,"protocol":1}),flush=True)\ntime.sleep(60)\n')
    original=module.subprocess.Popen;children=[]
    def capture(*a,**kw):
        p=original(*a,**kw);children.append(p);return p
    monkeypatch.setattr(module.subprocess,'Popen',capture)
    start=time.monotonic()
    with pytest.raises(module.SandboxError) as error:service.run('\x01'*32768,[])
    assert error.value.code=='TIMEOUT' and time.monotonic()-start<8
    assert len(children)==1 and children[0].poll() is not None
    assert children[0].stdin.closed and children[0].stdout.closed


def test_global_gate_refuses_second_run_without_spawning(runner,tmp_path,monkeypatch):
    import threading
    module,service=fake_runner(runner,tmp_path,PREFIX+'time.sleep(.5)\nprint(json.dumps({"ok":True,"result":1,"fuel_used":1}),flush=True)\n')
    original=module.WindowsJob.assign_process;entered=threading.Event();results=[]
    def assigned(self,handle):
        original(self,handle);entered.set()
    monkeypatch.setattr(module.WindowsJob,'assign_process',assigned)
    thread=threading.Thread(target=lambda:results.append(service.run(ONE,[])))
    thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(module.SandboxError) as error:service.run(ONE,[])
        assert error.value.code=='BUSY'
    finally:thread.join(8)
    assert not thread.is_alive() and results[0]['result']==1
    assert service.run(ONE,[])['result']==1


def test_native_job_close_reaps_only_owned_live_child():
    import ctypes,subprocess
    from ff_agent.sandbox_job import WindowsJob,JobError
    child=subprocess.Popen([sys._base_executable,'-I','-c','import sys;sys.stdin.readline();sys.stdin.readline()'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
    job=WindowsJob()
    try:
        job.assign_process(int(child._handle));child.stdin.write(b'wait\n');child.stdin.flush()
        assert child.poll() is None
        job.close();assert child.wait(timeout=2)!=0
        job.close()
        assert os.getpid()>0
    finally:
        job.close()
        if child.poll() is None:child.kill();child.wait(timeout=2)
        child.stdin.close();child.stdout.close();child.stderr.close()
    kernel=ctypes.WinDLL('kernel32');kernel.GetCurrentProcess.restype=ctypes.c_void_p
    with WindowsJob() as own:
        with pytest.raises(JobError):own.assign_process(int(kernel.GetCurrentProcess()))



def test_cpu_accounting_at_acceptance_cannot_exceed_limit(runner):
    module,service=runner()
    with pytest.raises(module.SandboxError) as error:
        service._result({'ok':True,'result':1,'fuel_used':1},b'fixed',time.monotonic(),{'user_time_100ns':20000001})
    assert error.value.code=='PROCESS_FAILED'


def test_failed_kill_cannot_block_cleanup_or_allow_next_run(runner,tmp_path,monkeypatch):
    import threading
    module,service=fake_runner(runner,tmp_path,'import json,time\nprint(json.dumps({"ready":True,"protocol":1}),flush=True)\ntime.sleep(60)\n')
    NativeJob=module.WindowsJob;jobs=[];children=[];errors=[]
    class FailingClose(NativeJob):
        def __init__(self,*a,**kw):super().__init__(*a,**kw);jobs.append(self)
        def close(self):raise module.JobError('synthetic termination denied')
    original=module.subprocess.Popen
    def capture(*a,**kw):
        child=original(*a,**kw);children.append(child);child.kill=lambda:None;return child
    monkeypatch.setattr(module,'WindowsJob',FailingClose)
    monkeypatch.setattr(module.subprocess,'Popen',capture)
    def run():
        try:service.run('\x01'*32768,[])
        except BaseException as e:errors.append(e)
    thread=threading.Thread(target=run,daemon=True);thread.start();thread.join(7.8)
    try:
        assert not thread.is_alive(),'cleanup blocked in a buffered pipe close after failed termination'
        assert len(children)==1 and children[0].poll() is None
        assert errors and isinstance(errors[0],module.SandboxError)
        with pytest.raises(module.SandboxError):service.run(ONE,[])
        assert len(children)==1,'failed cleanup must prevent spawning another worker'
    finally:
        for job in jobs:NativeJob.close(job)
        for child in children:child.wait(timeout=2)
        thread.join(2)



def test_trusted_runtime_must_be_explicit(runner,tmp_path):
    module,_=runner()
    with pytest.raises(ValueError):module.SandboxRunner(Path(sys._base_executable),ROOT/'backend/ff_agent/sandbox_worker.py',tmp_path/'implicit-runtime')



def test_transient_windows_cwd_lock_is_bounded_and_does_not_corrupt_result(runner,monkeypatch):
    module,service=runner();original=module.tempfile.TemporaryDirectory;instances=[]
    def transient(*a,**kw):
        tmp=original(*a,**kw);cleanup=tmp.cleanup;attempts=[]
        def close():
            attempts.append(1)
            if len(attempts)==1:raise PermissionError('synthetic Windows cwd release lag')
            return cleanup()
        tmp.cleanup=close;instances.append((tmp,attempts));return tmp
    monkeypatch.setattr(module.tempfile,'TemporaryDirectory',transient)
    assert service.run(ONE,[])['result']==1
    assert len(instances)==1 and len(instances[0][1])>=2
    assert not Path(instances[0][0].name).exists()



def test_post_spawn_cleanup_failure_is_explicit_unknown_outcome(runner,monkeypatch):
    module,service=runner();original=module.SandboxRunner._cleanup_temp
    monkeypatch.setattr(module.SandboxRunner,'_cleanup_temp',staticmethod(lambda tmp:False))
    try:
        with pytest.raises(module.SandboxError) as e:service.run(ONE,[])
        assert e.value.code=='CLEANUP_UNVERIFIED' and module._QUARANTINE
    finally:
        monkeypatch.setattr(module.SandboxRunner,'_cleanup_temp',staticmethod(original))
        service._check_quarantine()

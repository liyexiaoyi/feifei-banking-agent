"""Protected component checks; this test harness is NOT the production broker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from ff_agent.sandbox_job import WindowsJob

ROOT=Path(__file__).resolve().parents[2]
PYTHON=Path(os.environ.get('FF_SANDBOX_TEST_PYTHON',sys.executable))
WORKER=ROOT/'backend/ff_agent/sandbox_worker.py'
ONE='(module (func (export "run") (result i64) i64.const 1))'


def protected(wat,args):
    """Fixed reviewed worker only; external Job and 5s watchdog before any input."""
    assert PYTHON.is_file()
    env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','TEMP','TMP')}
    # Windows venv launchers spawn another interpreter and violate ActiveProcessLimit=1.
    # This fixed trusted bootstrap selects only the configured runtime site, never guest code.
    bootstrap='import sys,runpy;sys.path.insert(0,sys.argv[1]);runpy.run_path(sys.argv[2],run_name="__main__")'
    runtime_site=PYTHON.parent.parent/'Lib/site-packages'
    base=Path(getattr(sys,'_base_executable',sys.executable))
    proc=subprocess.Popen([str(base),'-I','-c',bootstrap,str(runtime_site),str(WORKER)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          env=env,creationflags=subprocess.CREATE_NO_WINDOW)
    job=WindowsJob()
    timer=None
    try:
        job.assign_process(int(proc._handle))
        def stop():
            try:job.terminate()
            except Exception:pass
        timer=threading.Timer(5,stop);timer.start()
        payload=json.dumps({'wat':wat,'args':args},ensure_ascii=False,separators=(',',':')).encode()+b'\n'
        stdout,stderr=proc.communicate(payload,timeout=6)
        assert proc.returncode==0 and not stderr,(proc.returncode,stderr[:200])
        assert len(stdout)<=4096
        frames=[json.loads(line) for line in stdout.splitlines()]
        assert len(frames)==2 and frames[0]=={'ready':True,'protocol':1}
        usage=job.usage()
        # Process.wait is authoritative; Job accounting may settle just after it.
        until=time.monotonic()+0.5
        while usage['active_processes'] and time.monotonic()<until:
            time.sleep(0.01)
            usage=job.usage()
        return frames[1],usage
    finally:
        if timer is not None:timer.cancel()
        if proc.poll() is None:proc.kill();proc.wait(timeout=3)
        job.close()


def test_protected_real_worker_computes_and_resources_are_kernel_owned():
    result,usage=protected('(module (func (export "run") (param i64 i64) (result i64) local.get 0 local.get 1 i64.add))',[3,4])
    assert result['ok'] is True and result['result']==7
    assert type(result['fuel_used']) is int and 0<result['fuel_used']<=100000
    assert usage['process_memory_bytes']==268435456 and usage['cpu_limit_100ns']==20000000
    assert usage['active_process_limit']==1 and usage['active_processes']==0


@pytest.mark.parametrize('namespace,function',[('env','open'),('env','fetch'),('env','bank_transfer'),('wasi_snapshot_preview1','path_open')])
def test_all_host_import_declarations_are_denied(namespace,function):
    result,_=protected(f'(module (import "{namespace}" "{function}" (func)) (func (export "run") (result i64) i64.const 1))',[])
    assert result['ok'] is False and result['error']['code']=='IMPORT_DENIED'


@pytest.mark.parametrize('wat,args,code',[
    (ONE,[True],'INVALID_INPUT'),(ONE,['1'],'INVALID_INPUT'),(ONE,[1.5],'INVALID_INPUT'),
    (ONE,[0]*9,'INVALID_INPUT'),(ONE,{},'INVALID_INPUT'),('a'*32769,[],'INVALID_INPUT'),
    ('print("not Python execution")',[],'INVALID_MODULE'),
    ('(module (func (export "run") (result f64) f64.const 1))',[],'INVALID_EXPORT'),
    ('(module (func (export "other") (result i64) i64.const 1))',[],'INVALID_EXPORT'),
    ('(module (func (export "run") (param i32) (result i32) local.get 0))',[2**31],'INVALID_INPUT'),
],ids=['bool','string','float','too-many','not-list','wat-size','host-language','float-return','wrong-export','i32-overflow'])
def test_worker_input_and_exports_fail_closed(wat,args,code):
    result,_=protected(wat,args)
    assert result['ok'] is False and result['error']['code']==code
    assert set(result['error'])=={'code','message'}


@pytest.mark.parametrize('wat',[
 '(module (func (export "run") (result i64) (loop $again br $again) i64.const 1))',
 '(module (func $begin (loop $again br $again)) (start $begin) (func (export "run") (result i64) i64.const 1))',
],ids=['function-loop','start-loop'])
def test_fuel_covers_function_and_start(wat):
    result,_=protected(wat,[])
    assert result['ok'] is False and result['error']['code']=='FUEL_EXHAUSTED'


def test_worker_linear_memory_limits():
    result,_=protected('(module (memory 16) (func (export "run") (result i32) i32.const 1 memory.grow))',[])
    assert result['ok'] is True and result['result']==-1
    result,_=protected('(module (memory 17) (func (export "run") (result i64) i64.const 1))',[])
    assert result['ok'] is False and result['error']['code']=='MEMORY_LIMIT'


def test_maximum_legal_wat_repeated_under_native_limits():
    prefix='(module (func (export "run") (result i64) i64.const 1))'
    wat=prefix+' '*(32768-len(prefix))
    for _ in range(10):
        result,usage=protected(wat,[])
        assert result['ok'] is True and result['result']==1
        assert usage['user_time_100ns']<=20000000
        assert usage['peak_process_memory_bytes']<=268435456

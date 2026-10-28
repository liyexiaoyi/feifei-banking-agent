# Server-owned sandbox broker: one trusted worker per run inside a Windows Job Object.
import hashlib
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .sandbox_job import JobError, WindowsJob

MAX_WAT_BYTES = 32768
MAX_ARGS = 8
MAX_FRAME_BYTES = 198656
TOTAL_TIMEOUT_S = 5.0
MAX_OUTPUT_BYTES = 4096
FUEL_LIMIT = 100000
WASM_MEMORY_BYTES = 1048576
JOB_MEMORY_BYTES = 268435456
JOB_CPU_SECONDS = 2
I64_MIN, I64_MAX = -(1 << 63), (1 << 63) - 1
# Fixed trusted bootstrap; argv[1]=runtime_site, argv[2]=worker. Never interpolates input.
BOOTSTRAP = 'import sys,runpy;sys.path.insert(0,sys.argv[1]);runpy.run_path(sys.argv[2],run_name=\'__main__\')'

_GATE = threading.BoundedSemaphore(1)
_EOF = object()
# Retain handles/streams when termination cannot be verified; no further spawn.
_QUARANTINE = []

MESSAGES = {
    'BUSY': '计算正忙，请稍后再试', 'INVALID_INPUT': '计算输入无效',
    'RUNTIME_UNAVAILABLE': '受限计算暂不可用', 'TIMEOUT': '计算超时，已停止',
    'OUTPUT_LIMIT': '计算输出超限，已停止', 'INVALID_OUTPUT': '计算结果无效',
    'PROCESS_FAILED': '计算进程异常，未取得结果', 'INVALID_MODULE': '计算模块无效',
    'IMPORT_DENIED': '计算不能访问外部功能', 'FUEL_EXHAUSTED': '计算步数超限，已停止',
    'MEMORY_LIMIT': '计算内存超限，已停止', 'INVALID_EXPORT': '计算入口无效',
    'GUEST_TRAP': '计算执行出错',
    'CLEANUP_UNVERIFIED': '计算清理状态未核实，请先核对原记录。',
}
WORKER_CODES = frozenset({'INVALID_INPUT', 'INVALID_MODULE', 'IMPORT_DENIED',
                          'FUEL_EXHAUSTED', 'MEMORY_LIMIT', 'INVALID_EXPORT',
                          'GUEST_TRAP', 'RUNTIME_UNAVAILABLE'})


class SandboxError(Exception):
    def __init__(self, code):
        code = code if code in MESSAGES else 'RUNTIME_UNAVAILABLE'
        super().__init__(MESSAGES[code])
        self.code = code
        self.message = MESSAGES[code]


def _no_dupes(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('duplicate key')
        out[key] = value
    return out


def _parse(line):
    try:
        return json.loads(line.decode('utf-8'), object_pairs_hook=_no_dupes, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise SandboxError('INVALID_OUTPUT') from None


def _is_ready(msg):
    return (type(msg) is dict and msg.keys() == {'ready', 'protocol'}
            and msg['ready'] is True and type(msg['protocol']) is int and msg['protocol'] == 1)


def _frame(wat, args):
    if type(wat) is not str:
        raise SandboxError('INVALID_INPUT')
    try:
        src = wat.encode('utf-8')
    except UnicodeEncodeError:
        raise SandboxError('INVALID_INPUT') from None
    if not src or len(src) > MAX_WAT_BYTES:
        raise SandboxError('INVALID_INPUT')
    if type(args) is not list or len(args) > MAX_ARGS:
        raise SandboxError('INVALID_INPUT')
    for arg in args:
        if type(arg) is not int or not I64_MIN <= arg <= I64_MAX:
            raise SandboxError('INVALID_INPUT')
    frame = json.dumps({'wat': wat, 'args': list(args)}, ensure_ascii=False,
                       separators=(',', ':')).encode('utf-8') + b'\n'
    if len(frame) > MAX_FRAME_BYTES:
        raise SandboxError('INVALID_INPUT')
    return src, frame


def _put(q, item, stop):
    while not stop.is_set():
        try:
            q.put(item, timeout=0.05)
            return
        except queue.Full:
            continue


def _reader(stream, q, stop):
    try:
        while not stop.is_set():
            chunk = stream.read1(1024)
            if not chunk:
                break
            _put(q, chunk, stop)
    except (OSError, ValueError):
        pass
    finally:
        _put(q, _EOF, stop)


def _writer(stream, frame):
    try:
        stream.write(frame)
        stream.flush()
        stream.close()
    except (OSError, ValueError):
        pass


class SandboxRunner:
    def __init__(self, base_python, worker, work_root, *, runtime_site=None):
        if runtime_site is None:
            raise ValueError('explicit trusted runtime_site is required')
        if not isinstance(runtime_site, Path):
            raise ValueError('runtime_site must be a Path')
        try:
            self.base_python = Path(base_python).resolve(strict=True)
            self.worker = Path(worker).resolve(strict=True)
            Path(work_root).mkdir(parents=True, exist_ok=True)
            self.work_root = Path(work_root).resolve(strict=True)
            self.runtime_site = runtime_site.resolve(strict=True)
        except (OSError, TypeError):
            raise ValueError('sandbox paths must exist') from None
        if not (self.base_python.is_file() and self.worker.is_file()
                and self.work_root.is_dir() and self.runtime_site.is_dir()):
            raise ValueError('invalid sandbox paths')

    @property
    def limits(self):
        return {'linear_memory_bytes': WASM_MEMORY_BYTES, 'process_memory_bytes': JOB_MEMORY_BYTES,
                'cpu_seconds': JOB_CPU_SECONDS, 'wall_seconds': int(TOTAL_TIMEOUT_S),
                'output_bytes': MAX_OUTPUT_BYTES}

    def run(self, wat, args=None):
        if args is None:
            args = []
        src, frame = _frame(wat, args)
        if not _GATE.acquire(blocking=False):
            raise SandboxError('BUSY')
        try:
            self._check_quarantine()
            return self._run(src, frame)
        finally:
            _GATE.release()

    @staticmethod
    def _check_quarantine():
        for proc, threads, tmp, job in list(_QUARANTINE):
            if proc.poll() is None or any(t.is_alive() for t in threads):
                raise SandboxError('RUNTIME_UNAVAILABLE')
            # Only close buffered pipes once both the child and I/O threads ended.
            for stream in (proc.stdin, proc.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            if not SandboxRunner._cleanup_temp(tmp):
                raise SandboxError('RUNTIME_UNAVAILABLE')
            _QUARANTINE.remove((proc, threads, tmp, job))

    @staticmethod
    def _cleanup_temp(tmp):
        # Windows can briefly retain the cwd lock after the process is signalled.
        # Bounded local cleanup never reruns the calculation or a model call.
        target = Path(tmp.name).resolve()
        root = tmp._ff_work_root
        if target == root or not target.is_relative_to(root):
            return False
        until = time.monotonic() + 0.3
        while True:
            try:
                tmp.cleanup()
                return True
            except OSError:
                if time.monotonic() >= until:
                    return False
                time.sleep(0.01)

    def _run(self, src, frame):
        start = time.monotonic()
        deadline = start + TOTAL_TIMEOUT_S
        stop = threading.Event()
        q = queue.Queue(maxsize=8)
        threads, proc, job = [], None, None
        try:
            tmp = tempfile.TemporaryDirectory(prefix='sbx-', dir=str(self.work_root))
        except OSError:
            raise SandboxError('RUNTIME_UNAVAILABLE') from None
        tmp._ff_work_root = self.work_root
        try:
            env = {k: os.environ[k] for k in ('SYSTEMROOT',) if k in os.environ}
            try:
                job = WindowsJob(JOB_MEMORY_BYTES, JOB_CPU_SECONDS)
            except JobError:
                raise SandboxError('RUNTIME_UNAVAILABLE') from None
            try:
                proc = subprocess.Popen(
                    [str(self.base_python), '-I', '-c', BOOTSTRAP, str(self.runtime_site), str(self.worker)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    cwd=tmp.name, env=env, close_fds=True,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError:
                raise SandboxError('RUNTIME_UNAVAILABLE') from None
            try:
                job.assign_process(proc._handle)
            except (JobError, AttributeError, TypeError, OSError):
                raise SandboxError('RUNTIME_UNAVAILABLE') from None
            reader = threading.Thread(target=_reader, args=(proc.stdout, q, stop), daemon=True)
            threads.append(reader)
            reader.start()
            buf, sent, eof = bytearray(), False, False
            while not eof:
                # Windows checks per-process time limits periodically. Poll its
                # measured job user CPU as well, so the 5s wall limit cannot hide
                # a delayed native CPU-limit notification. Never infer from exit code.
                try:
                    if job.usage()['user_time_100ns'] >= JOB_CPU_SECONDS * 10_000_000:
                        raise SandboxError('PROCESS_FAILED')
                except JobError:
                    raise SandboxError('RUNTIME_UNAVAILABLE') from None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SandboxError('TIMEOUT')
                try:
                    item = q.get(timeout=min(remaining, 0.05))
                except queue.Empty:
                    continue
                if item is _EOF:
                    eof = True
                else:
                    buf += item
                    if len(buf) > MAX_OUTPUT_BYTES:
                        raise SandboxError('OUTPUT_LIMIT')
                if not sent and b'\n' in buf:
                    if not _is_ready(_parse(bytes(buf.split(b'\n', 1)[0]))):
                        raise SandboxError('INVALID_OUTPUT')
                    writer = threading.Thread(target=_writer, args=(proc.stdin, frame), daemon=True)
                    threads.append(writer)
                    writer.start()
                    sent = True
            try:
                code = proc.wait(timeout=max(deadline - time.monotonic(), 0.001))
            except subprocess.TimeoutExpired:
                raise SandboxError('TIMEOUT') from None
            if time.monotonic() > deadline:
                raise SandboxError('TIMEOUT')
            try:
                usage = job.usage()
            except JobError:
                raise SandboxError('RUNTIME_UNAVAILABLE') from None
            if code != 0:
                # Accounting remains evidence, not a guessed cause of termination.
                raise SandboxError('PROCESS_FAILED')
            lines = bytes(buf).split(b'\n')
            if not sent or len(lines) != 3 or lines[2] != b'':
                raise SandboxError('INVALID_OUTPUT')
            return self._result(_parse(lines[1]), src, start, usage)
        finally:
            stop.set()
            cleanup_error = False
            if job is not None:
                try:
                    job.close()
                except JobError:
                    cleanup_error = True
            if proc is not None:
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except OSError:
                        cleanup_error = True
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    cleanup_error = True
            for thread in threads:
                thread.join(timeout=0.2)
            if proc is not None and (proc.poll() is None or any(t.is_alive() for t in threads)):
                # Do not block the request in BufferedIO.close while an I/O thread
                # holds its lock. Preserve ownership and refuse further runs.
                _QUARANTINE.append((proc, threads, tmp, job))
                raise SandboxError('CLEANUP_UNVERIFIED')
            if proc is not None:
                for stream in (proc.stdin, proc.stdout):
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        cleanup_error = True
            target = Path(tmp.name).resolve()
            if target == self.work_root or not target.is_relative_to(self.work_root):
                cleanup_error = True
            elif not self._cleanup_temp(tmp):
                if proc is not None:
                    _QUARANTINE.append((proc, threads, tmp, job))
                cleanup_error = True
            if cleanup_error:
                raise SandboxError('CLEANUP_UNVERIFIED' if proc is not None else 'RUNTIME_UNAVAILABLE')

    def _result(self, msg, src, start, usage):
        if usage['user_time_100ns'] >= JOB_CPU_SECONDS * 10_000_000:
            raise SandboxError('PROCESS_FAILED')
        if type(msg) is not dict:
            raise SandboxError('INVALID_OUTPUT')
        if msg.keys() == {'ok', 'result', 'fuel_used'} and msg['ok'] is True:
            result, fuel = msg['result'], msg['fuel_used']
            if (type(result) is int and I64_MIN <= result <= I64_MAX
                    and type(fuel) is int and 0 <= fuel <= FUEL_LIMIT):
                return {'ok': True, 'result': result, 'fuel_used': fuel, 'limits': self.limits,
                        'source_sha256': hashlib.sha256(src).hexdigest(),
                        'elapsed_ms': int((time.monotonic() - start) * 1000),
                        'cpu_user_ms': usage['user_time_100ns'] // 10000,
                        'simulated': True}
        elif msg.keys() == {'ok', 'error'} and msg['ok'] is False:
            err = msg['error']
            if (type(err) is dict and err.keys() == {'code', 'message'}
                    and type(err['code']) is str and type(err['message']) is str):
                raise SandboxError(err['code'] if err['code'] in WORKER_CODES else 'RUNTIME_UNAVAILABLE')
        raise SandboxError('INVALID_OUTPUT')

"""Fail-closed Windows Job Object wrapper for sandbox worker processes."""
import ctypes
import os

__all__ = ['JobError', 'WindowsJob']

HANDLE = ctypes.c_void_p
DWORD = ctypes.c_uint32
BOOL = ctypes.c_int
LARGE_INTEGER = ctypes.c_int64
ULONGLONG = ctypes.c_uint64
SIZE_T = ctypes.c_size_t

BASIC_ACCOUNTING_INFO = 1
EXTENDED_LIMIT_INFO = 9
LIMIT_PROCESS_TIME = 0x2
LIMIT_ACTIVE_PROCESS = 0x8
LIMIT_PROCESS_MEMORY = 0x100
LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x400
LIMIT_KILL_ON_JOB_CLOSE = 0x2000
LIMIT_FLAGS = (LIMIT_PROCESS_MEMORY | LIMIT_PROCESS_TIME | LIMIT_ACTIVE_PROCESS
               | LIMIT_KILL_ON_JOB_CLOSE | LIMIT_DIE_ON_UNHANDLED_EXCEPTION)
TICKS_PER_SECOND = 10_000_000
MIN_MEMORY_BYTES = 16 * 1024 * 1024
MAX_MEMORY_BYTES = 4 * 1024 ** 3
MAX_CPU_SECONDS = 3600


class JobError(Exception):
    """Job Object setup, assignment or query failed; caller must fail closed."""


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', LARGE_INTEGER), ('PerJobUserTimeLimit', LARGE_INTEGER),
                ('LimitFlags', DWORD), ('MinimumWorkingSetSize', SIZE_T), ('MaximumWorkingSetSize', SIZE_T),
                ('ActiveProcessLimit', DWORD), ('Affinity', SIZE_T), ('PriorityClass', DWORD),
                ('SchedulingClass', DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ULONGLONG) for name in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                                               'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION), ('IoInfo', IO_COUNTERS),
                ('ProcessMemoryLimit', SIZE_T), ('JobMemoryLimit', SIZE_T),
                ('PeakProcessMemoryUsed', SIZE_T), ('PeakJobMemoryUsed', SIZE_T)]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [('TotalUserTime', LARGE_INTEGER), ('TotalKernelTime', LARGE_INTEGER),
                ('ThisPeriodTotalUserTime', LARGE_INTEGER), ('ThisPeriodTotalKernelTime', LARGE_INTEGER),
                ('TotalPageFaultCount', DWORD), ('TotalProcesses', DWORD), ('ActiveProcesses', DWORD),
                ('TotalTerminatedProcesses', DWORD)]


_SIGNATURES = {
    'CreateJobObjectW': (HANDLE, [ctypes.c_void_p, ctypes.c_wchar_p]),
    'SetInformationJobObject': (BOOL, [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD]),
    'QueryInformationJobObject': (BOOL, [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)]),
    'AssignProcessToJobObject': (BOOL, [HANDLE, HANDLE]),
    'IsProcessInJob': (BOOL, [HANDLE, HANDLE, ctypes.POINTER(BOOL)]),
    'GetProcessId': (DWORD, [HANDLE]),
    'TerminateJobObject': (BOOL, [HANDLE, ctypes.c_uint]),
    'CloseHandle': (BOOL, [HANDLE]),
}
_kernel32 = None


def _api():
    global _kernel32
    if _kernel32 is None:
        if os.name != 'nt' or not hasattr(ctypes, 'WinDLL'):
            raise JobError('Windows Job Objects are unavailable on this platform')
        if ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION) != 144:
            raise JobError('unsupported Windows ABI')
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, (restype, argtypes) in _SIGNATURES.items():
            function = getattr(kernel32, name)
            function.restype, function.argtypes = restype, argtypes
        _kernel32 = kernel32
    return _kernel32


def _error(operation):
    return JobError(f'{operation} failed (winerror {ctypes.get_last_error()})')


class WindowsJob:
    """Unnamed Job Object owning one sandbox process; closing the job kills it."""

    def __init__(self, memory_bytes=268435456, cpu_seconds=2):
        for value in (memory_bytes, cpu_seconds):
            if type(value) is not int:
                raise JobError('job limits must be integers')
        if not MIN_MEMORY_BYTES <= memory_bytes <= MAX_MEMORY_BYTES or not 1 <= cpu_seconds <= MAX_CPU_SECONDS:
            raise JobError('job limits out of range')
        self._handle = None
        self._assigned = False
        self._k = _api()
        handle = self._k.CreateJobObjectW(None, None)
        if not handle:
            raise _error('CreateJobObjectW')
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = LIMIT_FLAGS
        info.BasicLimitInformation.PerProcessUserTimeLimit = cpu_seconds * TICKS_PER_SECOND
        info.BasicLimitInformation.ActiveProcessLimit = 1
        info.ProcessMemoryLimit = memory_bytes
        if not self._k.SetInformationJobObject(handle, EXTENDED_LIMIT_INFO, ctypes.byref(info), ctypes.sizeof(info)):
            error = _error('SetInformationJobObject')
            self._k.CloseHandle(handle)
            raise error
        self._handle = handle

    @property
    def handle(self):
        if self._handle is None:
            raise JobError('job is closed')
        return self._handle

    def assign_process(self, handle):
        """Assign a caller-owned live process handle (e.g. Popen._handle), never a PID."""
        job = self.handle
        if self._assigned:
            raise JobError('job already has an assigned process')
        if not isinstance(handle, int) or isinstance(handle, bool) or handle <= 0:
            raise JobError('invalid process handle')
        pid = self._k.GetProcessId(int(handle))
        if pid == 0 or pid in (os.getpid(), os.getppid()):
            raise JobError('refusing to assign this process handle')
        if not self._k.AssignProcessToJobObject(job, int(handle)):
            raise _error('AssignProcessToJobObject')
        self._assigned = True
        try:
            member = BOOL(0)
            if not self._k.IsProcessInJob(int(handle), job, ctypes.byref(member)):
                raise _error('IsProcessInJob')
            if not member.value or self.usage()['active_processes'] != 1:
                raise JobError('job assignment could not be verified')
        except JobError:
            self._k.TerminateJobObject(job, 1)
            raise

    def _query(self, info_class, info, operation):
        if not self._k.QueryInformationJobObject(self.handle, info_class, ctypes.byref(info),
                                                 ctypes.sizeof(info), None):
            raise _error(operation)
        return info

    def usage(self):
        accounting = self._query(BASIC_ACCOUNTING_INFO, JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(),
                                 'QueryInformationJobObject(accounting)')
        limits = self._query(EXTENDED_LIMIT_INFO, JOBOBJECT_EXTENDED_LIMIT_INFORMATION(),
                             'QueryInformationJobObject(limits)')
        basic = limits.BasicLimitInformation
        return {'user_time_100ns': int(accounting.TotalUserTime),
                'total_processes': int(accounting.TotalProcesses),
                'active_processes': int(accounting.ActiveProcesses),
                'peak_process_memory_bytes': int(limits.PeakProcessMemoryUsed),
                'limit_flags': int(basic.LimitFlags),
                'process_memory_bytes': int(limits.ProcessMemoryLimit),
                'cpu_limit_100ns': int(basic.PerProcessUserTimeLimit),
                'active_process_limit': int(basic.ActiveProcessLimit)}

    def terminate(self):
        """Terminate every process in this job only."""
        if not self._k.TerminateJobObject(self.handle, 1):
            raise _error('TerminateJobObject')

    def close(self):
        """Idempotent: terminate this job and close only the job handle we own."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        terminated = self._k.TerminateJobObject(handle, 1)
        terminate_error = None if terminated else _error('TerminateJobObject')
        if not self._k.CloseHandle(handle):
            raise _error('CloseHandle')
        if terminate_error is not None and self._assigned:
            raise terminate_error

    def __enter__(self):
        self.handle
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def __del__(self):
        try:
            if getattr(self, '_handle', None) is not None:
                self.close()
        except Exception:
            pass

"""Windows process-tree lifecycle guard for offline role CLI execution.

The target is created suspended, assigned to an isolated kill-on-close Job,
then resumed. This avoids the create/assign race where an uncontained child
could otherwise escape. No breakaway permission is granted. Closing the Job
also reaps descendants left by a normally exiting target.

Requires Windows and ntdll.NtResumeProcess (Popen closes the initial thread
handle). A host Job policy that forbids nesting causes a fail-closed error;
there is intentionally no uncontained fallback. This is lifecycle containment,
NOT a filesystem/network sandbox or protection against privileged malware.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import math
import os
import subprocess

_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
                ("ReadOperationCount","WriteOperationCount","OtherOperationCount",
                 "ReadTransferCount","WriteTransferCount","OtherTransferCount")]

class _BASIC_LIMIT(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit",ctypes.c_longlong),
        ("PerJobUserTimeLimit",ctypes.c_longlong),
        ("LimitFlags",wintypes.DWORD),
        ("MinimumWorkingSetSize",ctypes.c_size_t),
        ("MaximumWorkingSetSize",ctypes.c_size_t),
        ("ActiveProcessLimit",wintypes.DWORD),
        ("Affinity",ctypes.c_size_t),
        ("PriorityClass",wintypes.DWORD),
        ("SchedulingClass",wintypes.DWORD)]

class _EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation",_BASIC_LIMIT),
        ("IoInfo",_IO_COUNTERS),
        ("ProcessMemoryLimit",ctypes.c_size_t),
        ("JobMemoryLimit",ctypes.c_size_t),
        ("PeakProcessMemoryUsed",ctypes.c_size_t),
        ("PeakJobMemoryUsed",ctypes.c_size_t)]

class _Job:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows Job Object process guard is required; no uncontained fallback")
        self.handle = None
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p,wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE,wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.nt = ctypes.WinDLL("ntdll")
        self.nt.NtResumeProcess.argtypes = [wintypes.HANDLE]
        self.nt.NtResumeProcess.restype = wintypes.LONG
        self.handle = self.kernel.CreateJobObjectW(None,None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limit = _EXTENDED_LIMIT()
        limit.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle,9,ctypes.byref(limit),ctypes.sizeof(limit)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign_and_resume(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle,wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())
        status = self.nt.NtResumeProcess(wintypes.HANDLE(int(process._handle)))
        if status != 0:
            raise OSError("NtResumeProcess failed with NTSTATUS 0x%08x" % (status & 0xffffffff))

    def close(self):
        if self.handle:
            handle,self.handle = self.handle,None
            if not self.kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())

def run_process_tree(command, *, input=None, text=False, encoding=None,
                     errors=None, stdout=None, stderr=None, env=None, cwd=None,
                     timeout=None):
    """subprocess.run-compatible subset; timeout/cancel kills only this Job tree.

    Returns CompletedProcess; native nonzero return codes are not rewritten.
    Raises TimeoutExpired or the original cancellation/launch error. Files or
    PIPE may be used for stdout/stderr. Input is passed via stdin=PIPE without
    shell interpretation. Timeout must be finite and positive when supplied.
    """
    if isinstance(command,(str,bytes)) or not command:
        raise ValueError("command must be a non-empty argument sequence, not shell text")
    if timeout is not None and (not isinstance(timeout,(int,float)) or
                                not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("timeout must be positive and finite")
    job = _Job()
    process = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, text=text,
                                   encoding=encoding, errors=errors, env=env, cwd=cwd,
                                   creationflags=_CREATE_SUSPENDED, close_fds=True)
        job.assign_and_resume(process)
        out,err = process.communicate(input=input,timeout=timeout)
        return subprocess.CompletedProcess(command,process.returncode,out,err)
    finally:
        # Closing kills the entire assigned tree even on BaseException, or if
        # a successful parent left background descendants behind.
        try:
            job.close()
        finally:
            if process is not None:
                # If assignment failed, this is still the suspended unassigned
                # target, which cannot have spawned children.
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
                for stream in (process.stdin,process.stdout,process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass

"""Exit-status-only execution of an operator's image verifier.

This is process lifetime control, not a sandbox for an untrusted verifier.
No scanner output is captured. A supervisor holds the process-group identity
until cleanup, and on Windows waits for private job assignment before launch.
"""
import os
import signal
import subprocess
import sys
import threading
import time


class _WindowsJob:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        api = self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, args, result in (
            ("CreateJobObjectW", [wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE),
            ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL),
            ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            ("QueryInformationJobObject", [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                           wintypes.DWORD, wintypes.LPVOID], wintypes.BOOL),
            ("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            ("IsProcessInJob", [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
            ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ):
            function = getattr(api, name)
            function.argtypes, function.restype = args, result

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                        ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", ctypes.c_ulonglong * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        self.handle = api.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("Cannot create verifier job")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; no child breakaway allowed.
        if not api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            api.CloseHandle(self.handle)
            raise OSError("Cannot configure verifier job")

    def assign(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise OSError("Cannot assign verifier job")

    def close(self):
        # Kill-on-close also handles abrupt owner termination.
        handles = []
        try:
            try:
                handles = self._process_handles()
            finally:
                if not self.api.TerminateJobObject(self.handle, 1):
                    raise OSError("Cannot terminate verifier job")
            # Termination is asynchronous. Waiting for the supervisor alone
            # does not prove its descendants have released cwd/file handles.
            # Retain this exact private job handle until its process count is
            # zero; never enumerate global processes or infer ownership by PID.
            deadline = time.monotonic() + 15
            while self._active_processes():
                if time.monotonic() >= deadline:
                    raise OSError("Verifier job cleanup timed out")
                time.sleep(.01)
            # ActiveProcesses may reach zero before terminating process objects
            # are signaled. Only the latter proves cwd and pending I/O released.
            for handle in handles:
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                result = self.api.WaitForSingleObject(handle, remaining_ms)
                if result == 258:  # WAIT_TIMEOUT
                    raise OSError("Verifier job cleanup timed out")
                if result != 0:  # WAIT_OBJECT_0
                    raise OSError("Cannot wait for verifier process")
        finally:
            for handle in handles:
                self.api.CloseHandle(handle)
            self.api.CloseHandle(self.handle)

    def _process_handles(self):
        import ctypes
        from ctypes import wintypes

        class ProcessIDs(ctypes.Structure):
            _fields_ = [("assigned", wintypes.DWORD), ("count", wintypes.DWORD),
                        ("ids", ctypes.c_size_t * 4096)]

        info = ProcessIDs()
        if (not self.api.QueryInformationJobObject(self.handle, 3, ctypes.byref(info), ctypes.sizeof(info), None)
                or info.assigned > 4096 or info.count > 4096 or info.count < info.assigned):
            raise OSError("Cannot enumerate verifier job")
        handles = []
        try:
            for pid in info.ids[:info.count]:
                # Synchronize/query rights only. No per-PID termination rights;
                # the job remains the sole authority for terminating processes.
                handle = self.api.OpenProcess(0x100000 | 0x1000, False, pid)
                if not handle:
                    if ctypes.get_last_error() == 87:  # Process already gone.
                        continue
                    raise OSError("Cannot open verifier process")
                belongs = wintypes.BOOL()
                if not self.api.IsProcessInJob(handle, self.handle, ctypes.byref(belongs)):
                    self.api.CloseHandle(handle)
                    raise OSError("Cannot verify process ownership")
                if not belongs.value:  # PID reused since the private snapshot.
                    self.api.CloseHandle(handle)
                    continue
                handles.append(handle)
            return handles
        except BaseException:
            for handle in handles:
                self.api.CloseHandle(handle)
            raise

    def _active_processes(self):
        import ctypes
        from ctypes import wintypes

        class Accounting(ctypes.Structure):
            _fields_ = [("user_time", ctypes.c_longlong), ("kernel_time", ctypes.c_longlong),
                        ("period_user_time", ctypes.c_longlong), ("period_kernel_time", ctypes.c_longlong),
                        ("page_faults", wintypes.DWORD), ("total_processes", wintypes.DWORD),
                        ("active_processes", wintypes.DWORD), ("terminated_processes", wintypes.DWORD)]

        info = Accounting()
        if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise OSError("Cannot query verifier job")
        return info.active_processes


_SUPERVISOR = r'''
import os, signal, subprocess, sys, threading
if sys.stdin.buffer.readline() != b"start\n":
    sys.exit(125)
def owner_lifetime():
    sys.stdin.buffer.read()
    if os.name != 'nt':
        os.killpg(0, signal.SIGKILL)
    os._exit(125)
watcher = threading.Thread(target=owner_lifetime, daemon=True)
watcher.start()
try:
    code = subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
except (OSError, ValueError):
    code = None
print("unavailable" if code is None else str(code), flush=True)
# Remain alive so the owner can kill this exact group before reaping its PID.
watcher.join()
'''


def verifier_exit_code(command, *, timeout=180):
    """Return the exit status; bound runtime/output and close owned descendants."""
    deadline = time.monotonic() + timeout
    job = _WindowsJob() if os.name == "nt" else None
    process = reader = None
    reply = []
    try:
        options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if job
                   else {"start_new_session": True})
        # The base interpreter avoids a virtualenv redirector outside the job.
        process = subprocess.Popen([sys._base_executable, "-I", "-c", _SUPERVISOR, *command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **options)
        if job:
            job.assign(process)
        process.stdin.write(b"start\n")
        process.stdin.flush()
        reader = threading.Thread(target=lambda: reply.append(process.stdout.readline(32)), daemon=True)
        reader.start()
        reader.join(max(0, deadline - time.monotonic()))
        if reader.is_alive():
            # Exception text deliberately contains no operator argv or output.
            raise subprocess.TimeoutExpired("image-admission-verifier", timeout)
        if not reply or not reply[0].endswith(b"\n"):
            raise OSError("Verifier response unavailable")
        try:
            return int(reply[0])
        except ValueError:
            raise OSError("Verifier could not start") from None
    finally:
        try:
            if job:
                job.close()
            elif process is not None:
                # Do not poll/reap first: reserve the owned group leader PID.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()  # Also covers Windows assignment failure before gate release.
                process.wait(timeout=15)
                if reader:
                    reader.join(15)
                process.stdin.close()
                process.stdout.close()

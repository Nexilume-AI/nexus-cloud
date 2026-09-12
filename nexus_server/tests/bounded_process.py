"""Bounded test subprocesses, including their owned children on timeout.

Only use for isolated acceptance fixtures, never for a running Cloud service.
Windows virtualenv launchers may have a child holding the fixture cwd and pipes;
killing just the launcher cannot safely complete TemporaryDirectory cleanup.
"""
import os
import signal
import subprocess
import sys


class WindowsTestJob:
    """A private kernel job, without WMI/taskkill or broad process enumeration."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, args, result in (
            ("CreateJobObjectW", [wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE),
            ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL),
            ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ):
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                        ("maximum_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io_counters", ctypes.c_ulonglong * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise OSError("Cannot isolate test process in its owned Windows job")

    def stop(self):
        if not self.api.TerminateJobObject(self.handle, 1):
            raise OSError("Cannot stop owned Windows test job")

    def close(self):
        self.api.CloseHandle(self.handle)


def run_bounded(command, *, cwd, timeout, env=None):
    # All callers are Python acceptance probes. A parent's -X utf8 flag is
    # not inherited by its children; normalize only this owned process tree.
    child_env = {**(os.environ if env is None else env), 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    windows = os.name == "nt"
    job = WindowsTestJob() if windows else None
    options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP, "stdin": subprocess.PIPE}
               if os.name == "nt" else {"start_new_session": True})
    # Use the real executable, not a virtualenv redirector. The gate prevents
    # any child spawning before job assignment, including on a loaded host.
    launched = ([sys._base_executable, "-c",
        "import subprocess,sys; line=sys.stdin.readline(); "
        "sys.exit(subprocess.call(sys.argv[1:]) if line == 'start\\n' else 125)",
        *command] if windows else command)
    try:
        # Capture bytes: decoding in a Windows reader thread can lose output
        # without making the caller fail. Decode strictly in this main thread.
        process = subprocess.Popen(launched, cwd=cwd, env=child_env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, **options)
    except BaseException:
        if job:
            job.close()
        raise
    try:
        if job:
            job.assign(process)
        try:
            stdout, stderr = process.communicate(input=b"start\n" if windows else None, timeout=timeout)
        except subprocess.TimeoutExpired:
            # The PID comes from this still-owned Popen handle, not a process
            # name, saved state file, or user-selected process inventory.
            if job:
                job.stop()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            stdout, stderr = process.communicate(timeout=15)
            raise subprocess.TimeoutExpired(command, timeout, output=stdout.decode('utf-8'),
                                            stderr=stderr.decode('utf-8')) from None
        return subprocess.CompletedProcess(command, process.returncode, stdout.decode('utf-8'), stderr.decode('utf-8'))
    finally:
        if job:
            try:
                job.stop()
            finally:
                job.close()
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=15)

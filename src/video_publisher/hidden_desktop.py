"""Run a browser command on its own Windows desktop, so nothing it opens appears on the user's screen."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import subprocess
import sys
import time
import uuid

from . import progress
from .core import VpubError, atomic_json, home, read_json

DESKTOP = "CrossCastBackground"
GENERIC_ALL = 0x10000000
CREATE_NO_WINDOW = 0x08000000
CREATE_SUSPENDED = 0x00000004
RESUME_FAILED = 0xFFFFFFFF
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
JOB_ACCOUNTING = 1  # JobObjectBasicAccountingInformation.
# A real Chrome whose worker had gone closed itself within a second and kept its newest cookies. Ended at once, it lost them.
CLOSES_ITSELF_SECONDS = 10
CLEANUP_SECONDS = 5


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong), ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD), ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD), ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_byte)), ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


def libraries():
    user32, kernel32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    kernel32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID, wintypes.BOOL, wintypes.DWORD,
                                        wintypes.LPVOID, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID]
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    return user32, kernel32


def launch(argv, *, suspended=False):
    """Start argv on the hidden desktop. The handles stay open until the caller closes them."""
    user32, kernel32 = libraries()
    desktop = user32.CreateDesktopW(DESKTOP, None, None, 0, GENERIC_ALL, None)
    if not desktop:
        raise VpubError("browser_unavailable", "Could not create a hidden desktop for the background browser.")
    startup = STARTUPINFOW(cb=ctypes.sizeof(STARTUPINFOW), lpDesktop=DESKTOP)
    process = PROCESS_INFORMATION()
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(part) for part in argv]))
    flags = CREATE_NO_WINDOW | (CREATE_SUSPENDED if suspended else 0)
    if not kernel32.CreateProcessW(None, command, None, None, False, flags, None, None, ctypes.byref(startup), ctypes.byref(process)):
        user32.CloseDesktop(desktop)
        raise VpubError("browser_unavailable", "Could not start the background browser on its hidden desktop.")
    return user32, kernel32, desktop, process


def still_running(kernel32, job, seconds):
    """Whether this job still holds a process once that long has passed, or sooner if it empties. One that can't be read counts as still running.
    The count can reach zero up to a second before an ended Chrome's last processes have fully gone, a real run found, about as long as they took after taskkill."""
    deadline = time.monotonic() + seconds
    while True:
        info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        if not kernel32.QueryInformationJobObject(job, JOB_ACCOUNTING, ctypes.byref(info), ctypes.sizeof(info), None):
            return True
        if not info.ActiveProcesses:
            return False
        if time.monotonic() >= deadline:
            return True
        time.sleep(0.05)


def end_job(kernel32, job, wait):
    """End what this command left behind, after giving it that long to close itself."""
    if still_running(kernel32, job, wait) and (not kernel32.TerminateJobObject(job, 1) or still_running(kernel32, job, CLEANUP_SECONDS)):
        raise VpubError("browser_error", "Could not confirm that the background browser processes have stopped.",
                        "Existing receipts are retained. Inspect the attempt and browser before retrying; do not repeat an upload or final submit.")


def run(argv, timeout):
    """Run argv on the hidden desktop until it exits, and return once everything it started has ended too. After a timeout, end all of it.
    The job only keeps track of those processes, so where one can't be made or joined the command still runs. It has no kill-on-close on purpose: a command that is killed
    leaves its worker to finish and close its browser properly, as other routes do, and a browser ended at once loses its newest cookies, while Google replaces its sign-in cookies as they are used."""
    user32, kernel32, desktop, process = launch(argv, suspended=True)
    job, tracked, wait = None, False, 0
    try:
        job = kernel32.CreateJobObjectW(None, None)
        # It joins before its first instruction, so nothing it starts can be missed.
        tracked = bool(job and kernel32.AssignProcessToJobObject(job, process.hProcess))
        if kernel32.ResumeThread(process.hThread) == RESUME_FAILED:
            raise VpubError("browser_unavailable", "Could not start the background browser on its hidden desktop.")
        status = kernel32.WaitForSingleObject(process.hProcess, int(timeout * 1000))
        if status == WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(argv, timeout)
        if status != WAIT_OBJECT_0:
            raise VpubError("browser_error", "Could not wait for the background browser worker.", "Read the attempt's receipt before retrying; do not repeat an upload or final submit.")
        wait = CLOSES_ITSELF_SECONDS
    finally:
        try:
            if tracked:
                end_job(kernel32, job, wait)
            elif not wait:
                subprocess.run(["taskkill", "/PID", str(process.dwProcessId), "/T", "/F"], capture_output=True)
        finally:
            if job:
                kernel32.CloseHandle(job)
            kernel32.CloseHandle(process.hThread)
            kernel32.CloseHandle(process.hProcess)
            user32.CloseDesktop(desktop)


class Started:
    """A process left running on the hidden desktop. Its handles close with this command's process, by when its Chrome holds the desktop open itself."""

    def __init__(self, argv):
        _, self.kernel32, self.desktop, self.process = launch(argv)

    def poll(self):
        status = self.kernel32.WaitForSingleObject(self.process.hProcess, 0)
        if status == WAIT_TIMEOUT:
            return None
        if status != WAIT_OBJECT_0:
            raise VpubError("browser_error", "Could not poll the background browser worker.")
        return 1


def call_worker(payload, timeout):
    """One browser command on the hidden desktop. It has no console to answer on, so it answers in a private file."""
    progress.stage("starting the browser", payload.get("stages_path"))
    folder = home() / "browser-jobs"
    name = uuid.uuid4().hex
    request, answer = folder / (name + ".json"), folder / (name + ".result.json")
    atomic_json(request, {**payload, "result_path": str(answer)})
    try:
        try:
            run([sys.executable, "-m", "video_publisher.browser_worker", "--job", str(request)], timeout)
        except subprocess.TimeoutExpired:
            raise VpubError("browser_timeout", "Browser command timed out; existing receipts are retained.", "Read the attempt's receipt, and look on the platform before running it again. Whether it kept a draft isn't known. Do not repeat an upload or final submit.")
        except VpubError:
            # A finished command's answer stands, whatever became of what it left behind.
            if not answer.exists():
                raise
        if not answer.exists():
            raise VpubError("browser_error", "Browser command returned no valid JSON receipt.")
        return read_json(answer)
    finally:
        request.unlink(missing_ok=True)
        answer.unlink(missing_ok=True)

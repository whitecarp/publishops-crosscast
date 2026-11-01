"""Run a browser command on its own Windows desktop, so nothing it opens appears on the user's screen."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import subprocess
import sys
import uuid

from .core import VpubError, atomic_json, home, read_json

DESKTOP = "CrossCastBackground"
GENERIC_ALL = 0x10000000
CREATE_NO_WINDOW = 0x08000000
WAIT_TIMEOUT = 0x102


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
    return user32, kernel32


def run(argv, timeout):
    """Run argv on the hidden desktop until it exits. After a timeout, end it and everything it started."""
    user32, kernel32 = libraries()
    desktop = user32.CreateDesktopW(DESKTOP, None, None, 0, GENERIC_ALL, None)
    if not desktop:
        raise VpubError("browser_unavailable", "Could not create a hidden desktop for the background browser.")
    try:
        startup = STARTUPINFOW(cb=ctypes.sizeof(STARTUPINFOW), lpDesktop=DESKTOP)
        process = PROCESS_INFORMATION()
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(part) for part in argv]))
        if not kernel32.CreateProcessW(None, command, None, None, False, CREATE_NO_WINDOW, None, None, ctypes.byref(startup), ctypes.byref(process)):
            raise VpubError("browser_unavailable", "Could not start the background browser on its hidden desktop.")
        try:
            if kernel32.WaitForSingleObject(process.hProcess, int(timeout * 1000)) == WAIT_TIMEOUT:
                subprocess.run(["taskkill", "/PID", str(process.dwProcessId), "/T", "/F"], capture_output=True)
                raise subprocess.TimeoutExpired(argv, timeout)
        finally:
            kernel32.CloseHandle(process.hThread)
            kernel32.CloseHandle(process.hProcess)
    finally:
        user32.CloseDesktop(desktop)


def call_worker(payload, timeout):
    """One browser command on the hidden desktop. It has no console to answer on, so it answers in a private file."""
    folder = home() / "browser-jobs"
    name = uuid.uuid4().hex
    request, answer = folder / (name + ".json"), folder / (name + ".result.json")
    atomic_json(request, {**payload, "result_path": str(answer)})
    try:
        try:
            run([sys.executable, "-m", "video_publisher.browser_worker", "--job", str(request)], timeout)
        except subprocess.TimeoutExpired:
            raise VpubError("browser_timeout", "Browser command timed out; existing receipts are retained.", "Inspect the existing draft/work; do not repeat an upload or final submit.")
        if not answer.exists():
            raise VpubError("browser_error", "Browser command returned no valid JSON receipt.")
        return read_json(answer)
    finally:
        request.unlink(missing_ok=True)
        answer.unlink(missing_ok=True)

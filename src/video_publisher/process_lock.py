"""Kernel-backed exclusion plus a legacy-compatible, inspectable owner marker."""
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import uuid


def alive(pid):
    """Return False only when the OS positively establishes process absence."""
    if type(pid) is not int or pid <= 0:
        return True
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except OSError:
            return True
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ctypes.get_last_error() != 87  # Invalid PID; denied/unknown stays busy.
    try:
        code = wintypes.DWORD()
        return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def hold(path, error_code, message):
    from .core import VpubError, now
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never unlink the guard: replacing its inode can create two lock owners.
    guard = path.with_name(path.name + '.guard')
    descriptor = os.open(guard, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    token = uuid.uuid4().hex
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise VpubError(error_code, message, 'Wait for the active command; do not clear its lock.') from None
        if path.exists():
            try:
                owner = json.loads(path.read_text(encoding='utf-8'))
                pid = owner.get('pid') if isinstance(owner, dict) else owner
            except (OSError, ValueError):
                pid = None
            if alive(pid):
                raise VpubError(error_code, message, 'The prior owner is active or unknown. Inspect it; do not delete an active lock.')
            path.unlink()  # Dead owner only; receipt and browser profile are untouched.
        try:
            with path.open('x', encoding='utf-8') as marker:
                json.dump({'pid': os.getpid(), 'token': token, 'created_at': now()}, marker)
                marker.flush()
                os.fsync(marker.fileno())
        except FileExistsError:
            raise VpubError(error_code, message) from None
        try:
            yield
        finally:
            try:
                current = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(current, dict) and current.get('token') == token:
                    path.unlink()
            except (OSError, ValueError):
                pass  # Never remove a replacement or unreadable marker.
    finally:
        if locked:
            if os.name == 'nt':
                import msvcrt
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

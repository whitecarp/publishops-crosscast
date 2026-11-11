"""Kernel-backed exclusion plus a legacy-compatible, inspectable owner marker."""
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import re
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


def released(owner):
    return (isinstance(owner, dict) and owner.get('lock_state') == 'released'
            and type(owner.get('pid')) is int and owner['pid'] > 0
            and isinstance(owner.get('token'), str) and bool(re.fullmatch(r'[a-f0-9]{32}', owner['token']))
            and isinstance(owner.get('released_at'), str))


@contextmanager
def hold(path, error_code, message):
    from .core import VpubError, atomic_json, now
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
        try:
            marker = path.read_text(encoding='utf-8')
        except FileNotFoundError:
            marker = None
        except UnicodeError:
            raise VpubError('lock_marker_unreadable', 'The command lock marker is not valid UTF-8.',
                            'Keep the marker in place. Ask the owner to inspect its format and origin; '
                            'an encoding error does not establish a permissions fault.') from None
        except OSError:
            # Windows may report access denied, rather than IsADirectoryError,
            # for a directory at the marker path. Metadata is only diagnostic.
            try: is_directory = path.is_dir()
            except OSError: is_directory = False
            if is_directory:
                raise VpubError('lock_marker_unreadable', 'The command lock marker is a directory, not a file.',
                                'Keep it in place. Ask the owner to inspect how the marker path became a directory.') from None
            raise VpubError('lock_marker_unreadable', 'The command lock marker could not be read.',
                            'Ask the owner to check this marker\'s ownership and permissions. On Windows, '
                            'a pending deletion can also deny access until its open handle closes. '
                            'Keep the marker in place while the owner diagnoses it.') from None
        if marker is not None:
            owner = None
            try:
                owner = json.loads(marker)
                pid = owner.get('pid') if isinstance(owner, dict) else owner
            except ValueError:
                pid = None
            if not released(owner) and alive(pid):
                raise VpubError(error_code, message, 'The prior owner is active or unknown. Inspect it; do not delete an active lock.')
        owner = {'pid': os.getpid(), 'token': token, 'created_at': now(), 'lock_state': 'held'}
        # The stable kernel guard still excludes every cooperating owner. Reuse
        # a released/dead marker without a deletion on the publication path.
        atomic_json(path, owner)
        try:
            yield
        finally:
            try:
                current = json.loads(path.read_text(encoding='utf-8'))
                if current == owner:
                    atomic_json(path, {**owner, 'lock_state': 'released', 'released_at': now()})
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

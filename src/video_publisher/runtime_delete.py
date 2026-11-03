"""Explicit cleanup only: validate and delete the same object through a Windows handle."""
import ctypes
import hashlib
import os
from pathlib import Path
import stat


def remove(path, expected, *, directory=False):
    if os.name != 'nt':
        raise OSError('Safe runtime cleanup is unavailable on this host; files retained')
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.SetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    def opened(name, access):
        handle = kernel.CreateFileW(str(name), access, 1, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle(handle)
            raise
        return handle, descriptor
    parents, descriptor = [], None
    try:
        chain = list(reversed(Path(path).absolute().parents))
        if [str(p) for p in chain] != [p['path'] for p in expected['parents']]:
            raise OSError('Parent chain changed')
        # Pin ancestors from the volume root down, excluding writes/deletes and
        # reparse points before opening each child. An outside hardlink cannot
        # be substituted through a junction with the same leaf inode/hash.
        for parent, saved in zip(chain, expected['parents']):
            _, fd = opened(parent, 0x80)  # FILE_READ_ATTRIBUTES
            parents.append(fd)
            info = os.fstat(fd)
            if (not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                    or [info.st_dev, info.st_ino] != saved['identity']):
                raise OSError('Parent directory changed or is a reparse point')
        # Read + DELETE, sharing reads only: replacement and writes cannot happen
        # between validation and disposition. Never follow a leaf reparse point.
        handle, descriptor = opened(path, 0x80000000 | 0x10000)
        info = os.fstat(descriptor)
        if getattr(info, 'st_file_attributes', 0) & 0x400:
            raise OSError('Reparse point retained')
        if directory:
            if not stat.S_ISDIR(info.st_mode) or [info.st_dev, info.st_ino] != expected['identity']:
                raise OSError('Directory identity changed')
        else:
            if not stat.S_ISREG(info.st_mode):
                raise OSError('Not a regular runtime file')
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
            actual = {'identity': [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns], 'sha256': digest.hexdigest()}
            if actual != {key: expected[key] for key in ('identity', 'sha256')}:
                raise OSError('Runtime file changed; replacement retained')
        class Disposition(ctypes.Structure):
            _fields_ = [('DeleteFile', wintypes.BOOL)]
        disposition = Disposition(True)
        if not kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
            raise ctypes.WinError(ctypes.get_last_error())
        # Conservative logical bytes, not a measured change in free disk space.
        return info.st_size if not directory and info.st_nlink == 1 else 0
    finally:
        if descriptor is not None:
            os.close(descriptor)  # Closes the validated handle and completes disposition.
        for fd in reversed(parents):
            os.close(fd)

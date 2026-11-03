"""Same-filesystem scratch directories, retained for explicit runtime cleanup."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import re
import stat
import uuid

from .runtime_files import _parents, _record, _stamp

SCHEMA = 'crosscast-runtime-scratch-v1'
_ACTIVE = ContextVar('crosscast_runtime_scratch', default=())


def directory_stamp(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise OSError('Scratch directory changed or is a reparse point')
    return {'identity': [info.st_dev, info.st_ino], 'parents': _parents(path)}


@contextmanager
def scratch(parent):
    root = Path(parent).absolute() / ('.crosscast-runtime-' + uuid.uuid4().hex)
    root.mkdir(mode=0o700)
    owned = directory_stamp(root)
    active = _ACTIVE.get()
    token = _ACTIVE.set((*active, root))
    try:
        yield root
    finally:
        _ACTIVE.reset(token)
        try:
            # A promoted directory is final output, not disposable. Never follow
            # a replacement or register somebody else's new directory.
            if not any(parent in root.parents for parent in active) and root.exists() and directory_stamp(root) == owned:
                entries = []
                for path in sorted(root.rglob('*')):
                    directory = path.is_dir()
                    saved = directory_stamp(path) if directory else _stamp(path)
                    if path.is_symlink():
                        raise OSError('Scratch contains a link')
                    entries.append({'path': path.relative_to(root).as_posix(), 'directory': directory, **saved})
                _record({'schema': SCHEMA, 'pid': os.getpid(), 'ready': True,
                         'root': str(root), 'directory': owned, 'entries': entries})
        except (OSError, ValueError, TypeError):
            pass  # Keep uncertain scratch; never mask the original operation.


def inspect(record):
    root = Path(record['root'])
    if not root.is_absolute() or not re.fullmatch(r'\.crosscast-runtime-[a-f0-9]{32}', root.name):
        raise ValueError('Unrecognized scratch directory')
    if not root.exists() and not root.is_symlink():
        return [], []
    if directory_stamp(root) != record['directory']:
        raise OSError('Scratch directory changed')
    saved = {}
    for entry in record['entries']:
        name = Path(entry['path'])
        if name.is_absolute() or '..' in name.parts or not name.parts:
            raise ValueError('Unsafe scratch entry')
        saved[name] = entry
    current = {p.relative_to(root) for p in root.rglob('*')}
    if not current <= saved.keys():
        raise OSError('Unregistered files in scratch; retained')
    files, directories = [], [(root, record['directory'])]
    for name in sorted(current):
        path, entry = root / name, saved[name]
        if path.is_symlink():
            raise OSError('Scratch contains a link')
        fields = ('identity', 'parents') if entry['directory'] else ('identity', 'sha256', 'parents')
        expected = {key: entry[key] for key in fields}
        actual = directory_stamp(path) if entry['directory'] else _stamp(path)
        if actual != expected:
            raise OSError('Scratch entry changed')
        (directories if entry['directory'] else files).append((path, expected))
    return files, sorted(directories, key=lambda pair: len(pair[0].parts), reverse=True)

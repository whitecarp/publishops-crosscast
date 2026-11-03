"""Retain disposable runtime files until an explicit cleanup after their owner exits."""
import json
import hashlib
import os
from pathlib import Path
import re
import stat
import uuid

from .process_lock import alive


def _inside(path):
    from .core import home
    root = home().absolute()
    path = Path(path).absolute()
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if '..' in relative.parts or not relative.parts:
        return False
    for part in (path, *path.parents):
        if part == root.parent:
            break
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                return False  # Includes Windows junctions and other reparse points.
    return relative


def _safe(path):
    relative = _inside(path)
    if relative is False:
        return False
    path = Path(path)
    return (bool(re.fullmatch(r'\.vpub-[a-z0-9_]{8,32}', path.name)) or
            relative.parent == Path('browser-jobs') and bool(re.fullmatch(r'[a-f0-9]{32}\.(?:stages|json|result\.json)', path.name)))


def _stamp(path):
    from .core import file_sha
    parents = _parents(path)
    before = path.stat()
    sha = file_sha(path)
    after = path.stat()
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')
    if any(getattr(before, field) != getattr(after, field) for field in fields) or parents != _parents(path):
        raise OSError('Runtime file changed during inspection')
    return {'identity': [getattr(after, field) for field in fields], 'sha256': sha, 'parents': parents}


def _parents(path):
    result = []
    for parent in reversed(Path(path).absolute().parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise OSError('Parent is a reparse point')
        result.append({'path': str(parent), 'identity': [info.st_dev, info.st_ino]})
    return result


def defer(*paths, ready=True):
    """Best effort only: a failed registration leaves files alone, never hides a result."""
    from .core import home
    try:
        root = home().absolute()
        files = []
        for path in map(Path, paths):
            if path.exists() and _safe(path) and path.is_file():
                files.append({'path': path.absolute().relative_to(root).as_posix(), **_stamp(path)})
        if not files:
            return
        record = {'schema': 'crosscast-runtime-cleanup-v1', 'pid': os.getpid(), 'ready': ready, 'files': files}
        _record(record)
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def _record(record):
    from .core import home
    folder = home().absolute() / 'runtime-cleanup'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if _inside(folder) is False:
        return
    # A partial manifest is deliberately unrecognized, not a deletion list.
    with (folder / (uuid.uuid4().hex + '.json')).open('x', encoding='utf-8') as output:
        json.dump(record, output)
        output.flush()
        os.fsync(output.fileno())


def registered_in(folder):
    """Legacy tree deletion must not bypass a runtime entry's separate validation."""
    from .core import home
    from .runtime_scratch import SCHEMA
    root, folder = home().absolute(), Path(folder).absolute()
    try:
        registry = root / 'runtime-cleanup'
        if _inside(registry) is False:
            return True
        try:
            # Path.glob silently hides listing errors. An inaccessible registry
            # is not evidence that no runtime entries protect this folder.
            with os.scandir(registry) as entries:
                manifests = [Path(entry.path) for entry in entries if entry.name.endswith('.json')]
        except FileNotFoundError:
            return False
        for manifest in manifests:
            if _inside(manifest) is False:
                return True
            record = json.loads(manifest.read_bytes())
            if not isinstance(record, dict):
                return True
            if record.get('schema') == SCHEMA:
                paths = [Path(record['root'])]
                if not paths[0].is_absolute():
                    return True
            elif record.get('schema') == 'crosscast-runtime-cleanup-v1':
                names = [Path(entry['path']) for entry in record['files']]
                if any(name.is_absolute() or '..' in name.parts for name in names):
                    return True
                paths = [root / name for name in names]
            else:
                continue
            if any(folder == path or folder in path.parents or path in folder.parents for path in paths):
                return True
    except (OSError, ValueError, TypeError, KeyError):
        return True  # Cannot identify the protected folder; retain all candidates.
    return False


def cleanup(apply=False):
    """Only registered, unchanged files of a confirmed-exited owner are disposable."""
    from .core import home
    from .runtime_delete import remove
    from . import runtime_scratch
    root = home().absolute()
    removable, kept = [], []
    for manifest in (root / 'runtime-cleanup').glob('*.json'):
        if _inside(manifest) is False or not re.fullmatch(r'[a-f0-9]{32}\.json', manifest.name):
            continue
        try:
            raw = manifest.read_bytes()
            manifest_stamp = _stamp(manifest)
            if hashlib.sha256(raw).hexdigest() != manifest_stamp['sha256']:
                raise OSError('Cleanup manifest changed during inspection')
            record = json.loads(raw)
            if (not isinstance(record, dict) or record.get('schema') not in {'crosscast-runtime-cleanup-v1', runtime_scratch.SCHEMA}
                    or type(record.get('pid')) is not int or record['pid'] <= 0):
                continue
            reason = 'worker_completion_unknown' if record.get('ready') is not True else 'owner_active_or_unknown' if alive(record['pid']) else None
            if record['schema'] == runtime_scratch.SCHEMA:
                files, directories = runtime_scratch.inspect(record)
                item = {'manifest': str(manifest), 'files': [str(p) for p, _ in files], 'scratch': record['root'],
                        'bytes': sum(p.stat().st_size for p, _ in files if p.stat().st_nlink == 1)}
                if reason:
                    kept.append({**item, 'kept_because': reason})
                    continue
                if apply:
                    removed_bytes = sum(remove(path, saved) for path, saved in files)
                    for path, saved in directories:
                        remove(path, saved, directory=True)
                    remove(manifest, manifest_stamp)
                    item['bytes'] = removed_bytes
                removable.append(item)
                continue
            if not isinstance(record.get('files'), list) or not record['files']:
                continue
            files = []
            for saved in record['files']:
                if not isinstance(saved, dict) or not isinstance(saved.get('path'), str) or Path(saved['path']).is_absolute():
                    raise ValueError('Invalid runtime path')
                path = root / saved['path']
                if not _safe(path):
                    reason = 'unsafe_path'
                    break
                if path.exists():
                    if _stamp(path) != {k: saved[k] for k in ('identity', 'sha256', 'parents')}:
                        reason = 'file_changed'
                        break
                    files.append(path)
            item = {'manifest': str(manifest), 'files': [str(p) for p in files],
                    'bytes': sum(p.stat().st_size for p in files if p.stat().st_nlink == 1)}
            if reason:
                kept.append({**item, 'kept_because': reason})
                continue
            if apply:
                # Recheck immediately before deletion; never follow a replacement.
                if manifest.read_bytes() != raw:
                    kept.append({**item, 'kept_because': 'manifest_changed'})
                    continue
                removed_bytes = 0
                for saved in record['files']:
                    path = root / saved['path']
                    if path.exists():
                        if not _safe(path) or _stamp(path) != {k: saved[k] for k in ('identity', 'sha256', 'parents')}:
                            raise OSError('Runtime file changed before cleanup')
                        removed_bytes += remove(path, {k: saved[k] for k in ('identity', 'sha256', 'parents')})
                remove(manifest, manifest_stamp)
                item['bytes'] = removed_bytes
            removable.append(item)
        except (OSError, ValueError, TypeError, KeyError):
            kept.append({'manifest': str(manifest), 'kept_because': 'unreadable_or_changed'})
    return {'removed' if apply else 'removable': removable, 'kept': kept,
            'byte_accounting': 'Conservative logical bytes with no retained hardlink; not measured free disk space.',
            'freed_bytes' if apply else 'reclaimable_bytes': sum(item['bytes'] for item in removable)}

"""One durable destructive attempt per exact publication generation."""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import re
import stat

from .core import VpubError, atomic_json, home, now, read_json, receipt_turn, stop_check


IDENTIFIERS = {'bilibili': r'BV[A-Za-z0-9]{10}', 'youtube': r'[A-Za-z0-9_-]{11}',
               'instagram': r'[A-Za-z0-9_-]{5,32}', 'douyin': r'[0-9]{10,25}', 'x': r'[0-9]{10,25}'}


def _plain(path, *, directory=False):
    """lstat also sees dangling links; Windows junctions are reparse points."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise VpubError('invalid_deletion_receipt', 'The deletion receipt path was redirected; nothing was changed.')
    return info.st_dev, info.st_ino


def _location(key):
    if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{64}', key):
        raise VpubError('invalid_receipt_key', 'Use a publication receipt key, never a platform ID or URL.')
    try:
        root = home()
        info = root.stat()
        root_id = info.st_dev, info.st_ino
        directory = root / 'work-deletions'
        if _plain(directory, directory=True) is None:
            directory.mkdir(mode=0o700, exist_ok=True)
        directory_id = _plain(directory, directory=True)
        # MSIX can reveal the physical spelling only after this child exists.
        # Accept that spelling only for the same direct parent, never a link.
        canonical = directory.resolve(strict=True)
        if (canonical.name != directory.name or not canonical.parent.samefile(root)
                or directory_id is None or _plain(canonical, directory=True) != directory_id):
            raise OSError('Deletion directory binding changed')
        info = root.stat()
        if (info.st_dev, info.st_ino) != root_id:
            raise OSError('Deletion home changed')
        path = canonical / (key + '.json')
        for leaf in (path, Path(str(path) + '.turn'), Path(str(path) + '.turn.guard')):
            _plain(leaf)
            if leaf.resolve() != leaf:
                raise OSError('Deletion receipt or lock redirected')
        return path, (root_id, directory_id)
    except (OSError, RuntimeError):
        raise VpubError('invalid_deletion_receipt', 'The deletion receipt path cannot be safely bound; nothing was changed.') from None


def receipt_path(key):
    return _location(key)[0]


def _bound(path, *, key=None, expected=None):
    path = Path(path)
    canonical, binding = _location(path.stem if key is None else key)
    if path.absolute() != canonical or (expected is not None and binding != expected):
        raise VpubError('invalid_deletion_receipt', 'Deletion requires its own unchanged durable receipt.')
    return binding


@contextmanager
def _receipt_lock(path):
    before = _bound(path)
    with receipt_turn(path):
        _bound(path, expected=before)
        yield before


def same_target(saved, target):
    return isinstance(saved, dict) and saved == target


def read_receipt(path, *, missing_ok=False):
    """Only an absent file is a fresh attempt; malformed existing records stay untouched."""
    path = Path(path)
    _bound(path)
    try:
        saved = read_json(path)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise VpubError('invalid_deletion_receipt', 'The deletion receipt is missing; nothing may be pressed.') from None
    except (OSError, ValueError):
        raise VpubError('invalid_deletion_receipt', 'The deletion receipt cannot be read; preserve it and inspect the post. Do not retry Delete.') from None
    target = saved.get('target') if isinstance(saved, dict) else None
    valid = (isinstance(saved, dict) and isinstance(saved.get('state'), str)
             and saved['state'] in {'DELETE_READY', 'DELETE_PENDING', 'DELETED_VERIFIED'}
             and type(saved.get('delete_attempted')) is bool and isinstance(target, dict))
    if valid:
        valid = (all(isinstance(target.get(k), str) and target[k].strip()
                     for k in ('key', 'platform', 'id', 'identity', 'account', 'generation', 'attempt', 'url'))
                 and target['key'] == path.stem and target['platform'] in IDENTIFIERS
                 and re.fullmatch(IDENTIFIERS[target['platform']], target['id'])
                 and isinstance(target.get('title'), str)
                 and (saved['state'] != 'DELETE_READY' or saved['delete_attempted'] is False)
                 and (saved['state'] != 'DELETED_VERIFIED' or verified(saved.get('evidence'), target)))
    if not valid:
        raise VpubError('invalid_deletion_receipt', 'The existing deletion record is malformed or has an unknown state; preserve it and inspect the post. Do not retry Delete.')
    return saved


def write_transition(path, saved, operation):
    atomic_json(path, saved)
    from .audit_journal import transition
    target = saved['target']
    transition('deletion', target['key'], target['platform'], saved, operation=operation)


def cancel(path, target):
    """Revoke a cancelled worker before the caller releases its publication lock."""
    while True:
        try:
            # Exclusive creation preserves even an unreadable pre-existing STOP.
            # Presence already stops workers if interruption occurs during writing.
            try:
                with (home() / 'STOP.json').open('x', encoding='utf-8') as output:
                    json.dump({'source': 'operator', 'code': 'interrupted', 'at': now(),
                               'reason': 'Deletion interrupted; inspect the exact post and retained receipt. Do not repeat Delete.'}, output)
                    output.flush()
                    os.fsync(output.fileno())
            except FileExistsError:
                pass
            with _receipt_lock(path) as binding:
                saved = read_receipt(path, missing_ok=True)
                if saved is not None and same_target(saved['target'], target) and saved['state'] != 'DELETED_VERIFIED':
                    saved.update(state='DELETE_PENDING', cancelled_at=now())
                    _bound(path, expected=binding)
                    write_transition(path, saved, 'cancel')
            return
        except (KeyboardInterrupt, SystemExit):
            continue  # A repeated interruption must not skip STOP or the durable fence.


def refuse_other_action(key):
    """Legacy handoffs and edits must not create another attempt beside this one."""
    if isinstance(key, str) and re.fullmatch(r'[a-f0-9]{64}', key) and receipt_path(key).exists():
        raise VpubError('deletion_record_exists', 'This post has a deletion record; inspect that outcome before other actions.',
                        f'vpub delete {key} --verify-only')


def begin_press(path, target):
    """Called by the owning worker immediately before its first destructive control/request."""
    _bound(path, key=target['key'])
    with _receipt_lock(path) as binding:
        saved = read_receipt(path)
        if (saved.get('state') != 'DELETE_READY' or saved.get('delete_attempted')
                or not target.get('attempt') or not same_target(saved.get('target'), target)):
            raise VpubError('deletion_already_attempted', 'This deletion is no longer ready; nothing else was pressed.',
                            f"vpub delete {target['key']} --verify-only")
        stop_check(target['platform'])
        from .audit import code_stamp
        saved.update(state='DELETE_PENDING', delete_attempted=True, attempted_at=now(), press_code=code_stamp())
        _bound(path, expected=binding)
        write_transition(path, saved, 'press')
    from .progress import stage
    stage('pressing', platform=target['platform'], action='delete_work')
    return saved


def verified(evidence, target):
    """Absence in a partial list, a generic error page or a write acknowledgement proves nothing."""
    return (isinstance(evidence, dict) and evidence.get('id') == target['id']
            and evidence.get('identity') == target['identity']
            and all(evidence.get(field) is True for field in
                    ('page_gone', 'list_absent', 'list_complete', 'fresh')))


def save_result(path, target, evidence=None, error=None):
    with _receipt_lock(path) as binding:
        saved = read_receipt(path)
        if not same_target(saved.get('target'), target):
            raise VpubError('deletion_changed', 'Another deletion check replaced this attempt; its record was preserved.')
        # A failed later read cannot undo a prior independently confirmed deletion.
        if saved.get('state') == 'DELETED_VERIFIED':
            return saved
        from .audit import code_stamp
        saved.update(state='DELETED_VERIFIED' if verified(evidence, target) else 'DELETE_PENDING', updated_at=now(),
                     readback_code=code_stamp())
        if evidence is not None:
            saved['evidence'] = evidence
        if error is not None:
            saved['error'] = error
        _bound(path, expected=binding)
        write_transition(path, saved, 'readback')
        return saved

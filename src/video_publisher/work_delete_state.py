"""One durable destructive attempt per exact publication generation."""
from pathlib import Path
import json
import os
import re

from .core import VpubError, atomic_json, home, now, read_json, receipt_turn, stop_check


IDENTIFIERS = {'bilibili': r'BV[A-Za-z0-9]{10}', 'youtube': r'[A-Za-z0-9_-]{11}',
               'instagram': r'[A-Za-z0-9_-]{5,32}', 'douyin': r'[0-9]{10,25}', 'x': r'[0-9]{10,25}'}


def receipt_path(key):
    if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{64}', key):
        raise VpubError('invalid_receipt_key', 'Use a publication receipt key, never a platform ID or URL.')
    path = home() / 'work-deletions' / (key + '.json')
    if path.resolve() != path:
        raise VpubError('invalid_deletion_receipt', 'The deletion receipt path was redirected; nothing was changed.')
    return path


def same_target(saved, target):
    return isinstance(saved, dict) and saved == target


def read_receipt(path, *, missing_ok=False):
    """Only an absent file is a fresh attempt; malformed existing records stay untouched."""
    path = Path(path)
    if path.absolute() != receipt_path(path.stem):
        raise VpubError('invalid_deletion_receipt', 'Deletion requires its own durable receipt.')
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
            with receipt_turn(path):
                saved = read_receipt(path, missing_ok=True)
                if saved is not None and same_target(saved['target'], target) and saved['state'] != 'DELETED_VERIFIED':
                    saved.update(state='DELETE_PENDING', cancelled_at=now())
                    atomic_json(path, saved)
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
    if Path(path).resolve() != receipt_path(target['key']).resolve():
        raise VpubError('invalid_deletion_receipt', 'Deletion requires its own durable receipt.')
    with receipt_turn(path):
        saved = read_receipt(path)
        if (saved.get('state') != 'DELETE_READY' or saved.get('delete_attempted')
                or not target.get('attempt') or not same_target(saved.get('target'), target)):
            raise VpubError('deletion_already_attempted', 'This deletion is no longer ready; nothing else was pressed.',
                            f"vpub delete {target['key']} --verify-only")
        stop_check(target['platform'])
        saved.update(state='DELETE_PENDING', delete_attempted=True, attempted_at=now())
        atomic_json(path, saved)
    return saved


def verified(evidence, target):
    """Absence in a partial list, a generic error page or a write acknowledgement proves nothing."""
    return (isinstance(evidence, dict) and evidence.get('id') == target['id']
            and evidence.get('identity') == target['identity']
            and all(evidence.get(field) is True for field in
                    ('page_gone', 'list_absent', 'list_complete', 'fresh')))


def save_result(path, target, evidence=None, error=None):
    with receipt_turn(path):
        saved = read_receipt(path)
        if not same_target(saved.get('target'), target):
            raise VpubError('deletion_changed', 'Another deletion check replaced this attempt; its record was preserved.')
        # A failed later read cannot undo a prior independently confirmed deletion.
        if saved.get('state') == 'DELETED_VERIFIED':
            return saved
        saved.update(state='DELETED_VERIFIED' if verified(evidence, target) else 'DELETE_PENDING', updated_at=now())
        if evidence is not None:
            saved['evidence'] = evidence
        if error is not None:
            saved['error'] = error
        atomic_json(path, saved)
        return saved

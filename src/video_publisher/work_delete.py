"""Explicit deletion of one verified CrossCast publication, never an arbitrary platform ID."""
from copy import deepcopy
import re
import uuid

from .core import Ledger, VpubError, atomic_json, digest, now, publication_lock, receipt_turn, stop_check
from . import work_delete_state as state


VERIFIED = {'SUBMISSION_VERIFIED', 'AGENT_REPORTED_VERIFIED'}
IDENTIFIERS = state.IDENTIFIERS


def target_for(row):
    if not row:
        raise VpubError('not_found', 'Publication receipt key not found.')
    data = row['data']
    receipt = data.get('receipt') or {}
    if (row['state'] not in VERIFIED or receipt.get('state') != row['state']
            or data.get('association') or not data.get('approved_plan')
            or not data.get('started_at') or not data.get('plan_hash')
            or (data.get('options') or {}).get('submission_mode') == 'draft_only'
            or receipt.get('still_a_draft') is True):
        raise VpubError('deletion_not_allowed', 'Only a verified CrossCast publication can be deleted here; adopted, draft and uncertain records cannot.')
    platform, wid = row['platform'], receipt.get('id')
    if platform not in IDENTIFIERS or not isinstance(wid, str) or not re.fullmatch(IDENTIFIERS[platform], wid):
        raise VpubError('deletion_not_allowed', 'The publication lacks a valid exact platform ID.')
    identity = (data.get('identity') or {}).get('identity') or data.get('browser_identity')
    if not isinstance(identity, str) or not identity.strip():
        raise VpubError('deletion_identity_missing', 'The publication did not record its publishing identity; inspect it on the platform.')
    if receipt.get('identity') and receipt['identity'] != identity:
        raise VpubError('deletion_identity_mismatch', 'The stored publishing identities disagree; inspect the original receipt.')
    # Identity forms are already supplied by the platform; links here use only validated IDs.
    urls = {'bilibili': f'https://www.bilibili.com/video/{wid}', 'youtube': f'https://www.youtube.com/watch?v={wid}',
            'instagram': f'https://www.instagram.com/p/{wid}/', 'douyin': f'https://www.douyin.com/video/{wid}',
            'x': f'https://x.com/i/status/{wid}'}
    return {'key': row['key'], 'platform': platform, 'id': wid, 'identity': identity,
            'account': row['account'], 'title': data.get('title') or '', 'url': urls[platform],
            'generation': digest([data['plan_hash'], data.get('started_at'), receipt,
                                  len(data.get('deleted_generations') or [])])}


def backend(target, operation, path):
    if target['platform'] == 'bilibili':
        from .work_delete_bili import execute
        return execute(target, operation, str(path))
    from .adapters import browser_call
    return browser_call(target['platform'], 'delete_work', target['account'],
                        expected_identity=target['identity'], target=target, operation=operation,
                        deletion_receipt_path=str(path))


def public_result(saved):
    target = saved['target']
    done = saved.get('state') == 'DELETED_VERIFIED'
    return {k: target[k] for k in ('key', 'platform', 'id', 'title', 'url')} | {
        'state': saved['state'], 'deleted': done, 'delete_attempted': saved.get('delete_attempted') is True,
        'evidence': saved.get('evidence'), 'error': None if done else saved.get('error'),
        'next_command': None if done else f"vpub delete {target['key']} --verify-only",
        'hint': 'Original publication receipt retained.' if done else
                'Inspect this exact post. Only verify again; never repeat Delete while its outcome is unclear.'}


def run(key, *, dry_run=False, confirm=None, person_approved=False, verify_only=False):
    path = state.receipt_path(key)
    with publication_lock():
        prior = state.read_receipt(path, missing_ok=True)
        ledger = Ledger()
        try:
            row = ledger.get(key)
            target = target_for(row)
        finally:
            ledger.close()
        if target['platform'] == 'bilibili':
            raise VpubError('unsupported_delete', 'Bilibili post deletion is not implemented: its free route has no verified deletion contract. No account was contacted.',
                            f"The person deletes this exact video ({target.get('id')}) by hand in Creator Center's 稿件管理, "
                            'https://member.bilibili.com/platform/upload-manager/article , from its own menu. Never call Bilibili\'s delete API directly '
                            '(it answers with a security check), and do not substitute a comment or collection delete command.')
        if prior is not None:
            old = dict(prior['target'])
            old.pop('attempt', None)
            if old != target:
                raise VpubError('deletion_target_changed', 'The publication differs from the deletion record; inspect the retained history.')
            if prior.get('state') == 'DELETED_VERIFIED':
                return public_result(prior)
            if not verify_only:
                raise VpubError('deletion_already_attempted', 'A deletion may have reached the platform; it will not be pressed again.',
                                f'vpub delete {key} --verify-only')
        code = digest(['delete-one-work', target])
        if dry_run:
            return {**target, 'state': 'DELETE_PREVIEW', 'confirmation': code,
                    'platform_writes': 0, 'requires_person_approval': True,
                    'hint': f"After the person approves this exact post: vpub delete {key} --confirm {code} --person-approved"}
        if verify_only:
            if prior is None:
                raise VpubError('deletion_not_started', 'No deletion record exists. Start with --dry-run.')
            target = prior['target']
        else:
            if confirm != code or person_approved is not True:
                raise VpubError('confirmation_required', 'Review --dry-run, then supply its exact code and --person-approved for this one post.')
            target = {**target, 'attempt': uuid.uuid4().hex}
        try:
            if not verify_only:
                stop_check(target['platform'])
                # Inspect has no writable receipt, even if its worker finishes late.
                evidence = backend(target, 'inspect', path)
                absent = state.verified(evidence, target)
                if not absent and (not isinstance(evidence, dict) or evidence.get('id') != target['id']
                        or evidence.get('identity') != target['identity'] or evidence.get('owned') is not True
                        or evidence.get('present') is not True):
                    raise VpubError('deletion_target_unverified', 'The exact owned post is not positively identified. Nothing was deleted.')
                with receipt_turn(path):
                    if state.read_receipt(path, missing_ok=True) is not None:
                        raise VpubError('deletion_already_attempted', 'A deletion record appeared during inspection; it was preserved. Only verify again.')
                    from .audit import code_stamp
                    created = now()
                    atomic_json(path, {'state': 'DELETE_READY', 'target': target, 'created_at': created,
                                       'code': code_stamp(), 'approval': {'confirm_matched': confirm == code,
                                       'person_approved': person_approved, 'at': created},
                                       'delete_attempted': False, 'publication_receipt': deepcopy(row['data']['receipt'])})
                    from .audit_journal import transition
                    transition('deletion', target['key'], target['platform'], state.read_receipt(path), operation='create')
            stop_check(target['platform'])
            try:
                if verify_only:
                    evidence = backend(target, 'verify', path)
                elif not absent:
                    evidence = backend(target, 'delete', path)
                saved = state.save_result(path, target, evidence)
            except Exception as exc:
                # Keep any worker's attempt marker; no raw page text is stored.
                code = exc.code if isinstance(exc, VpubError) else type(exc).__name__
                saved = state.save_result(path, target, error={'code': code})
                from .safety import pushed_back
                pushed_back(target['platform'], code)
            return public_result(saved)
        except (KeyboardInterrupt, SystemExit) as exc:
            # Includes interruption immediately before/after atomic READY, not
            # just a backend wait. STOP also blocks a worker already past its mark.
            try:
                state.cancel(path, target)
            except Exception as failure:
                raise exc from failure
            raise


def status_rows():
    from .core import home
    for path in (home() / 'work-deletions').glob('*.json'):
        if not re.fullmatch(r'[a-f0-9]{64}', path.stem):
            continue
        try:
            yield public_result(state.read_receipt(path))
        except (OSError, ValueError, KeyError, TypeError, VpubError):
            # An unreadable attempt still needs attention. Do not expose any
            # unvalidated target fields or imply that no deletion was attempted.
            yield {'key': path.stem, 'platform': None, 'id': None, 'title': None, 'url': None,
                   'state': 'DELETE_RECORD_INVALID', 'deleted': False, 'delete_attempted': None,
                   'evidence': None, 'error': {'code': 'invalid_deletion_receipt'},
                   'next_command': 'vpub receipts',
                   'hint': 'Deletion outcome unknown. Preserve the invalid deletion record, inspect the original publication receipt and exact post; do not retry Delete.'}

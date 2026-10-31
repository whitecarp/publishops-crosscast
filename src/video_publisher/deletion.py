"""Explicit Instagram deletion/repost handoffs; no platform API or browser access."""
from copy import deepcopy
from pathlib import Path
import re

from .core import Ledger, VpubError, home, load_plan, now, publication_lock, read_json, snapshot_file, stop_check
from . import native_browser


def _work(ledger, key, identity, work_id):
    old = ledger.get(key)
    if (not old or old['platform'] != 'instagram'
            or old['data'].get('options', {}).get('method') != 'native'):
        raise VpubError('unsupported_deletion', 'This deletion handoff currently supports Instagram native jobs only.')
    if identity != old['data'].get('browser_identity'):
        raise VpubError('wrong_account', 'Inspect the same signed-in Instagram account first.')
    if not isinstance(work_id, str) or not re.fullmatch(r'[\w-]+', work_id) or work_id != old['data'].get('receipt', {}).get('id'):
        raise VpubError('wrong_work', 'Deletion must name the exact durable work ID from its publication receipt.')
    return old


def request(key, identity, work_id):
    """Called by a host with explicit authorization for this work, before UI Delete."""
    with publication_lock():
        stop_check('instagram')
        ledger = Ledger()
        try:
            old = _work(ledger, key, identity, work_id)
            if old['state'] not in {'AGENT_REPORTED_VERIFIED', 'DELETE_PENDING'}:
                raise VpubError('deletion_not_allowed', 'Only a verified work can enter deletion; inspect uncertain attempts first.')
            data = old['data']
            data.setdefault('deletion_request', {'id': work_id, 'identity': identity, 'at': now(),
                                               'publication_receipt': deepcopy(data['receipt'])})
            ledger.save(key, 'DELETE_PENDING', data)
            return {'key': key, 'state': 'DELETE_PENDING', 'id': work_id,
                    'page_url': f'https://www.instagram.com/reel/{work_id}/', 'platform_writes': 0,
                    'hint': 'Host must have explicit deletion authorization. Inspect the exact work and recovery wording, delete once in the official UI, then record deletion acknowledgement AND refreshed profile absence. No automatic repost.'}
        finally:
            ledger.close()


def record(key, evidence_path):
    proof = read_json(evidence_path)
    allowed = {'id', 'identity', 'page_url', 'profile_url', 'delete_acknowledged',
               'work_absent', 'observation', 'recovery_note'}
    if not isinstance(proof, dict) or set(proof) != allowed:
        raise VpubError('invalid_evidence', 'Use the documented deletion fields only; exclude raw logs and credentials.')
    for field in ('observation', 'recovery_note'):
        if not isinstance(proof[field], str) or not 1 <= len(proof[field]) <= 1000:
            raise VpubError('invalid_evidence', 'Record a short observed deletion result and recovery wording.')
    with publication_lock():
        ledger = Ledger()
        try:
            old = _work(ledger, key, proof['identity'], proof['id'])
            if old['state'] != 'DELETE_PENDING':
                raise VpubError('deletion_not_started', 'Record only a pending deletion; do not replay old evidence.')
            if (proof['delete_acknowledged'] is not True or proof['work_absent'] is not True
                    or proof['page_url'] != f"https://www.instagram.com/reel/{proof['id']}/"
                    or proof['profile_url'] != f"https://www.instagram.com/{proof['identity']}/"):
                raise VpubError('deletion_not_verified', 'Require an explicit UI deletion acknowledgement and fresh same-account profile absence.')
            result = {**proof, 'state': 'AGENT_REPORTED_DELETED', 'recorded_at': now(),
                      'verification_source': 'host_agent_ui_observation'}
            old['data']['deletion_receipt'] = result
            ledger.save(key, result['state'], old['data'])
            return {'key': key, **result, 'platform_writes': 0,
                    'note': 'Host UI evidence, not an independent API query. Original publication receipt retained.'}
        finally:
            ledger.close()


def repost(key, manifest, confirmation, identity, deleted_id):
    """Explicit new generation after verified deletion; keeps immutable history."""
    plan = load_plan(manifest, ['instagram'])
    if plan['confirmation'] != confirmation:
        raise VpubError('confirmation_required', 'Use the current replacement plan hash.')
    with publication_lock():
        stop_check('instagram')
        ledger = Ledger()
        try:
            old = _work(ledger, key, identity, deleted_id)
            data = old['data']
            if (old['state'] != 'AGENT_REPORTED_DELETED'
                    or data.get('deletion_receipt', {}).get('id') != deleted_id):
                raise VpubError('repost_not_allowed', 'Repost requires verified deletion of this exact work and explicit user authorization.')
            opts = plan['platforms']['instagram']
            if (plan['video_sha256'] != data['approved_plan']['video_sha256']
                    or opts != data['options']):
                raise VpubError('repost_scope_changed', 'This cover-replacement operation preserves video bytes, account, metadata, audience and declarations.')
            generation = len(data.get('deleted_generations', [])) + 1
            snap = home() / 'jobs' / f"{plan['confirmation']}-repost-{generation}"
            safe = deepcopy(plan)
            def copy_asset(source, sha, name):
                return str(snapshot_file(source, snap / (name + Path(source).suffix.lower()), sha))
            safe['video'] = copy_asset(plan['video'], plan['video_sha256'], 'video')
            if plan.get('cover'):
                safe['cover'] = copy_asset(plan['cover'], plan['cover_sha256'], 'cover')
            for role, asset in safe.get('covers', {}).get('instagram', {}).items():
                asset['path'] = copy_asset(asset['path'], asset['sha256'], 'instagram-' + role)
            data.setdefault('deleted_generations', []).append({
                'at': now(), 'plan': deepcopy(data['approved_plan']),
                'publication_receipt': deepcopy(data['receipt']),
                'deletion_receipt': deepcopy(data['deletion_receipt'])})
            data.update(approved_plan=plan, plan_hash=plan['confirmation'], options=opts, title=opts['title'])
            for field in ('receipt', 'deletion_request', 'deletion_receipt', 'browser_started_at'):
                data.pop(field, None)
            # Persist the non-startable state before writing the next job. A crash
            # here requires inspection, never an automatic extra submission.
            ledger.save(key, 'REPOST_PREPARING', data)
            result = native_browser.queue('instagram', safe, opts)
            ledger.save(key, 'WAITING_FOR_BROWSER', data)
            return {**result, 'deleted_id': deleted_id, 'generation': generation,
                    'platform_writes': 0, 'hint': 'Run browser begin once before selecting the replacement snapshot. Read back the new ID and cover; retain the old deletion history.'}
        finally:
            ledger.close()

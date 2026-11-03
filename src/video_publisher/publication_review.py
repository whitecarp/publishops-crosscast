"""One local review gate for every new publication, before account access."""
import json
from pathlib import Path
import sqlite3

from .core import VpubError, home, read_json


def folder_choice(folder):
    path = Path(folder) / 'review.json'
    if not path.exists():
        return False
    saved = read_json(path)
    if (not isinstance(saved, dict) or saved.get('schema_version') != 1
            or set(saved) != {'schema_version', 'show_first'}
            or saved['show_first'] is not None and type(saved['show_first']) is not bool):
        raise VpubError('invalid_review', 'The prepared review settings are invalid; nothing was uploaded.',
                        'Keep review.json from prepare; show_first must be true, false or null.')
    return saved['show_first'] is True


def manifest_choice(path, manifest):
    value = manifest.get('review_before_publish', False)
    if type(value) is not bool:
        raise VpubError('invalid_review', 'review_before_publish must be true or false.')
    # Old prepared folders keep their choice in review.json. Recognise their
    # original manifest and build/<hash>/ready.json without rewriting records.
    parent = Path(path).resolve().parent
    candidates = [parent]
    if parent.parent.name == 'build':
        candidates.append(parent.parent.parent)
    for folder in candidates:
        if (folder / 'publish.json').is_file() and (folder / 'copy.json').is_file():
            value = folder_choice(folder) or value
    return value


def published_platforms():
    path = home() / 'publications.sqlite3'
    if not path.exists():
        return set()
    published = set()
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            rows = db.execute("SELECT platform, data FROM publications WHERE state='SUBMISSION_VERIFIED'")
            for platform, raw in rows:
                data = json.loads(raw)
                if isinstance(data, dict) and 'association' not in data and isinstance(data.get('options'), dict):
                    if data['options'].get('submission_mode', 'publish') == 'publish':
                        published.add(platform)
        finally:
            db.close()
    except (sqlite3.Error, ValueError, OSError) as exc:
        raise VpubError('review_history_unavailable', 'Publication history could not be checked; nothing was uploaded.',
                        'Inspect the local publication records; do not delete them to bypass review.') from exc
    return published


def has_published(platform=None):
    published = published_platforms()
    return bool(published) if platform is None else platform in published


def carry_choice(source, destination, manifest):
    """Keep a legacy folder's review choice when generating a portable plan."""
    if manifest_choice(source, manifest) or manifest_choice(destination, manifest):
        manifest['review_before_publish'] = True


def requirements(plan):
    from .preferences import read
    reasons = []
    if read()['show_first'] is True or plan.get('review_before_publish') is True:
        reasons.append('show_first')
    targets = {p for p, o in plan['platforms'].items() if o.get('submission_mode', 'publish') == 'publish'}
    first = sorted(targets - published_platforms()) if targets else []
    if first:
        reasons.append('first_publication')
    return {'review_required': bool(reasons), 'review_reasons': reasons,
            'first_publication_platforms': first}


def check(plan, confirmation, person_approved=False, *, draft_promotion=False):
    """Call under publication_lock, before dispatch or enabling a draft's Post.

    This is caller attestation, not a trusted human-input channel. A plan code
    identifies content; it does not imply the person reviewed that content.
    """
    review = requirements(plan)
    if draft_promotion:
        review['review_required'] = True
        review['review_reasons'].append('draft_promotion')
    if confirmation is None:
        return pending(plan, review)
    if confirmation != plan['confirmation']:
        raise VpubError('confirmation_required', 'The confirmation hash must match the current plan.',
                        'Show the changed plan to the user and wait for approval before continuing.')
    if review['review_required'] and person_approved is not True:
        return pending(plan, review)
    return None


def pending(plan, requirements):
    return {'ready': False, 'state': 'NEEDS_A_PERSON', 'platform_writes': 0,
            **requirements, 'review': plan, 'confirmation': plan['confirmation'],
            'next': 'Stop and show this exact plan to the user: text, accounts, platforms, audience, AI labels and covers. '
                    'Wait for their yes. Only then use vpub publish PLAN --confirm ' + plan['confirmation'] + ' --person-approved. '
                    'The flags record the caller\'s attestation, not proof of a human response. Changed plans need a new code.'}

"""Local, account-scoped comment history. Marking items never changes a platform."""
import re
from .core import VpubError, atomic_json, digest, home, now, publication_lock, read_json

STATES = ('unread', 'read', 'handled', 'deferred')


def merge(platform, work, observation):
    """Called under the observation writer's lock; missing rows are NOT deletions."""
    identity = observation['identity']
    path = home() / 'operations/inbox' / (digest([platform, identity, work]) + '.json')
    data = read_json(path) if path.exists() else {'schema_version': 1, 'items': {}}
    for comment in observation.get('items', []):
        key = digest([platform, identity, work, comment['id']])
        old = data['items'].get(key)
        changed = bool(old and any(old['comment'].get(k) != comment.get(k) for k in ('text', 'author_id', 'parent_id')))
        status = 'unread' if changed or not old else old['status']
        row = {**(old or {}), 'key': key, 'platform': platform, 'identity': identity,
               'work_id': work, 'comment': dict(comment), 'status': status,
               'first_seen_at': (old or {}).get('first_seen_at', observation['observed_at']),
               'last_seen_at': observation['observed_at'],
               'revision': (old or {}).get('revision', 1) + int(changed)}
        if changed:
            row['reopened_at'] = observation['observed_at']
        data['items'][key] = row
    data['observed_at'] = observation['observed_at']
    atomic_json(path, data)


def entries():
    for path in sorted((home() / 'operations/inbox').glob('*.json')):
        for item in read_json(path).get('items', {}).values():
            yield path, item


def listing(platform=None, status='pending', limit=50, cursor=None, include_own=False, identity=None):
    from .core import platform_check
    if platform: platform_check(platform)
    if status not in (*STATES, 'pending', 'all') or not 1 <= limit <= 200:
        raise VpubError('invalid_inbox_filter', 'Choose pending/all/unread/read/handled/deferred and limit 1–200.')
    if cursor and not re.fullmatch(r'[a-f0-9]{64}', cursor):
        raise VpubError('invalid_cursor', 'Use the next_cursor returned by inbox list.')
    rows = [row for _, row in entries() if (not platform or row['platform'] == platform)
            and (not identity or row['identity'] == identity)
            and (include_own or not row['comment'].get('is_owner'))
            and (status == 'all' or (status == 'pending' and row['status'] in {'unread', 'read'}
                                    and row['comment'].get('owner_replied') is not True) or row['status'] == status)]
    rows.sort(key=lambda r: r['key'])
    eligible = [row for row in rows if not cursor or row['key'] > cursor]
    page = eligible[:limit]
    return {'items': page, 'matching_total': len(rows), 'next_cursor': page[-1]['key'] if len(eligible) > limit else None,
            'platform_writes': 0, 'scope': 'Local observed comments only. Missing rows are not proof of deletion; last_seen_at records freshness.'}


def mark(key, status):
    if not re.fullmatch(r'[a-f0-9]{64}', key) or status not in STATES:
        raise VpubError('invalid_inbox_mark', 'Use an inbox key and unread/read/handled/deferred.')
    with publication_lock():
        matches = [(path, row) for path, row in entries() if row['key'] == key]
        if len(matches) != 1: raise VpubError('inbox_item_missing', 'The exact local comment was not found.')
        path, row = matches[0]
        data = read_json(path)
        row.update(status=status, marked_at=now())
        data['items'][key] = row
        atomic_json(path, data)
    return {**row, 'platform_writes': 0}


def find(platform, identity, work, comment_id):
    row = find_item(platform, identity, work, comment_id)
    return row['comment'] if row else None


def find_item(platform, identity, work, comment_id):
    path = home() / 'operations/inbox' / (digest([platform, identity, work]) + '.json')
    if not path.exists(): return None
    row = read_json(path)['items'].get(digest([platform, identity, work, comment_id]))
    return row

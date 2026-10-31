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

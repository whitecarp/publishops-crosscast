"""Explicit single-recipient messages with durable attempt IDs and no resends."""
import re
import uuid
from . import adapters
from .core import VpubError, atomic_json, digest, home, now, publication_lock, read_json, stop_check

PENDING = {'DM_SUBMITTING', 'DM_UNVERIFIED'}


def recipient(platform, value):
    pattern = {'bilibili': r'[1-9][0-9]{0,19}', 'instagram': r'[A-Za-z0-9_.]{1,30}',
               'x': r'[A-Za-z0-9_]{1,15}', 'douyin': r'MS4w[A-Za-z0-9_-]{10,100}'}.get(platform)
    if not pattern: raise VpubError('unsupported_operation', 'No direct-message adapter on this platform.')
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise VpubError('invalid_recipient', 'Use one exact platform recipient identifier, not a URL or option.')
    return value


def identity(platform):
    if platform == 'bilibili': return adapters.auth_status(platform, 'default', 'cli')['identity']
    from .browser_runtime import profile_dir
    path = profile_dir(platform).parent / (platform + '-identity.json')
    if not path.exists(): raise VpubError('not_logged_in', 'Run auth login first.')
    return read_json(path)['identity']  # Worker rechecks this against the live account.


def dispatch(platform, operation, expected, **kw):
    stop_check(platform)
    if platform == 'bilibili':
        from .direct_messages_bili import execute
        return execute(operation, expected, **kw)
    return adapters.browser_call(platform, 'direct_messages', 'default', expected_identity=expected,
                                 operation=operation, **kw)


def read(platform, who=None, limit=20):
    if platform == 'youtube': raise VpubError('unsupported_operation', 'This YouTube adapter has no private-message feature.')
    if not 1 <= limit <= 50: raise VpubError('invalid_limit', 'Use a limit from 1 to 50.')
    if who is not None: recipient(platform, who)
    return dispatch(platform, 'read' if who else 'list', identity(platform), recipient=who, limit=limit)


def validate_text(text, language):
    if not isinstance(text, str) or not text.strip() or len(text) > 1000 or '\x00' in text:
        raise VpubError('invalid_text', 'Supply one message of 1–1000 characters.')
    if not re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', language or ''):
        raise VpubError('language_required', 'Specify the recipient language; the host writes one language only.')


def send(platform, who, text, language, dry_run=False, test_id=None):
    who = recipient(platform, who); validate_text(text, language)
    if test_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', test_id):
        raise VpubError('invalid_test_id', 'Use a short test label.')
    expected = identity(platform)
    with publication_lock():
        canonical=who.casefold() if platform in {'instagram','x'} else who
        target_path = home() / 'operations/dm-targets' / (digest([platform, expected, canonical]) + '.json')
        if target_path.exists():
            prior = read_json(home() / 'operations/dm-actions' / (read_json(target_path)['key'] + '.json'))
            if prior['state'] in PENDING or (prior['request']['text'] == text and prior['state'] == 'DM_VISIBLE'):
                return {**prior, 'reused': True, 'platform_writes': 0, 'hint': 'Use dm verify KEY; no automatic resend.'}
        request = {'platform': platform, 'identity': expected, 'recipient': who, 'text': text, 'language': language}
        before = dispatch(platform, 'read', expected, recipient=who, limit=20)
        if before.get('identity') != expected or before.get('recipient') != who:
            raise VpubError('wrong_recipient', 'The live account/conversation does not match the requested target.')
        if dry_run: return {'state': 'DRY_RUN', 'request': request, 'observed': before, 'platform_writes': 0}
        if not before.get('can_send'): raise VpubError('dm_send_unavailable', 'This recipient or platform currently has no verified send control.')
        key = uuid.uuid4().hex; path = home() / 'operations/dm-actions' / (key + '.json')
        saved = {'key': key, 'state': 'DM_READY', 'request': request, 'at': now(), 'test': bool(test_id),
                 'test_id': test_id, 'platform_writes': 0, 'before': before}
        atomic_json(path, saved); atomic_json(target_path, {'key': key})
        try:
            result = dispatch(platform, 'send', expected, recipient=who, text=text, receipt_path=str(path), limit=20)
            saved = {**read_json(path), **result}
            atomic_json(path, saved)
            return _verify(path)
        except Exception as exc:
            saved = read_json(path)
            saved.update(state='DM_UNVERIFIED' if saved['state'] in PENDING else 'DM_FAILED',
                         error_code=exc.code if isinstance(exc, VpubError) else type(exc).__name__)
            saved['platform_writes'] = None if saved['state'] == 'DM_UNVERIFIED' else 0
            atomic_json(path, saved)
            return record(saved)


def submitting(path):
    saved = read_json(path); saved.update(state='DM_SUBMITTING', submitted_at=now()); atomic_json(path, saved)


def _verify(path):
    saved = read_json(path); r = saved['request']
    after = dispatch(r['platform'], 'verify', r['identity'], recipient=r['recipient'],
                     receipt=saved, limit=50)
    if saved['state'] in PENDING | {'DM_VISIBLE'}:
        matched = (after.get('matching_outgoing_message') is True and after.get('identity') == r['identity']
                   and after.get('recipient') == r['recipient'])
        saved['state'] = 'DM_VISIBLE' if matched else 'DM_UNVERIFIED'
    saved.update(after=after, verified_at=now(), delivery='Recipient receipt/read status is not established.')
    atomic_json(path, saved)
    return record(saved)


def record(saved):
    from .operations import record as event
    # Operational reports contain only aggregate state, never DM bodies/contacts.
    event('direct_message', saved['request']['platform'], None,
          {k:saved.get(k) for k in ('key','state','test','test_id','platform_writes')})
    return saved


def verify(key):
    if not re.fullmatch(r'[a-f0-9]{32}', key): raise VpubError('invalid_receipt', 'Use a DM receipt key.')
    path = home() / 'operations/dm-actions' / (key + '.json')
    if not path.exists(): raise VpubError('receipt_required', 'DM receipt not found.')
    with publication_lock(): return {**_verify(path), 'platform_writes': 0}

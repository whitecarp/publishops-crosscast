"""Explicit comment management with exact targets and durable no-resend receipts."""
import re
import uuid
from . import adapters, operations as ops
from .core import VpubError, atomic_json, digest, home, now, publication_lock, read_json, stop_check

SUPPORTED = {'bilibili': {'like', 'pin'}, 'instagram': {'like'}, 'x': {'like','hide'}, 'youtube': {'edit', 'delete','moderate'}, 'douyin': {'like', 'delete'}}
PENDING = {'COMMENT_SUBMITTING', 'COMMENT_UNVERIFIED'}


def target(platform, work, comment_id):
    ops.work_id(platform, work)
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', comment_id):
        raise VpubError('invalid_comment_id', 'Use an exact observed comment ID.')
    path = home() / 'operations/comments' / (digest([platform, work]) + '.json')
    if not path.exists(): raise VpubError('comment_snapshot_required', 'Read this work’s comments first.')
    snapshot = read_json(path)
    rows = [c for c in snapshot['items'] if c['id'] == comment_id]
    if not rows:
        from .operations_inbox import find
        old = find(platform, snapshot['identity'], work, comment_id)
        if old: rows = [old]
    if len(rows) != 1: raise VpubError('comment_not_observed', 'The exact comment was not observed.')
    return {'platform': platform, 'work_id': work, 'identity': snapshot['identity'], 'comment': rows[0]}


def dispatch(request, operation='status', receipt_path=None):
    p = request['platform']; stop_check(p)
    if p in {'bilibili', 'youtube'}:
        from .comment_actions_api import execute
        return execute(request, operation, receipt_path)
    return adapters.browser_call(p, 'comment_management', 'default', expected_identity=request['identity'],
        request=request, operation=operation, receipt_path=str(receipt_path) if receipt_path else None)


def status(platform, work, comment_id):
    if not SUPPORTED.get(platform): raise VpubError('unsupported_operation', 'Comment management has no verified adapter on this platform.')
    request = target(platform, work, comment_id)
    return {**dispatch(request), 'platform': platform, 'work_id': work, 'comment_id': comment_id, 'platform_writes': 0}


def submitting(path):
    saved = read_json(path); saved.update(state='COMMENT_SUBMITTING', submitted_at=now())
    atomic_json(path, saved)


def matches(request, observed):
    if observed.get('identity') != request['identity']: return False
    key = {'like': 'liked', 'pin': 'pinned', 'hide':'hidden', 'moderate':'moderation_status', 'edit': 'text', 'delete': 'deleted'}[request['kind']]
    return observed.get(key) == request['desired'] and (key in {'text','moderation_status'} or type(observed.get(key)) is bool)


def mutate(platform, work, comment_id, kind, desired=True, dry_run=False, test_id=None):
    if kind not in SUPPORTED.get(platform, set()):
        raise VpubError('unsupported_operation', 'This comment action is not supported on this platform.')
    if kind=='moderate':
        if desired not in {'published','heldForReview'}:raise VpubError('invalid_action','Choose published or heldForReview. Rejection/author bans are not implicit actions.')
    elif kind == 'edit':
        if not isinstance(desired, str) or not desired.strip() or len(desired) > 2000 or '\x00' in desired:
            raise VpubError('invalid_text', 'Supply nonempty comment text of at most 2000 characters.')
    elif type(desired) is not bool or (kind == 'delete' and not desired):
        raise VpubError('invalid_action', 'Invalid target state.')
    if test_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', test_id): raise VpubError('invalid_test_id', 'Use a short test label.')
    request = {**target(platform, work, comment_id), 'kind': kind, 'desired': desired}
    if kind=='moderate' and request['comment'].get('is_owner'):
        raise VpubError('other_author_required','Use a viewer comment for moderation. The live self-comment probe did not change state.')
    if kind in {'edit', 'delete'} and not request['comment'].get('is_owner'):
        raise VpubError('own_comment_required', 'Editing/deleting is restricted to your own comments on your own work.')
    with publication_lock():
        index = home() / 'operations/comment-targets' / (digest([platform, request['identity'], work, comment_id]) + '.json')
        if index.exists():
            prior = read_json(home() / 'operations/comment-actions' / (read_json(index)['key'] + '.json'))
            if prior['state'] in PENDING:
                return {**prior, 'platform_writes': 0, 'reused': True, 'hint': 'Use comments verify-action KEY; no automatic resend or reversal.'}
        before = dispatch(request)
        if matches(request, before): return {'state': 'NO_CHANGE', 'platform_writes': 0, 'observed': before}
        if before.get('deleted'): raise VpubError('comment_missing', 'The comment is no longer present.')
        if kind in {'like','pin','hide'} and type(before.get({'like':'liked','pin':'pinned','hide':'hidden'}[kind])) is not bool:
            raise VpubError('state_unavailable', 'Exact comment action state is unavailable.')
        if kind=='moderate' and before.get('moderation_status') not in {'published','heldForReview'}:
            raise VpubError('state_unavailable','Exact moderation status is unavailable; no write attempted.')
        if before.get('text') != request['comment']['text']:
            raise VpubError('comment_changed', 'The comment text changed; read it again before changing it.')
        if dry_run: return {'state': 'DRY_RUN', 'request': request, 'observed': before, 'platform_writes': 0}
        key = uuid.uuid4().hex; path = home() / 'operations/comment-actions' / (key + '.json')
        saved = {'key': key, 'state': 'COMMENT_READY', 'request': request, 'before': before, 'at': now(),
                 'test': bool(test_id), 'test_id': test_id, 'platform_writes': 0}
        atomic_json(path, saved); atomic_json(index, {'key': key})
        try:
            result = dispatch(request, 'set', path)
            saved = {**read_json(path), **result}
            after = dispatch({**request, 'verification': True})
            state = 'COMMENT_VERIFIED' if matches(request, after) else 'COMMENT_UNVERIFIED'
            if result.get('state') == 'NO_CHANGE' and state == 'COMMENT_VERIFIED': state = 'NO_CHANGE'
            saved.update(after=after, state=state)
        except Exception as exc:
            saved = read_json(path)
            saved.update(state='COMMENT_UNVERIFIED' if saved['state'] in PENDING else 'COMMENT_FAILED',
                         error_code=exc.code if isinstance(exc, VpubError) else type(exc).__name__)
            saved['platform_writes'] = None if saved['state'] == 'COMMENT_UNVERIFIED' else 0
        atomic_json(path, saved)
        return ops.record('comment_management', platform, work, saved)


def verify(key):
    if not re.fullmatch(r'[a-f0-9]{32}', key): raise VpubError('invalid_receipt', 'Use a comment action key.')
    path = home() / 'operations/comment-actions' / (key + '.json')
    if not path.exists(): raise VpubError('receipt_required', 'Comment action receipt not found.')
    with publication_lock():
        saved = read_json(path); r = saved['request']
        after = dispatch({**r, 'verification': True})
        if saved['state'] in PENDING | {'COMMENT_VERIFIED'}:
            saved['state'] = 'COMMENT_VERIFIED' if matches(r, after) else 'COMMENT_UNVERIFIED'
        if saved['state']=='COMMENT_VERIFIED' and saved.get('error_code'):
            saved.setdefault('resolved_errors',[]).append({'code':saved.pop('error_code'),'resolved_at':now()})
        saved.update(after=after, verified_at=now()); atomic_json(path, saved)
        ops.record('comment_management', r['platform'], r['work_id'], saved)
        return {**saved, 'platform_writes': 0}


def resume(key):
    """Explicit recovery only for reversible state setters, never an automatic retry."""
    if not re.fullmatch(r'[a-f0-9]{32}',key):raise VpubError('invalid_receipt','Use a comment action key.')
    path=home()/'operations/comment-actions'/(key+'.json')
    if not path.exists():raise VpubError('receipt_required','Comment action receipt not found.')
    with publication_lock():
        saved=read_json(path);r=saved['request']
        if r['kind'] not in {'hide','moderate'} or saved['state'] not in PENDING:
            raise VpubError('resume_unavailable','Only a pending hide/moderation state change can be explicitly resumed.')
        index=home()/'operations/comment-targets'/(digest([r['platform'],r['identity'],r['work_id'],r['comment']['id']])+'.json')
        if not index.exists() or read_json(index)['key']!=key:
            raise VpubError('stale_receipt','This is no longer the current action for the comment.')
        current=dispatch({**r,'verification':True})
        if matches(r,current):
            saved.update(state='COMMENT_VERIFIED',after=current,verified_at=now());atomic_json(path,saved)
            return {**saved,'platform_writes':0}
        field='hidden' if r['kind']=='hide' else 'moderation_status'
        if (current.get('identity')!=r['identity'] or current.get('text')!=r['comment']['text']
            or current.get(field) is None or current.get(field)!=saved['before'].get(field)):
            raise VpubError('state_unavailable','Cannot establish the exact original state; no retry was made.')
        saved.setdefault('recovery_attempts',[]).append({'at':now(),'before':current,'previous_error':saved.get('error_code'),
                                                       'previous_platform_writes':saved.get('platform_writes')})
        saved.update(state='COMMENT_READY',platform_writes=0);saved.pop('error_code',None);atomic_json(path,saved)
        try:
            result=dispatch(r,'set',path)
            saved={**read_json(path),**result}
            after=dispatch({**r,'verification':True})
            saved.update(after=after,state='COMMENT_VERIFIED' if matches(r,after) else 'COMMENT_UNVERIFIED')
        except Exception as exc:
            saved=read_json(path)
            saved.update(state='COMMENT_UNVERIFIED' if saved['state'] in PENDING else 'COMMENT_FAILED',
                         error_code=exc.code if isinstance(exc,VpubError) else type(exc).__name__)
            saved['platform_writes']=None if saved['state']=='COMMENT_UNVERIFIED' else 0
        atomic_json(path,saved)
        return ops.record('comment_management',r['platform'],r['work_id'],saved)

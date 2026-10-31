"""Comment likes and pins through the included bili-cli."""
from . import adapters
from .core import VpubError, atomic_json, read_json, stop_check


def execute(request, operation, receipt_path=None):
    p = request['platform']; w = request['work_id']; target = request['comment']; cid = target['id']
    expected = request['identity']; kind = request.get('kind')
    if p != 'bilibili':
        return adapters.youtube_api().comment_action(request, operation, receipt_path)
    identity = adapters.auth_status(p, 'default', 'cli')['identity']
    info = adapters.bili(['video', 'info', w])
    if str(info.get('owner', {}).get('mid')) != identity: raise VpubError('wrong_work', 'Work owner mismatch.')
    if not cid.isdigit(): raise VpubError('invalid_comment_id', 'Bilibili comment IDs must be numeric.')
    root = target.get('parent_id') or cid
    if not root.isdigit(): raise VpubError('invalid_comment_id', 'Bilibili root IDs must be numeric.')
    data = adapters.bili(['comment', 'replies', w, root, '--size', '20', '--raw'])
    rows = [data.get('root') or {}, *(data.get('replies') or [])]
    found = [c for c in rows if str(c.get('rpid')) == cid]
    if len(found) != 1: raise VpubError('comment_not_visible', 'Exact comment not found in the bounded reply page.')
    c = found[0]; action = c.get('action')
    observed = {'identity': identity, 'text': (c.get('content') or {}).get('message'), 'deleted': False,
                'liked': action == 1 if type(action) is int and action in {0, 1} else None,
                'source': 'bili_cli_raw_comment_action'}
    if kind == 'pin':
        if target.get('parent_id'): raise VpubError('root_required', 'Only top-level comments can be pinned.')
        first = adapters.bili(['comment','list',w,'--page','1','--raw'])
        if 'upper' not in first and 'top_replies' not in first:
            raise VpubError('state_unavailable','Pinned-comment state unavailable.')
        tops = list(first.get('top_replies') or [])
        upper = (first.get('upper') or {}).get('top')
        if upper: tops.append(upper)
        pinned = {str(c.get('rpid')) for c in tops}
        observed['pinned'] = cid in pinned
        if request.get('desired') and pinned - {cid}:
            raise VpubError('another_comment_pinned','Another comment is pinned; this command will not replace it implicitly.')
    if identity != expected: raise VpubError('wrong_account', 'The signed-in account changed.')
    if operation == 'status': return observed
    if operation != 'set': raise VpubError('unsupported_operation', 'Choose status or set.')
    if observed['text'] != target['text']: raise VpubError('comment_changed', 'The comment changed since it was read.')
    if kind not in {'like','pin'} or type(observed.get('pinned' if kind=='pin' else 'liked')) is not bool:
        raise VpubError('state_unavailable', 'Exact comment like state is unavailable.')
    from .comment_actions import submitting
    stop_check(p); submitting(receipt_path)
    adapters.bili(['comment', kind, w, cid] + ([] if request['desired'] else ['--undo']))
    saved = read_json(receipt_path); saved.update(state='COMMENT_UNVERIFIED', platform_writes=1); atomic_json(receipt_path, saved)
    return {'state': 'COMMENT_UNVERIFIED', 'platform_writes': 1}

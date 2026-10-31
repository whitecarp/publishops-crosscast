"""Pinned bili-cli comment likes and YouTube own-comment editing/deletion."""
from . import adapters
from .core import VpubError, atomic_json, read_json, stop_check


def execute(request, operation, receipt_path=None):
    p = request['platform']; w = request['work_id']; target = request['comment']; cid = target['id']
    expected = request['identity']; kind = request.get('kind')
    if p == 'bilibili':
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
    else:
        service = adapters.yt_service('default')
        owners = service.channels().list(part='id', mine=True).execute().get('items', [])
        if len(owners) != 1: raise VpubError('channel_required', 'Expected one authorized channel.')
        identity = owners[0]['id']
        videos = service.videos().list(part='snippet', id=w).execute().get('items', [])
        if len(videos) != 1 or videos[0]['snippet']['channelId'] != identity:
            raise VpubError('wrong_work', 'The work does not belong to this channel.')
        rows = service.comments().list(part='snippet', id=cid, textFormat='plainText').execute().get('items', [])
        queue_root=None
        if not rows and kind=='moderate' and not target.get('parent_id'):
            from .youtube_moderation import root_in_queues
            queue_root=root_in_queues(service,w,cid)
            if queue_root:rows=[queue_root['comment']]
        if not rows:
            if identity != expected: raise VpubError('wrong_account', 'Authorized channel changed.')
            if not request.get('verification') or kind != 'delete':
                raise VpubError('comment_missing', 'The comment cannot be read; no write attempted.')
            return {'identity': identity, 'deleted': True, 'text': None, 'source': 'youtube_exact_comment_id_absent'}
        if len(rows) != 1 or rows[0]['id'] != cid: raise VpubError('wrong_comment', 'Unexpected comment ID.')
        s = rows[0]['snippet']; root = s.get('parentId') or cid
        threads = [queue_root['thread']] if queue_root else service.commentThreads().list(part='snippet', id=target.get('thread_id') or root).execute().get('items', [])
        if len(threads) != 1 or threads[0]['snippet'].get('videoId') != w or threads[0]['snippet']['topLevelComment']['id'] != root:
            raise VpubError('wrong_work', 'Exact comment thread does not belong to this work.')
        if kind != 'moderate' and s.get('authorChannelId', {}).get('value') != identity:
            raise VpubError('own_comment_required', 'Only your own comments can be edited/deleted.')
        observed = {'identity': identity, 'deleted': False, 'text': s.get('textOriginal',s.get('textDisplay')),
                    'moderation_status':queue_root['status'] if queue_root else s.get('moderationStatus'),
                    'source':'youtube_exact_owner_review_queue' if queue_root else 'youtube_comments_list'}
        if kind=='moderate' and not observed['moderation_status']:
            from .youtube_moderation import state
            observed['moderation_status']=state(service,w,cid,s.get('parentId'))
            observed['source']='youtube_exact_comment_and_bounded_owner_review_queue'
    if identity != expected: raise VpubError('wrong_account', 'The signed-in account changed.')
    if operation == 'status': return observed
    if operation != 'set': raise VpubError('unsupported_operation', 'Choose status or set.')
    if observed['text'] != target['text']: raise VpubError('comment_changed', 'The comment changed since it was read.')
    if p == 'bilibili' and (kind not in {'like','pin'} or type(observed.get('pinned' if kind=='pin' else 'liked')) is not bool):
        raise VpubError('state_unavailable', 'Exact comment like state is unavailable.')
    if p == 'youtube' and (kind not in {'edit', 'delete','moderate'} or not adapters.yt_credentials('default').has_scopes([adapters.PLAYLIST_SCOPE])):
        raise VpubError('oauth_scope_required', 'YouTube comment management requires youtube.force-ssl.')
    if kind=='moderate' and (request['desired'] not in {'published','heldForReview'} or observed.get('moderation_status') not in {'published','heldForReview'}):
        raise VpubError('state_unavailable','Only observed published/heldForReview states can be changed.')
    from .comment_actions import submitting
    stop_check(p); submitting(receipt_path)
    if p == 'bilibili':
        adapters.bili(['comment', kind, w, cid] + ([] if request['desired'] else ['--undo']))
    elif kind == 'edit':
        service.comments().update(part='snippet', body={'id': cid, 'snippet': {'textOriginal': request['desired']}}).execute()
    elif kind == 'moderate':
        service.comments().setModerationStatus(id=cid,moderationStatus=request['desired']).execute()
    else: service.comments().delete(id=cid).execute()
    saved = read_json(receipt_path); saved.update(state='COMMENT_UNVERIFIED', platform_writes=1); atomic_json(receipt_path, saved)
    return {'state': 'COMMENT_UNVERIFIED', 'platform_writes': 1}

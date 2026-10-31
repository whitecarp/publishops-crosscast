"""Read every API page with durable cursors, including full reply threads."""
import re
import uuid

from .core import VpubError, atomic_json, home, now, read_json, publication_lock, stop_check
from . import operations_api, operations_sync, operations as ops


def path(run_id):
    if not re.fullmatch(r'[a-f0-9]{32}', run_id or ''):
        raise VpubError('invalid_run', 'Use the returned read-all run ID.')
    return home() / 'operations/full-reads' / (run_id + '.json')


def save(run):
    run['updated_at'] = now()
    atomic_json(path(run['run_id']), run)


def status(run_id):
    return read_json(path(run_id))


def start(platform, resource, work=None, page_size=50, max_pages=100, collection=None):
    if resource not in {'works', 'comments', 'collections', 'collection-items'}:
        raise VpubError('unsupported_read', 'Use works, comments, collections or collection-items.')
    if type(page_size) is not int or not 1 <= page_size <= 50:
        raise VpubError('invalid_limit', 'Page size must be 1–50.')
    if platform == 'bilibili' and resource not in {'works', 'comments'}:
        raise VpubError('unsupported_read', 'This API route supports works and comments.')
    if platform not in {'youtube', 'bilibili'} and resource not in {'collections', 'collection-items'}:
        raise VpubError('unsupported_read', 'Complete browser pagination currently supports collections; other DOM feeds remain bounded.')
    if resource == 'comments':
        ops.work_id(platform, work)
    if resource == 'collection-items':
        if not collection:
            raise VpubError('collection_required', 'Use an exact collection ID.')
        if platform == 'youtube':
            from .playlists import identifier
            identifier(collection)
        else:
            from .collections import validate
            validate(platform, collection)
    if platform not in {'youtube','bilibili','douyin','instagram','x'}:
        raise VpubError('unsupported_read', 'Unsupported platform.')
    run = {'schema_version':1, 'run_id':uuid.uuid4().hex, 'platform':platform, 'resource':resource,
           'work':work, 'collection_id':collection, 'page_size':page_size, 'identity':None,
           'state':'READY', 'complete':False, 'items':[], 'pages_read':0, 'omitted_count':0,
           'started_at':now(), 'platform_writes':0,
           'tasks':[{'kind':resource, 'cursor':None, 'seen_cursors':[], 'done':False}]}
    budget(max_pages)
    save(run)
    return resume(run['run_id'], max_pages)


def budget(value):
    if type(value) is not int or not 1 <= value <= 1000:
        raise VpubError('invalid_limit', 'max-pages must be 1–1000 per invocation; resume retains the cursor.')


def resume(run_id, max_pages=100):
    budget(max_pages)
    with publication_lock(), operations_api.read_session():
        run = status(run_id)
        if run['state'] == 'COMPLETE':
            return {**run, 'reused':True}
        stop_check(run['platform'])
        if run['platform'] not in {'youtube','bilibili'}:
            from . import adapters
            try:
                result = adapters.browser_call(run['platform'], 'read_all', 'default', expected_identity=run['identity'],
                    resource=run['resource'], collection_id=run['collection_id'], max_pages=max_pages, read_run_id=run_id)
            except Exception as exc:
                run=status(run_id)
                run.update(state='INTERRUPTED', error=exc.code if isinstance(exc,VpubError) else type(exc).__name__)
                save(run)
                raise
            if run['identity'] and run['identity'] != result['identity']:
                raise VpubError('wrong_account', 'Read account changed.')
            run.update(result, state='COMPLETE' if result['complete'] else 'INCOMPLETE',
                       resume_mode='replay_official_page', platform_writes=0)
            save(run)
            return run
        run.update(state='READING', complete=False)
        save(run)
        used = 0
        try:
            while used < max_pages:
                pending = [t for t in run['tasks'] if not t['done']]
                if not pending:
                    break
                task = pending[0]
                stop_check(run['platform'])
                if task['kind'] == 'works':
                    page = operations_sync.works(run['platform'], run['page_size'], task['cursor'])
                elif task['kind'] in {'collections', 'collection-items'}:
                    from . import playlists
                    service, channel = operations_api.youtube_account()
                    args={'part':'snippet,status,contentDetails','maxResults':run['page_size']}
                    if task['cursor']:args['pageToken']=task['cursor']
                    if task['kind']=='collection-items':
                        playlists.owned(service,channel['id'],run['collection_id'])
                        data=service.playlistItems().list(**args,playlistId=run['collection_id']).execute()
                    else:data=service.playlists().list(**args,mine=True).execute()
                    page={'identity':channel['id'],'items':data.get('items',[]),'next_cursor':data.get('nextPageToken'),
                          'source':'youtube_playlists_api'}
                else:
                    kwargs = {'limit':run['page_size'], 'cursor':task['cursor']}
                    if task['kind'] == 'replies':
                        kwargs['comment_id'] = task['parent_id']
                    page = ops.backend(run['platform'], task['kind'], run['work'], **kwargs)
                commit_page(run, task, page)
                used += 1
                save(run)  # Items and next cursor advance in the same atomic write.
            pending = any(not t['done'] for t in run['tasks'])
            by_id = {x['id']:x for x in run['items']}
            missing_replies = False
            if run['resource'] == 'comments':
                for root in list(by_id.values()):
                    if root.get('parent_id'):
                        continue
                    children = [c for c in by_id.values() if c.get('parent_id') == root['id']]
                    missing_replies |= len(children) < (root.get('reply_count') or 0)
                    root['owner_replied'] = True if any(c.get('is_owner') for c in children) else False if not pending and not missing_replies else None
                run['items'] = list(by_id.values())
            complete = not pending and not missing_replies and not run['omitted_count']
            run.update(complete=complete, state='COMPLETE' if complete else 'PAUSED' if pending else 'INCOMPLETE',
                       scope='Pages observed during this run; live platform data is not a frozen historical snapshot.')
            save(run)
            return run
        except Exception as exc:
            run.update(state='INTERRUPTED', error=exc.code if isinstance(exc,VpubError) else type(exc).__name__)
            save(run)
            raise


def commit_page(run, task, page):
    owner = page.get('identity')
    if not owner or (run['identity'] and owner != run['identity']):
        raise VpubError('wrong_account', 'Page account changed; checkpoint not advanced.')
    cursor = page.get('next_cursor')
    if cursor is not None and (not isinstance(cursor,str) or not cursor or cursor == task['cursor'] or cursor in task['seen_cursors']):
        raise VpubError('cursor_loop', 'Invalid/repeated cursor; checkpoint not advanced.')
    items = page.get('items')
    if not isinstance(items,list) or any(not isinstance(x.get('id'),str) or not x['id'] for x in items):
        raise VpubError('invalid_page', 'Page lacks stable item IDs.')
    if task['kind'] == 'replies' and any(x['id'] != task['parent_id'] and x.get('parent_id') != task['parent_id'] for x in items):
        raise VpubError('wrong_thread', 'A reply page belongs to another thread.')
    rows = {x['id']:x for x in run['items']}
    for item in items:
        rows[item['id']] = {**rows.get(item['id'],{}), **item}
    # Always enumerate reply threads, rather than treating a preview as an export.
    if task['kind'] == 'comments':
        queued = {t.get('parent_id') for t in run['tasks']}
        for item in items:
            if not item.get('parent_id') and item.get('reply_count',0) and item['id'] not in queued:
                run['tasks'].append({'kind':'replies', 'parent_id':item['id'], 'cursor':None, 'seen_cursors':[], 'done':False})
                queued.add(item['id'])
    if task['cursor']:
        task['seen_cursors'].append(task['cursor'])
    task.update(cursor=cursor, done=cursor is None)
    run.update(identity=owner, items=list(rows.values()), pages_read=run['pages_read']+1,
               omitted_count=run['omitted_count']+page.get('omitted_count',0), source=page.get('source'))

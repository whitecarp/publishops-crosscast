"""Bili CLI and YouTube Data API operations. No automatic translation or likes."""
from pathlib import Path
import re
from . import adapters
from .core import VpubError, atomic_json, read_json, stop_check

from contextlib import contextmanager
from threading import local

_READ = local()

@contextmanager
def read_session():
    previous=getattr(_READ,'cache',None)
    _READ.cache={}
    try:yield
    finally:
        service=_READ.cache.get('service')
        if service is not None and hasattr(service,'close'):service.close()
        _READ.cache=previous


def youtube_context(action,work):
    cache=getattr(_READ,'cache',None) if action in {'comments','stats','replies'} else None
    cache=cache if cache is not None else {}
    if 'service' not in cache:
        service=adapters.yt_service('default')
        owners=service.channels().list(part='id',mine=True).execute().get('items',[])
        if len(owners)!=1:raise VpubError('channel_required','Expected one authorized YouTube channel.')
        cache.update(service=service,identity=owners[0]['id'],works={})
    service,identity=cache['service'],cache['identity']
    if work not in cache['works']:
        works=service.videos().list(part='snippet,statistics,status',id=work).execute().get('items',[])
        if len(works)!=1 or works[0]['snippet']['channelId']!=identity:
            raise VpubError('wrong_work','This work does not belong to the signed-in YouTube channel.')
        cache['works'][work]=works[0]
    return service,identity,cache['works'][work]


def youtube_account():
    cache = getattr(_READ, 'cache', None)
    if cache is None:
        cache = {}
    if 'service' not in cache:
        service = adapters.yt_service('default')
        owners = service.channels().list(part='id,contentDetails',mine=True).execute().get('items',[])
        if len(owners) != 1:
            raise VpubError('channel_required','Expected one authorized YouTube channel.')
        cache.update(service=service,identity=owners[0]['id'],works={},channel=owners[0])
    if 'channel' not in cache:
        owners=cache['service'].channels().list(part='id,contentDetails',mine=True).execute().get('items',[])
        if len(owners)!=1 or owners[0]['id']!=cache['identity']:
            raise VpubError('wrong_account','YouTube account changed during pagination.')
        cache['channel']=owners[0]
    return cache['service'],cache['channel']


def metric(value, label, unit='count'):
    return {'value':value,'label':label,'unit':unit,'availability':'observed' if value is not None else 'unavailable'}


def _yt_comment(c, owner, parent=None):
    s=c['snippet'];author=s.get('authorChannelId',{}).get('value')
    return {'id':c['id'],'parent_id':s.get('parentId') or parent,'text':s.get('textOriginal',s.get('textDisplay','')),
            'author':s.get('authorDisplayName'),'author_id':author,'published_at':s.get('publishedAt'),
            'likes':s.get('likeCount'),'is_owner':author==owner,'owner_replied':None,'moderation_status':s.get('moderationStatus')}


def _bili_comment(c, owner):
    return {'id':str(c['rpid']),'parent_id':str(c['root']) if c.get('root') else None,
            'text':c['content'],'author':c['user'].get('name'),'author_id':str(c['user']['mid']),
            'published_at':c.get('time'),'likes':c.get('like'),'is_owner':str(c['user']['mid'])==owner,
            'owner_replied':any(str(r['user']['mid'])==owner for r in c.get('replies_preview',[])) or None,
            'thread_id':str(c.get('root') or c['rpid']),'reply_count':c.get('reply_count')}


def execute(platform, action, work, **kw):
    if platform=='bilibili': return bilibili(action,work,**kw)
    service,identity,video=youtube_context(action,work)
    if action in {'test_seed','test_seed_verify'}:
        test=kw['test_request']
        if identity!=test['identity']: raise VpubError('wrong_account','Test account changed.')
        saved=dict(test);path=Path(kw['test_receipt_path'])
        if action=='test_seed':
            if not adapters.yt_credentials('default').has_scopes([adapters.PLAYLIST_SCOPE]):
                raise VpubError('oauth_scope_required','Test comments require youtube.force-ssl.')
            stop_check('youtube')
            saved['state']='TEST_SEED_SUBMITTING';atomic_json(path,saved)
            data=service.commentThreads().insert(part='snippet',body={'snippet':{'videoId':work,
                'topLevelComment':{'snippet':{'textOriginal':'你好'}}}}).execute()
            saved['comment_id']=data['snippet']['topLevelComment']['id'];saved['thread_id']=data['id'];atomic_json(path,saved)
        args={'part':'snippet','textFormat':'plainText'}
        args.update({'id':saved.get('thread_id') or saved['comment_id']} if saved.get('comment_id') else
                    {'videoId':work,'maxResults':100,'order':'time'})
        threads=service.commentThreads().list(**args).execute().get('items',[])
        comments=[t['snippet']['topLevelComment'] for t in threads if t['snippet'].get('videoId')==work]
        matches=[c for c in comments if c['id'] not in test['before_ids'] and c['snippet'].get('textOriginal')=='你好'
                 and c['snippet'].get('authorChannelId',{}).get('value')==identity and not c['snippet'].get('parentId')]
        return {'state':'TEST_SEED_VERIFIED' if len(matches)==1 else 'TEST_SEED_UNVERIFIED',
                'comment_id':matches[0]['id'] if len(matches)==1 else saved.get('comment_id')}
    if action=='stats':
        s=video.get('statistics',{})
        def count(key): return int(s[key]) if key in s else None
        return {'state':'OBSERVED','identity':identity,'title':video['snippet']['title'],'source':'youtube_data_api',
                'metrics':{'views':metric(count('viewCount'),'YouTube viewCount'),
                           'likes':metric(count('likeCount'),'YouTube likeCount'),
                           'comments':metric(count('commentCount'),'YouTube commentCount'),
                           'completion_percent':metric(None,'Not supplied by YouTube Data API','percent'),
                           'average_watch_seconds':metric(None,'Requires separate YouTube Analytics access','seconds')},
                'url':'https://www.youtube.com/watch?v='+work}
    if action=='replies':
        root=kw['comment_id']
        threads=service.commentThreads().list(part='snippet',id=root,textFormat='plainText').execute().get('items',[])
        if len(threads)!=1 or threads[0]['snippet'].get('videoId')!=work or threads[0]['snippet']['topLevelComment']['id']!=root:
            raise VpubError('wrong_work','The root comment does not belong to the requested video.')
        args={'part':'snippet','parentId':root,'maxResults':kw['limit'],'textFormat':'plainText'}
        if kw.get('cursor'):args['pageToken']=kw['cursor']
        data=service.comments().list(**args).execute()
        children=[_yt_comment(c,identity,root) for c in data.get('items',[])]
        if any(c['parent_id']!=root for c in children):raise VpubError('wrong_thread','Reply parent mismatch.')
        top=_yt_comment(threads[0]['snippet']['topLevelComment'],identity)
        top['owner_replied']=True if any(c['is_owner'] for c in children) else False if not kw.get('cursor') and not data.get('nextPageToken') else None
        rows=[top,*children]
        for c in rows:c['thread_id']=threads[0]['id']
        return {'state':'OBSERVED','identity':identity,'items':rows,'root_id':root,'next_cursor':data.get('nextPageToken'),
                'complete':not kw.get('cursor') and not data.get('nextPageToken'),'source':'youtube_comments_api',
                'scope':'One page of replies to this root, with its root comment; not other video threads.'}
    if action=='comments':
        args={'part':'snippet,replies','videoId':work,'maxResults':kw['limit'],'order':'time','textFormat':'plainText'}
        if kw.get('moderation_status'):
            if kw['moderation_status'] not in {'heldForReview','published'}:raise VpubError('invalid_moderation','Unknown review filter.')
            args['moderationStatus']=kw['moderation_status']
        if kw.get('cursor'): args['pageToken']=kw['cursor']
        try: data=service.commentThreads().list(**args).execute()
        except Exception as exc:
            # Never turn disabled comments or denied permission into an empty inbox.
            if getattr(getattr(exc,'resp',None),'status',None) in {403,404}:
                raise VpubError('comments_unavailable','Comments are disabled, inaccessible or the request lacks permission.') from None
            raise
        rows=[]
        for thread in data.get('items',[]):
            s=thread['snippet'];top=_yt_comment(s['topLevelComment'],identity)
            children=[_yt_comment(c,identity,top['id']) for c in thread.get('replies',{}).get('comments',[])]
            for row in [top,*children]: row['thread_id']=thread.get('id',top['id'])
            top['reply_count']=s.get('totalReplyCount')
            top['owner_replied']=True if any(c['is_owner'] for c in children) else False if len(children)==s.get('totalReplyCount',0) else None
            rows.extend([top,*children])
        return {'state':'OBSERVED','identity':identity,'items':rows,'next_cursor':data.get('nextPageToken'),
                'scope':'One API page of top-level threads, with the API-provided reply preview; not a full reply export.',
                'complete':not data.get('nextPageToken') and all(t['snippet'].get('totalReplyCount',0)==len(t.get('replies',{}).get('comments',[])) for t in data.get('items',[])),
                'replies_complete':all(t['snippet'].get('totalReplyCount',0)==len(t.get('replies',{}).get('comments',[])) for t in data.get('items',[])),
                'source':'youtube_data_api','moderation_filter':kw.get('moderation_status','published')}
    request=kw['reply_request'];target=request['comment']
    if identity!=request['expected_identity']: raise VpubError('wrong_account','The authorized channel changed since the comment was read.')
    matches=service.comments().list(part='snippet',id=target['id'],textFormat='plainText').execute().get('items',[])
    if len(matches)!=1 or _yt_comment(matches[0],identity)['text']!=target['text']:
        raise VpubError('comment_changed','The target comment changed, disappeared, or belongs to another work.')
    root=matches[0]['snippet'].get('parentId') or target['id']
    # Comment resources need not expose videoId. Thread resources establish
    # membership; channelId alone cannot distinguish two works by the same owner.
    threads=service.commentThreads().list(part='snippet',id=target.get('thread_id') or root,textFormat='plainText').execute().get('items',[])
    if len(threads)!=1 or threads[0]['snippet'].get('videoId')!=work or threads[0]['snippet']['topLevelComment']['id']!=root:
        raise VpubError('wrong_work','The comment thread does not belong to the requested video.')
    if action=='verify_reply':
        rid=kw.get('existing_reply',{}).get('reply_id')
        args={'part':'snippet','textFormat':'plainText'}
        args.update({'id':rid} if rid else {'parentId':root,'maxResults':100})
        replies=service.comments().list(**args).execute().get('items',[])
        found=[c for c in replies if c['snippet'].get('parentId')==root and
               c['snippet'].get('authorChannelId',{}).get('value')==identity and
               c['snippet'].get('textOriginal')==request['text']]
        return {'state':'REPLY_VERIFIED' if len(found)==1 else 'REPLY_UNVERIFIED',
                'reply_id':found[0]['id'] if len(found)==1 else rid,'platform_writes':0,'verification_source':'youtube_comments_list'}
    if action!='reply': raise VpubError('unsupported_operation','Unsupported YouTube operation.')
    creds=adapters.yt_credentials('default')
    if not creds.has_scopes([adapters.PLAYLIST_SCOPE]):
        raise VpubError('oauth_scope_required','Comment replies require youtube.force-ssl; reauthorize the initial management permissions.')
    # Observe the current thread again before sending, not only a stale snapshot.
    reply_page=service.comments().list(part='snippet',parentId=root,maxResults=100,textFormat='plainText').execute()
    replies=reply_page.get('items',[])
    if any(c['snippet'].get('authorChannelId',{}).get('value')==identity for c in replies):
        raise VpubError('already_replied','An owner reply already exists in the current thread.')
    if reply_page.get('nextPageToken'):
        raise VpubError('thread_too_large','This thread has more replies than the bounded pre-send duplicate check can inspect.')
    path=Path(kw['reply_receipt_path']);saved=read_json(path)
    stop_check('youtube');saved['state']='REPLY_SUBMITTING';atomic_json(path,saved)
    posted=service.comments().insert(part='snippet',body={'snippet':{'parentId':root,'textOriginal':request['text']}}).execute()
    saved.update(state='REPLY_UNVERIFIED',reply_id=posted.get('id'));atomic_json(path,saved)
    verified=execute('youtube','verify_reply',work,**{**kw,'existing_reply':saved})
    return {**verified,'platform_writes':1}


def bilibili(action,work,**kw):
    identity=adapters.auth_status('bilibili','default','cli')['identity']
    info=adapters.bili(['video','info',work])
    if str(info.get('owner',{}).get('mid'))!=identity:
        raise VpubError('wrong_work','Operations target your own Bilibili work.')
    if action in {'test_seed','test_seed_verify'}:
        test=kw['test_request'];saved=dict(test);path=Path(kw['test_receipt_path'])
        if identity!=test['identity']: raise VpubError('wrong_account','Test account changed.')
        if action=='test_seed':
            stop_check('bilibili')
            saved['state']='TEST_SEED_SUBMITTING';atomic_json(path,saved)
            posted=adapters.bili(['comment','post',work,'你好'])
            saved['comment_id']=str(posted['rpid']);atomic_json(path,saved)
        comments=adapters.bili(['comment','list',work,'--sort','time','--page','1']).get('items',[])
        matches=[c for c in comments if str(c['rpid']) not in test['before_ids'] and c.get('content')=='你好'
                 and str(c.get('user',{}).get('mid'))==identity and not c.get('root')
                 and (not saved.get('comment_id') or str(c['rpid'])==saved['comment_id'])]
        return {'state':'TEST_SEED_VERIFIED' if len(matches)==1 else 'TEST_SEED_UNVERIFIED',
                'comment_id':str(matches[0]['rpid']) if len(matches)==1 else saved.get('comment_id')}
    if action=='stats':
        d=adapters.bili(['upload','stats',work]);s=d.get('stat',{})
        return {'state':'OBSERVED','identity':identity,'title':info['title'],'source':'bili_cli_creator_stats',
                'url':info.get('url'),'metrics':{**{name:metric(s.get(key),'Bilibili '+key) for name,key in
                 [('views','view'),('likes','like'),('comments','reply'),('shares','share'),('favorites','favorite'),('coins','coin'),('danmaku','danmaku')]},
                 'completion_percent':metric(d.get('completion_percent'),'Creator completion percentage','percent'),
                 'average_watch_seconds':metric(d.get('avg_watch_seconds'),'Creator average watch duration','seconds')},
                'note':'Creator completion/watch metrics may update on the following day; observations are not real-time guarantees.'}
    if action=='replies':
        root=kw['comment_id'];cursor=kw.get('cursor') or '1'
        if not root.isdigit() or not re.fullmatch(r'[1-9][0-9]{0,7}',cursor):raise VpubError('invalid_cursor','Use numeric Bilibili root and page IDs.')
        page=int(cursor);size=min(20,kw['limit'])
        data=adapters.bili(['comment','replies',work,root,'--page',str(page),'--size',str(size)])
        if str(data.get('root',{}).get('rpid'))!=root:raise VpubError('wrong_thread','Root comment mismatch.')
        children=[_bili_comment(c,identity) for c in data.get('items',[])]
        if any(c.get('parent_id')!=root for c in children):raise VpubError('wrong_thread','Reply root mismatch.')
        total=data.get('total');more=page*size<total if isinstance(total,int) else len(children)==size
        top=_bili_comment(data['root'],identity)
        top['owner_replied']=True if any(c['is_owner'] for c in children) else False if page==1 and not more else None
        return {'state':'OBSERVED','identity':identity,'items':[top,*children],'root_id':root,'next_cursor':str(page+1) if more else None,
                'complete':page==1 and not more,'source':'bili_cli_replies','reported_total':total,
                'scope':'One upstream reply page plus the root; not other work threads.'}
    if action=='comments':
        cursor=kw.get('cursor') or '1'
        match=re.fullmatch(r'([1-9][0-9]*)(?::([0-9]+))?',str(cursor))
        if not match: raise VpubError('invalid_cursor','Bilibili cursor is page or page:offset.')
        page,offset=int(match[1]),int(match[2] or 0)
        data=adapters.bili(['comment','list',work,'--sort','time','--page',str(page)])
        page_items=data.get('items',[])
        if offset>len(page_items): raise VpubError('page_changed','Refresh this comment page; its offsets changed.')
        rows=[]
        selected=page_items[offset:offset+kw['limit']]
        for item in selected:
            rows.append(_bili_comment(item,identity))
            rows.extend(_bili_comment(c,identity) for c in item.get('replies_preview',[]))
        return {'state':'OBSERVED','identity':identity,'source':'bili_cli','items':rows,
                'next_cursor':f'{page}:{offset+len(selected)}' if offset+len(selected)<len(page_items) else str(page+1) if page_items else None,
                'complete':False,'scope':'One upstream page with reply previews; the displayed total may include replies.',
                'replies_complete':False,
                'reported_total':data.get('total')}
    request=kw['reply_request'];target=request['comment'];root=target.get('parent_id') or target['id']
    if identity!=request['expected_identity']: raise VpubError('wrong_account','Bilibili account changed since reading.')
    data=adapters.bili(['comment','replies',work,root,'--size','20'])
    candidates=[data.get('root',{}),*data.get('items',[])]
    actual=[c for c in candidates if str(c.get('rpid'))==target['id'] and c.get('content')==target['text']]
    if not actual: raise VpubError('comment_changed','Target comment is missing or changed; read its current page.')
    rid=kw.get('existing_reply',{}).get('reply_id')
    found=[c for c in data.get('items',[]) if str(c.get('user',{}).get('mid'))==identity and c.get('content')==request['text']
           and str(c.get('parent'))==target['id'] and (not rid or str(c.get('rpid'))==rid)]
    if action=='verify_reply':
        return {'state':'REPLY_VERIFIED' if len(found)==1 else 'REPLY_UNVERIFIED','platform_writes':0,
                'reply_id':str(found[0]['rpid']) if len(found)==1 else kw.get('existing_reply',{}).get('reply_id'),
                'verification_source':'bili_cli_comment_replies'}
    if found: raise VpubError('already_replied','An identical owner reply already exists.')
    path=Path(kw['reply_receipt_path']);text_path=path.with_suffix('.txt');text_path.write_text(request['text'],encoding='utf-8')
    stop_check('bilibili')
    saved=read_json(path);saved['state']='REPLY_SUBMITTING';atomic_json(path,saved)
    args=['comment','reply',work,target['id'],'--file',str(text_path)]
    if target.get('parent_id'): args+=['--root',root]
    posted=adapters.bili(args)
    saved.update(state='REPLY_UNVERIFIED',reply_id=str(posted.get('rpid','')) or None);atomic_json(path,saved)
    return {**bilibili('verify_reply',work,**{**kw,'existing_reply':saved}),'platform_writes':1}

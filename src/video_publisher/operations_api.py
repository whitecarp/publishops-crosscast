"""Bili CLI operations and the shared read session. No automatic translation or likes."""
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


def metric(value, label, unit='count'):
    return {'value':value,'label':label,'unit':unit,'availability':'observed' if value is not None else 'unavailable'}


def _bili_comment(c, owner):
    return {'id':str(c['rpid']),'parent_id':str(c['root']) if c.get('root') else None,
            'text':c['content'],'author':c['user'].get('name'),'author_id':str(c['user']['mid']),
            'published_at':c.get('time'),'likes':c.get('like'),'is_owner':str(c['user']['mid'])==owner,
            'owner_replied':any(str(r['user']['mid'])==owner for r in c.get('replies_preview',[])) or None,
            'thread_id':str(c.get('root') or c['rpid']),'reply_count':c.get('reply_count')}


def bilibili_source(kind, identity, limit):
    """Bilibili @ mentions or conversation previews for the local message inbox. Read-only."""
    if kind == 'mentions':
        data=adapters.bili(['feed','mentions','--limit',str(limit)])
        items=[{'id':str(r['id']),'text':r.get('content') or '','url':(r.get('on') or {}).get('url'),
                'author':(r.get('user') or {}).get('name'),'is_owner':str((r.get('user') or {}).get('mid'))==identity}
               for r in data.get('items',[]) if r.get('id') is not None]
        return {'identity':identity,'items':items,'complete':not data.get('next_cursor'),'source':'bili_cli_feed_mentions',
                'scope':'Latest @ mentions; not the full notification history.'}
    items=[]
    for r in adapters.bili(['dm','list','--limit',str(limit)]).get('items',[]):
        last=r.get('last_message') or {}
        # A Bilibili auto-reply is not an answer: the other person is still waiting for one.
        items.append({'conversation_id':str(r['uid']),'name':r.get('name'),
                      'preview':'\n'.join(str(part) for part in (r.get('name'),last.get('text')) if part),
                      'is_owner':last.get('from')=='me' and not last.get('auto_reply')})
    return {'identity':identity,'items':items,'complete':False,'source':'bili_cli_dm_list',
            'scope':'Most recent conversations; previews, not incoming-message counts.'}


def execute(platform, action, work, **kw):
    if platform=='bilibili': return bilibili(action,work,**kw)
    return adapters.youtube_api().operations(action,work,**kw)


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

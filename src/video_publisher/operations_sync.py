"""Read-only multi-platform watchlists and bounded collection; never send replies."""
import math
import re

from . import adapters, operations as ops
from .core import VpubError, atomic_json, home, now, platform_check, publication_lock, read_json, stop_check


def metric_changes(previous, current):
    changes={}
    for name, metric in current.get('metrics',{}).items():
        old=(previous or {}).get('metrics',{}).get(name,{})
        value,before=metric.get('value'),old.get('value')
        numeric=lambda v:isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
        comparable=bool(previous and previous.get('identity')==current.get('identity')
            and previous.get('source')==current.get('source') and previous.get('work_id')==current.get('work_id')
            and metric.get('unit')==old.get('unit') and metric.get('label')==old.get('label')
            and numeric(value) and numeric(before) and not metric.get('approximate') and not old.get('approximate'))
        changes[name]={'previous':before,'current':value,'delta':value-before if comparable else None,
                       'unit':'percentage_points' if metric.get('unit')=='percent' else metric.get('unit'),
                       'comparable':comparable}
    return changes


def works(platform, limit=20, cursor=None):
    platform_check(platform);stop_check(platform)
    if not 1<=limit<=50: raise VpubError('invalid_limit','Work limit must be 1–50.')
    if cursor and (not isinstance(cursor,str) or len(cursor)>1000): raise VpubError('invalid_cursor','Invalid cursor.')
    if platform=='youtube':
        result=adapters.youtube_api().works(limit,cursor)
    elif platform=='bilibili':
        if cursor and not re.fullmatch(r'[1-9][0-9]{0,7}',cursor): raise VpubError('invalid_cursor','Use a positive page number.')
        page=int(cursor or 1);owner=adapters.auth_status('bilibili','default','cli')['identity']
        data=adapters.bili(['upload','list','--status','published','--page',str(page),'--size',str(limit)])
        items=[{'id':v['bvid'],'title':v['title'],'url':'https://www.bilibili.com/video/'+v['bvid'],
                'published_at':v.get('publish_time'),'status':v.get('state_desc')} for v in data.get('items',[]) if v.get('published')]
        total=data.get('counts',{}).get('published')
        more=page*limit<total if isinstance(total,int) else len(data.get('items',[]))==limit
        result={'identity':owner,'items':items,'next_cursor':str(page+1) if more else None,'complete':page==1 and not more,
                'source':'bili_cli_creator_list','scope':'Published works only; scheduled/review works are excluded.','reported_total':total}
    else:
        if cursor: raise VpubError('unsupported_cursor','This browser work list exposes the visible first page only.')
        result=adapters.browser_call(platform,'works','default')
        result.update(items=result['items'][:limit],next_cursor=None,complete=False,source='cli_browser_visible_works')
    return {**result,'platform':platform,'observed_at':now(),'platform_writes':0}


def watch(action, platform=None, work=None):
    path=home()/'operations/watchlist.json'
    if action=='list': return read_json(path) if path.exists() else {'schema_version':1,'items':[]}
    work=ops.work_id(platform,work)
    observed=None
    if action=='add':
        # API owner checks or the existing browser publication receipt bind the account.
        observed=ops.observe(platform,work,'stats')
    elif action!='remove': raise VpubError('invalid_operation','Choose add, remove or list.')
    with publication_lock():
        data=watch('list')
        items=[x for x in data['items'] if (x['platform'],x['work_id'])!=(platform,work)]
        if observed:
            items.append({'platform':platform,'work_id':work,'identity':observed['identity'],
                          'added_at':now(),'title':observed.get('title'),'url':observed.get('url')})
        data={'schema_version':1,'items':items};atomic_json(path,data)
    return {**data,'platform_writes':0}


def collect(platform, work, identity, limit, pages, reply_pages=0, reply_threads=5):
    cursor=None;seen_cursors=set();rows={};first=None;last=None;complete=True
    for index in range(pages):
        stop_check(platform)
        page=ops.backend(platform,'comments',work,limit=limit,cursor=cursor)
        if page.get('identity')!=identity: raise VpubError('wrong_account','The watched account changed. No snapshot was advanced.')
        if first is None:first=page
        for c in page.get('items',[]):rows[c['id']]=c
        # API thread previews can be partial even when top-level pagination ends.
        complete=complete and page.get('replies_complete',page.get('complete',False) or bool(page.get('next_cursor')))
        last=page;cursor=page.get('next_cursor')
        if not cursor:break
        if cursor in seen_cursors: raise VpubError('cursor_loop','Backend repeated a page cursor; collection stopped.')
        seen_cursors.add(cursor)
    result={**first,'items':list(rows.values()),'next_cursor':cursor,'pages_read':index+1,
            'complete':bool(platform in {'bilibili','youtube'} and complete and not cursor and last.get('complete')),
            'bounded':True,'page_budget':pages}
    expanded=0;continuations=[]
    if platform in {'bilibili','youtube'} and reply_pages:
        for root in list(rows.values()):
            count=root.get('reply_count')
            children=[c for c in rows.values() if c.get('parent_id')==root['id']]
            if root.get('parent_id') or not isinstance(count,int) or count<=len(children):continue
            if expanded>=reply_threads:
                continuations.append({'comment_id':root['id'],'next_cursor':None,'reason':'thread_budget'})
                continue
            expanded+=1;rc=None;seen=set();complete_replies=False
            for _ in range(reply_pages):
                page=ops.backend(platform,'replies',work,comment_id=root['id'],limit=limit,cursor=rc)
                if page.get('identity')!=identity:raise VpubError('wrong_account','Reply-page account changed.')
                for c in page['items']:
                    if c['id']==root['id']:
                        rows[c['id']]={**root,**c}
                    elif c.get('parent_id')==root['id']:rows[c['id']]=c
                    else:raise VpubError('wrong_thread','Reply page contains another thread.')
                rc=page.get('next_cursor')
                if not rc:
                    complete_replies=True;break
                if rc in seen:raise VpubError('cursor_loop','Reply page cursor repeated.')
                seen.add(rc)
            children=[c for c in rows.values() if c.get('parent_id')==root['id']]
            rows[root['id']]['owner_replied']=True if any(c.get('is_owner') for c in children) else False if complete_replies else None
            if rc:continuations.append({'comment_id':root['id'],'next_cursor':rc,'reason':'page_budget'})
        result['items']=list(rows.values())
    result.update(reply_threads_expanded=expanded,reply_continuations=continuations)
    return ops.save_observation(platform,work,'comments',result)

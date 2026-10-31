"""Durable read-only jobs, account-scoped freshness and API page checkpoints."""
from contextlib import contextmanager
from datetime import datetime, timezone
import os
import re
import uuid

from . import adapters, operations as ops
from .core import VpubError, atomic_json, digest, home, now, platform_check, read_json, stop_check


@contextmanager
def run_lock():
    """Kernel lock is released on process death; a leftover file is not a busy owner."""
    path=home()/'operations/sync.lock';path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0,2)
        if f.tell()==0: f.write(b'0'); f.flush()
        f.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError: raise VpubError('ops_busy','Another read-only sync is active; resume after it finishes.') from None
        try: yield
        finally:
            f.seek(0)
            if os.name=='nt': msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else: fcntl.flock(f.fileno(),fcntl.LOCK_UN)


def path_for(run_id):
    if not isinstance(run_id,str) or not re.fullmatch(r'[a-f0-9]{32}',run_id):
        raise VpubError('invalid_run','Use the exact run_id returned by ops sync.')
    return home()/'operations/sync-runs'/(run_id+'.json')


def status(run_id):
    path=path_for(run_id)
    if not path.exists():raise VpubError('run_missing','No saved sync with this run_id.')
    from .read_scheduler import MERGE_LOCK
    with MERGE_LOCK:return read_json(path)


def listing(limit=20):
    if type(limit) is not int or not 1<=limit<=100:
        raise VpubError('invalid_limit','Use limit 1–100.')
    paths=sorted((home()/'operations/sync-runs').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)
    rows=[]
    for path in paths:
        run=read_json(path)
        if run.get('parent_run_id'):continue
        if len(rows)>=limit:break
        rows.append({'run_id':run['run_id'],'state':run['state'],'started_at':run['started_at'],
                     'updated_at':run['updated_at'],'jobs':len(run['jobs']),
                     'finished':sum(j['state'] in {'DONE','CACHED'} for j in run['jobs']),
                     'resume_command':'vpub ops resume '+run['run_id']})
    return {'items':rows,'platform_writes':0,
            'scope':'Local saved runs. RUNNING may be an interrupted process; resume acquires the kernel lock before proceeding.'}


def save(run):
    from .read_scheduler import MERGE_LOCK
    with MERGE_LOCK:
        run['updated_at']=now();atomic_json(path_for(run['run_id']),run)


def cache_path(job, options):
    return home()/'operations/sync-cache'/(digest([job['platform'],job['identity'],job['work_id'],job['operation'],
        {k:options[k] for k in ('limit','pages','reply_pages','reply_threads')}])+'.json')


def make_run(platforms,limit,pages,language,reply_pages,reply_threads,min_interval,full,workers=3,sources=None):
    from .operations_sync import watch
    from .comment_collection import budgets
    budgets(limit,pages,reply_pages)
    if type(reply_threads) is not int or not 0<=reply_threads<=20 or type(min_interval) is not int or not 0<=min_interval<=86400:
        raise VpubError('invalid_limit','Use reply-threads 0–20 and min-interval 0–86400 seconds.')
    selected=platforms.split(',') if platforms else list(ops.NAMES)
    for p in selected:platform_check(p)
    targets=[t for t in watch('list')['items'] if t['platform'] in selected]
    if not targets and sources is None:raise VpubError('watchlist_empty','Adopt or watch a work before syncing.')
    if type(workers) is not int or not 1<=workers<=4:
        raise VpubError('invalid_workers','Use 1–4 platform workers.')
    kinds = sources.split(',') if sources is not None else ['comments','stats']
    if not kinds or any(k not in {'comments','stats','mentions','dm_preview'} for k in kinds):
        raise VpubError('invalid_sources','Choose comments, mentions and/or dm_preview.')
    options=dict(workers=workers, sources=sources, limit=limit,pages=pages,language=language,reply_pages=reply_pages,reply_threads=reply_threads,
                 min_interval=min_interval,full=full)
    jobs=[]; source_status=[]
    for t in targets:
        for action in [k for k in kinds if k in {'comments','stats'}]:
            job={**t,'operation':action,'state':'PENDING','job_id':digest([t['platform'],t['identity'],t['work_id'],action])}
            jobs.append(job)
    for p in selected:
        for kind in dict.fromkeys(k for k in kinds if k in {'mentions','dm_preview'}):
            if p not in {'bilibili','douyin','instagram','x'}:
                source_status.append({'platform':p,'kind':kind,'state':'UNSUPPORTED'})
                continue
            from .direct_messages import identity
            try: owner=identity(p)
            except VpubError as exc:
                source_status.append({'platform':p,'kind':kind,'state':'FAILED','code':exc.code})
                continue
            jobs.append({'platform':p,'identity':owner,'work_id':'@account','operation':kind,
                         'state':'PENDING','job_id':digest([p,owner,'@account',kind])})
        if sources is not None and 'comments' in kinds and not any(t['platform']==p for t in targets):
            source_status.append({'platform':p,'kind':'comments','state':'NOT_CONFIGURED'})
    for job in jobs:
        cp=cache_path(job,options)
        if not full and min_interval and cp.exists():
            cached=read_json(cp)
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(cached['checked_at'].replace('Z','+00:00'))).total_seconds()
            if 0<=age<min_interval:
                job.update(state='CACHED',result=cached['result'],checked_at=cached['checked_at'],cache_age_seconds=round(age,2))
    run={'schema_version':1,'run_id':uuid.uuid4().hex,'state':'RUNNING','started_at':now(),'options':options,
         'source_status':source_status,'jobs':jobs,'platform_writes':0,'browser_sessions':0,'events':[], 'batches':[],
         'scope':'Watched works, bounded comments and metrics. Cached items are not fresh platform reads. No platform writes.'}
    save(run);return run


def changes_for(job,value):
    if job['operation']!='comments':return []
    from .operations_inbox import find_item
    rows=[]
    for c in value.get('items',[]):
        if c.get('is_owner'):continue
        old=find_item(job['platform'],job['identity'],job['work_id'],c['id'])
        changed=bool(old and any(old['comment'].get(k)!=c.get(k) for k in ('text','author_id','parent_id')))
        if old is None or changed:
            rows.append({'comment_id':c['id'],'change':'newly_observed' if old is None else 'changed',
                         'key':digest([job['platform'],job['identity'],job['work_id'],c['id']])})
    return rows


def finish(run,job,value):
    from .read_scheduler import MERGE_LOCK
    with MERGE_LOCK:
        return _finish(run,job,value)


def _finish(run,job,value):
    if value.get('identity')!=job['identity']:
        raise VpubError('wrong_account','Read account differs from the persisted job; checkpoint not advanced.')
    # Write the diff before merging. Recovery retains it even if interrupted after the merge.
    if 'changes' not in job:
        job['changes']=changes_for(job,value);job['raw_result']=value;save(run)
    if job['operation'] in {'mentions','dm_preview'}:
        from .messages import merge
        summary=merge(job['platform'],job['operation'],value,observation_id=run['run_id']+job['job_id'])
        value={**value,'summary':summary,'observed_at':summary['observed_at']}
    else:
        value=ops.save_observation(job['platform'],job['work_id'],job['operation'],value)
    job.update(state='DONE',result=value,checked_at=value['observed_at'])
    job.pop('error',None);job.pop('raw_result',None)
    run['events'].append({'platform':job['platform'],'work_id':job['work_id'],'action':job['operation'],
                          'at':value['observed_at'],'result':value})
    save(run)
    atomic_json(cache_path(job,run['options']),{'checked_at':value['observed_at'],'result':value})


def api_comments(run,job):
    """Persist each validated page, including reply continuations, before reading another."""
    p,w,identity=job['platform'],job['work_id'],job['identity'];o=run['options']
    cp=job.setdefault('checkpoint',{'root_pages':[],'reply_pages':{},'roots_done':False})
    roots=cp['root_pages']
    while not cp['roots_done']:
        cursor=roots[-1].get('next_cursor') if roots else None
        stop_check(p)
        v=ops.backend(p,'comments',w,limit=o['limit'],cursor=cursor)
        if v.get('identity')!=identity:raise VpubError('wrong_account','Account changed while reading a page.')
        token=v.get('next_cursor')
        if token and (token==cursor or token in [x.get('next_cursor') for x in roots]):
            raise VpubError('cursor_loop','Repeated page cursor; failed page not checkpointed.')
        roots.append(v);cp['roots_done']=not token or len(roots)>=o['pages'];save(run)
    rows={c['id']:dict(c) for page in roots for c in page.get('items',[])}
    pending=[];expanded=0
    for root in list(rows.values()):
        children=[c for c in rows.values() if c.get('parent_id')==root['id']]
        if root.get('parent_id') or not isinstance(root.get('reply_count'),int) or root['reply_count']<=len(children):continue
        if not o['reply_pages'] or expanded>=o['reply_threads']:
            pending.append({'comment_id':root['id'],'next_cursor':None,'reason':'thread_budget'});continue
        expanded+=1;pages=cp['reply_pages'].setdefault(root['id'],[])
        while len(pages)<o['reply_pages'] and (not pages or pages[-1].get('next_cursor')):
            cursor=pages[-1].get('next_cursor') if pages else None
            stop_check(p)
            v=ops.backend(p,'replies',w,limit=o['limit'],cursor=cursor,comment_id=root['id'])
            if v.get('identity')!=identity:raise VpubError('wrong_account','Reply-page account changed.')
            if any(c['id']!=root['id'] and c.get('parent_id')!=root['id'] for c in v['items']):
                raise VpubError('wrong_thread','A reply belongs to another root.')
            token=v.get('next_cursor')
            if token and (token==cursor or token in [x.get('next_cursor') for x in pages]):
                raise VpubError('cursor_loop','Repeated reply cursor.')
            pages.append(v);save(run)
        for v in pages:
            for c in v['items']:rows[c['id']]={**rows.get(c['id'],{}),**c}
        cursor=pages[-1].get('next_cursor') if pages else None
        rows[root['id']]['owner_replied']=True if any(c.get('is_owner') and c.get('parent_id')==root['id'] for c in rows.values()) else None if cursor else False
        if cursor:pending.append({'comment_id':root['id'],'next_cursor':cursor,'reason':'page_budget'})
    return {**roots[0],'items':list(rows.values()),'next_cursor':roots[-1].get('next_cursor'),
            'complete':not roots[-1].get('next_cursor') and not pending,
            'bounded':True,'pages_read':len(roots),'page_budget':o['pages'],
            'reply_threads_expanded':expanded,'reply_continuations':pending}


def recover_batches(run):
    """Worker writes each completed read before starting another. Timeout retains those reads."""
    jobs={j['job_id']:j for j in run['jobs']}
    for batch in run['batches']:
        if batch.get('invalid'):continue
        path=home()/'operations/sync-progress'/run['run_id']/(batch['id']+'.json')
        if not path.exists():continue
        value=read_json(path)
        if value.get('identity')!=batch['identity'] or value.get('request_hash')!=batch['request_hash']:
            batch['invalid']=True;save(run)
            raise VpubError('checkpoint_conflict','Batch checkpoint account/request mismatch.')
        for row in value.get('results',[]):
            key=digest([batch['platform'],batch['identity'],row['job']['work_id'],row['job']['operation']])
            if key not in batch['job_ids']:
                batch['invalid']=True;save(run)
                raise VpubError('checkpoint_conflict','Unexpected job in batch checkpoint.')
            job=jobs[key]
            if job['state'] in {'DONE','CACHED'}:continue
            if row['ok']:finish(run,job,row['data'])
            else:job.update(state='FAILED',error=row['error']['code'])
    save(run)


def browser_group(run,group):
    from .direct_messages import identity as account_identity
    p,identity=group[0]['platform'],group[0]['identity'];o=run['options']
    for j in group:
        if (ops.browser_identity(p,j['work_id']) if j['work_id']!='@account' else account_identity(p))!=identity:raise VpubError('wrong_account','Work binding changed.')
    reads=[{'operation':j['operation'],'work_id':j['work_id'],'limit':o['limit'],
            **({'pages':o['pages'],'reply_pages':o['reply_pages']} if j['operation']=='comments' else {})} for j in group]
    batch={'id':uuid.uuid4().hex,'identity':identity,'platform':p,'job_ids':[j['job_id'] for j in group],
           'request_hash':digest(reads)}
    run['batches'].append(batch);run['browser_sessions']+=1
    for j in group:j['state']='RUNNING'
    save(run)
    path=home()/'operations/sync-progress'/run['run_id']/(batch['id']+'.json')
    value=adapters.browser_call(p,'operations_batch','default',expected_identity=identity,jobs=reads,progress_path=str(path))
    # Also support a worker that returned before the parent had read its last checkpoint.
    atomic_json(path,{**value,'request_hash':batch['request_hash']})
    recover_batches(run)


def read_direct(run,job):
    """A read through the platform's own CLI or API rather than a shared browser."""
    if job['operation']=='comments':return api_comments(run,job)
    if job['operation'] in {'mentions','dm_preview'}:
        from .operations_api import bilibili_source
        return bilibili_source(job['operation'],job['identity'],run['options']['limit'])
    return ops.backend(job['platform'],'stats',job['work_id'])


def execute_serial(run):
    run['state']='RUNNING';save(run)
    recover_batches(run)
    blocked=set()
    for job in run['jobs']:
        if job['state'] in {'DONE','CACHED'}:continue
        p=job['platform']
        if p in blocked:continue
        group=[job]
        try:
            stop_check(p)
            if p in {'douyin','instagram','x'}:
                group=[j for j in run['jobs'] if j['platform']==p and j['identity']==job['identity'] and j['state'] not in {'DONE','CACHED'}][:20]
                browser_group(run,group)
                if any(j['state']!='DONE' for j in group):blocked.add(p)
            else:
                job['state']='RUNNING';save(run)
                value=job.get('raw_result') or read_direct(run,job)
                finish(run,job,value)
        except Exception as exc:
            # Do not try another route on a challenged account. Other platforms can continue.
            recover_batches(run)
            for j in group:
                if j['state'] not in {'DONE','CACHED'}:
                    j.update(state='FAILED',error=exc.code if isinstance(exc,VpubError) else type(exc).__name__)
            blocked.add(p);save(run)
    return finalize(run)


def finalize(run):
    errors=[{'platform':j['platform'],'work_id':j['work_id'],'action':j['operation'],'code':j.get('error','not_started')}
            for j in run['jobs'] if j['state'] not in {'DONE','CACHED'}]
    items={};inbox=[]
    from .operations_inbox import find_item
    for j in run['jobs']:
        key=(j['platform'],j['identity'],j['work_id'])
        item=items.setdefault(key,{k:j[k] for k in ('platform','identity','work_id')})
        item[j['operation']]={**j['result'],'cached':j['state']=='CACHED'} if j['state'] in {'DONE','CACHED'} else {'state':j['state'],'code':j.get('error','not_started')}
        if j['operation']=='comments' and j['state'] in {'DONE','CACHED'}:
            for c in j['result'].get('items',[]):
                if c.get('is_owner') or c.get('owner_replied') is True:continue
                row=find_item(j['platform'],j['identity'],j['work_id'],c['id'])
                if row and row['status'] in {'handled','deferred'}:continue
                inbox.append({'platform':j['platform'],'work_id':j['work_id'],'comment':c,
                              'key':row['key'] if row else None,'local_status':row['status'] if row else None,
                              'status':'unreplied' if c.get('owner_replied') is False else 'needs_thread_check',
                              'reply_language':c.get('language_hint','und'),'cached':j['state']=='CACHED',
                              'observed_at':j['checked_at']})
    run.update(state='PARTIAL' if errors else 'COLLECTED',completed_at=now(),errors=errors,
               items=list(items.values()),inbox=inbox,
               reads_completed=sum(j['state']=='DONE' for j in run['jobs']),cached_reads=sum(j['state']=='CACHED' for j in run['jobs']),
               changes=[{'platform':j['platform'],'work_id':j['work_id'],**c} for j in run['jobs'] if j['state']=='DONE' for c in j.get('changes',[])],
               report=ops.report(since=run['started_at'],language=run['options']['language'],_events=run['events']))
    run['sources']=list(run.get('source_status',[]))
    for j in run['jobs']:
        if j['operation'] not in {'mentions','dm_preview','comments'}:continue
        row={'platform':j['platform'],'kind':j['operation'],'work_id':j['work_id'],
             'state':'CACHED' if j['state']=='CACHED' else 'OBSERVED' if j['state']=='DONE' else 'FAILED'}
        if j['state'] in {'DONE','CACHED'}:
            row.update(j['result'].get('summary',{}),observed_at=j['checked_at'])
            if j['state']=='CACHED':row.update(newly_observed=0,changed=0)
        else:row['code']=j.get('error','not_started')
        run['sources'].append(row)
    if any(r['state'] not in {'OBSERVED','CACHED'} for r in run['sources']):run['state']='PARTIAL'
    run['account_health']=[{'platform':p,'state':'READ_FAILED' if any(e['platform']==p for e in errors) else 'CACHED' if all(j['state']=='CACHED' for j in run['jobs'] if j['platform']==p) else 'OBSERVED',
                           'errors':[e['code'] for e in errors if e['platform']==p]} for p in dict.fromkeys(j['platform'] for j in run['jobs'])]
    if run['options'].get('sources') is not None:
        run['comment_report']=run['report']
        good=sum(r['state']=='OBSERVED' for r in run['sources']);cached=sum(r['state']=='CACHED' for r in run['sources'])
        added=sum(r.get('newly_observed',0) for r in run['sources'])
        run['report']=(f'本轮读取 {good} 个消息来源，复用 {cached} 项缓存；新观察到 {added} 条提及或会话预览。首次读取仅建立基线，受限来源见明细。未发送回复、私信或邮件。' if ops.reporting_language(run['options']['language'])=='zh' else
                       f'Read {good} message sources; reused {cached} cached reads; {added} newly observed mentions or conversation previews. First reads establish a baseline; see coverage limits. No replies, DMs or email sent.')
    save(run);return run


def execute(run,progress=False):
    from .operations_api import read_session
    if run['options'].get('workers',1)>1:
        from .read_scheduler import execute as schedule
        return schedule(run,progress)
    with read_session():return execute_serial(run)


def sync(platforms=None,limit=20,pages=2,language='auto',reply_pages=1,reply_threads=5,min_interval=300,full=False,workers=3,progress=False,sources=None):
    with run_lock():
        run=make_run(platforms,limit,pages,language,reply_pages,reply_threads,min_interval,full,workers,sources)
        try:return execute(run,progress)
        except BaseException:
            run['state']='INTERRUPTED';save(run);raise


def resume(run_id,progress=False):
    with run_lock():
        run=status(run_id)
        if run['state']=='COLLECTED':return {**run,'reused':True,'platform_writes':0}
        from .operations_sync import watch
        current={(t['platform'],t['work_id']):t['identity'] for t in watch('list')['items']}
        for j in run['jobs']:
            from .direct_messages import identity
            owner=identity(j['platform']) if j['work_id']=='@account' else current.get((j['platform'],j['work_id']))
            if owner!=j['identity']:
                raise VpubError('watch_changed','The saved run no longer matches the watchlist/account; start a fresh sync.')
        try:return execute(run,progress)
        except BaseException:
            run['state']='INTERRUPTED';save(run);raise

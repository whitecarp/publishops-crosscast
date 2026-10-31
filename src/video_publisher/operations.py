"""Account operations, bounded observations and direct, deduplicated replies."""
from datetime import datetime, timedelta, timezone
import locale
from pathlib import Path
import re
import uuid

from .core import (Ledger, VpubError, atomic_json, config, digest, home,
                   language_tag, now, platform_check, publication_lock, read_json, stop_check)

NAMES={'bilibili':'B站','youtube':'YouTube','douyin':'抖音','instagram':'Instagram','x':'X'}


def work_id(platform, value):
    platform_check(platform)
    pattern={'bilibili':r'BV[A-Za-z0-9]{10}', 'youtube':r'[A-Za-z0-9_-]{11}',
             'instagram':r'[A-Za-z0-9_-]{5,32}', 'douyin':r'\d{8,30}', 'x':r'\d{8,30}'}[platform]
    if not isinstance(value,str) or not re.fullmatch(pattern,value):
        raise VpubError('invalid_work_id','Use an exact platform work ID, not a URL or command-line option.')
    return value


def language_hint(text):
    if re.search(r'[\u3040-\u30ff]',text): return 'ja'
    if re.search(r'[\uac00-\ud7af]',text): return 'ko'
    if re.search(r'[\u4e00-\u9fff]',text): return 'zh'
    # Latin characters alone do not distinguish English from other languages.
    return 'und'


def reporting_language(value='auto'):
    if value!='auto': return value
    selected=config().get('report_language')
    if selected in {'zh','en'}: return selected
    return 'zh' if (locale.getlocale()[0] or '').lower().startswith(('zh','chinese')) else 'en'


def set_language(value):
    if value not in {'zh','en','auto'}: raise VpubError('invalid_language','Choose zh, en or auto.')
    current=config();current['report_language']=value
    atomic_json(home()/'config.json',current)
    return {'report_language':reporting_language(value),'reply_language':'comment language only; single language'}


def record(action, platform, work, result):
    event={'schema_version':1,'event_id':uuid.uuid4().hex,'at':now(),
           'action':action,'platform':platform,'work_id':work,'result':result}
    atomic_json(home()/'operations/events'/(event['event_id']+'.json'),event)
    return result


def browser_identity(platform, work):
    ledger=Ledger()
    try:
        rows=[r for r in ledger.rows() if r['platform']==platform and r['data'].get('receipt',{}).get('id')==work]
        identities={r['data'].get('identity',{}).get('identity') or r['data'].get('receipt',{}).get('identity') for r in rows}
        identities.discard(None)
        from .work_catalog import binding
        adopted = binding(platform, work)
        if adopted:
            identities.add(adopted['identity'])
        if len(identities)!=1:
            raise VpubError('work_identity_required','Browser operations need an existing work receipt with its verified owner.',
                            'Run vpub ops adopt PLATFORM WORK for a fresh owner check, or use an existing upload receipt.')
        return identities.pop()
    finally: ledger.close()


def backend(platform, action, work, **kw):
    from . import adapters, operations_api
    if platform in {'bilibili','youtube'}:
        return operations_api.execute(platform,action,work,**kw)
    return adapters.browser_call(platform,'operations','default',expected_identity=browser_identity(platform,work),
                                 operation=action,work_id=work,**kw)


def save_observation(platform, work, action, result):
    result.update(platform=platform,work_id=work,observed_at=now())
    with publication_lock():
        if action=='comments':
            for comment in result.get('items',[]):
                comment.setdefault('language_hint',language_hint(comment.get('text','')))
            path=home()/'operations/seen'/(digest([platform,work])+'.json')
            old=read_json(path) if path.exists() else None
            if old and old.get('identity')!=result['identity']: old=None
            ids={c['id'] for c in result.get('items',[])}
            audience_ids={c['id'] for c in result.get('items',[]) if not c.get('is_owner')}
            result['newly_observed_count']=len(audience_ids-set(old['ids'])) if old else None
            result['newly_observed_ids']=sorted(audience_ids-set(old['ids'])) if old else []
            result['baseline']=old is None
            atomic_json(path,{'identity':result['identity'],'ids':sorted(ids|set((old or {}).get('ids',[]))),'at':now()})
            atomic_json(home()/'operations/comments'/(digest([platform,work])+'.json'),result)
            from .operations_inbox import merge
            merge(platform,work,result)
        elif action=='stats':
            from .operations_sync import metric_changes
            path=home()/'operations/stats'/(digest([platform,result['identity'],work])+'.json')
            old=read_json(path) if path.exists() else None
            result['changes']=metric_changes(old,result)
            result['baseline']=old is None
            result['previous_observed_at']=(old or {}).get('observed_at')
            atomic_json(path,result)
    return record(action,platform,work,result)


def observe(platform, work, action, limit=20, cursor=None, comment_id=None):
    work=work_id(platform,work);stop_check(platform)
    if action not in {'comments','stats','replies'}: raise VpubError('invalid_operation','Choose comments, replies or stats.')
    if not 1<=limit<=100: raise VpubError('invalid_limit','Limit must be 1–100.')
    if cursor and (not isinstance(cursor,str) or len(cursor)>1000): raise VpubError('invalid_cursor','Invalid page cursor.')
    try:
        if action=='replies':
            if platform not in {'bilibili','youtube'}:
                raise VpubError('unsupported_operation','Paged reply reading is available for Bilibili and YouTube; browser reads remain bounded.')
            if not isinstance(comment_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',comment_id):
                raise VpubError('invalid_comment_id','Use a top-level comment ID from comments list.')
        result=backend(platform,action,work,limit=limit,cursor=cursor,comment_id=comment_id)
        return save_observation(platform,work,'comments' if action=='replies' else action,result)
    except Exception as e:
        record(action,platform,work,{'state':'FAILED','code':e.code if isinstance(e,VpubError) else type(e).__name__})
        raise


def reply(platform, work, comment_id, text, language='auto', dry_run=False, _test_key=None):
    work=work_id(platform,work);stop_check(platform)
    if not isinstance(comment_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',comment_id):
        raise VpubError('invalid_comment_id','Use the comment ID returned by comments list.')
    if not isinstance(text,str) or not text.strip() or '\x00' in text or len(text)>2000:
        raise VpubError('invalid_reply','Supply one nonempty reply of at most 2000 characters.')
    cache=home()/'operations/comments'/(digest([platform,work])+'.json')
    if not cache.exists(): raise VpubError('comment_snapshot_required','Read this work’s comments before replying.')
    snapshot=read_json(cache)
    candidates=[c for c in snapshot.get('items',[]) if c['id']==comment_id]
    if not candidates:
        from .operations_inbox import find
        historical=find(platform,snapshot['identity'],work,comment_id)
        if historical: candidates=[historical]
    if len(candidates)!=1: raise VpubError('comment_not_observed','The comment is absent from the latest collected page; collect its page again.')
    target=candidates[0]
    if _test_key:
        test=read_json(home()/'operations/tests'/(_test_key+'.json'))
        if test.get('platform')!=platform or test.get('work_id')!=work or test.get('comment_id')!=comment_id or text!='你好':
            raise VpubError('invalid_self_test','Self-test replies must match their exact recorded test comment.')
    if target.get('is_owner') and not _test_key: raise VpubError('own_comment','Do not auto-reply to your own comment.')
    if target.get('owner_replied') is True and not _test_key:
        raise VpubError('already_replied','The current read already shows an owner reply; no extra reply is sent.')
    hint=target.get('language_hint','und')
    chosen=hint if language=='auto' else language_tag(language).lower().split('-')[0]
    if chosen in {'und','mul','zxx'}:
        raise VpubError('comment_language_required','The host must identify the comment’s language and pass --language; user-interface language is not used.')
    if hint!='und' and hint!=chosen:
        raise VpubError('wrong_reply_language','Reply in the comment’s language only, without an added translation.')
    text_hint=language_hint(text)
    if text_hint!='und' and text_hint!=chosen:
        raise VpubError('wrong_reply_language','The reply script conflicts with the selected comment language.')
    # No translation, signature or second-language block is appended by this CLI.
    request={'text':text,'language':chosen,'comment':target,'expected_identity':snapshot['identity'],'test':bool(_test_key)}
    if dry_run:
        return {'state':'DRY_RUN','platform_writes':0,'platform':platform,'work_id':work,**request}
    key=digest([platform,snapshot['identity'],work,comment_id])
    path=home()/'operations/replies'/(key+'.json')
    with publication_lock():
        previous={}
        if path.exists():
            previous=read_json(path)
            if previous.get('state')!='REPLY_READY':
                return {**previous,'reused':True,'platform_writes':0,
                        'hint':'An existing attempt is retained. Use comments verify-reply; never send again on a timeout.'}
        # Durable before any backend can submit; even a lost response prevents duplicates.
        attempt={'state':'REPLY_READY','reply_key':key,'platform':platform,'work_id':work,
                 'identity':snapshot['identity'],'request':request,'at':now(),'test':bool(_test_key)}
        # An explicitly prepared repair must retain earlier failed attempts.
        for field in ('recovery_history','recovery_reason','creator_repair_prepared'):
            if field in previous:attempt[field]=previous[field]
        atomic_json(path,attempt)
        try:
            result=backend(platform,'reply',work,reply_request=request,reply_receipt_path=str(path))
        except Exception as e:
            durable=read_json(path)
            if durable.get('state')=='REPLY_SUBMITTING':
                durable['state']='REPLY_UNVERIFIED';atomic_json(path,durable)
            record('reply',platform,work,{'state':durable.get('state'),'reply_key':key,'test':bool(_test_key),
                                        'code':e.code if isinstance(e,VpubError) else type(e).__name__})
            raise
        # Backends retain the exact request before and after submission.
        final={**read_json(path),**result};atomic_json(path,final)
        return record('reply',platform,work,final)


def verify_reply(key):
    if not re.fullmatch(r'[a-f0-9]{64}',key): raise VpubError('invalid_reply_key','Use a reply receipt key.')
    path=home()/'operations/replies'/(key+'.json')
    if not path.exists(): raise VpubError('reply_receipt_required','Reply receipt not found.')
    with publication_lock():
        saved=read_json(path)
        result=backend(saved['platform'],'verify_reply',saved['work_id'],reply_request=saved['request'],
                       reply_receipt_path=str(path),existing_reply=saved)
        saved.update(result);atomic_json(path,saved)
        return record('reply_verify',saved['platform'],saved['work_id'],saved)


def self_test(platform, work, test_id):
    """Explicitly requested live hello test, at most one seed and one reply per ID."""
    work=work_id(platform,work);stop_check(platform)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',test_id):
        raise VpubError('invalid_test_id','Use a stable alphanumeric test ID; reruns reuse it.')
    key=digest([platform,work,test_id]);path=home()/'operations/tests'/(key+'.json')
    # Collect first for owner checks and a pre-send snapshot. No ordinary report
    # counts this test traffic as an audience interaction.
    with publication_lock():
        if path.exists(): saved=read_json(path)
        else:
            before=backend(platform,'comments',work,limit=100,cursor=None)
            saved={'test':True,'test_key':key,'test_id':test_id,'platform':platform,'work_id':work,
                   'identity':before['identity'],'before_ids':[c['id'] for c in before['items']],
                   'state':'TEST_READY','text':'你好','at':now()}
            atomic_json(path,saved)
        if saved.get('state')=='TEST_READY':
            result=backend(platform,'test_seed',work,test_request=saved,test_receipt_path=str(path))
            saved={**read_json(path),**result};atomic_json(path,saved)
        if saved.get('state')!='TEST_SEED_VERIFIED':
            result=backend(platform,'test_seed_verify',work,test_request=saved,test_receipt_path=str(path))
            saved.update(result);atomic_json(path,saved)
        if saved.get('state')!='TEST_SEED_VERIFIED': return saved
    observed=observe(platform,work,'comments',limit=100)
    target=next((c for c in observed['items'] if c['id']==saved['comment_id']),None)
    if target is None: return {**saved,'state':'TEST_COMMENT_NOT_VISIBLE','hint':'No duplicate test comment is sent.'}
    result=reply(platform,work,saved['comment_id'],'你好','zh',_test_key=key)
    if result['state']!='REPLY_VERIFIED': result=verify_reply(result['reply_key'])
    return {**saved,'state':'TEST_VERIFIED' if result['state']=='REPLY_VERIFIED' else 'TEST_REPLY_UNVERIFIED','reply':result}


def report(since=None, language='auto', notes=None, _events=None):
    lang=reporting_language(language)
    try:
        start=datetime.fromisoformat(since.replace('Z','+00:00')) if since else datetime.now(timezone.utc)-timedelta(days=1)
        if start.tzinfo is None: raise ValueError()
    except ValueError: raise VpubError('invalid_since','Use an ISO timestamp with a timezone.')
    events=_events if _events is not None else [read_json(p) for p in (home()/'operations/events').glob('*.json')]
    events=sorted((e for e in events if datetime.fromisoformat(e['at'].replace('Z','+00:00'))>=start),key=lambda e:e['at'])
    parts=[];details={}
    for platform in NAMES:
        selected=[e for e in events if e['platform']==platform and not e['result'].get('test')]
        if not selected: continue
        comments={};stats={};replies={};engagement={};management={};messages={};failed=[];new_comments=set();baselines=set()
        for e in selected:
            v=e['result']
            if v.get('state')=='FAILED': failed.append(v);continue
            if v.get('test'): continue
            if e['action']=='comments':
                new_comments.update((e['work_id'],cid) for cid in v.get('newly_observed_ids',[]))
                if v.get('baseline'):baselines.add(e['work_id'])
                for c in v.get('items',[]):
                    if not c.get('is_owner'): comments[(e['work_id'],c['id'])]=c
            if e['action']=='stats': stats[e['work_id']]=v
            if e['action'] in {'reply','reply_verify'}: replies[v.get('reply_key',e['event_id'])]=v
            if e['action']=='engagement': engagement[v.get('key',e['event_id'])]=v
            if e['action']=='comment_management':management[v.get('key',e['event_id'])]=v
            if e['action']=='direct_message':messages[v.get('key',e['event_id'])]=v
        unavailable_stats=sum(not any(m.get('value') is not None for m in v.get('metrics',{}).values()) for v in stats.values())
        collected_stats=len(stats)-unavailable_stats
        confirmed=sum(v.get('state')=='REPLY_VERIFIED' for v in replies.values())
        pending=sum(v.get('state')!='REPLY_VERIFIED' for v in replies.values())
        actions={kind+'_'+direction:sum(v.get('state')=='ACTION_VERIFIED' and v.get('kind')==kind and v.get('desired') is desired for v in engagement.values())
                 for kind in ('like','save') for direction,desired in (('on',True),('off',False))}
        actions_pending=sum(v.get('state') in {'ACTION_SUBMITTING','ACTION_UNVERIFIED'} for v in engagement.values())
        actions_failed=sum(v.get('state')=='ACTION_FAILED' for v in engagement.values())
        managed=sum(v.get('state')=='COMMENT_VERIFIED' for v in management.values())
        manage_pending=sum(v.get('state') in {'COMMENT_SUBMITTING','COMMENT_UNVERIFIED'} for v in management.values())
        manage_failed=sum(v.get('state')=='COMMENT_FAILED' for v in management.values())
        dm_visible=sum(v.get('state')=='DM_VISIBLE' for v in messages.values())
        dm_pending=sum(v.get('state') in {'DM_SUBMITTING','DM_UNVERIFIED'} for v in messages.values())
        dm_failed=sum(v.get('state')=='DM_FAILED' for v in messages.values())
        details[platform]={'comments_read':len(comments),'newly_observed_comments':len(new_comments),'baseline_works':len(baselines),'replies_verified':confirmed,'work_metrics':stats,
                           'replies_pending':pending,'works_with_stats':collected_stats,'works_without_visible_metrics':unavailable_stats,'failed_reads':len(failed),
                           'engagement_verified':actions,'engagement_pending':actions_pending,'engagement_failed':actions_failed,
                           'direct_messages_visible':dm_visible,'direct_messages_pending':dm_pending,'direct_messages_failed':dm_failed,'comment_management_verified':managed,'comment_management_pending':manage_pending,'comment_management_failed':manage_failed}
        if lang=='zh':
            pieces=[]
            if any(e['action']=='comments' and e['result'].get('state')!='FAILED' for e in selected): pieces.append(f'读取{len(comments)}条观众评论')
            if new_comments:pieces.append(f'其中{len(new_comments)}条为新读到的评论')
            if baselines:pieces.append(f'{len(baselines)}条作品首次建立评论基线')
            if confirmed: pieces.append(f'回复{confirmed}条并确认可见')
            if pending: pieces.append(f'{pending}条回复尚未确认成功，未重复发送')
            if collected_stats: pieces.append(f'采集{collected_stats}条作品的数据')
            if unavailable_stats: pieces.append(f'{unavailable_stats}条作品暂未返回可用数值')
            if failed: pieces.append(f'{len(failed)}次读取未完成')
            for kind,label in [('like_on','点赞'),('like_off','取消点赞'),('save_on','保存到收藏／书签／播放列表'),('save_off','从收藏／书签／播放列表移除')]:
                if actions[kind]:pieces.append(f'{label}{actions[kind]}次并读回确认')
            if actions_pending:pieces.append(f'{actions_pending}次点赞或保存操作待核验，未重试')
            if actions_failed:pieces.append(f'{actions_failed}次点赞或保存操作在提交前停止')
            if managed:pieces.append(f'{managed}次评论管理操作已读回确认')
            if manage_pending:pieces.append(f'{manage_pending}次评论管理操作待核验，未重试')
            if manage_failed:pieces.append(f'{manage_failed}次评论管理操作在提交前停止')
            if dm_visible:pieces.append(f'{dm_visible}条发出私信已在会话中读回，不代表对方已读')
            if dm_pending:pieces.append(f'{dm_pending}条私信待核验，未重发')
            if dm_failed:pieces.append(f'{dm_failed}次私信操作在提交前停止')
            if pieces:parts.append(NAMES[platform]+'：'+'，'.join(pieces))
        else:
            pieces=[]
            counted=lambda n,noun:f'{n} {noun}'+('' if n==1 else 's')
            if any(e['action']=='comments' and e['result'].get('state')!='FAILED' for e in selected): pieces.append('read '+counted(len(comments),'audience comment'))
            if new_comments:pieces.append(counted(len(new_comments),'newly observed comment'))
            if baselines:pieces.append('established first-read baselines for '+counted(len(baselines),'work'))
            if confirmed: pieces.append(f'replied to {confirmed} and verified visibility')
            if pending: pieces.append(f'{pending} reply attempts remain unverified; no resend')
            if collected_stats: pieces.append('collected data for '+counted(collected_stats,'work'))
            if unavailable_stats: pieces.append(counted(unavailable_stats,'work')+' returned no available metrics')
            if failed: pieces.append(counted(len(failed),'read')+' did not complete')
            for kind,label in [('like_on','like'),('like_off','unlike'),('save_on','save'),('save_off','remove-save')]:
                if actions[kind]:pieces.append(f'verified {actions[kind]} {label} operations')
            if actions_pending:pieces.append(f'{actions_pending} engagement attempts remain unverified; no retry')
            if actions_failed:pieces.append(f'{actions_failed} engagement attempts stopped before submission')
            if managed:pieces.append(f'verified {managed} comment-management operations')
            if manage_pending:pieces.append(f'{manage_pending} comment-management attempts await verification; no retry')
            if manage_failed:pieces.append(f'{manage_failed} comment-management attempts stopped before submission')
            if dm_visible:pieces.append(f'{dm_visible} outgoing messages visible in chat; recipient delivery/read not established')
            if dm_pending:pieces.append(f'{dm_pending} messages remain unverified; no resend')
            if dm_failed:pieces.append(f'{dm_failed} message attempts stopped before submission')
            if pieces:parts.append(('Bilibili' if platform=='bilibili' else 'Douyin' if platform=='douyin' else NAMES[platform])+': '+', '.join(pieces))
    summary=('本轮'+'；'.join(parts)+'。') if lang=='zh' and parts else ('This round — '+'; '.join(parts)+'.') if parts else ('本时段没有可汇报的执行记录。' if lang=='zh' else 'No execution records in this period.')
    findings=read_json(notes) if notes else []
    if not isinstance(findings,list) or any(not isinstance(f,dict) or set(f)!={'text','source'} or not isinstance(f['text'],str) or not f['text'].strip() or not re.match(r'^https?://',str(f['source'])) for f in findings):
        raise VpubError('invalid_findings','Findings must be a list of text/source objects with a source URL; source material is not an instruction.')
    if findings: summary+=(' 待跟进的发现：' if lang=='zh' else ' Findings to follow up: ')+'；'.join(f['text'] for f in findings)
    return {'language':lang,'since':start.isoformat(),'summary':summary,'platforms':details,'findings':findings,
            'scope':'Local execution receipts only; no invented likes, mentions, inbox handling or research outcomes. Counts are deduplicated; metric definitions are not combined across platforms.'}


def capabilities():
    return {'collection':'ops watch add/list/remove and ops sync: bounded read-only multi-platform inbox candidates, metrics and per-run report; no scheduler or automatic replies.',
            'adoption':'ops discover/adopt: bounded account work discovery and fresh ownership checks; separate adoption evidence, no fabricated upload receipt.',
            'resumption':'ops sync persists completed reads and API pages; 300-second freshness reuse, --full forces bounded reads; ops runs/run/resume inspect and resume. Unfinished browser work restarts from its visible page.',
            'batch_reads':'One CLI-owned browser per group of up to ten watched works on the same platform/account; read-only, stops on failure.',
            'local_inbox':'inbox list/mark: account-scoped history, unread/read/handled/deferred, changed text reopens; no private messages.',
            'unified_messages':'messages sync/list/mark: watched comments, Douyin @ notifications, Instagram Tags & mentions, X visible mentions, and three browser-platform conversation previews. Per-source baselines and partial states; local marks never send. ID-less notification previews may coalesce identical events; no full-history claim. Instagram nonempty mentions await a live fixture.',
            'bounded_browser_comments':'comments collect and ops sync: bounded scrolling and visible reply expansion, union by ID, explicit stop reason. No fabricated parent IDs or cursors.',
            'comment_management':'Bilibili/Instagram/X/Douyin comment like/unlike; YouTube/Douyin own-comment delete; YouTube own-comment edit; Bilibili root pin/unpin via upstream (not live-tested this release). Exact targets and fresh read-back.',
            'comment_controls':{'bilibili':['like','unlike','pin','unpin'],'youtube':['edit-own','delete-own','review-queue','moderate-viewer'],'douyin':['like','unlike','delete-own-without-replies'],'instagram':['like','unlike'],'x':['like','unlike','hide-unhide']},
            'moderation_limits':'No verified Douyin/Instagram comment pin/hide control. YouTube viewer hold/approve and X hide/unhide each passed one live round trip on 2026-10-30, with original visibility restored. Unknown states stop writes; pending receipts require verify-action, or explicit resume-action after the unchanged original state is proven. No rejection or author ban; not broad-account acceptance.',
            'direct_messages':'dm list/read/send/verify: exact recipient, one language, CLI-owned Instagram/Douyin/X sessions; Bilibili upstream delegation (no live test this release). X dm unlock opens official PIN UI on explicit request. YouTube has no DM adapter. Visible outgoing messages do not prove recipient delivery/read.',
            'works':'Bilibili published pages, YouTube uploads playlist pages, other platforms visible first page.',
            'reply_pages':'comments replies: Bilibili and YouTube roots with next_cursor; browser replies remain bounded.',
            'comments':{'bilibili':'upstream CLI, paged threads','youtube':'official API, paged threads and replies',
                        'douyin':'CLI public work reads; Creator Center top-level replies and statistics','instagram':'CLI visible comments','x':'CLI visible conversation entries; parent ID is not exposed'},
            'reply':'Direct send, optional --dry-run; one language matching the comment; durable deduplication and read-back.',
            'stats':'Per-work observations with source and timestamp; missing metrics stay null. X post views are not video plays.',
            'report':'Receipt-based natural Chinese/English, selected from host user language or saved report_language; excludes owner comments and self-tests; no automatic email.',
            'live_reply_tests':{'bilibili':'verified','youtube':'verified','instagram':'visible author/text/ID verified; browser reply-action evidence','x':'visible author/text/ID verified; browser reply-action evidence','douyin':'Creator Center reply verified in a fresh session with author/text/new ID and nested parent'},
            'not_in_this_scope':['automatic/bulk private messages','automatic likes','watch/transcribe','hot-topic research','unbounded historical export','Douyin replies to nested replies','Douyin/Instagram comment pin/hide']}

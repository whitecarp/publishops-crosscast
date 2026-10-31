"""Creator Center reply controls; observe only requests made by the official UI."""
from pathlib import Path
from urllib.parse import urlsplit

from .core import VpubError, atomic_json, read_json, stop_check
from .browser_runtime import risk_check, unique_visible
from .browser_backends import normalize


def creator_target(data, work, target):
    if not isinstance(data,dict) or data.get('status_code')!=0:
        raise VpubError('comments_unavailable','Creator Center did not return a successful comment page.')
    matches=[c for c in data.get('comments',[]) if str(c.get('cid'))==target['id']]
    if len(matches)!=1 or str(matches[0].get('aweme_id'))!=work or matches[0].get('text')!=target['text']:
        raise VpubError('comment_changed','The exact target is absent or changed in this work’s Creator Center page.')
    return matches[0]


def acknowledgement(data, work, target_id, text, identity):
    """An acknowledgement is never a fresh visibility check."""
    unknown={'state':'REPLY_UNVERIFIED','send_acknowledged':False}
    if not isinstance(data,dict) or 'status_code' not in data:return unknown
    if data['status_code']!=0:
        return {'state':'REPLY_REJECTED','send_acknowledged':False,'platform_status':data['status_code'],
                'hint':'Creator Center rejected this reply; inspect the official page before any new attempt.'}
    comment=data.get('comment') or {}
    if (not comment.get('cid') or str(comment.get('aweme_id'))!=work or comment.get('text')!=text
            or str(comment.get('reply_id'))!=target_id
            or identity not in {str(comment.get('user',{}).get('unique_id')),str(comment.get('user',{}).get('short_id'))}):
        return unknown
    return {'state':'REPLY_UNVERIFIED','send_acknowledged':True,'reply_id':str(comment['cid']),
            'acknowledged_parent_id':target_id,'send_source':'creator_center_ui_response'}


async def creator_page(page):
    from patchright.async_api import TimeoutError as BrowserTimeout
    for attempt in range(3):
        try:
            async with page.expect_response(lambda r:urlsplit(r.url).hostname=='creator.douyin.com'
                    and urlsplit(r.url).path.endswith('/comment/list/select/')
                    and r.request.method=='GET',timeout=6000) as pending:
                await page.get_by_text('评论管理',exact=True).click()
            response=await pending.value
            return await response.json()
        except BrowserTimeout:
            if attempt==2:
                raise VpubError('comments_unavailable','Creator Center comment data did not finish loading; no reply sent.') from None


async def reply(page, identity, payload):
    request=payload['reply_request'];target=request['comment'];work=payload['work_id']
    if identity!=request['expected_identity']:raise VpubError('wrong_account','Reply account changed.')
    data=await creator_page(page)
    current=creator_target(data,work,target)
    if any(identity in {str(c.get('user',{}).get('unique_id')),str(c.get('user',{}).get('short_id'))}
           for c in current.get('reply_comment') or []):
        raise VpubError('already_replied','Creator Center already shows an owner reply in this thread.')
    if str(current.get('reply_id','0')) not in {'0','None',''}:
        raise VpubError('reply_level_unsupported','This Creator Center route currently supports replies to top-level comments.')
    rows=page.locator('[class^="comment-item-"]')
    candidates=[]
    for row in await rows.all():
        body=row.locator('[class^="comment-main-"]').first
        text=body.locator('[class^="text-"]').first
        name=body.locator('[class^="username-"]').first
        if not await text.count() or not await name.count():continue
        visible_name=await name.evaluate("e=>[...e.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim()")
        if await text.inner_text()==current['text'] and visible_name==current['user']['nickname']:candidates.append(row)
    same=[c for c in data.get('comments',[]) if c.get('text')==current['text'] and c.get('user',{}).get('nickname')==current['user']['nickname']]
    if len(candidates)!=1 or len(same)!=1:
        raise VpubError('comment_ambiguous','Cannot bind the exact comment ID to one visible Creator Center row.')
    row=candidates[0]
    await row.get_by_role('button',name='回复',exact=True).click()
    editor=await unique_visible(row.locator('[contenteditable=true]'),'target reply editor')
    await editor.fill(request['text'])
    if normalize(await editor.inner_text())!=normalize(request['text']):
        raise VpubError('reply_text_mismatch','Creator Center reply text differs; nothing sent.')
    button=await unique_visible(row.get_by_role('button',name='发送',exact=True),'target reply send')
    await risk_check(page);stop_check('douyin')
    await button.click(trial=True,timeout=10000)
    path=Path(payload['reply_receipt_path']);saved=read_json(path)
    saved.update(state='REPLY_SUBMITTING',submitted_text=request['text'],
                 before_reply_ids=[str(c['cid']) for c in data.get('comments',[])],send_surface='creator_center')
    atomic_json(path,saved)
    from patchright.async_api import TimeoutError as BrowserTimeout
    result={'state':'REPLY_UNVERIFIED','send_acknowledged':False}
    try:
        async with page.expect_response(lambda r:urlsplit(r.url).hostname=='creator.douyin.com'
                and 'comment' in urlsplit(r.url).path and r.request.method=='POST',timeout=20000) as pending:
            await button.click(timeout=15000)
        response=await pending.value
        try: result=acknowledgement(await response.json(),work,target['id'],request['text'],identity)
        except (ValueError,TypeError): pass
    except BrowserTimeout: pass
    finally:
        saved.update(result);atomic_json(path,saved)
    await risk_check(page)
    return {**result,'platform_writes':1,
            'hint':result.get('hint') or 'Submitted once through Creator Center; verify the exact reply ID after a fresh read. No automatic resend.'}

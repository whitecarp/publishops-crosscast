"""Hide/unhide only an exact reply to an owned X conversation."""
import re
from .core import VpubError, read_json, atomic_json, stop_check
from .browser_runtime import unique_visible, risk_check
from .operations_browser import open_work, comments


async def execute(page,identity,payload):
    r=payload['request'];target=r['comment'];work=r['work_id']
    if identity!=r['identity']:raise VpubError('wrong_account','X owner changed.')
    if target.get('is_owner') or target.get('author_id','').casefold()==identity.casefold():
        raise VpubError('other_author_required','X reply hiding requires a reply by another account.')
    if target.get('conversation_work_id')!=work:
        raise VpubError('wrong_thread','A comment from this owned conversation must be observed first.')
    await open_work(page,'x',identity,work)
    data=await comments(page,'x',identity,work,100)
    found=[c for c in data['items'] if c['id']==target['id']]
    if not found:
        # Only follow a link actually exposed by the owned conversation.
        link=page.locator('main a').filter(has=page.locator('svg[aria-label="Hidden replies"]'))
        named=page.get_by_role('link',name=re.compile(r'^(View )?[Hh]idden replies$'))
        available=[x for x in await named.all() if await x.is_visible()]
        if not available:available=[x for x in await link.all() if await x.is_visible()]
        if not available:
            root=await unique_visible(page.locator('main article').filter(has=page.locator(
                f'a[href="/{identity}/status/{work}"]').filter(has=page.locator('time'))),'owned conversation')
            await root.locator('[data-testid="caret"]').click()
            hidden=page.locator(f'a[href="/{identity}/status/{work}/hidden"]')
            available=[x for x in await hidden.all() if await x.is_visible()]
        if len(available)!=1:raise VpubError('hidden_replies_unavailable','No verified hidden-replies entry for this conversation; absence is not a hide confirmation.')
        href=await available[0].get_attribute('href')
        if not href or not re.fullmatch(r'/[A-Za-z0-9_]+/status/'+re.escape(work)+r'/hidden',href):
            raise VpubError('wrong_thread','Hidden-replies link points outside the requested conversation.')
        await available[0].click()
        await page.locator(f'main article a[href="/{target["author"]}/status/{target["id"]}"]').filter(has=page.locator('time')).wait_for(state='visible',timeout=20000)
        await risk_check(page)
    row=await unique_visible(page.locator('main article').filter(has=page.locator(
        f'a[href="/{target["author"]}/status/{target["id"]}"]').filter(has=page.locator('time'))),'exact reply')
    body=await unique_visible(row.locator('[data-testid="tweetText"]'),'reply text')
    if await body.inner_text()!=target['text']:raise VpubError('comment_changed','Reply text changed.')
    await row.locator('[data-testid="caret"]').click()
    menu=await unique_visible(page.get_by_role('menu'),'reply menu')
    hide=menu.get_by_role('menuitem',name=re.compile(r'^Hide reply$'))
    undo=menu.get_by_role('menuitem',name=re.compile(r'^Unhide reply$'))
    ons=[x for x in await undo.all() if await x.is_visible()];offs=[x for x in await hide.all() if await x.is_visible()]
    if len(ons)+len(offs)!=1:raise VpubError('hide_unavailable','The exact reply menu does not expose hide/unhide for this account.')
    hidden=bool(ons);observed={'identity':identity,'text':target['text'],'hidden':hidden,'deleted':False,'source':'exact_reply_menu_state'}
    if payload['operation']=='status':return observed
    if payload['operation']!='set' or r['kind']!='hide':raise VpubError('unsupported_operation','Expected explicit hide action.')
    if hidden==r['desired']:return {'state':'NO_CHANGE','platform_writes':0}
    await risk_check(page);stop_check('x')
    from .comment_actions import submitting
    submitting(payload['receipt_path'])
    await (ons or offs)[0].click()
    # The first hide may ask for confirmation; later hides can submit directly.
    confirm=page.get_by_role('button',name='Hide reply',exact=True) if r['desired'] else page.locator('[data-testid="confirmationSheetConfirm"]')
    try:await confirm.wait_for(state='visible',timeout=1500)
    except Exception:pass
    if await confirm.is_visible():
        if (await confirm.inner_text()).strip()!='Hide reply':raise VpubError('unexpected_confirmation','Unexpected action confirmation; inspect before retrying.')
        dialog=page.get_by_role('dialog').filter(has=confirm)
        if await dialog.count()!=1 or 'Hiding replies to your posts' not in await dialog.inner_text():
            raise VpubError('unexpected_confirmation','Unrecognized hide-reply dialog; inspect before retrying.')
        await risk_check(page);stop_check('x');await confirm.click()
    await page.wait_for_timeout(1200)
    saved=read_json(payload['receipt_path']);saved.update(state='COMMENT_UNVERIFIED',platform_writes=1);atomic_json(payload['receipt_path'],saved)
    return {'state':'COMMENT_UNVERIFIED','platform_writes':1}

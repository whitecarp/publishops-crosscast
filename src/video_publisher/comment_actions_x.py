"""Hide/unhide only an exact reply to an owned X conversation."""
from . import x_pages
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
    rows=x_pages.post_row(page,target['id'])
    if not found:
        # A hidden reply moves to the conversation's own hidden-replies page.
        await page.goto(f'https://x.com/{identity}/status/{work}/hidden',wait_until='domcontentloaded',timeout=60000)
        try:await rows.first.wait_for(state='visible',timeout=20000)
        except Exception:
            await risk_check(page)
            raise VpubError('hidden_replies_unavailable','This reply is not on the conversation or its hidden-replies page; absence is not a hide confirmation.') from None
        await risk_check(page)
    row=await unique_visible(rows,'exact reply')
    post=await x_pages.post_fields(row)
    if not post or post['text']!=target['text']:raise VpubError('comment_changed','Reply text changed.')
    await (await unique_visible(row.locator(x_pages.MORE),'reply menu button')).click()
    menu=await unique_visible(page.get_by_role('menu'),'reply menu')
    ons=[x for x in await menu.get_by_role('menuitem',name=x_pages.UNHIDE_REPLY).all() if await x.is_visible()]
    offs=[x for x in await menu.get_by_role('menuitem',name=x_pages.HIDE_REPLY).all() if await x.is_visible()]
    if len(ons)+len(offs)!=1:raise VpubError('hide_unavailable','The exact reply menu does not expose hide/unhide for this account.')
    hidden=bool(ons);observed={'identity':identity,'text':target['text'],'hidden':hidden,'deleted':False,'source':'exact_reply_menu_state'}
    if payload['operation']=='status':
        await page.keyboard.press('Escape')
        return observed
    if payload['operation']!='set' or r['kind']!='hide':raise VpubError('unsupported_operation','Expected explicit hide action.')
    if hidden==r['desired']:return {'state':'NO_CHANGE','platform_writes':0}
    await risk_check(page);stop_check('x')
    from .comment_actions import submitting
    submitting(payload['receipt_path'])
    await (ons or offs)[0].click()
    # The first hide may ask for confirmation; later hides can submit directly.
    confirm=page.get_by_role('button',name=x_pages.HIDE_REPLY) if r['desired'] else page.locator('[data-testid="confirmationSheetConfirm"]')
    try:await confirm.wait_for(state='visible',timeout=1500)
    except Exception:pass
    if await confirm.is_visible():
        if not r['desired']:raise VpubError('unexpected_confirmation','Unexpected action confirmation; inspect before retrying.')
        window=page.locator('[role="dialog"],[role="alertdialog"]').filter(has=confirm)
        if not await window.count():raise VpubError('unexpected_confirmation','Unrecognized hide-reply dialog; inspect before retrying.')
        await risk_check(page);stop_check('x');await confirm.click()
    await page.wait_for_timeout(1200)
    saved=read_json(payload['receipt_path']);saved.update(state='COMMENT_UNVERIFIED',platform_writes=1);atomic_json(payload['receipt_path'],saved)
    return {'state':'COMMENT_UNVERIFIED','platform_writes':1}


async def delete_own(page,identity,payload):
    """Delete the account's own reply, read from that reply's own page so a long conversation proves nothing either way."""
    r=payload['request'];target=r['comment'];reply=target['id']
    if identity!=r['identity']:raise VpubError('wrong_account','X owner changed.')
    if not target.get('is_owner'):raise VpubError('own_comment_required','Only your own reply can be deleted.')
    await page.goto(f'https://x.com/{identity}/status/{reply}',wait_until='domcontentloaded',timeout=60000)
    rows=x_pages.post_row(page,reply)
    try:await rows.first.wait_for(state='visible',timeout=20000)
    except Exception:
        await risk_check(page)
        missing=x_pages.GONE_PAGE.search(await page.locator('body').inner_text())
        return {'identity':identity,'text':None,'liked':None,'deleted':True if missing else None,'source':'reply_page'}
    await risk_check(page)
    row=await unique_visible(rows,'exact reply')
    post=await x_pages.post_fields(row)
    if not post or post['author'].casefold()!=identity.casefold():raise VpubError('own_comment_required','This page shows a reply by another account; nothing changed.')
    observed={'identity':identity,'text':post['text'],'liked':None,'deleted':False,'source':'reply_page'}
    if payload['operation']=='status':return observed
    if payload['operation']!='set':raise VpubError('unsupported_operation','Choose status or set.')
    if post['text']!=target['text']:raise VpubError('comment_changed','Reply text changed.')
    await (await unique_visible(row.locator(x_pages.MORE),'reply menu button')).click()
    menu=await unique_visible(page.get_by_role('menu'),'reply menu')
    await (await unique_visible(menu.get_by_role('menuitem',name=x_pages.DELETE_POST),'reply Delete')).click()
    confirm=page.locator('[role="dialog"],[role="alertdialog"]').get_by_role('button',name=x_pages.DELETE_POST)
    try:await confirm.first.wait_for(state='visible',timeout=10000)
    except Exception:raise VpubError('unexpected_confirmation','X did not ask to confirm the delete; nothing was deleted.') from None
    confirm=await unique_visible(confirm,'delete confirmation')
    await risk_check(page);stop_check('x')
    from .comment_actions import submitting
    submitting(payload['receipt_path'])
    await confirm.click()
    saved=read_json(payload['receipt_path']);saved.update(state='COMMENT_UNVERIFIED',platform_writes=1);atomic_json(payload['receipt_path'],saved)
    await page.wait_for_timeout(2000)
    return {'state':'COMMENT_UNVERIFIED','platform_writes':1}

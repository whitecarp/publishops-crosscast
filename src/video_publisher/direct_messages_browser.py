"""Official message UI through CrossCast-owned sessions; no private API calls."""
import re
from urllib.parse import urlsplit
from .browser_runtime import risk_check, unique_visible
from .core import VpubError, atomic_json, digest, read_json, stop_check

MESSAGE_ROWS = r'''root => [...root.querySelectorAll('[role="article"][aria-roledescription="message"]')].map(article=>{
 const row=article.parentElement;
 if(row.querySelectorAll('[role="article"][aria-roledescription="message"]').length!==1)return null;
 const label=row.querySelector('button[aria-label^="Reply to message from "],[role="button"][aria-label^="Reply to message from "]')?.getAttribute('aria-label');
 return {text:article.innerText,sender:label?.slice('Reply to message from '.length)||null};
}).filter(Boolean)'''

INBOX_SUMMARIES = '''es=>es.filter(e=>e.getClientRects().length && !e.closest("[role=presentation]") && e.innerText.includes("\\n") && !e.querySelector("[role=button]")).map(e=>e.innerText)'''


def outgoing_counts(items, identity):
    counts={}
    for item in items:
        if item.get('sender')==identity:
            key=digest(item['text']);counts[key]=counts.get(key,0)+1
    return counts


def new_outgoing(items, before, text, identity):
    count = lambda rows: sum(x.get('sender') == identity and x.get('text') == text for x in rows)
    if 'visible_outgoing_counts' in before:
        return count(items)>before['visible_outgoing_counts'].get(digest(text),0)
    # Migration for a pending receipt created before structured row collection.
    if 'items' not in before and text in before.get('visible_text', ''): return False
    if text in before.get('visible_text', '') and count(before.get('items', [])) == 0: return False
    return count(items) > count(before.get('items', []))


async def instagram_chat(page, recipient):
    await page.goto(f'https://www.instagram.com/{recipient}/', wait_until='domcontentloaded', timeout=45000)
    button = page.get_by_role('button', name='Message', exact=True)
    await button.wait_for(state='visible', timeout=30000)
    if urlsplit(page.url).path.strip('/').casefold() != recipient.casefold():
        raise VpubError('wrong_recipient', 'Profile redirected away from the requested recipient.')
    await risk_check(page)
    await button.click()
    editor = page.locator('[data-mw-file-dropzone] [contenteditable=true][role=textbox]')
    await editor.wait_for(state='visible', timeout=45000)
    area = await unique_visible(page.locator('[data-mw-file-dropzone]').filter(has=page.locator('[contenteditable=true][role=textbox]')), 'message conversation')
    # The profile link is inside the opened conversation, not the background profile.
    link = area.get_by_role('link', name='View profile', exact=True)
    if await link.count() != 1 or (await link.get_attribute('href') or '').strip('/').casefold() != recipient.casefold():
        raise VpubError('wrong_recipient', 'Conversation does not identify the requested profile.')
    await risk_check(page)
    return area, await unique_visible(editor, 'message composer')


async def execute(page, platform, identity, payload):
    action = payload['operation']; who = payload.get('recipient'); limit = payload.get('limit',20)
    if platform == 'x':
        from .direct_messages_x import execute as x
        return await x(page,identity,payload)
    if platform == 'douyin':
        if action != 'list':
            from .direct_messages_douyin import execute as douyin
            return await douyin(page,identity,payload)
        await page.get_by_text('私信管理', exact=True).click(timeout=30000)
        await page.get_by_text('朋友私信', exact=True).wait_for(timeout=30000)
        await page.wait_for_function('''() => document.body.innerText.includes('还没有收到私信') || [...document.querySelectorAll('[role="tab-panel"][aria-hidden="false"] li[role="list-item"]')].some(e=>e.querySelector('[class^="item-header-name-"]')?.textContent?.trim())''',timeout=20000)
        await risk_check(page)
        empty = await page.get_by_text('还没有收到私信', exact=True).is_visible()
        if action == 'list' and empty:
            return {'identity':identity,'items':[],'platform_writes':0,'scope':'Creator Center all-conversations tab; empty inbox observed.',
                    'can_start_conversation':False}
        panel=page.locator('[role="tab-panel"][aria-hidden="false"]')
        rows=await panel.locator('li[role="list-item"]').evaluate_all('es=>es.filter(e=>e.getClientRects().length).map(e=>({name:e.querySelector("[class^=item-header-name-]")?.innerText,preview:e.querySelector("[class^=item-content-]")?.innerText,time:e.querySelector("[class^=item-header-time-]")?.innerText})).filter(x=>x.name)')
        if rows:
            return {'identity':identity,'items':rows[:limit],'platform_writes':0,
                    'scope':'Visible Creator Center conversation summaries, not full history. Names are not recipient IDs; read/send requires the exact public profile ID.'}
        raise VpubError('dm_surface_unverified', 'Creator Center has no verified new-recipient composer in this adapter; no message sent.')
    if platform != 'instagram': raise VpubError('unsupported_operation','No browser DM adapter.')
    if action == 'list':
        await page.goto('https://www.instagram.com/direct/inbox/', wait_until='domcontentloaded')
        await page.get_by_role('link',name='Requests',exact=True).wait_for(timeout=30000)
        await page.wait_for_timeout(1500); await risk_check(page)
        # Summaries are local only and not used as recipient identifiers.
        rows = await page.locator('[data-pagelet="IGDInboxThreadListScrollableAreaPagelet"] [role=button]').evaluate_all(INBOX_SUMMARIES)
        if not rows:raise VpubError('dm_surface_unverified','No verified conversation previews; an empty DOM is not an empty-history confirmation.')
        return {'identity':identity,'items':[{'summary':s} for s in rows[:limit]],'platform_writes':0,
                'scope':'Visible conversation summaries only; use an exact handle for dm read/send.'}
    area, editor = await instagram_chat(page, who)
    messages = area.locator('[data-pagelet="IGDMessagesList"]')
    body = await messages.inner_text()
    items = await messages.evaluate(MESSAGE_ROWS)
    result = {'identity':identity,'recipient':who,'visible_text':body,'can_send':True,'platform_writes':0,
              'visible_outgoing_counts':outgoing_counts(items,identity),
              'items':items[-limit:], 'scope':'Visible conversation text; opening the chat can mark incoming messages read.'}
    if action == 'read': return result
    if action == 'verify':
        receipt=payload['receipt']; text=receipt['request']['text']
        # Require a new outgoing bubble in a fresh session, not text in the composer.
        result['matching_outgoing_message']=new_outgoing(items,receipt['before'],text,identity)
        result['source']='Fresh conversation message article and sender-labelled reply control; not recipient delivery.'
        return result
    if action != 'send': raise VpubError('invalid_operation','Unknown DM action.')
    text=payload['text']
    await editor.fill(text)
    if await editor.inner_text() != text: raise VpubError('message_text_mismatch','Composer text differs; nothing sent.')
    button=await unique_visible(area.get_by_role('button',name='Send',exact=True),'message Send')
    await risk_check(page); stop_check(platform)
    await button.click(trial=True,timeout=10000)
    from .direct_messages import submitting
    submitting(payload['receipt_path'])
    await button.click(timeout=15000)
    saved=read_json(payload['receipt_path']);saved.update(state='DM_UNVERIFIED',platform_writes=1)
    atomic_json(payload['receipt_path'],saved)
    await page.wait_for_timeout(2500)
    return {'state':'DM_UNVERIFIED','platform_writes':1}

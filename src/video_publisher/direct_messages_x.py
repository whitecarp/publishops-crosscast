"""X Chat's official UI, including its shadow-root controls and recipient gates."""
import re
from urllib.parse import urlsplit
from .browser_runtime import risk_check, unique_visible
from .core import VpubError, atomic_json, read_json, stop_check

MESSAGE_ROWS = '''(root, identity) => [...root.querySelectorAll('[data-dm-message-row]')].map(row=>{
 const sender=row.querySelector('span[id$="-sender"]')?.textContent;
 const article=row.querySelector('[role="article"][aria-labelledby]');
 const id=row.getAttribute('data-dm-message-row');
 const text=row.querySelector('[data-testid="message-text-'+id+'"] span[dir="auto"]')?.textContent;
 return {id,text:text||'',sender:sender==='You' && article && row.getAttribute('data-send-status')==='sent' ? identity : null};
}).filter(x=>x.text)'''


async def inbox(page):
    await page.locator('a[data-testid="AppTabBar_DirectMessage_Link"]').click()
    for _ in range(60):
        if '/pin/' in page.url or await page.locator('input[aria-label="Digit 1 of 4"]').count():
            raise VpubError('chat_unlock_required','X Chat requires user verification.','Run vpub dm unlock x and enter the PIN only in the official page; never share/reset it for this command.')
        if await page.locator('[data-testid=dm-new-chat-button]').is_visible():return
        await page.wait_for_timeout(500)
    raise VpubError('dm_surface_unverified','X Chat did not become ready; no message sent.')


async def unlock(page,identity):
    """User explicitly opens this interactive window; no PIN is inspected or entered."""
    import time
    await page.locator('a[data-testid="AppTabBar_DirectMessage_Link"]').click()
    deadline=time.monotonic()+300
    while time.monotonic()<deadline:
        if page.is_closed():raise VpubError('chat_unlock_cancelled','X Chat window was closed.')
        stop_check('x')
        if await page.locator('[data-testid=dm-new-chat-button]').is_visible():
            return {'state':'CHAT_READY','identity':identity,'platform_writes':0,'hint':'Future DM commands reuse this profile; another official challenge can require the user again.'}
        await page.wait_for_timeout(1000)
    raise VpubError('chat_unlock_timeout','X Chat was not ready within five minutes; profile retained.')


def conversation_id(url, recipient_id):
    path=urlsplit(url).path
    match=re.fullmatch(r'/i/chat/(\d+-\d+)',path)
    if not match or recipient_id not in match[1].split('-'):
        raise VpubError('wrong_recipient','Conversation URL does not contain the selected exact recipient.')
    return match[1]


async def chat(page, who):
    await inbox(page)
    await page.locator('[data-testid=dm-new-chat-button]').click()
    dialog=page.locator('[data-testid=dm-new-chat-modal]')
    await dialog.locator('[data-testid=new-dm-search-input]').fill(who)
    found=[]
    for _ in range(20):
        found=[]
        for row in await dialog.locator('[data-testid^=new-dm-user-suggestion-]').all():
            if '@'+who.casefold() in [s.strip().casefold() for s in (await row.inner_text()).splitlines()]:found.append(row)
        if len(found)==1:break
        await page.wait_for_timeout(500)
    if len(found)!=1:raise VpubError('wrong_recipient','No unique exact handle in X recipient results.')
    row=found[0]
    if await row.get_attribute('aria-disabled')=='true':raise VpubError('dm_send_unavailable','Recipient cannot receive this message.')
    uid=(await row.get_attribute('data-testid')).removeprefix('new-dm-user-suggestion-')
    if not uid.isdigit():raise VpubError('wrong_recipient','Recipient result has no stable numeric identity.')
    await row.click()
    editor=page.locator('[data-testid=dm-composer-textarea]')
    for _ in range(30):
        if await page.get_by_text(re.compile('has a closed inbox')).count():
            raise VpubError('recipient_inbox_closed','Recipient has a closed inbox; no X Number workaround or send attempted.')
        if await editor.is_visible():break
        await page.wait_for_timeout(500)
    if not await editor.is_visible():raise VpubError('dm_send_unavailable','No verified message composer; nothing sent.')
    await risk_check(page)
    cid=conversation_id(page.url,uid)
    scope=await unique_visible(page.locator('[data-testid=dm-conversation-content]'),'X conversation')
    return scope,editor,cid


async def execute(page,identity,payload):
    action=payload['operation'];who=payload.get('recipient');limit=payload.get('limit',20)
    if action=='list':
        await inbox(page);await risk_check(page)
        # The toolbar renders before conversation rows; do not snapshot that gap.
        for _ in range(20):
            if await page.locator('[data-testid^=dm-conversation-item-]').count():break
            await page.wait_for_timeout(500)
        rows=await page.locator('[data-testid^=dm-conversation-item-]').evaluate_all('es=>es.filter(e=>e.getClientRects().length).map(e=>({conversation_id:e.getAttribute("data-testid").slice("dm-conversation-item-".length),summary:e.innerText}))')
        return {'identity':identity,'items':rows[:limit],'complete':False,'platform_writes':0,'scope':'Visible inbox summaries only; an empty visible page does not establish empty history. Use an exact handle for read/send.'}
    scope,editor,cid=await chat(page,who)
    messages=scope.locator('[data-testid=dm-message-list]')
    body=await messages.inner_text()
    items=await messages.evaluate(MESSAGE_ROWS,identity)
    from .direct_messages_browser import outgoing_counts
    result={'identity':identity,'recipient':who,'conversation_id':cid,'visible_text':body,'items':items[-limit:],
            'visible_outgoing_counts':outgoing_counts(items,identity),
            'can_send':await editor.is_enabled(),'platform_writes':0,'scope':'Visible X Chat; opening may mark incoming messages read.'}
    if action=='read':return result
    receipt=payload.get('receipt') or read_json(payload['receipt_path'])
    if receipt['before'].get('conversation_id')!=cid:raise VpubError('wrong_recipient','Conversation changed from the preflight.')
    if action=='verify':
        from .direct_messages_browser import new_outgoing
        result['matching_outgoing_message']=new_outgoing(items,receipt['before'],receipt['request']['text'],identity)
        result['source']='Fresh exact-recipient X Chat row, You label and sent status; not recipient delivery.'
        return result
    if action!='send':raise VpubError('invalid_operation','Unknown DM action.')
    await editor.fill(payload['text'])
    if await editor.input_value()!=payload['text']:raise VpubError('message_text_mismatch','Composer text differs; nothing sent.')
    button=await unique_visible(scope.locator('[data-testid=dm-composer-send-button]'),'X message Send')
    await risk_check(page);stop_check('x');await button.click(trial=True,timeout=10000)
    from .direct_messages import submitting
    submitting(payload['receipt_path']);await button.click(timeout=15000)
    saved=read_json(payload['receipt_path']);saved.update(state='DM_UNVERIFIED',platform_writes=1);atomic_json(payload['receipt_path'],saved)
    await page.wait_for_timeout(2000)
    return {'state':'DM_UNVERIFIED','platform_writes':1}

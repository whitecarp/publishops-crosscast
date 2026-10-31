"""Douyin text messages via the recipient's official public profile."""
import re
from urllib.parse import urlsplit
from .browser_runtime import risk_check, unique_visible
from .core import VpubError, atomic_json, read_json, stop_check

MESSAGE_ROWS = '''(root, identity) => [...root.querySelectorAll('.messageMessageBoxcontentBox')].map(row=>({
 text:row.querySelector('.TextMessageTexttextInnerContent')?.innerText || '',
 sender:row.classList.contains('messageMessageBoxisFromMe') ? identity : null
})).filter(x=>x.text)'''


async def execute(page, identity, payload):
    who=payload['recipient']; action=payload['operation']
    if not re.fullmatch(r'MS4w[A-Za-z0-9_-]{10,100}',who or ''):
        raise VpubError('profile_id_required','Use the exact ID after /user/ in an observed Douyin public profile URL.')
    await page.goto('https://www.douyin.com/user/'+who,wait_until='domcontentloaded',timeout=45000)
    button=page.get_by_role('button',name='私信',exact=True)
    await button.wait_for(state='visible',timeout=30000); await risk_check(page)
    if urlsplit(page.url).path.rstrip('/')!='/user/'+who:
        raise VpubError('wrong_recipient','Public profile redirected to another recipient.')
    # Open the message button on this exact profile, never an inbox nickname match.
    await button.click()
    scope=page.locator('[data-stack-layer="chat"]')
    await scope.locator('[data-e2e="msg-input"]').wait_for(state='visible',timeout=30000)
    scope=await unique_visible(scope,'Douyin conversation')
    title=await scope.locator('.StackLayoutStackChatHeadertitle').inner_text()
    if not title.strip():raise VpubError('wrong_recipient','Conversation has no recipient heading.')
    editor=scope.locator('.messageEditorinputArea[contenteditable=true]')
    messages=scope.locator('.messageMessageListlist')
    body=await messages.inner_text()
    items=await messages.evaluate(MESSAGE_ROWS,identity)
    from .direct_messages_browser import outgoing_counts
    result={'identity':identity,'recipient':who,'recipient_name':title,'visible_text':body,
            'visible_outgoing_counts':outgoing_counts(items,identity),
            'items':items[:payload.get('limit',20)],
            'can_send':await editor.is_visible(),'platform_writes':0,
            'scope':'Visible official conversation; opening may mark incoming messages read.'}
    if action=='read':return result
    if action=='verify':
        from .direct_messages_browser import new_outgoing
        receipt=payload['receipt']
        result['matching_outgoing_message']=new_outgoing(items,receipt['before'],receipt['request']['text'],identity)
        result['source']='Fresh exact-profile conversation and outgoing message bubble; not recipient delivery.'
        return result
    if action!='send':raise VpubError('invalid_operation','Unknown DM action.')
    text=payload['text'];await editor.fill(text)
    actual=(await editor.inner_text()).replace('\u200b','').strip()
    if actual!=text:raise VpubError('message_text_mismatch','Message composer text differs; nothing sent.')
    button=await unique_visible(scope.locator('.e2e-send-msg-btn'),'Douyin message send')
    await risk_check(page);stop_check('douyin');await button.click(trial=True,timeout=10000)
    from .direct_messages import submitting
    submitting(payload['receipt_path']);await button.click(timeout=15000)
    saved=read_json(payload['receipt_path']);saved.update(state='DM_UNVERIFIED',platform_writes=1)
    atomic_json(payload['receipt_path'],saved)
    await page.wait_for_timeout(1500)
    return {'state':'DM_UNVERIFIED','platform_writes':1}

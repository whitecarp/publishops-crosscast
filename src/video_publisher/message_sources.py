"""Read-only signal sources, with independently reported coverage limits."""
from . import x_pages
from .core import VpubError, stop_check
from .browser_runtime import risk_check


async def x_mentions(page, identity, limit):
    await page.goto('https://x.com/notifications/mentions',wait_until='domcontentloaded',timeout=60000)
    for _ in range(20):
        await risk_check(page)
        if await page.locator('main article').count():break
        await page.wait_for_timeout(500)
    if not page.url.startswith('https://x.com/notifications/mentions'):
        raise VpubError('wrong_surface','Expected the official Mentions tab.')
    rows=[]
    for article in await page.locator('main article').all():
        post=await x_pages.post_fields(article)
        if not post or not post['text'] or post['is_ad']:continue
        rows.append({'id':post['id'],'author':post['author'],'text':post['text'],'url':'https://x.com'+post['href'],
                     'is_owner':post['author'].casefold()==identity.casefold(),
                     'published_at':post['published_at']})
        if len(rows)>=limit:break
    if not rows:
        text=await page.locator('main').inner_text()
        if not any(s in text for s in ('Nothing to see here','When someone mentions you','Join the conversation')):
            raise VpubError('mentions_surface_unverified','No identifiable mention rows or explicit empty state.')
    return {'identity':identity,'items':rows,'complete':False,'source':'x_mentions_tab',
            'scope':'Visible Mentions tab; not full notification history.'}


async def execute(page, platform, identity, payload):
    sources=payload.get('sources',[]);limit=payload.get('limit',20)
    if not sources or any(s not in {'mentions','dm_preview'} for s in sources) or type(limit) is not int or not 1<=limit<=50:
        raise VpubError('invalid_sources','Select mentions/dm_preview with limit 1–50.')
    result={}
    # Mentions first: X Chat is a separate shadow-DOM application.
    for source in sorted(set(sources),reverse=True):
        stop_check(platform)
        if source=='mentions' and platform not in {'x','instagram','douyin'}:
            result[source]={'state':'UNSUPPORTED','reason':'No verified mention-inbox adapter on this platform.'};continue
        try:
            if source=='mentions':
                if platform=='x':value=await x_mentions(page,identity,limit)
                else:
                    from .mentions_browser import instagram_mentions, douyin_mentions
                    value=await (instagram_mentions if platform=='instagram' else douyin_mentions)(page,identity,limit)
            else:
                if platform=='douyin' and 'mentions' in result:
                    from .browser_runtime import signed_in_page
                    page,_=await signed_in_page(page.context,platform,identity)
                from .direct_messages_browser import execute as dm
                value=await dm(page,platform,identity,{'operation':'list','limit':limit})
            result[source]={'state':'OBSERVED',**value}
        except Exception as exc:
            result[source]={'state':'FAILED','code':exc.code if isinstance(exc,VpubError) else type(exc).__name__}
            # No alternate route through a challenged account.
            for remaining in sources:
                result.setdefault(remaining,{'state':'NOT_CHECKED','reason':'Earlier source failed.'})
            break
    return {'identity':identity,'sources':result,'platform_writes':0,
            'read_side_effects':'Opening platform notification surfaces may mark their notifications read; local handled marks are separate.'}

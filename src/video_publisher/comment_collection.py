"""Bounded DOM comment collection; expansion is not a complete-history claim."""
import re
import time
from .core import VpubError, stop_check
from .browser_runtime import risk_check


def budgets(limit, pages, reply_pages):
    if any(type(v) is not int for v in (limit, pages, reply_pages)) or not 1 <= limit <= 100 or not 1 <= pages <= 10 or not 0 <= reply_pages <= 5:
        raise VpubError('invalid_limit', 'Use limit 1–100, pages 1–10 and reply-pages 0–5.')


SCROLL = r'''element => {
 let node=element;
 while(node && node!==document.body){
  const s=getComputedStyle(node);
  if(/auto|scroll/.test(s.overflowY) && node.scrollHeight>node.clientHeight+4){
   const before=node.scrollTop;node.scrollTop+=Math.max(200,node.clientHeight*.8);
   return node.scrollTop!==before;
  }
  node=node.parentElement;
 }
 return false;
}'''


async def expand(page, platform, remaining):
    if not remaining: return 0
    if platform == 'douyin':
        buttons=page.locator('[data-e2e="comment-list"]').get_by_text(re.compile(r'^展开\s*\d+\s*条回复'))
    elif platform == 'instagram':
        buttons=page.get_by_role('button', name=re.compile(r'^View (?:all )?replies\s*\(\d+\)$', re.I))
    else:
        # Exact labels, scoped to this conversation, never the recommendations rail.
        buttons=page.locator('main').get_by_role('button',name=re.compile(r'^(Show more replies|Show additional replies)$'))
    used=0
    for button in (await buttons.all())[:remaining]:
        if await button.is_visible():
            await risk_check(page);stop_check(platform)
            await button.click(timeout=5000);used+=1
    if used: await page.wait_for_timeout(700)
    return used


async def scroll(page, platform):
    if platform == 'x':
        if not await page.locator('main article').count(): return False
        return await page.evaluate('''() => {const e=document.scrollingElement;const p=e.scrollTop;e.scrollTop+=Math.max(300,innerHeight*.8);return e.scrollTop!==p;}''')
    rows=page.locator('[data-e2e="comment-list"]' if platform=='douyin' else '[data-crosscast-comment-row]')
    if not await rows.count(): return False
    return await rows.first.evaluate(SCROLL)


async def collect(page, platform, identity, work, limit, pages=2, reply_pages=1):
    from .operations_browser import comments
    budgets(limit,pages,reply_pages)
    rows={}; started=time.monotonic(); expanded=0; reads=0; first=None
    reason='page_budget';stalled=0
    for _ in range(pages):
        stop_check(platform);await risk_check(page)
        if time.monotonic()-started>55: reason='time_budget';break
        # Read first so that the collector establishes a verified comment surface.
        value=await comments(page,platform,identity,work,100,expand_replies=False)
        first=first or value;reads+=1;before=len(rows)
        for c in value['items']:rows[c['id']]=c
        if len(rows)>=limit:reason='item_budget';break
        clicks=await expand(page,platform,max(0,reply_pages*5-expanded));expanded+=clicks
        if clicks:
            value=await comments(page,platform,identity,work,100,expand_replies=False)
            for c in value['items']:rows[c['id']]=c
            if len(rows)>=limit:reason='item_budget';break
        if reads>=pages:reason='page_budget';break
        moved=await scroll(page,platform)
        stalled=stalled+1 if len(rows)==before and not clicks else 0
        if not moved and not clicks:reason='no_visible_continuation';break
        if stalled>=2:reason='no_new_rows';break
        if moved:await page.wait_for_timeout(900)
    return {**(first or {}),'items':list(rows.values())[:limit],'complete':False,'next_cursor':None,
            'bounded':True,'pages_read':reads,'page_budget':pages,'reply_expansions':expanded,
            'stop_reason':reason,'source':'official_page_dom',
            'scope':'Bounded visible comments and expanded replies. Missing rows are not deleted; nested parent relationships are retained only when exposed by the DOM.'}

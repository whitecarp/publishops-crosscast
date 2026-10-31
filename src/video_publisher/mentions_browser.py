"""Bounded notification previews from verified official notification panels."""
import re
from urllib.parse import urlsplit, urljoin
from .core import VpubError, digest, stop_check
from .browser_runtime import risk_check, check_identity, identity as page_identity


def preview(platform, row):
    """DOM-only notifications without an exposed event ID are content snapshots."""
    text='\n'.join(s.strip() for s in row['text'].splitlines() if s.strip() and not re.fullmatch(
        r'\d+\s*(?:s|m|h|d|w|秒|分钟|小时|天|周)(?: ago|前)?|\d{1,2}-\d{1,2}|Today|Yesterday|今天|昨天|刚刚',s.strip(),re.I))
    host={'instagram':'www.instagram.com','douyin':'www.douyin.com'}[platform]
    links=[]
    for link in row.get('links',[]):
        url=urljoin('https://'+host,link);parts=urlsplit(url)
        if parts.scheme=='https' and parts.hostname in {host,host.removeprefix('www.')}:
            links.append(url)
    # A post URL identifies content, not a notification. Never use it alone as
    # an event ID, nor use a preview key to address a reply or private message.
    target=next((u for u in links if re.search(r'/(?:p|reel|video)/',urlsplit(u).path)),None)
    key='preview-'+digest([platform,text,sorted(set(links))])
    return {'id':key,'text':text,'url':target,'stable_source_id':False,
            'source_kind':'notification_preview','is_owner':False}


IG_ROWS=r'''root=>{
 const match=/(?:mentioned you|tagged you|提及了?你|提到了?你|標註了?你|标记了?你)/i;
 const rows=[];const used=new Set();
 for(const leaf of root.querySelectorAll('span')){
  if(!leaf.getClientRects().length||!match.test(leaf.innerText)||[...leaf.children].some(e=>match.test(e.innerText)))continue;
  let row=leaf;
  for(let i=0;row&&row!==root&&i<8;i++,row=row.parentElement){
   if(row.querySelector('[aria-selected]')||row.innerText.length>1800)break;
   if(row.querySelector('a[href]')){
    const text=row.innerText;
    if(!used.has(text)){used.add(text);rows.push({text,links:[...row.querySelectorAll('a[href]')].map(a=>a.getAttribute('href'))});}
    break;
   }
  }
 }
 return rows;
}'''


async def instagram_panel(page):
    # Keyboard activation waits for the actual navigation element and avoids
    # clicking a loading feed or treating its @handles as notifications.
    nav=page.get_by_role('link',name=re.compile(r'^(Notifications|通知)$'))
    await nav.wait_for(state='visible',timeout=30000)
    await nav.press('Enter')
    tab=page.get_by_role('button',name='Tags & mentions',exact=True)
    await tab.wait_for(state='visible',timeout=20000)
    await tab.click()
    await page.wait_for_function('''()=>[...document.querySelectorAll('[role=button]')].some(e=>e.innerText==='Tags & mentions'&&e.getAttribute('aria-selected')==='true')''',timeout=15000)
    found=await tab.evaluate('''e=>{for(let i=0;e&&i<8;i++,e=e.parentElement){if([...e.querySelectorAll('[role=heading]')].some(h=>h.innerText==='Notifications')){e.setAttribute('data-crosscast-mentions-panel','instagram');return true;}}return false;}''')
    if not found:raise VpubError('mentions_surface_unverified','The notification panel cannot be isolated from the feed.')
    return page.locator('[data-crosscast-mentions-panel="instagram"]')


async def instagram_mentions(page,identity,limit):
    panel=await instagram_panel(page)
    rows=[];empty=False
    for _ in range(20):
        stop_check('instagram');await risk_check(page)
        rows=await panel.evaluate(IG_ROWS)
        empty=await panel.get_by_text("When someone likes or comments on one of your posts, you'll see it here.",exact=True).is_visible()
        if rows or empty:break
        await page.wait_for_timeout(500)
    if not rows and not empty:raise VpubError('mentions_surface_unverified','No identifiable notification rows or explicit empty state in Tags & mentions.')
    return {'identity':identity,'items':[preview('instagram',r) for r in rows[:limit]],'complete':False,
            'source':'instagram_tags_mentions_filter','empty_state_observed':empty,
            'scope':'Visible Tags & mentions only. ID-less previews can coalesce identical notifications; not complete history or reply targets.'}


DY_ROWS=r'''root=>{
 const match=/(?:提到了你|提及了你|@了你)/;
 const result=[];const used=new Set();
 for(const leaf of root.querySelectorAll('span,div')){
  if(!leaf.getClientRects().length||!match.test(leaf.innerText)||[...leaf.children].some(e=>match.test(e.innerText)))continue;
  let row=leaf;
  for(let i=0;row&&row!==root&&i<8;i++,row=row.parentElement){
   if(row.querySelector('[data-e2e="cur-filter"]')||row.innerText.length>1800)break;
   const names=row.querySelectorAll('[data-e2e="user-name-card"]');
   if(names.length===1){
    const text=[names[0].innerText,row.querySelector('pre')?.innerText||'',leaf.innerText].filter(Boolean).join('\n');
    if(!used.has(text)){used.add(text);result.push({text,links:[...row.querySelectorAll('a[href]')].map(a=>a.getAttribute('href'))});}
    break;
   }
  }
 }
 return result;
}'''


async def douyin_mentions(page,identity,limit):
    # Creator Center and the public site must both identify the same account.
    await page.goto('https://www.douyin.com/user/self?from_nav=1',wait_until='domcontentloaded',timeout=60000)
    actual=None
    for _ in range(30):
        stop_check('douyin');await risk_check(page)
        actual=await page_identity(page,'douyin')
        if actual:break
        await page.wait_for_timeout(500)
    check_identity(actual,identity)
    await page.get_by_text('通知',exact=True).hover()
    panel=page.locator('[data-e2e="listDlgTest-container"]')
    await panel.wait_for(state='visible',timeout=15000)
    await panel.locator('[data-e2e="cur-filter"]').click()
    await page.get_by_text('@我的',exact=True).click()
    await page.wait_for_function('''()=>[...document.querySelectorAll('[data-e2e="cur-filter"]')].some(e=>e.getClientRects().length&&e.innerText==='@我的')''',timeout=15000)
    await page.wait_for_timeout(1200)
    rows=[];empty=False
    for _ in range(20):
        stop_check('douyin');await risk_check(page)
        rows=await panel.evaluate(DY_ROWS)
        names=await panel.locator('[data-e2e="user-name-card"]').count()
        text=await panel.inner_text()
        empty=not names and any(s in text for s in ('暂无消息','暂时没有更多了','暂无互动消息'))
        if rows or empty:break
        await page.wait_for_timeout(500)
    if not rows and not empty:raise VpubError('mentions_surface_unverified','The @ mentions filter has no identifiable rows or explicit empty state.')
    return {'identity':identity,'items':[preview('douyin',r) for r in rows[:limit]],'complete':False,
            'source':'douyin_notifications_at_filter','empty_state_observed':empty,
            'scope':'Visible @ mentions only. DOM previews lack stable event IDs; identical content can coalesce. Not full history or reply targets.'}

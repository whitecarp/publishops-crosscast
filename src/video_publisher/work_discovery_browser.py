"""Bounded official-page discovery and fresh per-work management proof."""
import re
from urllib.parse import urlsplit
from .core import VpubError, stop_check
from .browser_runtime import risk_check, unique_visible
from .browser_backends import MANAGE, links, work_url


async def scan(page, platform, identity, limit, pages):
    if type(limit) is not int or not 1 <= limit <= 200 or type(pages) is not int or not 1 <= pages <= 10:
        raise VpubError('invalid_limit','Use limit 1–200 and pages 1–10.')
    url = MANAGE if platform=='douyin' else f'https://www.instagram.com/{identity}/' if platform=='instagram' else f'https://x.com/{identity}'
    await page.goto(url,wait_until='domcontentloaded',timeout=60000)
    rows={}; reason='page_budget'; stalled=0; omitted=0
    for index in range(pages):
        stop_check(platform); await risk_check(page)
        await page.wait_for_timeout(2000 if index else 4000)
        before=len(rows)
        # Scope link extraction to the authenticated account profile, not recommendation rails.
        found=await links(page,platform,identity)
        if platform=='instagram':
            hrefs=await page.locator('main a[href]').evaluate_all('es=>es.map(e=>e.href)')
            ids={m[1] for href in hrefs if (m:=re.search(r'/(?:p|reel)/([\w-]+)/?',urlsplit(href).path))}
            found={k:v for k,v in found.items() if k in ids}
        rows.update(found)
        if platform=='douyin':
            titles=page.locator('[class^="info-title-text-"]')
            for title in (await titles.all())[:max(0,limit-len(rows))]:
                text=await title.inner_text()
                card=title.locator('xpath=ancestor::div[starts-with(@class,"video-card-content-")]')
                if re.search('审核中|定时发布',await card.inner_text()): omitted+=1; continue
                async with page.expect_popup(timeout=15000) as opened: await title.click()
                detail=await opened.value
                try:
                    await detail.wait_for_url(re.compile(r'/work-detail/\d+'),timeout=15000)
                    u=urlsplit(detail.url); match=re.fullmatch(r'/creator-micro/work-management/work-detail/(\d+)',u.path)
                    if u.hostname=='creator.douyin.com' and match:
                        k=match[1]; rows[k]={'id':k,'text':text,'url':work_url(platform,identity,k)}
                finally: await detail.close()
        if len(rows)>=limit: reason='item_budget'; break
        if index+1>=pages: break
        stalled=stalled+1 if len(rows)==before else 0
        if stalled>=2: reason='no_new_rows'; break
        if platform=='douyin':
            next_button=page.locator('li.ant-pagination-next:not(.ant-pagination-disabled), button[aria-label="下一页"]:not([disabled])')
            if await next_button.count()==1 and await next_button.is_visible():
                await next_button.click(); continue
        moved=await page.evaluate('''() => {
          const nodes=[document.scrollingElement,...document.querySelectorAll('main,main div')];
          const e=nodes.filter(e=>e && e.clientHeight>200 && e.scrollHeight>e.clientHeight+4)
            .filter(e=>e===document.scrollingElement || /auto|scroll/.test(getComputedStyle(e).overflowY))
            .sort((a,b)=>b.clientHeight-a.clientHeight)[0];
          if(!e)return false;const before=e.scrollTop;e.scrollTop+=Math.max(300,e.clientHeight*.85);return e.scrollTop!==before;
        }''')
        if not moved: reason='no_visible_continuation'; break
    if not rows:
        body=await page.locator('body').inner_text()
        if not re.search(r'暂无作品|暂无内容|No posts yet|hasn.t posted',body,re.I):
            raise VpubError('works_layout_unverified','No verified works or explicit empty state; discovery is incomplete.')
    return {'identity':identity,'items':list(rows.values())[:limit],'pages_read':index+1,
            'complete':False,'next_cursor':None,'stop_reason':reason,'omitted_cards':omitted,
            'source':'official_profile_or_creator_list','platform_writes':0}


async def execute(page,platform,identity,payload):
    from .operations import work_id
    if payload['action']=='discover_works':
        return await scan(page,platform,identity,payload['limit'],payload['pages'])
    work=work_id(platform,payload['work_id'])
    title=None
    if platform=='douyin':
        result=await scan(page,platform,identity,200,payload['pages'])
        matches=[v for v in result['items'] if v['id']==work]
        if len(matches)!=1: raise VpubError('owner_unverified','Work not found in this account’s bounded creator list; increase --pages if needed.')
        title=matches[0].get('text'); evidence='authenticated_creator_card_and_exact_detail_id'
    else:
        from .operations_browser import open_work, x_root
        await open_work(page,platform,identity,work)
        if platform=='x':
            root=await x_root(page,identity,work)
            if not await root.locator(f'[data-testid="User-Name"] a[href="/{identity}"]').count():
                raise VpubError('owner_unverified','Exact post author was not verified.')
            evidence='exact_post_permalink_and_author'
        else:
            more=await unique_visible(page.locator('svg[aria-label="More options"]'),'post menu')
            await more.click()
            edit=page.get_by_role('button',name='Edit',exact=True)
            try: await edit.wait_for(state='visible',timeout=8000)
            except Exception: pass
            if await edit.count()!=1 or not await edit.is_visible():
                await page.keyboard.press('Escape')
                raise VpubError('owner_unverified','The work does not expose an owner Edit control.')
            # Inspecting the menu is read-only; do not enter edit or save.
            await page.keyboard.press('Escape'); evidence='exact_work_and_owner_edit_control'
    await risk_check(page)
    return {'identity':identity,'work_id':work,'title':title,'url':work_url(platform,identity,work),
            'owner_verified':True,'ownership_evidence':evidence,'platform_writes':0}

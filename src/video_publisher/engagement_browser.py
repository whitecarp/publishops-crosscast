"""Likes and saves through real controls in the CLI-owned browser profile."""
import asyncio
import re
from urllib.parse import urlsplit
from .browser_runtime import risk_check, unique_visible
from .core import VpubError, stop_check


async def open_target(page,platform,work):
    url={'instagram':f'https://www.instagram.com/p/{work}/','douyin':f'https://www.douyin.com/video/{work}',
         'x':f'https://x.com/i/status/{work}'}[platform]
    await page.goto(url,wait_until='domcontentloaded',timeout=60000)
    selector={'instagram':'svg[aria-label="Comment"]','douyin':f'[data-e2e="detail-video-info"][data-e2e-aweme-id="{work}"]',
              'x':f'article a[href$="/status/{work}"]'}[platform]
    await page.locator(selector).first.wait_for(state='visible',timeout=45000)
    await risk_check(page)
    u=urlsplit(page.url)
    pattern={'instagram':rf'/(p|reel)/{re.escape(work)}/?', 'douyin':rf'/video/{work}/?',
             'x':rf'/(?:[A-Za-z0-9_]+/status|i/status)/{work}/?'}[platform]
    if u.hostname!=urlsplit(url).hostname or not re.fullmatch(pattern,u.path):
        raise VpubError('wrong_work','The page left the exact requested work.')


async def pair(root,off,on,label):
    candidates=root.locator(off+','+on)
    # The post timestamp can render before its action toolbar has hydrated.
    await candidates.first.wait_for(state='visible',timeout=20000)
    node=await unique_visible(candidates,label)
    state=await node.evaluate('(e,selector)=>e.matches(selector)',on)
    return state,node


def douyin_detail_state(data,work):
    if not isinstance(data,dict) or not isinstance(data.get('aweme_detail'),dict):
        raise VpubError('state_unavailable','The official page detail response is missing.')
    detail=data['aweme_detail']
    if data.get('status_code')!=0 or str(detail.get('aweme_id'))!=work:
        raise VpubError('state_unavailable','The official page detail response did not identify the requested video.')
    values=[detail.get('user_digged'),detail.get('collect_stat')]
    if any(type(v) is not int or v not in (0,1) for v in values):
        raise VpubError('state_unavailable','The official page did not return exact account like/save states.')
    return {'liked':bool(values[0]),'saved':bool(values[1])}


async def controls(page,platform,work):
    if platform=='x':
        root=await unique_visible(page.locator('article').filter(has=page.locator(f'a[href$="/status/{work}"] time')),'target post')
        liked,like=await pair(root,'button[data-testid="like"]','button[data-testid="unlike"]','post Like')
        saved,save=await pair(root,'button[data-testid="bookmark"]','button[data-testid="removeBookmark"]','post Bookmark')
    elif platform=='instagram':
        # The post action section excludes like icons attached to comments.
        toolbar='section:has(svg[aria-label="Comment"]):has(svg[aria-label="Save"],svg[aria-label="Remove"])'
        # Ancestor sections also contain these icons; select the innermost toolbar.
        root=await unique_visible(page.locator(toolbar).filter(has_not=page.locator(toolbar)),'post action section')
        liked,like=await pair(root,'svg[aria-label="Like"]','svg[aria-label="Unlike"]','post Like')
        saved,save=await pair(root,'svg[aria-label="Save"]','svg[aria-label="Remove"]','post Save')
    else:
        root=await unique_visible(page.locator(f'[data-e2e="detail-video-info"][data-e2e-aweme-id="{work}"]'),'target video details')
        found={}
        for node in await root.locator('[tabindex="0"][aria-describedby]').all():
            if not await node.is_visible():continue
            await node.hover()
            tip=await node.get_attribute('aria-describedby')
            if not tip or not re.fullmatch(r'[A-Za-z0-9_-]+',tip):continue
            tooltip=page.locator(f'[id="{tip}"]')
            try:
                await tooltip.wait_for(state='visible',timeout=1800)
                text=(await tooltip.inner_text()).strip()
            except Exception:continue
            # These labels locate controls only. Douyin retains "Like" even
            # when already liked; never infer a false state from that tooltip.
            labels={'点赞':'like','取消点赞':'like','收藏':'save','取消收藏':'save'}
            if text in labels:
                kind=labels[text]
                if kind in found:raise VpubError('layout_changed','Ambiguous video action controls.')
                found[kind]=(None,node)
        if set(found)!={'like','save'}:raise VpubError('state_unavailable','Exact Like/Save tooltip states are unavailable; no click performed.')
        liked,like=found['like'];saved,save=found['save']
    return {'liked':liked,'saved':saved},{'like':like,'save':save}


async def execute(page,platform,identity,payload):
    work=payload['work_id'];loaded=None
    if platform=='douyin':
        # Observe only the normal page's own detail load; no forged requests,
        # copied credentials, hidden app state or third-party API client.
        def detail_response(response):
            u=urlsplit(response.url)
            return u.hostname=='www.douyin.com' and u.path=='/aweme/v1/web/aweme/detail/'
        async with page.expect_response(detail_response,timeout=45000) as response:
            await open_target(page,platform,work)
        loaded=douyin_detail_state(await asyncio.wait_for((await response.value).json(),timeout=10),work)
    else:await open_target(page,platform,work)
    values,nodes=await controls(page,platform,work)
    if loaded is not None:values=loaded
    observed={**values,'identity':identity,'collection':None,'source':'official_page_controls',
              'save_semantics':'private bookmark' if platform=='x' else 'platform default saved items'}
    if platform=='douyin':observed['source']='official_page_loaded_aweme_detail'
    if payload['operation']=='status':return observed
    if payload['operation']!='set':raise VpubError('invalid_action','Unsupported engagement action.')
    kind=payload['kind'];desired=payload['desired'];field='liked' if kind=='like' else 'saved'
    if values[field] is desired:return {'state':'NO_CHANGE','platform_writes':0}
    await risk_check(page);stop_check(platform)
    node=nodes[kind]
    await node.click(trial=True,timeout=10000)
    from .engagement import submitting
    submitting(payload['receipt_path'])
    await node.click(timeout=15000)
    await page.wait_for_timeout(1200)
    await risk_check(page)
    return {'state':'ACTION_UNVERIFIED','platform_writes':1}

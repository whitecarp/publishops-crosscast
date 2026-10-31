"""Observed official UI for private saved collections and creator work collections."""
import asyncio
import re
from urllib.parse import urlsplit
from . import browser_runtime as rt
from .core import VpubError, now
from .collections import before_submit


def folder_response(data):
    """Fail closed if X changes its official page response shape."""
    try:section=data['data']['viewer']['user_results']['result']['bookmark_collections_slice']
    except (KeyError,TypeError):raise VpubError('layout_changed','Bookmark folder response schema changed.')
    if not isinstance(section,dict) or not isinstance(section.get('items'),list) or not isinstance(section.get('slice_info'),dict):
        raise VpubError('layout_changed','Bookmark folder page is incomplete.')
    rows=[]
    for item in section['items']:
        if not isinstance(item,dict) or not re.fullmatch(r'\d{1,40}',str(item.get('id',''))) or not isinstance(item.get('name'),str):
            raise VpubError('layout_changed','Unexpected bookmark folder identity.')
        rows.append({'id':str(item['id']),'title':item['name']})
    if len({x['id'] for x in rows})!=len(rows):raise VpubError('layout_changed','Duplicate folder IDs in response.')
    return rows,section['slice_info']=={}


def created_id(platform,data,title):
    try:
        value=data['data']['bookmark_collection_create'] if platform=='x' else data['data']['xig_media_collection_create']['collection']
    except (KeyError,TypeError):return None
    if not isinstance(value,dict) or value.get('name')!=title or not re.fullmatch(r'\d{1,40}',str(value.get('id',''))):
        raise VpubError('wrong_collection','Creation response has an unexpected ID or title.')
    return str(value['id'])


def instagram_response(data):
    try:section=data['data']['viewer']['collections_unified_with_auto_collections']
    except (KeyError,TypeError):return None
    if not isinstance(section,dict) or not isinstance(section.get('edges'),list) or not isinstance(section.get('page_info'),dict) or type(section['page_info'].get('has_next_page')) is not bool:
        raise VpubError('layout_changed','Instagram collection pagination schema changed.')
    rows=[]
    for edge in section['edges']:
        node=edge.get('node',{}) if isinstance(edge,dict) else {}
        if node.get('__typename')=='IGAllMediaAutoCollection' and node.get('collection_id')=='ALL_MEDIA_AUTO_COLLECTION':
            continue  # System aggregate, never a mutable named collection.
        if node.get('__typename')!='MediaCollection' or not re.fullmatch(r'\d{1,40}',str(node.get('collection_id',''))) or not isinstance(node.get('collection_name'),str):
            raise VpubError('layout_changed','Unknown Instagram collection type or identity.')
        rows.append({'id':str(node['collection_id']),'title':node['collection_name']})
    if len({x['id'] for x in rows})!=len(rows):raise VpubError('layout_changed','Duplicate collection IDs.')
    return rows,not section['page_info']['has_next_page']


def response_capture(page,platform,extract):
    future=asyncio.get_running_loop().create_future()
    async def capture(response):
        parsed=urlsplit(response.url)
        match=(platform=='x' and parsed.hostname=='x.com' and parsed.path.endswith('/createBookmarkFolder')) or (platform=='instagram' and parsed.hostname=='www.instagram.com' and parsed.path=='/api/graphql')
        if not match or response.request.method!='POST' or response.status!=200:return
        try:
            value=extract(await response.json())
            if value is not None and not future.done():future.set_result(value)
        except VpubError as exc:
            if not future.done():future.set_exception(exc)
        except Exception:pass
    page.on('response',capture)
    return future,capture


async def click(locator):
    await locator.first.wait_for(state='visible',timeout=20000)
    await (await rt.unique_visible(locator,'collection control')).click()


async def root(page, platform, owner):
    if platform=='x':
        await page.goto('https://x.com/i/history',wait_until='domcontentloaded')
        trigger=page.get_by_role('tab',name='Bookmarks',exact=True)
        marker=page.get_by_role('button',name='Create Folder',exact=True)
        # The tab may render before its click handler is hydrated. Only retry
        # opening this read-only menu, never a create/delete confirmation.
        for _ in range(3):
            if await rt.visible(marker):break
            await click(trigger)
            try:await marker.wait_for(state='visible',timeout=2500);break
            except Exception:
                await rt.risk_check(page)
        if not await rt.visible(marker):raise VpubError('collection_menu_unavailable','X bookmark folder controls did not become ready.')
    elif platform=='instagram':
        if not re.fullmatch(r'[A-Za-z0-9_.]+',owner):raise VpubError('invalid_identity','Invalid Instagram handle.')
        await page.goto(f'https://www.instagram.com/{owner}/saved/',wait_until='domcontentloaded')
        await page.get_by_role('button',name='+ New Collection',exact=True).wait_for(state='visible')
    else:
        await page.goto('https://creator.douyin.com/creator-micro/content/manage?tab=collections',wait_until='domcontentloaded')
        await page.get_by_role('button',name='创建合集',exact=True).wait_for(state='visible')
    await rt.risk_check(page)
    await page.wait_for_timeout(900)


async def read_list(page, platform, owner):
    if platform=='douyin':
        from .collections_douyin import listing
        return await listing(page,owner)
    if platform=='x':
        # Read only the response generated by the official page. Never replay a private API request.
        async with page.expect_response(lambda r:urlsplit(r.url).hostname=='x.com' and urlsplit(r.url).path.endswith('/BookmarkFoldersSlice') and r.status==200,timeout=25000) as result:
            await root(page,platform,owner)
        data=await (await result.value).json()
        rows,complete=folder_response(data)
        return {'platform':platform,'identity':owner,'items':rows,'complete':complete,
                'scope':'private_saved_collections','coverage':'official_page_response','platform_writes':0,'observed_at':now()}
    if platform=='instagram':
        future,capture=response_capture(page,platform,instagram_response)
        try:
            await root(page,platform,owner)
            ig_rows,ig_complete=await asyncio.wait_for(future,20)
        finally:page.remove_listener('response',capture)
    else:await root(page,platform,owner)
    links=await page.locator('a[href]').evaluate_all('es=>es.filter(e=>e.getBoundingClientRect().width&&e.getBoundingClientRect().height).map(e=>({href:e.getAttribute("href"),text:e.innerText}))')
    rows={}
    patterns={'x':r'/i/bookmarks/(\d+)(?:[/?#]|$)',
              'instagram':r'/'+re.escape(owner)+r'/saved/[^/]+/(\d+)(?:[/?#]|$)',
              'douyin':r'/collection/[^?]+\?.*?(?:collection_id|id)=(\d+)(?:&|$)'}
    for link in links:
        match=re.search(patterns[platform],link['href'])
        if match:rows[match[1]]={'id':match[1],'title':link['text'].strip(),'url':link['href']}
    if platform=='instagram':
        for row in ig_rows:
            if row['id'] in rows:row['url']=rows[row['id']]['url']
        return {'platform':platform,'identity':owner,'items':ig_rows,'complete':ig_complete,
                'scope':'private_saved_collections','coverage':'official_page_response','platform_writes':0,'observed_at':now()}
    text=await page.locator('body').inner_text()
    empty={'instagram':"Save photos and videos that you want to see again.",
           'douyin':'你没有合集，点击创建合集','x':'Save posts for later'}
    if platform=='douyin' and empty[platform] not in text:
        raise VpubError('unverified_layout','Douyin nonempty work-collection lists have not been verified; no guessed IDs returned.')
    if not rows and empty[platform] not in text:
        raise VpubError('read_incomplete','Collection list is not ready or its layout changed; absence is not confirmed.')
    return {'platform':platform,'identity':owner,'items':list(rows.values()),
            'complete':not rows and empty[platform] in text,
            'scope':'owned_work_collections' if platform=='douyin' else 'private_saved_collections',
            'coverage':'visible_page','observed_at':now(),'platform_writes':0}


async def execute(page,platform,owner,payload):
    op=payload['operation']
    if platform=='douyin':
        from .collections_douyin import execute as douyin_execute
        return await douyin_execute(page,owner,payload)
    if platform=='instagram' and op in {'items','rename','add','remove'}:
        from .collections_instagram import execute as instagram_execute
        return await instagram_execute(page,owner,payload)
    if op=='list':return await read_list(page,platform,owner)
    if op in {'items','rename','delete'} and platform=='x':
        current=await read_list(page,platform,owner)
        matches=[x for x in current['items'] if x['id']==payload['collection_id']]
        if len(matches)!=1:raise VpubError('collection_not_visible','Exact folder ID is not visible; no changes made.')
        await page.goto('https://x.com/i/history/bookmarks/'+payload['collection_id'],wait_until='domcontentloaded')
        tab=page.get_by_role('tab',name=matches[0]['title'],exact=True)
        await tab.wait_for(state='visible');await rt.risk_check(page)
        if urlsplit(page.url).path!='/i/history/bookmarks/'+payload['collection_id']:
            raise VpubError('wrong_collection','X redirected away from the requested folder.')
        if op=='items':
            await page.wait_for_timeout(1500)
            links=await page.locator('article a[href]').evaluate_all('es=>es.map(e=>e.getAttribute("href"))')
            ids=sorted({m[1] for href in links if (m:=re.fullmatch(r'/[^/]+/status/(\d+)',href))})
            text=await page.locator('body').inner_text()
            if 'Something went wrong' in text:raise VpubError('read_failed','X did not load the bookmark folder.')
            return {'identity':owner,'collection_id':payload['collection_id'],'items':[{'id':i} for i in ids],
                    'complete':not ids and 'Save posts for later' in text,'coverage':'visible_page','platform_writes':0}
        await click(tab)
        await click(page.get_by_role('menu').get_by_role('button',name='More',exact=True))
        await click(page.get_by_role('menuitem',name='Edit' if op=='rename' else 'Delete',exact=True))
        if op=='rename':
            await page.locator('input[name="Folder Name"]').fill(payload['title'])
            final=page.get_by_role('button',name='Save',exact=True)
        else:
            await page.get_by_text('Delete Folder?',exact=True).wait_for(state='visible')
            final=page.locator('[data-testid="confirmationSheetConfirm"]').filter(has_text=re.compile('^Delete$'))
        final=await rt.unique_visible(final,'folder confirmation')
        before_submit(payload,owner);await final.click()
        await final.wait_for(state='hidden',timeout=20000)
        return {'identity':owner,'submitted':True}
    if op in {'items','delete'} and platform=='instagram':
        current=await read_list(page,platform,owner)
        matches=[x for x in current['items'] if x['id']==payload['collection_id']]
        if len(matches)!=1:raise VpubError('collection_not_visible','Exact collection ID is not visible; no changes made.')
        if not matches[0].get('url'):raise VpubError('collection_not_visible','The collection link is outside the visible page.')
        await page.goto('https://www.instagram.com'+matches[0]['url'],wait_until='domcontentloaded')
        await page.locator('svg[aria-label="Edit options"]').wait_for(state='visible')
        await rt.risk_check(page)
        if not urlsplit(page.url).path.startswith('/'+owner+'/saved/') or not urlsplit(page.url).path.endswith('/'+payload['collection_id']+'/'):
            raise VpubError('wrong_collection','Instagram redirected away from the requested collection.')
        if op=='items':
            links=await page.locator('a[href]').evaluate_all('es=>es.map(e=>e.getAttribute("href"))')
            ids=sorted({m[1] for href in links if (m:=re.fullmatch(r'/(?:p|reel)/([A-Za-z0-9_-]+)/',href))})
            return {'identity':owner,'collection_id':payload['collection_id'],'items':[{'id':i} for i in ids],
                    'complete':not ids and 'Start Saving' in await page.locator('body').inner_text(),
                    'coverage':'visible_page','platform_writes':0}
        await click(page.locator('svg[aria-label="Edit options"]'))
        await click(page.get_by_role('button',name='Delete collection',exact=True))
        await page.get_by_text('Delete collection?',exact=True).wait_for(state='visible')
        final=page.get_by_role('dialog').get_by_role('button',name='Delete',exact=True)
        final=await rt.unique_visible(final,'collection confirmation')
        before_submit(payload,owner);await final.click()
        await final.wait_for(state='hidden',timeout=20000)
        return {'identity':owner,'submitted':True}
    if op=='create':
        await root(page,platform,owner)
        if platform=='x':
            await click(page.get_by_role('button',name='Create Folder',exact=True))
            dialog=page.get_by_role('dialog')
            await dialog.get_by_role('textbox').fill(payload['title'])
            final=dialog.get_by_role('button',name='Create',exact=True)
        elif platform=='instagram':
            await click(page.get_by_role('button',name='+ New Collection',exact=True))
            await page.get_by_placeholder('Collection name',exact=True).fill(payload['title'])
            await click(page.get_by_role('button',name='Next',exact=True))
            final=page.get_by_role('dialog').get_by_role('button',name='Done',exact=True)
        else:raise VpubError('unsupported_operation','Douyin work-collection writes are not verified.')
        final=await rt.unique_visible(final,'create collection')
        if not await final.is_enabled():raise VpubError('collection_requires_items','The platform requires saved items before creation.')
        future,capture=response_capture(page,platform,lambda data:created_id(platform,data,payload['title']))
        before_submit(payload,owner)
        try:
            await final.click()
            new_id=await asyncio.wait_for(future,20)
            from .core import atomic_json,read_json
            from .collections import receipt_path
            path=receipt_path(payload['receipt_key']);r=read_json(path)
            if new_id in r['before_ids']:raise VpubError('wrong_collection','Creation response points to a pre-existing collection.')
            r['collection_id']=new_id;atomic_json(path,r)
            await final.wait_for(state='hidden',timeout=20000);await rt.risk_check(page)
            return {'identity':owner,'submitted':True,'collection_id':new_id}
        finally:page.remove_listener('response',capture)
    raise VpubError('unsupported_operation','This collection operation is not available yet.')

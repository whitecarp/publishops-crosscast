"""Douyin creator collections through observed official controls and responses."""
import re
from urllib.parse import urlsplit
from . import browser_runtime as rt
from .core import VpubError, now, atomic_json, read_json
from .collections import before_submit, receipt_path

ROOT='https://creator.douyin.com/creator-micro/content/manage?tab=collections'
BASE='https://creator.douyin.com/creator-micro/content/collection/'


def rows(data, owner):
    if data.get('status_code')!=0 or not isinstance(data.get('mix_list'),list) or type(data.get('has_more')) is not bool:
        raise VpubError('read_failed','Douyin did not return a complete collection response schema.')
    result=[]
    for item in data['mix_list']:
        aid=item.get('author',{}).get('unique_id') or item.get('author',{}).get('short_id')
        if str(aid)!=owner:raise VpubError('wrong_account','Collection author does not match the signed-in account.')
        cid=str(item.get('mix_id','')); title=item.get('mix_name')
        if not re.fullmatch(r'\d{1,40}',cid) or not isinstance(title,str):raise VpubError('layout_changed','Unexpected collection ID or title.')
        result.append({'id':cid,'title':title,'description':item.get('desc',''),
                       'status':item.get('status',{}).get('status'),'count':item.get('statis',{}).get('current_episode'),
                       'url':BASE+'detail/'+cid})
    if len({r['id'] for r in result})!=len(result):raise VpubError('layout_changed','Duplicate collection IDs.')
    return {'platform':'douyin','identity':owner,'items':result,'complete':not data['has_more'],
            'coverage':'official_page_response','scope':'owned_work_collections','platform_writes':0,'observed_at':now()}


def matches(response, path):
    parsed=urlsplit(response.url)
    return parsed.hostname=='creator.douyin.com' and parsed.path.rstrip('/')==path.rstrip('/') and response.status==200


async def listing(page, owner):
    async with page.expect_response(lambda r:matches(r,'/web/api/mix/list/'),timeout=25000) as found:
        await page.goto(ROOT,wait_until='domcontentloaded')
    result=rows(await (await found.value).json(),owner)
    await page.get_by_role('button',name='创建合集',exact=True).wait_for(state='visible')
    await rt.risk_check(page)
    return result


async def detail(page, owner, cid):
    # The authenticated detail route and its own returned ID must agree.
    async with page.expect_response(lambda r:matches(r,'/web/api/mix/detail/'),timeout=25000) as found:
        await page.goto(BASE+'detail/'+cid,wait_until='domcontentloaded')
    data=await (await found.value).json(); item=data.get('mix_info',{})
    if data.get('status_code')!=0 or str(item.get('mix_id'))!=cid:raise VpubError('wrong_collection','Collection detail did not confirm its ID.')
    author=item.get('author',{}); aid=author.get('unique_id') or author.get('short_id')
    if str(aid)!=owner:raise VpubError('wrong_account','Collection detail belongs to another account.')
    await page.get_by_placeholder('请输入合集的标题',exact=True).wait_for(state='visible')
    await rt.risk_check(page)
    return item


def member_rows(data, owner, cid):
    if data.get('status_code') != 0 or not isinstance(data.get('aweme_list'),list) or type(data.get('has_more')) is not bool:
        raise VpubError('read_failed','Collection members were not confirmed.')
    items=[]
    for item in data['aweme_list']:
        wid=str(item.get('aweme_id',''))
        if not re.fullmatch(r'\d{1,40}',wid):raise VpubError('layout_changed','Invalid collection member ID.')
        items.append({'id':wid,'title':item.get('desc','')})
    if len({r['id'] for r in items})!=len(items):raise VpubError('layout_changed','Duplicate collection member IDs.')
    return {'identity':owner,'collection_id':cid,'items':items,'complete':not data['has_more'],'coverage':'official_page_response','platform_writes':0}


async def execute(page, owner, payload):
    from .collections_browser import click
    op=payload['operation'];cid=payload.get('collection_id')
    if op=='list':return await listing(page,owner)
    if op=='items':
        async with page.expect_response(lambda r:matches(r,'/web/api/mix/item/list/'),timeout=25000) as found:
            await detail(page,owner,cid)
        data=await (await found.value).json()
        if data.get('status_code')!=0 or not isinstance(data.get('aweme_list'),list) or type(data.get('has_more')) is not bool:
            raise VpubError('read_failed','Collection members were not confirmed.')
        items=[]
        for item in data['aweme_list']:
            wid=str(item.get('aweme_id',''))
            if not re.fullmatch(r'\d{1,40}',wid):raise VpubError('layout_changed','Invalid collection member ID.')
            items.append({'id':wid,'title':item.get('desc','')})
        if len({r['id'] for r in items})!=len(items):raise VpubError('layout_changed','Duplicate collection member IDs.')
        return {'identity':owner,'collection_id':cid,'items':items,'complete':not data['has_more'],'coverage':'official_page_response','platform_writes':0}
    if op=='create':
        await page.goto(BASE+'create',wait_until='domcontentloaded')
        await page.get_by_placeholder('请输入合集的标题',exact=True).fill(payload['title'])
        await page.get_by_placeholder('请输入合集的简介',exact=True).fill(payload.get('description') or '')
        await page.locator('input[type=file]').set_input_files(payload['cover'])
        await click(page.get_by_role('button',name='保存',exact=True))
        await page.get_by_text('修改合集封面',exact=True).wait_for(state='visible')
        final=await rt.unique_visible(page.get_by_role('button',name='创建',exact=True),'create collection')
        route='/web/api/mix/create'
    elif op=='rename':
        await detail(page,owner,cid)
        await page.get_by_placeholder('请输入合集的标题',exact=True).fill(payload['title'])
        final=await rt.unique_visible(page.get_by_role('button',name='保存',exact=True),'save collection')
        route='/web/api/mix/update'
    elif op in {'add','remove'}:
        async with page.expect_response(lambda r:matches(r,'/web/api/mix/item/list/'),timeout=25000) as existing:
            await detail(page,owner,cid)
        data=await (await existing.value).json()
        if data.get('status_code')!=0 or data.get('has_more') is not False or not isinstance(data.get('aweme_list'),list):
            raise VpubError('read_incomplete','Cannot safely preserve the other collection members.')
        members=data['aweme_list'];ids=[str(x['aweme_id']) for x in members]
        if set(ids)!=set(payload['before_members']):raise VpubError('collection_changed','Collection changed since preparation; no write attempted.')
        wid=payload['work_id']
        if op=='add':
            async with page.expect_response(lambda r:matches(r,'/aweme/v1/personal/search/item/'),timeout=20000) as candidates:
                label='添加作品' if ids else '点击添加作品'
                await click(page.get_by_text(label,exact=True))
            candidates=await (await candidates.value).json()
            if candidates.get('status_code')!=0 or not isinstance(candidates.get('aweme_list'),list):raise VpubError('read_failed','Work picker did not load.')
            found=[x for x in candidates['aweme_list'] if str(x.get('aweme_id'))==wid]
            if len(found)!=1:raise VpubError('work_not_visible','Exact work is not in the loaded picker; no change made.')
            title=found[0].get('desc')
            if not title or sum(x.get('desc')==title for x in candidates['aweme_list'])!=1:raise VpubError('ambiguous_work','Work titles do not identify one picker row.')
            row=page.locator('[class^="drawer-item-"]:has(> [class^="plus-area-"])').filter(has=page.get_by_text(title,exact=True))
            await row.first.wait_for(state='visible',timeout=20000)
            row=await rt.unique_visible(row,'work picker row')
            if 'disabled-' in (await row.get_attribute('class') or ''):raise VpubError('work_ineligible','Douyin marks this work as ineligible for the collection.')
            await click(row.locator('[class^="plus-area-"]'))
            await click(page.locator('[role=sidesheet] [class^="title-"] span[class^="icon-"]'))
        else:
            found=[x for x in members if str(x.get('aweme_id'))==wid]
            if len(found)!=1:raise VpubError('work_not_visible','Exact collection member is not visible.')
            title=found[0].get('desc')
            if not title or sum(x.get('desc')==title for x in members)!=1:raise VpubError('ambiguous_work','Work titles do not identify one collection row.')
            row=page.locator('[class^="video-card-"]:has(> [class^="video-card-operation-"])').filter(has=page.get_by_text(title,exact=True))
            await row.first.wait_for(state='visible',timeout=20000)
            row=await rt.unique_visible(row,'collection member')
            await row.hover()
            await click(row.get_by_role('button',name='从合集中移除',exact=True))
            await page.get_by_text('确定将视频移出合集吗？',exact=True).wait_for(state='visible')
            await page.get_by_text('移出合集后将不会删除原视频',exact=True).wait_for(state='visible')
            # This confirmation changes the local editor; Save persists it.
            await click(page.get_by_role('button',name='移除',exact=True))
            await page.get_by_text('确定将视频移出合集吗？',exact=True).wait_for(state='hidden')
        final=await rt.unique_visible(page.get_by_role('button',name='保存',exact=True),'save membership')
        route='/web/api/mix/update'
    elif op=='delete':
        current=await listing(page,owner);target=[r for r in current['items'] if r['id']==cid]
        if len(target)!=1:raise VpubError('collection_not_visible','Exact collection is not visible.')
        if sum(r['title']==target[0]['title'] for r in current['items'])!=1:raise VpubError('ambiguous_collection','Duplicate titles cannot safely identify the deletion control.')
        row=page.locator('[class*=new-layout-title-row-]').filter(has=page.get_by_text(target[0]['title'],exact=True))
        await click(row.get_by_role('button',name='删除合集',exact=True))
        modal=page.locator('[role=modal]')
        await modal.wait_for(state='visible')
        message=await modal.inner_text()
        if '确定要删除此合集吗' not in message or '删除合集时不会删除合集里的视频' not in message:
            raise VpubError('unexpected_confirmation','The collection-only deletion confirmation is missing.')
        # The exact confirmation is intentionally selected after observing its text.
        final=await rt.unique_visible(modal.get_by_role('button',name='删除',exact=True),'delete collection confirmation')
        route='/web/api/mix/delete'
    else:raise VpubError('unsupported_operation','This Douyin collection operation is not implemented.')
    await rt.risk_check(page)
    await final.click(trial=True,timeout=10000)
    before_submit(payload,owner)
    if op=='delete':
        await final.click()
        await modal.wait_for(state='hidden',timeout=20000)
        await rt.risk_check(page)
        # Only the caller's fresh complete list can confirm deletion.
        return {'identity':owner,'submitted':True,'collection_id':cid}
    # Membership edits can navigate immediately using a different mix action.
    # Observe this official submit, then require an independent member readback.
    def accepted_response(r):
        parsed=urlsplit(r.url)
        return matches(r,route) or (op in {'add','remove'} and parsed.hostname=='creator.douyin.com' and parsed.path.startswith('/web/api/mix/') and r.request.method not in {'GET','OPTIONS'} and r.status==200)
    async with page.expect_response(accepted_response,timeout=30000) as result:
        await final.click()
    response=await result.value
    data=await response.json()
    if data.get('status_code')!=0:raise VpubError('platform_rejected','Douyin did not accept this collection change.')
    if op=='create':
        cid=str(data.get('mix_id',''))
        if not re.fullmatch(r'\d{1,40}',cid):raise VpubError('creation_id_missing','Creation response did not contain a collection ID.')
        path=receipt_path(payload['receipt_key']);r=read_json(path)
        if cid in r['before_ids']:raise VpubError('wrong_collection','Create returned an existing ID.')
        r['collection_id']=cid;atomic_json(path,r)
    return {'identity':owner,'submitted':True,'collection_id':cid,'response_path':urlsplit(response.url).path}

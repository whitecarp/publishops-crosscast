"""Saved collections via the signed-in official page and its own client module."""
import asyncio
import re
from urllib.parse import urlsplit
from . import browser_runtime as rt
from .core import VpubError
from .collections import before_submit


def members(data, cid):
    section=data.get('data',{}).get('fetch__MediaCollection')
    if section is None:return None
    if str(section.get('id'))!=cid:raise VpubError('wrong_collection','Unexpected collection response.')
    media=section.get('media',{});info=media.get('page_info',{})
    if not isinstance(media.get('edges'),list) or type(info.get('has_next_page')) is not bool:
        raise VpubError('layout_changed','Collection pagination is missing.')
    rows=[]
    for edge in media['edges']:
        node=edge.get('node',{});code=node.get('code');pk=str(node.get('pk',''))
        if not isinstance(code,str) or not re.fullmatch(r'[A-Za-z0-9_-]{5,32}',code) or not re.fullmatch(r'\d{1,40}',pk):
            raise VpubError('layout_changed','Unexpected saved media identity.')
        rows.append({'id':code,'media_id':pk})
    if len({r['id'] for r in rows})!=len(rows):raise VpubError('layout_changed','Duplicate saved media IDs.')
    return {'collection_id':cid,'items':rows,'complete':not info['has_next_page'],
            'cursor':info.get('end_cursor'),
            'coverage':'official_page_response','platform_writes':0}


async def detail(page, owner, cid):
    from .collections_browser import read_list,response_capture
    current=await read_list(page,'instagram',owner)
    found=[r for r in current['items'] if r['id']==cid]
    if len(found)!=1 or not found[0].get('url'):raise VpubError('collection_not_visible','Exact collection link is not visible.')
    future,listener=response_capture(page,'instagram',lambda data:members(data,cid))
    try:
        await page.goto('https://www.instagram.com'+found[0]['url'],wait_until='domcontentloaded')
        await page.locator('svg[aria-label="Edit options"]').wait_for(state='visible')
        await rt.risk_check(page)
        if not urlsplit(page.url).path.endswith('/'+cid+'/'):raise VpubError('wrong_collection','Unexpected collection route.')
        result=await asyncio.wait_for(future,20)
        return {**result,'identity':owner}
    finally:page.remove_listener('response',listener)


async def execute(page,owner,payload):
    from .collections_browser import click
    cid=payload['collection_id'];op=payload['operation']
    observed=await detail(page,owner,cid)
    if op=='items':return observed
    args={'collectionId':cid}
    if op=='rename':args['collectionName']=payload['title']
    elif op in {'add','remove'}:
        if not observed['complete'] or {r['id'] for r in observed['items']}!=set(payload['before_members']):
            raise VpubError('collection_changed','Complete membership no longer matches the prepared baseline.')
        if op=='remove':
            found=[r for r in observed['items'] if r['id']==payload['work_id']]
            if len(found)!=1:raise VpubError('work_not_visible','Exact saved member is absent.')
            args['postIdsToRemove']=[found[0]['media_id']]
        else:
            # Load the official Saved picker to bind shortcode to numeric media ID.
            if observed['items']:
                await click(page.locator('svg[aria-label="Edit options"]'))
                trigger=page.get_by_role('button',name='Add from saved',exact=True)
            else:trigger=page.get_by_role('button',name='Add to collection',exact=True)
            async with page.expect_response(lambda r:urlsplit(r.url).hostname=='www.instagram.com' and urlsplit(r.url).path=='/api/v1/feed/saved/posts/' and r.status==200,timeout=20000) as response:
                await click(trigger)
            data=await (await response.value).json()
            if data.get('status')!='ok' or not isinstance(data.get('items'),list):raise VpubError('read_failed','Saved picker did not load.')
            found=[r['media'] for r in data['items'] if r.get('media',{}).get('code')==payload['work_id']]
            if len(found)!=1:raise VpubError('work_not_saved','Save the work first with vpub engage; it must be in the loaded Saved picker.')
            pk=str(found[0].get('pk',''))
            if not re.fullmatch(r'\d{1,40}',pk):raise VpubError('layout_changed','Invalid saved media ID.')
            args['postIdsToAdd']=[pk]
    else:raise VpubError('unsupported_operation','Unsupported Instagram collection action.')
    # The website edit modal currently stalls before dispatching its request.
    # Use the exact module shipped by the official page, with its own Relay session.
    # No cookies, tokens, endpoint templates or GraphQL IDs are copied/replayed.
    available=await page.evaluate('() => {try{return typeof require("PolarisAPIEditCollection").editCollection==="function"}catch(e){return false}}',isolated_context=False)
    if not available:raise VpubError('layout_changed','Official collection client is not available.')
    await rt.risk_check(page);before_submit(payload,owner)
    result=await page.evaluate('''args => Promise.race([
        require("PolarisAPIEditCollection").editCollection(args).then(r=>({id:r.collection_id,name:r.collection_name})),
        new Promise((_,reject)=>setTimeout(()=>reject(new Error("Collection request timed out; verify before retrying")),25000))
    ])''',args,isolated_context=False)
    if str(result.get('id'))!=cid:raise VpubError('wrong_collection','Native client returned a different collection.')
    if op=='rename' and result.get('name')!=payload['title']:raise VpubError('platform_rejected','Collection name was not accepted.')
    return {'identity':owner,'submitted':True,'collection_id':cid}

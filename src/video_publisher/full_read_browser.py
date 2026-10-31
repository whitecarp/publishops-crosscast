"""Follow official collection pagination; lack of scrolling is never end proof."""
import asyncio
from urllib.parse import urlsplit, parse_qs

from .core import VpubError, digest, stop_check
from .browser_runtime import risk_check


def extract(platform, resource, cid, owner, response, data):
    url = urlsplit(response.url)
    if platform == 'douyin':
        from .collections_douyin import rows
        route = '/web/api/mix/list/' if resource == 'collections' else '/web/api/mix/item/list/'
        if url.hostname != 'creator.douyin.com' or url.path != route:
            return None
        if resource == 'collections':
            value = rows(data, owner)
        else:
            from .collections_douyin import member_rows
            if parse_qs(url.query).get('mix_id') != [cid]:
                raise VpubError('wrong_collection', 'Member response request belongs to another collection.')
            value = member_rows(data, owner, cid)
        return {**value, 'cursor':data.get('cursor')}
    if platform == 'instagram':
        if url.hostname != 'www.instagram.com' or url.path != '/api/graphql':
            return None
        if not isinstance(data, dict) or not isinstance(data.get('data'), dict):
            return None  # Unrelated GraphQL errors and streaming responses.
        if resource == 'collection-items':
            from .collections_instagram import members
            value = members(data, cid)
            return {**value, 'identity':owner} if value else None
        from .collections_browser import instagram_response
        value = instagram_response(data)
        if value is None:
            return None
        return {'items':value[0], 'complete':value[1], 'identity':owner,
                'cursor':data['data']['viewer']['collections_unified_with_auto_collections']['page_info'].get('end_cursor')}
    if platform == 'x' and resource == 'collections' and url.hostname == 'x.com' and url.path.endswith('/BookmarkFoldersSlice'):
        from .collections_browser import folder_response
        items, complete = folder_response(data)
        return {'items':items, 'complete':complete, 'identity':owner}
    return None


async def execute(page, platform, owner, payload):
    from .collections_browser import execute as read
    resource, cid = payload['resource'], payload.get('collection_id')
    if platform == 'x' and resource == 'collection-items':
        raise VpubError('unsupported_read', 'Complete X folder-member pagination is not yet verified.')
    queue = asyncio.Queue()
    async def capture(response):
        if response.status != 200:
            return
        url = urlsplit(response.url)
        relevant = ((platform == 'instagram' and url.hostname == 'www.instagram.com' and url.path == '/api/graphql')
                    or (platform == 'douyin' and url.hostname == 'creator.douyin.com' and url.path in {'/web/api/mix/list/','/web/api/mix/item/list/'})
                    or (platform == 'x' and url.hostname == 'x.com' and url.path.endswith('/BookmarkFoldersSlice')))
        if not relevant:
            return
        try:
            data = await response.json()
        except Exception:
            return
        try:
            value = extract(platform, resource, cid, owner, response, data)
            if value is not None:
                await queue.put(value)
        except VpubError as exc:
            await queue.put(exc)
        except Exception:
            await queue.put(VpubError('invalid_page', 'Official collection response could not be parsed.'))
    rows, seen = {}, set()
    prior = {}
    if payload.get('read_run_id'):
        from .full_read import status
        saved = status(payload['read_run_id'])
        if (saved['platform'], saved['resource'], saved.get('collection_id')) != (platform, resource, cid) or saved['identity'] not in {None, owner}:
            raise VpubError('read_conflict', 'Browser checkpoint account/resource changed.')
        prior = {r['id']: r for r in saved['items']}
    page.on('response', capture)
    pages = 0
    complete = False
    reason = 'page_budget'
    try:
        initial = await read(page, platform, owner, {**payload, 'operation':'list' if resource == 'collections' else 'items'})
        while pages < payload['max_pages']:
            stop_check(platform)
            await risk_check(page)
            try:
                value = await asyncio.wait_for(queue.get(), 8)
            except asyncio.TimeoutError:
                if pages == 0:
                    value = initial
                else:
                    reason = 'no_confirmed_next_page'
                    break
            if isinstance(value, Exception):
                raise value
            fingerprint = digest([value.get('cursor'), [r['id'] for r in value['items']], value['complete']])
            if fingerprint in seen:
                reason = 'repeated_page'
                break
            seen.add(fingerprint)
            rows.update({r['id']:r for r in value['items']})
            pages += 1
            if payload.get('read_run_id'):
                from .full_read import status, save
                run = status(payload['read_run_id'])
                if (run['platform'],run['resource'],run.get('collection_id')) != (platform,resource,cid) or run['identity'] not in {None,owner}:
                    raise VpubError('read_conflict','Browser checkpoint account/resource changed.')
                run.update(identity=owner, items=list({**prior, **rows}.values()), pages_read=pages, complete=False,
                           state='READING', resume_mode='replay_official_page')
                save(run)
            if value['complete']:
                complete, reason = True, 'official_end_of_list'
                break
            if pages >= payload['max_pages']:
                break
            # Only read-only next/scroll controls; no endpoint or token replay.
            next_button = page.locator('li.ant-pagination-next:not(.ant-pagination-disabled) button')
            if await next_button.count() == 1 and await next_button.is_visible():
                await next_button.click()
            else:
                await page.evaluate('''() => {for(const e of [document.scrollingElement,...document.querySelectorAll('main,section,div')]){
                    if(e && e.clientHeight>150 && e.scrollHeight>e.clientHeight+8 && (e===document.scrollingElement||/auto|scroll/.test(getComputedStyle(e).overflowY)))e.scrollTop=e.scrollHeight;}}''')
        retained = len(prior.keys() - rows.keys()) if not complete else 0
        result_rows = rows if complete else {**prior, **rows}
        return {'identity':owner, 'collection_id':cid, 'items':list(result_rows.values()), 'pages_read':pages,
                'retained_previous_items':retained,
                'complete':complete, 'stop_reason':reason, 'source':'official_page_responses',
                'scope':'Partial replays retain prior observations. A complete replay uses only the current pass and requires the server end marker.', 'platform_writes':0}
    finally:
        page.remove_listener('response', capture)

"""Like/unlike the exact visible Instagram/X comment, never a nearby video, and delete your own on Instagram."""
from . import x_pages
from .browser_runtime import acknowledged, risk_check, unique_visible, write_answers
from .operations_browser import open_work, comments
from .core import VpubError, atomic_json, read_json, stop_check

COMMENT_OPTIONS = '[role="button"]:has(svg[aria-label="Comment Options"]), [role="button"][aria-label="Comment Options"]'
# The request a real delete sent, from a field check, and the field its answer came back in. That check saw the field and not the type
# inside it, which is what says whether it worked, so no answer counts as acknowledged until a real delete has shown that type.
DELETE_REQUEST = '/api/graphql'
DELETE_FIELD = 'data.xig_comment_delete.__typename'
DELETE_TYPES = frozenset()


def delete_answered(answer):
    return DELETE_FIELD in answer['shape'] and bool(DELETE_TYPES) and set(answer['types']) <= DELETE_TYPES and bool(answer['types'])


async def press_once(page, platform, payload, control):
    """The receipt says it is going before the one click that sends it, and that it went straight after."""
    await risk_check(page); stop_check(platform)
    await control.click(trial=True, timeout=10000)
    from .comment_actions import submitting
    submitting(payload['receipt_path'])
    await control.click(timeout=15000)
    saved = read_json(payload['receipt_path']); saved.update(state='COMMENT_UNVERIFIED', platform_writes=1); atomic_json(payload['receipt_path'], saved)


async def instagram_delete(page, identity, payload, comment):
    """Your own comment's menu, which appears when the pointer rests on its row. A field check found only Delete and Cancel in it."""
    observed = {'identity': identity, 'text': comment['text'], 'liked': None, 'deleted': False, 'source': 'instagram_comment_list'}
    if payload['operation'] == 'status': return observed
    if payload['operation'] != 'set' or not comment.get('is_owner'):
        raise VpubError('own_comment_required', 'Deleting is restricted to your own comment; nothing changed.')
    row = page.locator(f'[data-crosscast-comment-row="{comment["ui_index"]}"]')
    await row.hover(timeout=10000)
    # The text and its menu can be sibling nodes. Climb only while the container holds this one marked comment.
    for _ in range(3):
        if await row.locator(COMMENT_OPTIONS).count(): break
        parent = row.locator('xpath=..')
        if await parent.locator('[data-crosscast-comment-row]').count() != 1: break
        row = parent
    await (await unique_visible(row.locator(COMMENT_OPTIONS), 'Comment Options')).click(timeout=10000)
    choices = page.get_by_role('dialog').get_by_role('button', name='Delete', exact=True)
    try:
        await choices.first.wait_for(state='visible', timeout=10000)
    except Exception:
        raise VpubError('layout_changed', 'Instagram did not offer Delete for this comment; nothing changed.') from None
    # By the comment's own address where it has one. Its words can't say, because a field run found another comment with the same words.
    still_there = page.locator(f'a[href*="/c/{comment["id"]}"]') if comment['id'].isdigit() else page.locator(f'[data-crosscast-comment-row="{comment["ui_index"]}"]')
    requests = []

    def heard(request):
        requests.append(request)
    page.on('request', heard)
    try:
        await press_once(page, 'instagram', payload, await unique_visible(choices, 'comment Delete'))
        # Whether it left this page after the one press, which says more than a later read of a list that only loads in part.
        gone = False
        for _ in range(10):
            gone = not await still_there.count()
            if gone: break
            await page.wait_for_timeout(500)
    finally:
        page.remove_listener('request', heard)
    answers = await write_answers(requests, comment['id'])
    return {'state': 'COMMENT_UNVERIFIED', 'platform_writes': 1, 'gone_from_page': gone, 'write_answers': answers, 'acknowledged': acknowledged(answers, DELETE_REQUEST, delete_answered)}


async def execute(page, platform, identity, payload):
    if platform == 'youtube':
        from .youtube_studio import comment_action
        return await comment_action(page, identity, payload)
    if platform=='x' and payload['request'].get('kind')=='hide':
        from .comment_actions_x import execute as hide
        return await hide(page,identity,payload)
    if platform=='x' and payload['request'].get('kind')=='delete':
        from .comment_actions_x import delete_own
        return await delete_own(page,identity,payload)
    if platform == 'douyin':
        from .comment_actions_douyin import execute as douyin
        return await douyin(page, identity, payload)
    r = payload['request']; target = r['comment']; work = r['work_id']
    if platform not in {'instagram', 'x'} or identity != r['identity']:
        raise VpubError('unsupported_operation', 'Comment likes require the expected Instagram/X account.')
    await open_work(page, platform, identity, work)
    data = await comments(page, platform, identity, work, 100)
    found = [c for c in data['items'] if c['id'] == target['id'] and c['text'] == target['text']]
    deleting = platform == 'instagram' and r.get('kind') == 'delete'
    # The page loads only part of the list, so absence alone proves nothing about this comment.
    if deleting and not [c for c in data['items'] if c['id'] == target['id']]:
        return {'identity': identity, 'text': None, 'liked': None, 'deleted': None, 'absent_from_loaded_list': True, 'source': 'instagram_comment_list'}
    if len(found) != 1: raise VpubError('comment_changed', 'Exact comment is absent or changed.')
    c = found[0]
    if deleting: return await instagram_delete(page, identity, payload, c)
    if platform == 'x':
        row = await unique_visible(x_pages.post_row(page, c['id']), 'comment article')
        on = row.locator(x_pages.LIKE_ON); off = row.locator(x_pages.LIKE_OFF)
    else:
        row = page.locator(f'[data-crosscast-comment-row="{c["ui_index"]}"]')
        # The collected text column and its heart are sibling DOM nodes. Climb
        # only while the container contains exactly this one marked comment.
        for _ in range(3):
            if await row.locator('svg[aria-label="Like"],svg[aria-label="Unlike"]').count(): break
            parent = row.locator('xpath=..')
            if await parent.locator('[data-crosscast-comment-row]').count() != 1: break
            row = parent
        if await row.locator('svg[aria-label="Save"],svg[aria-label="Remove"],svg[aria-label="Comment"]').count():
            raise VpubError('comment_control_ambiguous', 'The comment container includes a post toolbar.')
        on = row.locator('svg[aria-label="Unlike"]'); off = row.locator('svg[aria-label="Like"]')
    ons = [e for e in await on.all() if await e.is_visible()]
    offs = [e for e in await off.all() if await e.is_visible()]
    if len(ons) + len(offs) != 1: raise VpubError('comment_control_ambiguous', 'Exactly one like state control is required on the comment row.')
    liked = bool(ons)
    observed = {'identity': identity, 'text': c['text'], 'liked': liked, 'deleted': False,
                'source': 'exact_comment_dom_like_control'}
    if payload['operation'] == 'status': return observed
    if payload['operation'] != 'set' or r['kind'] != 'like': raise VpubError('unsupported_operation', 'Only comment likes are supported.')
    if liked == r['desired']: return {'state': 'NO_CHANGE', 'platform_writes': 0}
    control = (ons or offs)[0]
    if platform == 'instagram': control = await unique_visible(control.locator('xpath=ancestor::*[@role="button" or self::button][1]'), 'comment like button')
    await press_once(page, platform, payload, control)
    await (off if liked else on).wait_for(state='visible', timeout=15000)
    return {'state': 'COMMENT_UNVERIFIED', 'platform_writes': 1}

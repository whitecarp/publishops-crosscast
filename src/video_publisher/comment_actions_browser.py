"""Like/unlike the exact visible Instagram/X comment, never a nearby video."""
from .browser_runtime import unique_visible, risk_check
from .operations_browser import open_work, comments
from .core import VpubError, atomic_json, read_json, stop_check


async def execute(page, platform, identity, payload):
    if platform=='x' and payload['request'].get('kind')=='hide':
        from .comment_actions_x import execute as hide
        return await hide(page,identity,payload)
    if platform == 'douyin':
        from .comment_actions_douyin import execute as douyin
        return await douyin(page, identity, payload)
    r = payload['request']; target = r['comment']; work = r['work_id']
    if platform not in {'instagram', 'x'} or identity != r['identity']:
        raise VpubError('unsupported_operation', 'Comment likes require the expected Instagram/X account.')
    await open_work(page, platform, identity, work)
    data = await comments(page, platform, identity, work, 100)
    found = [c for c in data['items'] if c['id'] == target['id'] and c['text'] == target['text']]
    if len(found) != 1: raise VpubError('comment_changed', 'Exact comment is absent or changed.')
    c = found[0]
    if platform == 'x':
        row = await unique_visible(page.locator('main article').filter(has=page.locator(f'a[href="/{c["author"]}/status/{c["id"]}"]').filter(has=page.locator('time'))), 'comment article')
        on = row.locator('[data-testid="unlike"]'); off = row.locator('[data-testid="like"]')
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
    await risk_check(page); stop_check(platform)
    await control.click(trial=True, timeout=10000)
    from .comment_actions import submitting
    submitting(payload['receipt_path'])
    await control.click(timeout=15000)
    saved = read_json(payload['receipt_path']); saved.update(state='COMMENT_UNVERIFIED', platform_writes=1); atomic_json(payload['receipt_path'], saved)
    await (off if liked else on).wait_for(state='visible', timeout=15000)
    return {'state': 'COMMENT_UNVERIFIED', 'platform_writes': 1}

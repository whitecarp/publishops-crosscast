"""Exact Creator Center comment likes/deletion, with fresh UI response evidence."""
from .core import VpubError, atomic_json, read_json, stop_check
from .browser_runtime import risk_check, unique_visible
from .operations_browser import open_work
from .operations_douyin import creator_page, creator_target


async def bound_row(page, data, current):
    same = [c for c in data.get('comments', []) if c.get('text') == current['text']
            and c.get('user', {}).get('nickname') == current['user']['nickname']]
    candidates = []
    for row in await page.locator('[class^="comment-item-"]').all():
        body = row.locator('[class^="comment-main-"]').first
        text = body.locator('[class^="text-"]').first
        name = body.locator('[class^="username-"]').first
        if not await text.count() or not await name.count(): continue
        nickname = await name.evaluate("e=>[...e.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim()")
        if await text.inner_text() == current['text'] and nickname == current['user']['nickname']:
            candidates.append(row)
    if len(same) != 1 or len(candidates) != 1:
        raise VpubError('comment_ambiguous', 'Cannot bind the exact comment ID to one visible Creator Center row.')
    return candidates[0]


def observed(data, request, identity):
    if not isinstance(data, dict) or data.get('status_code') != 0:
        raise VpubError('comments_unavailable', 'Creator Center comment read failed.')
    target = request['comment']
    found = [c for c in data.get('comments', []) if str(c.get('cid')) == target['id']]
    if not found and request.get('verification') and request.get('kind') == 'delete':
        # Absence in a partial page never proves deletion.
        if data.get('has_more') is False or data.get('has_more') == 0:
            return {'identity': identity, 'text': None, 'deleted': True, 'source': 'creator_complete_comment_page'}
        raise VpubError('incomplete_comment_page', 'Deletion cannot be verified from this partial comment page.')
    current = creator_target(data, request['work_id'], target)
    value = current.get('user_digged')
    return {'identity': identity, 'text': current['text'], 'deleted': False,
            'liked': bool(value) if type(value) in {int, bool} and value in {0, 1} else None,
            'source': 'creator_center_comment_response'}


async def execute(page, identity, payload):
    r = payload['request']
    if identity != r['identity']: raise VpubError('wrong_account', 'Comment account changed.')
    await open_work(page, 'douyin', identity, r['work_id'])
    data = await creator_page(page)
    state = observed(data, r, identity)
    if payload['operation'] == 'status': return state
    kind = r['kind']
    if kind not in {'like', 'delete'}:
        raise VpubError('unsupported_operation', 'Creator Center exposes no verified pin/hide control in this adapter.')
    current = creator_target(data, r['work_id'], r['comment'])
    row = await bound_row(page, data, current)
    if kind == 'like':
        if type(state['liked']) is not bool: raise VpubError('state_unavailable', 'Comment like state unavailable.')
        if state['liked'] == r['desired']: return {'state': 'NO_CHANGE', 'platform_writes': 0}
        buttons = row.locator('[class^="operations-"]').first.get_by_role('button')
        # Observed Creator Center layout: heart/count, dislike, Reply, Delete, Report.
        if await buttons.count() != 5 or await buttons.nth(2).inner_text() != '回复':
            raise VpubError('layout_changed', 'Creator comment action toolbar changed.')
        button = buttons.nth(0)
        if not (await button.inner_text()).strip().isdigit():
            raise VpubError('layout_changed', 'Expected the comment heart/count control.')
    else:
        owner = current.get('user', {})
        if identity not in {str(owner.get('unique_id')), str(owner.get('short_id'))}:
            raise VpubError('own_comment_required', 'Deletion is limited to your own test/owner comments.')
        if current.get('reply_comment_total', 0):
            raise VpubError('comment_has_replies', 'Do not delete a parent comment with replies through this adapter.')
        # Opening confirmation is reversible; persist intent before the final action.
        await row.get_by_role('button', name='删除', exact=True).click()
        await page.get_by_role('dialog').wait_for(state='visible', timeout=10000)
        dialog = await unique_visible(page.get_by_role('dialog'), 'delete confirmation')
        if '确定要删除吗' not in await dialog.inner_text():
            raise VpubError('layout_changed', 'Unexpected confirmation dialog.')
        button = await unique_visible(dialog.get_by_role('button', name='confirm', exact=True), 'confirm delete')
    await risk_check(page); stop_check('douyin')
    await button.click(trial=True, timeout=10000)
    from .comment_actions import submitting
    submitting(payload['receipt_path'])
    await button.click(timeout=15000)
    saved = read_json(payload['receipt_path'])
    saved.update(state='COMMENT_UNVERIFIED', platform_writes=1)
    atomic_json(payload['receipt_path'], saved)
    await page.wait_for_timeout(1000)
    return {'state': 'COMMENT_UNVERIFIED', 'platform_writes': 1}

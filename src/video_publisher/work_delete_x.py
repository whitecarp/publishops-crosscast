"""Delete an exact owned X post once; partial profile reads never prove deletion."""
import re
from urllib.parse import urlsplit

from . import browser_runtime as runtime, x_pages
from .core import VpubError, stop_check
from .work_delete_state import begin_press


EMPTY = re.compile(r"^(?:No posts yet|You haven[’']t posted yet|你还没有发帖|你還沒有發文|尚无帖子|尚無貼文)$", re.I)
GONE = re.compile(r"^(?:This (?:Post|post|Tweet|tweet) (?:was deleted|doesn[’']t exist)|此(?:帖子|推文)不存在|这条(?:帖子|推文)已被删除|這則(?:貼文|推文)已被刪除)[.!。]?$", re.I)
FINAL = '[data-testid="confirmationSheetConfirm"]'
SAME = '(node, retained) => node === retained && node.isConnected'
CONTAINS = '(node, child) => node.isConnected && child.isConnected && node.contains(child)'


async def guard(page):
    stop_check('x')
    await runtime.risk_check(page)
    stop_check('x')


def exact_address(page, target):
    address = urlsplit(page.url)
    if (address.scheme != 'https' or address.netloc != 'x.com'
            or address.path != f"/{target['identity']}/status/{target['id']}"
            or address.query or address.fragment):
        raise VpubError('wrong_work', 'X did not retain the exact post address.')


async def exact_account(page, actual, target):
    exact_address(page, target)
    if actual != target['identity'] or await runtime.identity(page, 'x') != actual:
        raise VpubError('wrong_account', 'The current X account changed or cannot be read.')
    exact_address(page, target)


def profile_address(page, actual):
    address = urlsplit(page.url)
    return (address.scheme == 'https' and address.netloc == 'x.com'
            and address.path in {f'/{actual}', f'/{actual}/'} and not address.query and not address.fragment)


async def profile_current(page, actual, target):
    if actual != target['identity'] or not profile_address(page, actual):
        return False
    await guard(page)
    if not profile_address(page, actual):
        return False
    current = await runtime.identity(page, 'x')
    return current == actual and profile_address(page, actual)


async def same_node(locator, retained, label):
    current = await runtime.unique_visible(locator, label)
    if retained is None or not await current.evaluate(SAME, retained):
        raise VpubError('wrong_work', 'A verified post control was replaced; nothing else is pressed.')


async def target_bound(page, actual, target, retained):
    await exact_account(page, actual, target)
    await same_node(x_pages.post_row(page, target['id']), retained, 'exact owned post')
    fields = await x_pages.post_fields(retained)
    if (not fields or fields['id'] != target['id']
            or fields['author'].casefold() != actual.casefold() or fields['is_ad']):
        raise VpubError('owner_unverified', 'The retained post no longer has the exact owned ID.')


async def overlays_bound(page, *, menu=None, dialog=None):
    for role, expected in (('menu', menu), ('dialog', dialog), ('alertdialog', dialog)):
        for node in await page.get_by_role(role).all():
            if await node.is_visible() and (expected is None or not await node.evaluate(SAME, expected)):
                raise VpubError('layout_changed', 'An unbound menu or dialog is already open.')
    if dialog is None and await runtime.visible(page.locator(FINAL)):
        raise VpubError('layout_changed', 'An unbound delete confirmation is already open.')


async def visible_label_matches(locator, pattern):
    for node in await locator.all():
        if await node.is_visible() and pattern.fullmatch((await node.inner_text()).strip()):
            return True
    return False


async def exact(page, target):
    url = f"https://x.com/{target['identity']}/status/{target['id']}"
    await page.goto(url, wait_until='domcontentloaded', timeout=60000)
    await page.wait_for_timeout(2000)
    await guard(page)
    await exact_account(page, target['identity'], target)
    rows = x_pages.post_row(page, target['id'])
    visible = [row for row in await rows.all() if await row.is_visible()]
    if len(visible) > 1:
        raise VpubError('wrong_work', 'X showed more than one target post.')
    if visible:
        fields = await x_pages.post_fields(visible[0])
        if not fields or fields['id'] != target['id'] or fields['author'].casefold() != target['identity'].casefold() or fields['is_ad']:
            raise VpubError('owner_unverified', 'The exact post author could not be verified.')
        await exact_account(page, target['identity'], target)
        return visible[0], False
    # Only platform error controls; never search post/reply words for "deleted".
    labels = page.locator('main [data-testid="emptyState"]')
    gone = await visible_label_matches(labels, GONE)
    await guard(page)
    await exact_account(page, target['identity'], target)
    return None, gone


async def absence(page, actual, target, gone, writes):
    url = f'https://x.com/{actual}'
    await page.goto(url, wait_until='domcontentloaded', timeout=60000)
    await page.wait_for_timeout(2500)
    correct, posts, empty = await profile_current(page, actual, target), [], False
    if correct:
        posts = await x_pages.own_posts(page, actual)
        correct = await profile_current(page, actual, target)
    if correct:
        labels = page.locator('main [data-testid="emptyState"]')
        empty = not posts and await visible_label_matches(labels, EMPTY)
        correct = await profile_current(page, actual, target)
    # A finite timeline with other posts needs a proven end signal. No-new-rows,
    # scroll height and a missing target are deliberately insufficient.
    return {'id': target['id'], 'identity': actual, 'present': False, 'owned': False, 'page_gone': gone,
            'list_absent': correct and all(post['id'] != target['id'] for post in posts),
            'list_complete': correct and empty, 'fresh': correct, 'platform_writes': writes,
            'note': 'Fresh own profile; explicit empty state only proves completion. A nonempty or unrecognized timeline remains pending.'}


async def execute(page, actual, target, operation, receipt_path):
    if operation not in {'inspect', 'delete', 'verify'}:
        raise VpubError('unsupported_operation', 'Use inspect, delete or verify.')
    if actual != target['identity'] or not re.fullmatch(r'[A-Za-z0-9_]{1,15}', actual):
        raise VpubError('wrong_account', 'Use the publishing X account from the receipt.')
    if target.get('platform') != 'x' or not re.fullmatch(r'[0-9]{10,25}', target['id']):
        raise VpubError('wrong_work', 'Use the exact X post from the publication receipt.')
    row, gone = await exact(page, target)
    if row is None:
        return await absence(page, actual, target, gone, 0)
    evidence = {'id': target['id'], 'identity': actual, 'present': True, 'owned': True,
                'page_gone': False, 'list_absent': False, 'list_complete': False, 'fresh': True, 'platform_writes': 0,
                'note': 'The exact owned post remains present.'}
    if operation != 'delete':
        return evidence
    held = []

    async def retain(locator, label):
        handles = await locator.element_handles()
        held.extend(handles)
        shown = [handle for handle in handles if await handle.is_visible()]
        if len(shown) != 1:
            raise VpubError('layout_changed', f'Expected one visible {label}.')
        handle = shown[0]
        await same_node(locator, handle, label)
        return handle

    try:
        original = await retain(x_pages.post_row(page, target['id']), 'exact owned post')
        more_query = row.locator(x_pages.MORE)
        more = await retain(more_query, 'exact post menu')
        await guard(page)
        await target_bound(page, actual, target, original)
        await same_node(more_query, more, 'exact post menu')
        if not await original.evaluate(CONTAINS, more):
            raise VpubError('wrong_work', 'The options control left the verified post.')
        await overlays_bound(page)
        stop_check('x')
        # One retained options node, without open_menu's unguarded keyboard retry.
        await more.click(timeout=10000)
        menus = page.get_by_role('menu')
        await menus.first.wait_for(state='visible', timeout=3000)
        menu = await retain(menus, 'own post menu')
        delete_query = menus.get_by_role('menuitem', name=x_pages.DELETE_POST)
        control = await retain(delete_query, 'own post Delete')

        async def menu_bound():
            await target_bound(page, actual, target, original)
            await same_node(menus, menu, 'own post menu')
            await same_node(delete_query, control, 'own post Delete')
            await overlays_bound(page, menu=menu)
            if not await menu.evaluate(CONTAINS, control):
                raise VpubError('wrong_work', 'Delete left the bound post menu.')

        await menu_bound()
        begin_press(receipt_path, target)
        await guard(page)
        await menu_bound()
        stop_check('x')
        await control.click(timeout=10000)
        dialogs = page.get_by_role('dialog').or_(page.get_by_role('alertdialog'))
        confirmation_query = dialogs.filter(has=page.locator(FINAL))
        confirmation = await retain(confirmation_query, 'post deletion confirmation')
        final_query = page.locator(FINAL)
        final = await retain(final_query, 'final post Delete')
        await guard(page)
        await target_bound(page, actual, target, original)
        await same_node(confirmation_query, confirmation, 'post deletion confirmation')
        await same_node(final_query, final, 'final post Delete')
        await overlays_bound(page, menu=menu, dialog=confirmation)
        if (not await confirmation.evaluate(CONTAINS, final)
                or not x_pages.DELETE_POST.fullmatch((await final.inner_text()).strip())):
            raise VpubError('layout_changed', 'X did not retain the bound delete confirmation.')
        stop_check('x')
        await final.click(timeout=15000)
        remaining, gone = await exact(page, target)
        result = await absence(page, actual, target, gone, 1)
        return {**result, 'present': remaining is not None, 'owned': remaining is not None}
    finally:
        for handle in held:
            if handle is not None:
                await handle.dispose()

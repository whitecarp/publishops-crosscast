"""Delete an exact owned X post once; partial profile reads never prove deletion."""
import re
from urllib.parse import urlsplit

from . import browser_runtime as runtime, x_pages
from .core import VpubError, stop_check
from .work_delete_state import begin_press
from .work_delete_x_readback import TimelineCapture


EMPTY = re.compile(r"^(?:No posts yet|You haven[’']t posted yet|你还没有发帖|你還沒有發文|尚无帖子|尚無貼文)$", re.I)
GONE = re.compile(r"^(?:This (?:Post|post|Tweet|tweet) (?:was deleted|doesn[’']t exist)|此(?:帖子|推文)不存在|这条(?:帖子|推文)已被删除|這則(?:貼文|推文)已被刪除)[.!。]?$", re.I)
GONE_DETAIL = re.compile(r"^Hmm\.\.\.this page doesn[’']t exist\. Try searching for something else\.$")
ERROR_DETAIL = 'main [data-testid="error-detail"]:not(article [data-testid="error-detail"]):not([data-testid="tweet"] [data-testid="error-detail"])'
FINAL = '[data-testid="confirmationSheetConfirm"]'
SAME = '(node, retained) => node === retained && node.isConnected'
CONTAINS = '(node, child) => node.isConnected && child.isConnected && node.contains(child)'
EXACT_WAIT_MILLISECONDS = 30000
POLL_MILLISECONDS = 500
CONFIRM_WAIT_MILLISECONDS = 10000


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
        expected = expected if isinstance(expected, (list, tuple)) else (() if expected is None else (expected,))
        for node in await page.get_by_role(role).all():
            matches = [await node.evaluate(SAME, retained) for retained in expected]
            if await node.is_visible() and not any(matches):
                raise VpubError('layout_changed', 'An unbound menu or dialog is already open.')
    if dialog is None and await runtime.visible(page.locator(FINAL)):
        raise VpubError('layout_changed', 'An unbound delete confirmation is already open.')


async def visible_label_matches(locator, pattern):
    for node in await locator.all():
        if await node.is_visible() and pattern.fullmatch((await node.inner_text()).strip()):
            return True
    return False


async def platform_gone(page):
    if await visible_label_matches(page.locator('main [data-testid="emptyState"]'), GONE):
        return True
    # Observed on the original deleted test's exact URL. This platform error
    # container is outside post/reply content; generic body words never count.
    panels = [node for node in await page.locator(ERROR_DETAIL).all() if await node.is_visible()]
    if len(panels) != 1:
        return False
    for label in await panels[0].locator('span').all():
        if await label.is_visible() and GONE_DETAIL.fullmatch(' '.join((await label.inner_text()).split())):
            return True
    return False


async def exact(page, target):
    url = f"https://x.com/{target['identity']}/status/{target['id']}"
    await page.goto(url, wait_until='domcontentloaded', timeout=60000)
    await page.wait_for_timeout(2000)
    # DOMContentLoaded can precede X's post fetch. A live read had no article
    # after two seconds and the exact owned article three seconds later.
    # Wait only for that article or an explicit platform absence control;
    # time spent loading never establishes that a post was deleted.
    for elapsed in range(2000, EXACT_WAIT_MILLISECONDS + 1, POLL_MILLISECONDS):
        await guard(page)
        exact_address(page, target)
        current = await runtime.identity(page, 'x')
        exact_address(page, target)
        if current and current != target['identity']:
            raise VpubError('wrong_account', 'The current X account changed.')
        if not current and elapsed < EXACT_WAIT_MILLISECONDS:
            # Navigation may replace the sidebar before its account link loads.
            # No post/error control is read until the live account is available.
            await page.wait_for_timeout(POLL_MILLISECONDS)
            continue
        await exact_account(page, target['identity'], target)
        rows = x_pages.post_row(page, target['id'])
        visible = [row for row in await rows.all() if await row.is_visible()]
        if len(visible) > 1:
            raise VpubError('wrong_work', 'X showed more than one target post.')
        if visible:
            fields = await x_pages.post_fields(visible[0])
            if not fields or fields['id'] != target['id'] or fields['author'].casefold() != target['identity'].casefold() or fields['is_ad']:
                raise VpubError('owner_unverified', 'The exact post author could not be verified.')
            await guard(page)
            await exact_account(page, target['identity'], target)
            return visible[0], False
        # Only platform error controls; never search post/reply words for "deleted".
        gone = await platform_gone(page)
        await guard(page)
        await exact_account(page, target['identity'], target)
        if gone:
            return None, True
        if elapsed < EXACT_WAIT_MILLISECONDS:
            await page.wait_for_timeout(POLL_MILLISECONDS)
    return None, False


async def absence(page, actual, target, gone, writes):
    url = f'https://x.com/{actual}'
    capture = TimelineCapture(page, actual)
    capture.start()
    correct, complete, empty, observed = False, False, False, set()
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=60000)
        await page.wait_for_timeout(2500)
        for _ in range(32):
            await guard(page)
            if not profile_address(page, actual):
                correct = False
                break
            current = await runtime.identity(page, 'x')
            if current and current != actual:
                correct = False
                break
            if not current:
                correct, complete = False, False
                await page.wait_for_timeout(1500)
                continue
            correct = await profile_current(page, actual, target)
            if not correct:
                break
            posts = await x_pages.own_posts(page, actual)
            observed.update(post['id'] for post in posts)
            labels = page.locator('main [data-testid="emptyState"]')
            empty = not observed and await visible_label_matches(labels, EMPTY)
            network_ids, end = capture.snapshot()
            observed.update(network_ids)
            complete = (empty and not observed) or end
            correct = await profile_current(page, actual, target)
            if not correct or complete or target['id'] in observed or not gone:
                break
            # Scroll only this verified own profile. No HTTP requests are replayed.
            await page.mouse.wheel(0, 1000)
            await page.wait_for_timeout(1500)
        # Re-read after the last awaited identity check, including requests that
        # began meanwhile. An unfinished/failed request cannot seal the list.
        network_ids, end = capture.snapshot()
        observed.update(network_ids)
        complete = complete and ((empty and not observed) or end)
    finally:
        await capture.close()
    # Closing outstanding response readers yields control; do not retain an
    # earlier identity/URL/STOP verdict across that asynchronous boundary.
    if correct:
        correct = await profile_current(page, actual, target)
    return {'id': target['id'], 'identity': actual, 'present': False, 'owned': False, 'page_gone': gone,
            'list_absent': correct and target['id'] not in observed,
            'list_complete': correct and complete, 'fresh': correct, 'platform_writes': writes,
            'note': 'Fresh own profile; completion requires an explicit empty state or an owner-bound uninterrupted originals timeline ending in the platform Bottom termination.'}


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
        final_query = page.locator(FINAL)
        dialogs = page.get_by_role('dialog').or_(page.get_by_role('alertdialog'))
        # The menu click may return before the sheet renders. Wait for one
        # final control, without clicking the menu again. All visible dialog
        # wrappers must contain that same node, so nested wrappers are allowed
        # while unrelated dialogs and duplicate final controls remain refused.
        for elapsed in range(0, CONFIRM_WAIT_MILLISECONDS + 1, POLL_MILLISECONDS):
            candidates = await final_query.element_handles()
            held.extend(candidates)
            shown = [node for node in candidates if await node.is_visible()]
            if len(shown) > 1:
                raise VpubError('layout_changed', 'Expected one visible final post Delete.')
            if shown:
                final = shown[0]
                wrappers = await dialogs.element_handles()
                held.extend(wrappers)
                # X's accessible alertdialog can have no painted box of its
                # own while containing the visible confirmation button. Keep
                # that exact structural ancestor, as well as any visible
                # competing wrapper; do not mistake its empty box for loading.
                confirmation = [node for node in wrappers if await node.is_visible()
                                or await node.evaluate(CONTAINS, final)]
                if confirmation:
                    if not all([await node.evaluate(CONTAINS, final) for node in confirmation]):
                        raise VpubError('layout_changed', 'An unrelated dialog is open beside the delete confirmation.')
                    break
            await guard(page)
            await target_bound(page, actual, target, original)
            if elapsed == CONFIRM_WAIT_MILLISECONDS:
                raise VpubError('layout_changed', 'The post deletion confirmation did not become ready; only verify this attempt.')
            await page.wait_for_timeout(POLL_MILLISECONDS)
        await guard(page)
        await target_bound(page, actual, target, original)
        await same_node(final_query, final, 'final post Delete')
        # Revalidate the retained ancestor chain, not a locator that could
        # silently follow a replacement confirmation after the safety checks.
        current = [node for node in await dialogs.all() if await node.is_visible()
                   or await node.evaluate(CONTAINS, final)]
        if len(current) != len(confirmation):
            raise VpubError('wrong_work', 'The deletion confirmation was replaced.')
        for node in current:
            if not any([await node.evaluate(SAME, retained) for retained in confirmation]):
                raise VpubError('wrong_work', 'The deletion confirmation was replaced.')
        await overlays_bound(page, menu=menu, dialog=confirmation)
        if (not all([await node.evaluate(CONTAINS, final) for node in confirmation])
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

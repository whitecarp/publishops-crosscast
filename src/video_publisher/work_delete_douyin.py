"""Free Creator Center deletion of one exact owned card, with no write retries."""
import re
import time
from urllib.parse import parse_qsl, urljoin, urlsplit

from . import browser_backends as bb, browser_runtime as rt, work_delete_state as state
from .core import VpubError, stop_check


DETAIL = '/creator-micro/work-management/work-detail/'
# The one part a real detail page's address was seen to carry besides the work: a field run's read named enter_from, which says
# where the page was opened from and not which work it shows. Anything else, a second copy of it, or a fragment still refuses.
DETAIL_ALLOWED = {'enter_from'}
SAME = '(node, retained) => node === retained && node.isConnected'
CONTAINS = '(node, child) => node.isConnected && child.isConnected && node.contains(child)'
GONE = re.compile(r'^(?:该)?(?:作品|视频)已(?:被)?删除[。！]?$')
# An unknown platform layout remains pending. User-authored card/title text is excluded.
TERMINAL = """nodes => {
  const shown = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
      && !e.closest('[hidden],[aria-hidden="true"]');
  if ([...document.querySelectorAll('[role="progressbar"],[aria-busy="true"],[role="alert"],'
      + '[role="dialog"],.semi-modal-content')].some(shown)) return false;
  if ([...document.querySelectorAll('input')].some(e => shown(e) && e.value.trim())
      || [...document.querySelectorAll('select')].some(shown)) return false;
  const tabs = [...document.querySelectorAll('[role="tab"][aria-selected="true"]')].filter(shown);
  if (tabs.length !== 1 || !['全部', '全部作品'].includes(tabs[0].innerText.trim())) return false;
  const ends = nodes.filter(e => shown(e)
      && !e.closest('[class^="video-card-content-"],header,nav,[role="dialog"],.semi-modal-content')
      && ['没有更多作品','暂无作品','暂无内容'].includes(e.innerText.trim()));
  if (ends.length !== 1) return false;
  const cards = document.querySelectorAll('[class^="video-card-content-"]');
  return !cards.length || ends[0].innerText.trim() === '没有更多作品';
}"""
GONE_SNAPSHOT = r"""(nodes, expectedURL) => {
  if (location.href !== expectedURL) return 'wrong_page';
  const eligible = nodes.filter(node => node.isConnected && [...node.getClientRects()].some(rect => rect.width > 0 && rect.height > 0)
      && getComputedStyle(node).visibility === 'visible'
      && !node.closest('[hidden],[aria-hidden="true"],[class^="video-card-content-"],article,[role="dialog"],.semi-modal-content'));
  if (!eligible.length) return 'no_platform_alert';
  if (eligible.length !== 1) return 'ambiguous_alerts';
  return /^(?:该)?(?:作品|视频)已(?:被)?删除[。！]?$/.test(eligible[0].innerText.trim())
      ? 'deleted_notice' : 'unrecognized_alert';
}"""
HOME_CONFIRM_SECONDS = 120
HOME_CONFIRMED = {}  # id(page) -> (account, when, page): the dedicated browser is locked to this run meanwhile.
GONE_WAIT_SECONDS = 10


async def guard(page):
    stop_check('douyin')
    await rt.risk_check(page)
    stop_check('douyin')


async def same_node(locator, retained, label):
    current = await rt.unique_visible(locator, label)
    if retained is None or not await current.evaluate(SAME, retained):
        raise VpubError('wrong_work', 'A bound work control was replaced; no other control is pressed.')


async def retain(locator, label, held):
    handles = await locator.element_handles()
    held.extend(handles)
    shown = [handle for handle in handles if await handle.is_visible()]
    if len(shown) != 1:
        raise VpubError('layout_changed', f'Expected one visible {label}.')
    await same_node(locator, shown[0], label)
    return shown[0]


def validate(actual, target, operation):
    if operation not in {'inspect', 'delete', 'verify'}:
        raise VpubError('unsupported_operation', 'Use inspect, delete or verify.')
    if (target.get('platform') != 'douyin' or actual != target.get('identity')
            or not isinstance(actual, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', actual)):
        raise VpubError('wrong_account', 'The publishing Douyin identity must match exactly.')
    wid = target.get('id')
    if (not isinstance(wid, str) or not re.fullmatch(r'[0-9]{10,25}', wid)
            or target.get('url') != bb.work_url('douyin', actual, wid)):
        raise VpubError('wrong_work', 'Use the exact work ID and URL from the receipt.')


def evidence(target, **fields):
    return {'id': target['id'], 'identity': target['identity'], 'present': False, 'owned': False,
            'page_gone': False, 'list_absent': False, 'list_complete': False, 'fresh': False,
            'platform_writes': 0, 'note': 'Deletion is unconfirmed; incomplete Creator Center lists remain pending.', **fields}


async def home_owner(page):
    """The account Creator Center's home page shows, read in a second tab of the same signed-in browser."""
    home = await page.context.new_page()
    try:
        await home.goto(rt.URLS['douyin'], wait_until='domcontentloaded', timeout=60000)
        return await rt.look_until(home, lambda: rt.identity(home, 'douyin'), 15000)
    finally:
        await home.close()


async def owner(page, target):
    address = urlsplit(page.url)
    if (address.scheme != 'https' or address.netloc != 'creator.douyin.com'
            or address.path != urlsplit(bb.MANAGE).path or address.query or address.fragment):
        raise VpubError('wrong_account', 'Creator Center did not stay on the publishing account\'s own work list.')
    shown = await rt.identity(page, 'douyin')
    if shown is None:
        # A real run found the work list no longer prints 抖音号, though its home page still does.
        # Every card is checked here, so one home-page read serves this browser for a short while.
        held = HOME_CONFIRMED.get(id(page))
        if held and held[2] is page and time.monotonic() - held[1] < HOME_CONFIRM_SECONDS:
            shown = held[0]
        else:
            shown = await home_owner(page)
            HOME_CONFIRMED[id(page)] = (shown, time.monotonic(), page)
    if shown != target['identity']:
        raise VpubError('wrong_account', 'Creator Center did not show the publishing account\'s 抖音号, on its work list or its home page.')


async def same_card(page, target, card, handle, words):
    await owner(page, target)
    if (await card.count() != 1 or not await card.is_visible()
            or not await card.evaluate('(node, original) => node === original && node.isConnected', handle)):
        raise VpubError('wrong_work', 'The ID-checked card changed or moved; no delete is allowed.')
    name = await rt.unique_visible(card.locator(bb.DOUYIN_NAME), 'bound work title')
    if (await name.inner_text()).strip() != words:
        raise VpubError('wrong_work', 'The ID-checked card changed its title.')


async def card_id(page, target, card, handle, words):
    """Use the proven same-card popup, never the helper's title-based private-card fallback."""
    if await card.get_by_text('私密', exact=True).count():
        return None
    await same_card(page, target, card, handle, words)
    name = await rt.unique_visible(card.locator(bb.DOUYIN_NAME), 'work detail trigger')
    await guard(page)
    async with page.expect_popup(timeout=bb.POPUP_MS) as opened:
        await name.click(timeout=10000)
    detail = await opened.value
    try:
        await detail.wait_for_url(re.compile(r'/work-detail/\d+'), wait_until='commit', timeout=20000)
        await guard(detail)
        address = urlsplit(detail.url)
        match = re.fullmatch(re.escape(DETAIL) + r'([0-9]{10,25})', address.path)
        # A field run's delete stopped here every time, while its works list read the same card's ID from the page's path,
        # and the one message couldn't say which check refused it. The checks are as strict as before; each now says which it was.
        if address.scheme != 'https' or address.netloc != 'creator.douyin.com':
            raise VpubError('wrong_work', "The card's detail page opened somewhere other than Creator Center, so nothing is deleted.")
        if not match:
            raise VpubError('wrong_work', "The card's detail page didn't name one exact work in its address, so nothing is deleted.")
        query = parse_qsl(address.query, keep_blank_values=True)
        names = [name for name, _ in query]
        allowed = bool(query) and len(names) == len(set(names)) and set(names) <= DETAIL_ALLOWED
        if address.fragment or (address.query and not allowed):
            # The names of its parts only, never what they hold.
            parts = sorted(set(names) - DETAIL_ALLOWED)[:6] or (['an unnamed part'] if address.query and not query else [])
            parts += ['enter_from twice'] if names.count('enter_from') > 1 else []
            parts += ['a fragment'] if address.fragment else []
            raise VpubError('wrong_work', f"The card's detail page address carries more than the work ({', '.join(parts)}), so nothing is deleted.")
        if await detail.opener() is not page:
            raise VpubError('wrong_work', "The card's detail page wasn't opened by the works list itself, so nothing is deleted.")
        return match[1]
    finally:
        await detail.close()


PRIVATE_EDIT_MENU = 'ul.semi-dropdown-menu[class*="edit-menu-"] > li.semi-dropdown-item'


async def private_editor_id(page, target, card, handle, words):
    """Read the editor opened by this retained card. No title-based search or save."""
    label = card.get_by_text('编辑作品', exact=True)
    if await label.count() != 1 or not await label.is_visible():
        return None
    item = page.locator(PRIVATE_EDIT_MENU).filter(has_text=re.compile(r'^\s*编辑作品\s*$'))
    opened_trigger = card.locator('[class*="edit-btn-"].semi-dropdown-showing')
    showing = page.locator('.semi-dropdown-showing')
    if await rt.visible(item) or await showing.count():
        return None
    await same_card(page, target, card, handle, words)
    await guard(page)
    await label.hover(timeout=5000)
    if not await rt.look_until(page, lambda: rt.visible(item), 3000):
        return None
    held = []
    try:
        if await opened_trigger.count() != 1 or await showing.count() != 1:
            return None
        edit = await retain(item, 'private card edit menu item', held)
        await guard(page)
        await same_card(page, target, card, handle, words)
        await same_node(item, edit, 'original edit menu item')
        if (not await opened_trigger.is_visible() or await showing.count() != 1
                or (await edit.inner_text()).strip() != '编辑作品'):
            return None
        links = item.locator('a[href]')
        link_count = await links.count()
        if link_count:
            if link_count != 1 or not await links.is_visible():
                return None
            href = await links.get_attribute('href')
            await same_node(item, edit, 'original edit menu item')
            if not href or not await opened_trigger.is_visible() or await showing.count() != 1:
                return None
            # Release this card's hover menu before proceeding to another card.
            await guard(page)
            await same_card(page, target, card, handle, words)
            await card.locator(bb.DOUYIN_NAME).hover(timeout=5000)
            return private_editor_work(urljoin(page.url, href))
        # Only a child editor keeps the original list and retained cards intact.
        async with page.expect_popup(timeout=bb.POPUP_MS) as opened:
            await edit.click(timeout=5000)
        detail = await opened.value
        if await detail.opener() is not page:
            return None
        try:
            await detail.wait_for_url(re.compile(r'/creator-micro/content/post/video\?'), wait_until='commit', timeout=10000)
            await guard(detail)
            wid = private_editor_work(detail.url)
            if not wid:
                return None
            title, caption = await bb.douyin_editor_boxes(detail, wid)
            shown = bb.douyin_card_says(await title.input_value(), await caption.inner_text())
            await guard(detail)
            if bb.douyin_card_words(words) in shown and private_editor_work(detail.url) == wid:
                return wid
            return None
        finally:
            await detail.close()
    except Exception as exc:
        if type(exc).__name__ != 'TimeoutError':
            raise
        await guard(page)
        return None
    finally:
        for node in held:
            await node.dispose()


def private_editor_work(url):
    try:
        fields = parse_qsl(urlsplit(url).query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
        wid = bb.douyin_editor_work(url)
    except ValueError:
        return None
    if (wid and re.fullmatch(r'[0-9]{10,25}', wid)
            and set(fields) == {('mid', wid), ('enter_from', 'edit_item')}):
        return wid
    return None


async def own_list(page, target, diagnostics=None, *, verify_private=False):
    await guard(page)
    await page.goto(bb.MANAGE, wait_until='domcontentloaded', timeout=60000)
    cards = page.locator(bb.DOUYIN_CARD)
    ends = page.get_by_text(bb.DOUYIN_LIST_ENDS)
    for _ in range(bb.DOUYIN_LIST_SECONDS):
        await guard(page)
        if await rt.visible(cards) or await rt.visible(ends):
            break
        await page.wait_for_timeout(1000)
    await owner(page, target)
    count = await cards.count()
    complete = await ends.evaluate_all(TERMINAL)
    checks = diagnostics if diagnostics is not None else {}
    checks.update(rows=count, resolved=0, private_unresolved=0, list=[])
    if not complete:
        checks['list'].append('terminal_or_filter_unconfirmed')
    if count > 100:
        checks['list'].append('read_limit')
    matches, seen, held, observed, finished = [], set(), [], [], False
    try:
        # Absence requires every card in the bounded read to have a distinct ID.
        for index in range(min(count, 100)):
            card = cards.nth(index)
            if not await card.is_visible():
                complete = False
                checks['list'].append('hidden_card')
                continue
            name = await rt.unique_visible(card.locator(bb.DOUYIN_NAME), 'work card title')
            words = (await name.inner_text()).strip()
            handle = await card.element_handle()
            held.append(handle)
            observed.append((card, handle, words))
            if verify_private and await card.get_by_text('私密', exact=True).count():
                wid = await private_editor_id(page, target, card, handle, words)
                if private_editor_work(page.url):
                    # In-place navigation replaced the inventory and its bindings.
                    checks['list'].append('editor_replaced_list')
                    checks['private_unresolved'] += 1
                    checks['resolved'] = len(seen)
                    return [], False
            else:
                wid = await card_id(page, target, card, handle, words)
            await same_card(page, target, card, handle, words)
            if not wid or wid in seen:
                complete = False
                checks['list'].append('unresolved_id' if not wid else 'duplicate_id')
                if not wid and await card.get_by_text('私密', exact=True).count():
                    checks['private_unresolved'] += 1
            if wid:
                seen.add(wid)
            if wid == target['id']:
                matches.append((card, handle, words))
        if len(matches) > 1:
            raise VpubError('wrong_work', 'More than one card resolves to this exact work ID.')
        await owner(page, target)
        # Opening a later popup can replace an earlier, already-read card while
        # leaving the count unchanged. Absence needs every observed binding intact.
        if complete:
            for binding in observed:
                try:
                    await same_card(page, target, *binding)
                except VpubError as exc:
                    if exc.code not in {'wrong_work', 'layout_changed'}:
                        raise
                    complete = False
                    checks['list'].append('card_changed')
                    break
        checks['resolved'] = len(seen)
        final_count = await cards.count()
        terminal = await ends.evaluate_all(TERMINAL)
        if count != final_count:
            checks['list'].append('row_count_changed')
        if not terminal:
            checks['list'].append('terminal_or_filter_unconfirmed')
        complete = bool(complete and count <= 100 and count == final_count
                        and len(seen) == count and terminal)
        if matches:
            await same_card(page, target, *matches[0])
        # Keep only the actual target handle alive until the caller finishes.
        finished = True
        return matches, complete
    finally:
        retained = matches[0][1] if finished and len(matches) == 1 else None
        for handle in held:
            if handle is not retained:
                await handle.dispose()


def exact_page_failure(url, target):
    """Fixed reason codes only; never expose arbitrary URLs, query values or page text."""
    address = urlsplit(url)
    if address.scheme != 'https' or address.netloc != 'www.douyin.com':
        return 'wrong_origin'
    if address.path != f"/video/{target['id']}":
        return 'wrong_path'
    if address.query or address.fragment:
        return 'query_or_fragment'
    return None


async def exact_gone(page, target, diagnostics=None):
    checks = diagnostics if diagnostics is not None else {}
    await guard(page)
    await page.goto(target['url'], wait_until='domcontentloaded', timeout=60000)
    # DOMContentLoaded precedes client-rendered notices. Re-read with a bound,
    # checking the exact URL, platform checks and STOP on every iteration.
    for attempt in range(GONE_WAIT_SECONDS * 2 + 1):
        await guard(page)
        failure = exact_page_failure(page.url, target)
        if failure:
            checks['exact_page'] = failure
            return False
        # Read membership, ancestry, text and URL in one DOM snapshot. A locator
        # checked before an await can otherwise adopt a new, user-authored alert.
        checks['exact_page'] = await page.locator('[role="alert"]').evaluate_all(GONE_SNAPSHOT, target['url'])
        stop_check('douyin')
        if checks['exact_page'] == 'deleted_notice':
            return True
        if checks['exact_page'] == 'wrong_page':
            return False
        if attempt < GONE_WAIT_SECONDS * 2:
            await page.wait_for_timeout(500)
    return False


def readback_note(checks, confirmed):
    # Only our own codes and counts reach the receipt; no private titles, DOM,
    # query strings, credentials or arbitrary exception text.
    status = 'Deletion verified.' if confirmed else 'Deletion is unconfirmed; verify only.'
    reasons = ','.join(sorted(set(checks.get('list', [])))) or 'complete'
    return (f"{status} Read-back: exact_page={checks.get('exact_page', 'not_read')}; list={reasons}; "
            f"rows={checks['rows']}; resolved={checks['resolved']}; private_unresolved={checks['private_unresolved']}.")


async def readback(page, target, writes=0, *, verify_private=False):
    checks = {}
    gone = await exact_gone(page, target, checks)
    matches, complete = await own_list(page, target, checks, verify_private=verify_private)
    try:
        return evidence(target, present=bool(matches), owned=bool(matches), page_gone=gone and not matches,
                        list_absent=not matches and complete, list_complete=complete, fresh=True,
                        platform_writes=writes,
                        note=readback_note(checks, gone and not matches and complete))
    finally:
        if matches:
            await matches[0][1].dispose()


async def execute(page, actual, target, operation, receipt_path):
    validate(actual, target, operation)
    writes, retained, held = 0, None, []
    try:
        if operation == 'verify':
            # Verify needs one fresh scan after the exact-page read, not two
            # scans opening every card's popup. It never reaches delete controls.
            return await readback(page, target, verify_private=True)
        matches, complete = await own_list(page, target)
        if not matches:
            return await readback(page, target)
        card, retained, words = matches[0]
        if operation != 'delete':
            return evidence(target, present=True, owned=True, list_complete=complete, fresh=True)
        if not target.get('title') or not words.startswith(target['title']):
            raise VpubError('wrong_work', 'The exact work title changed; inspect its receipt.')
        if await rt.visible(page.locator('.semi-modal-content')):
            raise VpubError('layout_changed', 'An existing confirmation has no bound deletion origin.')
        # Other cards were read after this one. Re-open this same retained node,
        # since a virtualized list may recycle a node without changing its title.
        if await card_id(page, target, card, retained, words) != target['id']:
            raise VpubError('wrong_work', 'The original card now resolves to a different work.')
        control = card.get_by_text('删除作品', exact=True)
        delete_control = await retain(control, 'bound work Delete', held)

        async def delete_bound():
            await same_card(page, target, card, retained, words)
            await same_node(control, delete_control, 'original work Delete')
            if not await retained.evaluate(CONTAINS, delete_control):
                raise VpubError('wrong_work', 'Delete left the ID-checked card.')
            if await rt.visible(page.locator('.semi-modal-content')):
                raise VpubError('layout_changed', 'An unbound confirmation appeared before Delete.')

        await delete_bound()
        state.begin_press(receipt_path, target)
        await guard(page)
        await delete_bound()
        stop_check('douyin')
        writes = 1  # One possibly destructive flow; never a claimed server acknowledgement.
        await delete_control.click(timeout=10000)
        dialog = page.locator('.semi-modal-content')
        original_dialog = await retain(dialog, 'work delete confirmation', held)
        final = dialog.get_by_role('button', name='确定', exact=True)
        final_control = await retain(final, 'final work delete', held)
        # Pin before any later read: a locator must never adopt a replacement modal.
        await guard(page)
        await same_card(page, target, card, retained, words)
        await same_node(dialog, original_dialog, 'original work confirmation')
        await same_node(final, final_control, 'final work delete')
        if (not await original_dialog.evaluate(CONTAINS, final_control)
                or (await original_dialog.inner_text()).strip() != '确定要移除此作品吗\n取消\n确定'):
            raise VpubError('layout_changed', 'Creator Center did not retain the bound delete confirmation.')
        stop_check('douyin')
        await final_control.click(timeout=15000)
        await page.wait_for_timeout(3000)
        await guard(page)
        return await readback(page, target, writes, verify_private=True)
    except Exception as exc:
        if type(exc).__name__ != 'TimeoutError':
            raise
        return evidence(target, platform_writes=writes, note='A browser read or press timed out; verify only. No click was retried.')
    finally:
        if retained is not None:
            await retained.dispose()
        for handle in held:
            await handle.dispose()

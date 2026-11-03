"""One-shot free Studio deletion. DOM contracts are built, not live-certified."""
import json
import re
from urllib.parse import parse_qs, urlsplit

from . import browser_runtime as rt, work_delete_state as state, youtube_studio as yt
from .browser_backends import work_url
from .core import VpubError, stop_check


OPTIONS = re.compile(r'^(选项|選項|Options)$')
DELETE = re.compile(r'^\s*(Delete forever|永久删除|永久刪除)\s*$')
FINAL = re.compile(r'^(永久删除|永久刪除|删除草稿视频|刪除草稿影片|Delete forever|Delete draft)$')
MENU = 'ytcp-menu-service-item-renderer,tp-yt-paper-item,[role=menuitem]'
SAME = '(node, retained) => node === retained && node.isConnected'
CONTAINS = '(node, child) => node.isConnected && child.isConnected && node.contains(child)'
GONE = re.compile(r'^(?:This video has been removed by the uploader|This video has been deleted'
                  r'|此视频已被上传者删除|此影片已由上傳者移除)[.!。]?$')
# Only the player's own error, never descriptions, comments or generic unavailable text.
GONE_PANEL = 'ytd-player-error-message-renderer #reason'
EMPTY = re.compile(r'^(?:No content available|没有可显示的内容|沒有可顯示的內容)$')
LIST_CLEAN = """nodes => {
  const shown = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
      && !e.closest('[hidden],[aria-hidden="true"]');
  if (nodes.length !== 1) return false;
  if ([...document.querySelectorAll('ytcp-filter-chip,ytcp-chip #delete-icon,[role="progressbar"],[aria-busy="true"],'
      + '[role="alert"],[role="dialog"],[role="alertdialog"]')].some(shown)) return false;
  return ![...document.querySelectorAll('input')].some(e => shown(e)
      && ['text','search'].includes(e.type) && e.value.trim());
}"""


async def guard(page):
    stop_check('youtube')
    await rt.risk_check(page)
    stop_check('youtube')


async def same_node(locator, retained, label):
    current = await rt.unique_visible(locator, label)
    if retained is None or not await current.evaluate(SAME, retained):
        raise VpubError('wrong_work', 'A bound video control was replaced; no other control is pressed.')


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
    if (target.get('platform') != 'youtube' or actual != target.get('identity')
            or not isinstance(actual, str) or not re.fullmatch(r'UC[A-Za-z0-9_-]{22}', actual)):
        raise VpubError('wrong_account', 'The publishing channel must match exactly.')
    wid = target.get('id')
    if (not isinstance(wid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', wid)
            or target.get('url') != work_url('youtube', actual, wid)):
        raise VpubError('wrong_work', 'Use the exact video ID and URL from the receipt.')


def evidence(target, **fields):
    return {'id': target['id'], 'identity': target['identity'], 'present': False, 'owned': False,
            'page_gone': False, 'list_absent': False, 'list_complete': False, 'fresh': False,
            'platform_writes': 0, 'note': 'Deletion is unconfirmed; only a fresh complete read can confirm it.', **fields}


def unfiltered_query(query):
    """Studio adds this default query after navigation; filters remain forbidden."""
    if not query:
        return True
    try:
        fields = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
        if set(fields) != {'filter', 'sort'} or any(len(v) != 1 for v in fields.values()):
            return False
        sort = json.loads(fields['sort'][0], object_pairs_hook=lambda pairs: pairs)
        return (json.loads(fields['filter'][0]) == []
                and isinstance(sort, list) and len(sort) == 2
                and ('columnType', 'date') in sort and ('sortOrder', 'DESCENDING') in sort)
    except (TypeError, ValueError):
        return False


async def owner(page, target, tab):
    address = urlsplit(page.url)
    if (address.scheme != 'https' or address.netloc != 'studio.youtube.com'
            or address.path != f"/channel/{target['identity']}/videos/{tab}"
            or not unfiltered_query(address.query) or address.fragment
            or await rt.identity(page, 'youtube') != target['identity']):
        raise VpubError('wrong_account', 'Studio did not retain the exact publishing channel list.')


async def empty_list(page):
    if await page.locator('ytcp-video-row').count():
        return False
    section = page.locator('ytcp-video-section-content')
    if await section.count() != 1 or not await section.evaluate_all(LIST_CLEAN):
        return False
    panels = section.locator('.no-content').filter(has_text=EMPTY)
    return await panels.count() == 1 and await panels.is_visible()


async def own_list(page, target):
    """Both supported publication tabs, every page, with no lost or repeated rows."""
    matches, complete = [], True
    for tab in yt.STUDIO_LISTS:
        await guard(page)
        rows = await yt.content_rows(page, target['identity'], tab)
        await owner(page, target, tab)
        page_number = 0
        clean = True

        def parse(raw):
            nonlocal page_number
            for row in raw:
                if row.get('href') == f"/video/{target['id']}/edit":
                    matches.append((tab, page_number))
            page_number += 1
            return yt.work_items(raw, target['identity'])

        # The shared paginator invokes evaluate_all for each page; check filters,
        # identity and STOP on each snapshot, not just at the beginning of a tab.
        class CheckedRows:
            first = rows.first

            async def count(self):
                return await rows.count()

            async def evaluate_all(self, script):
                nonlocal clean
                await guard(page)
                await owner(page, target, tab)
                clean = clean and await page.locator('body').evaluate_all(LIST_CLEAN)
                clean = clean and all([await row.is_visible() for row in await rows.all()])
                return await rows.evaluate_all(script)

        _, ended = await yt.studio_table(page, CheckedRows(), yt.WORK_ROWS, parse,
                                        require_last_page=True, empty_first_page=lambda: empty_list(page))
        await guard(page)
        await owner(page, target, tab)
        complete = complete and ended and clean
    if len(matches) > 1:
        raise VpubError('wrong_work', 'The exact video occurs more than once; no delete is allowed.')
    return matches, bool(complete)


async def exact_gone(page, target):
    await guard(page)
    await page.goto(target['url'], wait_until='domcontentloaded', timeout=60000)
    await guard(page)
    address = urlsplit(page.url)
    if (address.scheme != 'https' or address.netloc != 'www.youtube.com' or address.path != '/watch'
            or parse_qs(address.query, keep_blank_values=True) != {'v': [target['id']]} or address.fragment):
        return False
    panels = page.locator(GONE_PANEL)
    visible = [node for node in await panels.all() if await node.is_visible()]
    return len(visible) == 1 and bool(GONE.fullmatch((await visible[0].inner_text()).strip()))


async def readback(page, target, writes=0):
    gone = await exact_gone(page, target)
    matches, complete = await own_list(page, target)
    return evidence(target, present=bool(matches), owned=bool(matches), page_gone=gone and not matches,
                    list_absent=not matches and complete, list_complete=complete, fresh=True,
                    platform_writes=writes)


async def bound_row(page, target, position):
    tab, index = position
    rows = await yt.content_rows(page, target['identity'], tab)
    for _ in range(index):
        await owner(page, target, tab)
        after = await rt.unique_visible(page.locator('#navigate-after'), 'next video page')
        if await after.get_attribute('aria-disabled') != 'false':
            raise VpubError('wrong_work', 'The target page is no longer reachable.')
        await guard(page)
        await after.click(timeout=15000)
        await rows.first.wait_for(state='visible', timeout=15000)
    row = rows.filter(has=page.locator(f'a[href="/video/{target["id"]}/edit"]'))
    await check_row(page, target, tab, row)
    return tab, row


async def check_row(page, target, tab, row, retained=None):
    await owner(page, target, tab)
    if await row.count() != 1 or not await row.is_visible():
        raise VpubError('wrong_work', 'The delete control is not bound to one exact video row.')
    title = await rt.unique_visible(row.locator('a#video-title'), 'exact video title')
    if not target.get('title') or (await title.inner_text()).strip() != target['title'].strip():
        raise VpubError('wrong_work', 'The exact video title changed; inspect the receipt.')
    if retained is not None:
        await same_node(row, retained, 'original video row')


async def execute(page, actual, target, operation, receipt_path):
    validate(actual, target, operation)
    writes, held = 0, []
    try:
        matches, complete = await own_list(page, target)
        if not matches:
            return await readback(page, target)
        if operation != 'delete':
            return evidence(target, present=True, owned=True, list_complete=complete, fresh=True)
        tab, row = await bound_row(page, target, matches[0])
        original_row = await retain(row, 'exact video row', held)
        if await rt.visible(page.get_by_role('dialog')) or await rt.visible(page.locator(MENU).filter(has_text=DELETE)):
            raise VpubError('layout_changed', 'An existing menu or dialog has no bound deletion origin.')
        await row.hover()
        options = row.get_by_role('button', name=OPTIONS)
        options_control = await retain(options, 'exact video options', held)
        await guard(page)
        await check_row(page, target, tab, row, original_row)
        await same_node(options, options_control, 'exact video options')
        if not await original_row.evaluate(CONTAINS, options_control):
            raise VpubError('wrong_work', 'Options left the original video row.')
        if await rt.visible(page.get_by_role('dialog')) or await rt.visible(page.locator(MENU).filter(has_text=DELETE)):
            raise VpubError('layout_changed', 'An unbound menu or dialog appeared before Options.')
        stop_check('youtube')
        await options_control.click(timeout=10000)
        control = page.locator(MENU).filter(has_text=DELETE)
        delete_control = await retain(control, 'video delete menu', held)

        async def menu_bound():
            await check_row(page, target, tab, row, original_row)
            await same_node(control, delete_control, 'original video delete menu')
            if await rt.visible(page.get_by_role('dialog')):
                raise VpubError('layout_changed', 'An unbound confirmation appeared before Delete.')

        await menu_bound()
        state.begin_press(receipt_path, target)
        await guard(page)
        await menu_bound()
        stop_check('youtube')
        writes = 1  # An attempted destructive flow, not an acknowledgement of a server write.
        await delete_control.click(timeout=10000)
        dialog = page.get_by_role('dialog')
        original_dialog = await retain(dialog, 'video delete confirmation', held)

        async def dialog_bound(query, handle, label):
            await check_row(page, target, tab, row, original_row)
            await same_node(dialog, original_dialog, 'original video confirmation')
            await same_node(query, handle, label)
            if (not await original_dialog.evaluate(CONTAINS, handle)
                    or not await rt.visible(dialog.get_by_text(target['title'], exact=True))):
                raise VpubError('wrong_work', 'The confirmation no longer contains the bound video control and title.')

        box = dialog.get_by_role('checkbox')
        box_control = await retain(box, 'delete acknowledgement', held)
        if await box_control.get_attribute('aria-checked') != 'true':
            checkbox = dialog.locator('ytcp-checkbox-lit#confirm-checkbox')
            checkbox_control = await retain(checkbox, 'delete checkbox', held)
            await guard(page)
            await dialog_bound(box, box_control, 'delete acknowledgement')
            await dialog_bound(checkbox, checkbox_control, 'delete checkbox')
            stop_check('youtube')
            await checkbox_control.click(timeout=10000)
        final = dialog.get_by_role('button', name=FINAL)
        final_control = await retain(final, 'final video delete', held)
        # Pin before any later read: a locator must never adopt a replacement dialog.
        await guard(page)
        await dialog_bound(box, box_control, 'delete acknowledgement')
        if await box_control.get_attribute('aria-checked') != 'true':
            raise VpubError('layout_changed', 'The delete acknowledgement did not remain checked.')
        await dialog_bound(final, final_control, 'final video delete')
        stop_check('youtube')
        await yt.act_and_settle(page, lambda: final_control.click(timeout=15000))
        return await readback(page, target, writes)
    except Exception as exc:
        if type(exc).__name__ != 'TimeoutError':
            raise
        # Never retry a timed-out press or serialize browser error text/page contents.
        return evidence(target, platform_writes=writes, note='A browser read or press timed out; verify only. No click was retried.')
    finally:
        for handle in held:
            await handle.dispose()

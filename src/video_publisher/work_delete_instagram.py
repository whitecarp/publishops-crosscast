"""One exact Instagram post, with conservative page and complete-profile read-back."""
from dataclasses import dataclass
import re
from urllib.parse import urljoin, urlsplit

from . import browser_runtime as runtime
from .browser_backends import instagram_address_is
from .core import VpubError, stop_check
from .work_delete_state import begin_press


DELETE = re.compile(r'^(Delete|删除|刪除)$')
EDIT = re.compile(r'^(Edit|编辑|編輯)$')
OPTIONS = re.compile(r'^(More options|更多选项|更多選項)$')
CONFIRM = re.compile(r'(?:Delete (?:post|reel|video)\?|删除(?:帖子|视频|短视频)[？?]|刪除(?:貼文|影片|連續短片)[？?])', re.I)
GONE = re.compile(r'^(?:This (?:post|reel|video) has been deleted|此(?:帖子|视频)已删除|此(?:貼文|影片)已刪除)[.!。]?$', re.I)
EMPTY = re.compile(r'^(?:No Posts Yet|还没有帖子|尚無貼文)$', re.I)
COUNT = re.compile(r'^([0-9][0-9,]*)\s*(?:posts?|篇帖子|篇貼文|帖子|貼文)$', re.I)
SAME = '(node, retained) => node === retained && node.isConnected'
CONTAINS = '(node, child) => node.isConnected && child.isConnected && node.contains(child)'
SCOPE_LINKS = "node => [...node.querySelectorAll('a[href]')].map(e => e.getAttribute('href'))"
POST_PATH = re.compile(r'/(?:[A-Za-z0-9_.]+/)?(?:p|reel|reels)/[A-Za-z0-9_-]+/?')
POST_PANELS = 'main div:has(> hr):has(svg[aria-label="More options"])'
AUTHOR_BAR = ':scope > div:first-child'
LAYOUT_OPTIONS = '[role="button"]:has(svg[aria-label="More options"])'
TIMESTAMP = ':scope > div:has(svg[aria-label="Like"]):has(svg[aria-label="Comment"]):has(svg[aria-label="Share"]):has(svg[aria-label="Save"]) a[href]:has(time)'
NAV_AVATARS = 'a[href]:has(img[alt])'
CONTROL_WAIT_MILLISECONDS = 10000
CONTROL_POLL_MILLISECONDS = 500
NAV_IDENTITY = r"""links => {
  const content = 'main,[role="main"],article,[role="dialog"],[role="alertdialog"],[contenteditable="true"]';
  const shown = e => e.isConnected && !!e.getClientRects().length
    && getComputedStyle(e).visibility !== 'hidden' && !e.closest('[hidden],[aria-hidden="true"]');
  const names = [];
  for (const link of links) {
    if (!shown(link) || link.closest(content)) continue;
    const pictures = [...link.querySelectorAll('img[alt]')].filter(shown);
    if (pictures.length !== 1) continue;
    const name = /^([A-Za-z0-9_.]{1,30})'s profile picture$/.exec(pictures[0].getAttribute('alt'))?.[1];
    if (!name || ['accounts', 'explore', 'reels'].includes(name)) continue;
    const href = link.getAttribute('href');
    if (href !== '/' + name + '/' && href !== '/' + name) continue;
    // Observed seven-link sidebar, separate from main and all post/comment text.
    for (let scope = link.parentElement; scope && ['DIV', 'SPAN'].includes(scope.tagName); scope = scope.parentElement) {
      if (scope.closest(content) || scope.querySelectorAll(content).length) break;
      const anchors = [...scope.querySelectorAll('a[href]')].filter(shown);
      if (anchors.length !== 7) continue;
      const icons = [...scope.querySelectorAll('svg[aria-label]')].filter(shown);
      const navigation = ['Home', 'Reels', 'Messages', 'Search', 'Notifications'].map(label => {
        const found = icons.filter(icon => icon.getAttribute('aria-label') === label);
        return found.length === 1 ? found[0].closest('a[href]') : null;
      });
      if (navigation.some(anchor => !anchor || !anchors.includes(anchor) || anchor === link)
          || new Set(navigation).size !== 5) continue;
      const avatars = anchors.filter(anchor => [...anchor.querySelectorAll('img[alt]')].some(img =>
        shown(img) && /^[A-Za-z0-9_.]{1,30}'s profile picture$/.test(img.getAttribute('alt'))));
      if (avatars.length !== 1 || avatars[0] !== link) continue;
      names.push(name);
      break;
    }
  }
  return names;
}"""
POST_STRUCTURE = r"""(scope, bound) => {
  const {author, more, permalink, identity} = bound;
  const shown = e => e && e.isConnected && !!e.getClientRects().length
    && getComputedStyle(e).visibility !== 'hidden' && !e.closest('[hidden],[aria-hidden="true"]');
  const controls = 'textarea,[contenteditable="true"],form,ul,ol,[role="list"]';
  const visible = (root, query) => [...root.querySelectorAll(query)].filter(shown);
  const main = scope.closest('main');
  if (![scope, author, more, permalink].every(shown) || !main
      || scope.closest('[role="dialog"],[role="alertdialog"],article')
      || visible(document, 'main').length !== 1) return false;
  const children = [...scope.children].filter(shown);
  // Observed author/options area, separator, caption area, then actions/time.
  if (children.length !== 5 || children[0].tagName !== 'DIV' || children[1].tagName !== 'HR'
      || children.slice(2).some(child => child.tagName !== 'DIV')) return false;
  const bar = children[0], actions = children[3];
  if (!bar.contains(author) || !bar.contains(more) || visible(bar, controls).length
      || !actions.contains(permalink) || visible(actions, controls).length
      || visible(permalink, 'time').length !== 1) return false;
  const profiles = visible(bar, 'a[href]').filter(link => {
    try { return /^\/[A-Za-z0-9_.]{1,30}\/?$/.test(new URL(link.getAttribute('href'), 'https://www.instagram.com/').pathname); }
    catch { return false; }
  });
  if (profiles.length !== 1 || profiles[0] !== author || author.innerText.trim() !== identity) return false;
  const icons = visible(scope, 'svg[aria-label="More options"]');
  if (icons.length !== 1 || icons[0].closest('[role="button"]') !== more) return false;
  if (more.getAttribute('role') !== 'button' || more.contains(author)) return false;
  for (const label of ['Like', 'Comment', 'Share', 'Save']) {
    if (visible(actions, 'svg[aria-label="' + label + '"]').length !== 1) return false;
  }
  const stamps = visible(actions, 'a[href]').filter(link => visible(link, 'time').length);
  return stamps.length === 1 && stamps[0] === permalink;
}"""


@dataclass
class PostSelection:
    queries: tuple
    headerless: bool = False


async def guard(page):
    stop_check('instagram')
    await runtime.risk_check(page)
    stop_check('instagram')


async def current_identity(page):
    current = await runtime.identity(page, 'instagram')
    # A readable different account must never be overridden by the fallback.
    if current is not None:
        return current
    candidates = page.locator(NAV_AVATARS)
    if not await candidates.count():
        return None
    names = await candidates.evaluate_all(NAV_IDENTITY)
    return names[0] if len(names) == 1 else None


def exact_address(page, actual, target):
    address = urlsplit(page.url)
    if (address.scheme != 'https' or address.netloc != 'www.instagram.com'
            or address.query or address.fragment or not instagram_address_is(page.url, actual, target['id'])):
        raise VpubError('wrong_work', 'Instagram did not retain this exact post address.')


async def exact_account(page, actual, target):
    exact_address(page, actual, target)
    if actual != target['identity'] or await current_identity(page) != actual:
        raise VpubError('wrong_account', 'The current Instagram account changed or cannot be read.')
    exact_address(page, actual, target)


def profile_address(page, actual):
    address = urlsplit(page.url)
    return (address.scheme == 'https' and address.netloc == 'www.instagram.com'
            and address.path in {f'/{actual}', f'/{actual}/'} and not address.query and not address.fragment)


async def profile_current(page, actual, target):
    if actual != target['identity'] or not profile_address(page, actual):
        return False
    await guard(page)
    if not profile_address(page, actual):
        return False
    current = await current_identity(page)
    return current == actual and profile_address(page, actual)


def permalink_selector(actual, target):
    paths = [f'/{prefix}{kind}/{target["id"]}{ending}' for prefix in ('', actual + '/')
             for kind in ('p', 'reel', 'reels') for ending in ('', '/')]
    return ','.join(f'a[href="{prefix}{path}"]' for path in paths for prefix in ('', 'https://www.instagram.com'))


def exact_permalink(href, actual, target):
    address = urljoin('https://www.instagram.com/', href or '')
    parts = urlsplit(address)
    return (parts.scheme == 'https' and parts.netloc == 'www.instagram.com' and not parts.query and not parts.fragment
            and instagram_address_is(address, actual, target['id']))


async def scope_permalink(scope, actual, target):
    # A caption link to the target cannot override another post's own permalink.
    # Unknown/mixed post containers remain ambiguous rather than borrowing an ID.
    hrefs = await scope.evaluate(SCOPE_LINKS)
    posts = [href for href in hrefs if POST_PATH.fullmatch(urlsplit(urljoin('https://www.instagram.com/', href or '')).path)]
    if not posts or any(not exact_permalink(href, actual, target) for href in posts):
        raise VpubError('wrong_work', 'The selected post container does not name only the exact requested work.')


async def same_node(locator, retained, label):
    current = await runtime.unique_visible(locator, label)
    if retained is None or not await current.evaluate(SAME, retained):
        raise VpubError('wrong_work', 'A verified post control was replaced; nothing else is pressed.')


async def overlays_bound(page, dialog=None):
    for role in ('menu', 'dialog', 'alertdialog'):
        for node in await page.get_by_role(role).all():
            if await node.is_visible() and (role == 'menu' or dialog is None or not await node.evaluate(SAME, dialog)):
                raise VpubError('layout_changed', 'An unbound menu or dialog is already open.')


async def target_bound(page, actual, target, queries, retained, *, headerless=False):
    await exact_account(page, actual, target)
    scope, author, more, permalink = retained
    for query, handle, label in zip(queries, retained, ('exact post container', 'own post author', 'exact post options', 'exact post permalink')):
        await same_node(query, handle, label)
    if (await author.get_attribute('href') != f'/{actual}/'
            or not await scope.evaluate(CONTAINS, author) or not await scope.evaluate(CONTAINS, more)):
        raise VpubError('owner_unverified', 'The retained author and options no longer belong to this post.')
    if (not await scope.evaluate(CONTAINS, permalink)
            or not exact_permalink(await permalink.get_attribute('href'), actual, target)):
        raise VpubError('wrong_work', 'The retained permalink no longer identifies this exact post.')
    if headerless and not await scope.evaluate(POST_STRUCTURE, {'author': author, 'more': more,
                                                              'permalink': permalink, 'identity': actual}):
        raise VpubError('owner_unverified', 'The observed author, options and timestamp no longer share one post layout.')
    await scope_permalink(scope, actual, target)
    await exact_account(page, actual, target)


async def visible_labels(locator):
    return [(await node.inner_text()).strip() for node in await locator.all() if await node.is_visible()]


async def exact(page, actual, target):
    await page.goto(f"https://www.instagram.com/p/{target['id']}/", wait_until='domcontentloaded', timeout=60000)
    await page.wait_for_timeout(2500)
    await guard(page)
    await exact_account(page, actual, target)
    # Only an explicit platform heading qualifies; generic unavailable/login/recovery text does not.
    gone = any(GONE.fullmatch(text) for text in await visible_labels(page.locator('main h1, main h2, main [role="alert"]')))
    await guard(page)
    await exact_account(page, actual, target)
    if gone:
        return None, True
    owned = page.locator(f'main header a[href="/{actual}/"], article header a[href="/{actual}/"]')
    if not any([await link.is_visible() for link in await owned.all()]):
        # A headerless layout must supply its own structural proof. Never widen
        # the author query to all profile links in main or the whole page.
        if await runtime.visible(page.locator('article')) or await runtime.visible(page.locator('main header')):
            return None, False
        panels = page.locator(POST_PANELS)
        if not await runtime.visible(panels):
            return None, False
        panel = await runtime.unique_visible(panels, 'exact headerless post panel')
        queries = (panels, panel.locator(AUTHOR_BAR).locator(f'a[href="/{actual}/"]'),
                   panel.locator(AUTHOR_BAR).locator(LAYOUT_OPTIONS), panel.locator(TIMESTAMP))
        # Retain and validate the complete structure before opening any options.
        return PostSelection(queries, headerless=True), False
    # Bind the author's header and options to one article (or the sole main post
    # on layouts without article). A global owner link cannot authorize another menu.
    author = page.locator(f'header a[href="/{actual}/"]')
    options = page.get_by_role('button', name=OPTIONS)
    articles = page.locator('article')
    permalinks = permalink_selector(actual, target)
    scopes = ((articles if await runtime.visible(articles) else page.locator('main'))
              .filter(has=author).filter(has=options).filter(has=page.locator(permalinks)))
    scope = await runtime.unique_visible(scopes, 'exact post container')
    await scope_permalink(scope, actual, target)
    await exact_account(page, actual, target)
    return PostSelection((scopes, scope.locator(f'header a[href="/{actual}/"]'), scope.get_by_role('button', name=OPTIONS),
                          scope.locator(permalinks))), False


async def listing(page, actual, target):
    await page.goto(f'https://www.instagram.com/{actual}/', wait_until='domcontentloaded', timeout=60000)
    await page.wait_for_timeout(2500)
    if not await profile_current(page, actual, target):
        return False, False, False
    seen = set()
    for _ in range(20):
        if not await profile_current(page, actual, target):
            return False, False, False
        hrefs = await page.locator('main a[href]').evaluate_all('es => es.map(e => e.href)')
        if not await profile_current(page, actual, target):
            return False, False, False
        for href in hrefs:
            found = re.fullmatch(r'/(?:p|reel|reels)/([A-Za-z0-9_-]+)/?', urlsplit(href).path)
            if found:
                seen.add(found[1])
        counts = [COUNT.fullmatch(text) for text in await visible_labels(page.locator('main header li'))]
        if not await profile_current(page, actual, target):
            return False, False, False
        totals = [int(match[1].replace(',', '')) for match in counts if match]
        explicit_empty = any(EMPTY.fullmatch(text) for text in await visible_labels(page.locator('main h1, main h2')))
        if not await profile_current(page, actual, target):
            return False, False, False
        # A profile count alone cannot bind every link to the own grid: recommendations
        # may contribute links. Only explicit empty + exact zero certifies completion.
        if not seen and explicit_empty and totals == [0]:
            return target['id'] not in seen, True, True
        if target['id'] in seen:
            return False, False, True
        await page.mouse.wheel(0, 1000)
        await page.wait_for_timeout(700)
    if not await profile_current(page, actual, target):
        return False, False, False
    return target['id'] not in seen, False, True


async def evidence(page, actual, target, gone, writes):
    absent, complete, fresh = await listing(page, actual, target)
    return {'id': target['id'], 'identity': actual, 'present': False, 'owned': False, 'page_gone': gone, 'list_absent': absent,
            'list_complete': complete, 'fresh': fresh, 'platform_writes': writes,
            'note': 'Explicit exact-post deletion heading and empty own profile with zero post count can confirm absence. Nonempty grids and generic unavailable pages stay pending.'}


async def execute(page, actual, target, operation, receipt_path):
    if operation not in {'inspect', 'delete', 'verify'}:
        raise VpubError('unsupported_operation', 'Use inspect, delete or verify.')
    if actual != target['identity'] or not re.fullmatch(r'[A-Za-z0-9_.]{1,30}', actual):
        raise VpubError('wrong_account', 'Use the publishing Instagram account from the receipt.')
    if target.get('platform') != 'instagram' or not re.fullmatch(r'[A-Za-z0-9_-]{5,32}', target['id']):
        raise VpubError('wrong_work', 'Use the exact Instagram post from the publication receipt.')
    selection, gone = await exact(page, actual, target)
    if selection is None:
        return await evidence(page, actual, target, gone, 0)
    queries = selection.queries
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

    async def wait_controls(dialog_query, control_queries, label):
        async def candidate(query):
            handles = await query.element_handles()
            held.extend(handles)
            shown = [handle for handle in handles if await handle.is_visible()]
            if len(shown) > 1:
                raise VpubError('layout_changed', f'Ambiguous {label}; nothing else is pressed.')
            return shown[0] if shown else None

        for elapsed in range(0, CONTROL_WAIT_MILLISECONDS + 1, CONTROL_POLL_MILLISECONDS):
            # Poll without another click. A loading shell (or the outgoing menu)
            # may lack the required labels, but multiple overlays fail at once.
            observed_dialog = await candidate(page.get_by_role('dialog'))
            if await runtime.visible(page.get_by_role('menu')) or await runtime.visible(page.get_by_role('alertdialog')):
                raise VpubError('layout_changed', 'An unbound overlay appeared while the post controls loaded.')
            dialog = await candidate(dialog_query)
            controls = [await candidate(query) for query in control_queries]
            if observed_dialog is not None:
                # The first visible dialog cannot be silently exchanged during
                # the qualifying-label/control reads, even before the guard.
                await same_node(page.get_by_role('dialog'), observed_dialog, 'first observed post dialog')
                if dialog is not None and not await dialog.evaluate(SAME, observed_dialog):
                    raise VpubError('wrong_work', 'The post dialog changed while its controls were being read.')
            if dialog is not None and all(control is not None for control in controls):
                # Capture the first ready nodes before the next guard can yield
                # to a replacement. The caller revalidates these exact handles.
                return (observed_dialog if observed_dialog is not None else dialog, *controls)
            await guard(page)
            await target_bound(page, actual, target, queries, retained, headerless=selection.headerless)
            if elapsed == CONTROL_WAIT_MILLISECONDS:
                raise VpubError('layout_changed', f'The {label} did not become ready; no click was retried.')
            await page.wait_for_timeout(CONTROL_POLL_MILLISECONDS)

    try:
        retained = [await retain(query, label) for query, label in zip(
            queries, ('exact post container', 'own post author', 'exact post options', 'exact post permalink'))]
        await guard(page)
        await target_bound(page, actual, target, queries, retained, headerless=selection.headerless)
        await overlays_bound(page)
        stop_check('instagram')
        await retained[2].click(timeout=10000)
        menus = page.get_by_role('dialog').filter(has=page.get_by_role('button', name=EDIT))
        edit_query = menus.get_by_role('button', name=EDIT)
        delete_query = menus.get_by_role('button', name=DELETE)
        menu, edit, delete = await wait_controls(menus, (edit_query, delete_query), 'own post options')

        async def menu_bound():
            await target_bound(page, actual, target, queries, retained, headerless=selection.headerless)
            await same_node(menus, menu, 'own post options')
            await same_node(edit_query, edit, 'own post Edit')
            await same_node(delete_query, delete, 'own post Delete')
            await overlays_bound(page, menu)
            if not await menu.evaluate(CONTAINS, edit) or not await menu.evaluate(CONTAINS, delete):
                raise VpubError('wrong_work', 'Edit or Delete left the bound post menu.')

        await guard(page)
        await menu_bound()
        if operation != 'delete':
            await guard(page)
            await menu_bound()
            await page.keyboard.press('Escape')
            return {'id': target['id'], 'identity': actual, 'present': True, 'owned': True,
                    'page_gone': False, 'list_absent': False, 'list_complete': False, 'fresh': True, 'platform_writes': 0,
                    'note': 'The exact owned post remains present.'}
        begin_press(receipt_path, target)
        await guard(page)
        await menu_bound()
        stop_check('instagram')
        await delete.click(timeout=10000)
        confirmations = page.get_by_role('dialog').filter(has_text=CONFIRM)
        final_query = confirmations.get_by_role('button', name=DELETE)
        dialog, final = await wait_controls(confirmations, (final_query,), 'post deletion confirmation')
        await guard(page)
        await target_bound(page, actual, target, queries, retained, headerless=selection.headerless)
        await same_node(confirmations, dialog, 'post deletion confirmation')
        await same_node(final_query, final, 'final post Delete')
        await overlays_bound(page, dialog)
        if (not await dialog.evaluate(CONTAINS, final)
                or not CONFIRM.search(await dialog.inner_text())
                or not DELETE.fullmatch((await final.inner_text()).strip())):
            raise VpubError('layout_changed', 'Instagram did not retain the bound delete confirmation.')
        stop_check('instagram')
        await final.click(timeout=15000)
        remaining, gone = await exact(page, actual, target)
        result = await evidence(page, actual, target, gone, 1)
        return {**result, 'present': remaining is not None, 'owned': remaining is not None}
    finally:
        for handle in held:
            await handle.dispose()

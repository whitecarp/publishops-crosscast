"""One exact Instagram post, with conservative page and complete-profile read-back."""
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


async def guard(page):
    stop_check('instagram')
    await runtime.risk_check(page)
    stop_check('instagram')


def exact_address(page, actual, target):
    address = urlsplit(page.url)
    if (address.scheme != 'https' or address.netloc != 'www.instagram.com'
            or address.query or address.fragment or not instagram_address_is(page.url, actual, target['id'])):
        raise VpubError('wrong_work', 'Instagram did not retain this exact post address.')


async def exact_account(page, actual, target):
    exact_address(page, actual, target)
    if actual != target['identity'] or await runtime.identity(page, 'instagram') != actual:
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
    current = await runtime.identity(page, 'instagram')
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


async def target_bound(page, actual, target, queries, retained):
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
        return None, False
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
    return (scopes, scope.locator(f'header a[href="/{actual}/"]'), scope.get_by_role('button', name=OPTIONS),
            scope.locator(permalinks)), False


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
    queries, gone = await exact(page, actual, target)
    if queries is None:
        return await evidence(page, actual, target, gone, 0)
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
        retained = [await retain(query, label) for query, label in zip(
            queries, ('exact post container', 'own post author', 'exact post options', 'exact post permalink'))]
        await guard(page)
        await target_bound(page, actual, target, queries, retained)
        await overlays_bound(page)
        stop_check('instagram')
        await retained[2].click(timeout=10000)
        menus = page.get_by_role('dialog').filter(has=page.get_by_role('button', name=EDIT))
        menu = await retain(menus, 'own post options')
        delete_query = menus.get_by_role('button', name=DELETE)
        delete = await retain(delete_query, 'own post Delete')

        async def menu_bound():
            await target_bound(page, actual, target, queries, retained)
            await same_node(menus, menu, 'own post options')
            await same_node(delete_query, delete, 'own post Delete')
            await overlays_bound(page, menu)
            if not await menu.evaluate(CONTAINS, delete):
                raise VpubError('wrong_work', 'Delete left the bound post menu.')

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
        dialog = await retain(confirmations, 'post deletion confirmation')
        final_query = confirmations.get_by_role('button', name=DELETE)
        final = await retain(final_query, 'final post Delete')
        await guard(page)
        await target_bound(page, actual, target, queries, retained)
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

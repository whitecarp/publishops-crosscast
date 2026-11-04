"""Offline X deletion controls, ownership, durable first press and absence proof."""
import ctypes
from copy import copy
import inspect
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from video_publisher import browser_runtime as runtime, work_delete_state as state, work_delete_x as route, x_pages
from video_publisher.core import VpubError, atomic_json, home, read_json


OWNER = "test_owner"
WORK = "2111111111111111111"


def below_normal():
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32")
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000):
            raise RuntimeError("Could not lower test priority")


class Node:
    def __init__(self, text="", *, role=None, name=None, selectors=(), children=(), visible=True,
                 event=None, action=None, href=None, article=None, linked_ids=(), user_words=False):
        self.text, self.role, self.name = text, role, name if name is not None else text
        self.selectors, self.children, self.visible = set(selectors), list(children), visible
        self.event, self.action, self.href = event, action, href
        self.article, self.linked_ids, self.user_words = article, set(linked_ids), user_words

    def descendants(self):
        for child in self.children:
            yield child
            yield from child.descendants()

    def words(self):
        return "\n".join(filter(None, [self.text, *(child.words() for child in self.children)]))


class Locator:
    """Re-evaluate every query, including all()/nth(), like Playwright locators."""
    def __init__(self, page, nodes):
        self.page = page
        self.resolve = nodes if callable(nodes) else lambda: list(nodes)

    @property
    def nodes(self):
        return list(self.resolve())

    def _below(self):
        seen = set()
        for node in self.nodes:
            for child in node.descendants():
                if id(child) not in seen:
                    seen.add(id(child))
                    yield child

    def locator(self, selector):
        self.page.queries.append((tuple(node.event for node in self.nodes), selector))
        def matches(node):
            if selector.startswith('article:has(a[href="/compose/post?in_reply_to='):
                number = re.search(r'in_reply_to=(\d+)', selector)[1]
                return node.article is not None and number in node.linked_ids
            if selector == 'a[href]':
                return node.href is not None
            if selector.startswith('a[href="'):
                return node.href in re.findall(r'a\[href="([^"]+)"\]', selector)
            return selector in node.selectors
        return Locator(self.page, lambda: [node for node in self._below() if matches(node)])

    def get_by_role(self, role, name=None, **kwargs):
        def named(node):
            return name is None or (bool(name.search(node.name)) if hasattr(name, 'search') else node.name == name)
        return Locator(self.page, lambda: [node for node in self._below() if node.role == role and named(node)])

    def filter(self, has=None, has_text=None):
        def selected():
            result = []
            for node in self.nodes:
                if has is not None and not any(child in has.nodes for child in node.descendants()):
                    continue
                if has_text is not None and not (has_text.search(node.words()) if hasattr(has_text, 'search') else has_text in node.words()):
                    continue
                result.append(node)
            return result
        return Locator(self.page, selected)

    @property
    def first(self):
        return self.nth(0)

    def nth(self, index):
        return Locator(self.page, lambda: self.nodes[index:index + 1])

    def or_(self, other):
        return Locator(self.page, lambda: list(dict.fromkeys(self.nodes + other.nodes)))

    async def all(self):
        return [self.nth(index) for index in range(len(self.nodes))]

    async def element_handles(self):
        handles = [Handle(self.page, node) for node in self.nodes]
        self.page.handles.extend(handles)
        return handles

    async def element_handle(self):
        handles = await self.element_handles()
        if len(handles) != 1:
            raise AssertionError('Ambiguous synthetic handle')
        return handles[0]

    async def count(self):
        return len(self.nodes)

    async def is_visible(self):
        if len(self.nodes) != 1:
            return False
        def shown(root, ancestors=True):
            visible = ancestors and root.visible
            if root is self.nodes[0]:
                return visible
            return any(shown(child, visible) for child in root.children)
        return shown(self.page.tree)

    async def wait_for(self, **kwargs):
        if not await self.is_visible():
            raise TimeoutError("Synthetic control is not visible")

    async def all_text_contents(self):
        # Playwright includes hidden nodes here; this deliberately does too.
        return [node.words() for node in self.nodes]

    async def inner_text(self):
        if len(self.nodes) != 1:
            raise AssertionError("Unscoped text read")
        value = self.nodes[0].words()
        self.page.read_complete('text', self.nodes)
        return value

    async def get_attribute(self, name):
        if len(self.nodes) != 1:
            raise AssertionError('Unscoped attribute read')
        value = {'href': self.nodes[0].href, 'role': self.nodes[0].role}.get(name)
        self.page.read_complete('attribute', self.nodes)
        return value

    async def evaluate(self, script, *args):
        if len(self.nodes) != 1:
            raise AssertionError("Unscoped element evaluation")
        node = self.nodes[0]
        if script == x_pages.POST_FIELDS:
            value = dict(node.article)
            self.page.read_complete('post', self.nodes)
            return value
        if script == "node => [...node.querySelectorAll('a[href]')].map(e => e.getAttribute('href'))":
            return [child.href for child in node.descendants() if child.href is not None]
        if script == runtime.SOMEONES_WORDS:
            return node.user_words
        if script == route.SAME:
            return node is args[0].nodes[0] and node in list(self.page.tree.descendants())
        if script == route.CONTAINS:
            child = args[0].nodes[0]
            return (node in list(self.page.tree.descendants()) and child in list(node.descendants()))
        raise AssertionError("Unexpected DOM evaluation")

    async def evaluate_all(self, script):
        if script != 'es => es.map(e => e.href)':
            raise AssertionError("Unexpected DOM list evaluation")
        value = [node.href for node in self.nodes]
        self.page.read_complete('links', self.nodes)
        return value

    async def click(self, **kwargs):
        if not await self.is_visible():
            raise TimeoutError("Synthetic control is detached, hidden or ambiguous")
        node = self.nodes[0]
        self.page.events.append(node.event or node.name)
        if node.action:
            result = node.action()
            if inspect.isawaitable(result):
                await result

    async def press(self, key):
        if key != "Enter":
            raise AssertionError(key)
        await self.click()


class Handle(Locator):
    """Element handles retain one node; a replacement cannot satisfy a click."""
    def __init__(self, page, node):
        super().__init__(page, [node])
        self.disposed = False

    async def click(self, **kwargs):
        if self.page.before_handle_click:
            result = self.page.before_handle_click(self)
            if inspect.isawaitable(result):
                await result
        await super().click(**kwargs)

    async def dispose(self):
        self.disposed = True


class FakePage:
    def __init__(self):
        self.url, self.events, self.queries, self.visits = "about:blank", [], [], []
        self.tree = Node()
        self.keyboard = self
        self.mouse = self
        self.scrolls = 0
        self.signed_identity = OWNER
        self.handles, self.before_handle_click = [], None
        self.after_read = None

    def read_complete(self, kind, nodes):
        if self.after_read:
            self.after_read(kind, nodes)

    def navigation(self):
        return Node(role='link', name='Profile', href=f'/{self.signed_identity}/',
                    selectors={'a[data-testid="AppTabBar_Profile_Link"]'})

    def locator(self, selector):
        return Locator(self, lambda: [self.tree]).locator(selector)

    def get_by_role(self, role, **kwargs):
        return Locator(self, lambda: [self.tree]).get_by_role(role, **kwargs)

    def get_by_text(self, text):
        return Locator(self, lambda: [node for node in self.tree.descendants() if node.text and
                                     (text.search(node.text) if hasattr(text, 'search') else node.text == text)])

    async def wait_for_timeout(self, milliseconds):
        pass

    def on(self, event, callback):
        # This DOM-only fixture emits no transport events or completion proof.
        pass

    def remove_listener(self, event, callback):
        pass

    async def press(self, key):
        if key != "Escape":
            raise AssertionError(key)
        self.events.append("escape")
        self.tree.children = [node for node in self.tree.children if node.role != "dialog"]

    async def wheel(self, x, y):
        self.scrolls += 1


class OfflineCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        below_normal()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {'VPUB_HOME': str(Path(temp.name) / 'vpub'),
                                      'BILI_CLI_HOME': str(Path(temp.name) / 'bili')})
        env.start()
        self.addCleanup(env.stop)
        for name in ('subprocess.Popen', 'socket.create_connection', 'socket.socket.connect'):
            blocked = patch(name, side_effect=AssertionError("No browser/process/network in this fixture"))
            blocked.start()
            self.addCleanup(blocked.stop)

    def ready(self, platform, wid):
        self.target = {'key': 'a' * 64, 'platform': platform, 'id': wid, 'identity': OWNER,
                       'account': 'default', 'title': 'Synthetic post', 'generation': 'synthetic', 'attempt': 'one',
                       'url': f'https://x.com/i/status/{wid}' if platform == 'x' else f'https://www.instagram.com/p/{wid}/'}
        self.path = state.receipt_path(self.target['key'])
        atomic_json(self.path, {'state': 'DELETE_READY', 'target': self.target, 'delete_attempted': False})

    def marked(self):
        saved = read_json(self.path)
        self.assertEqual('DELETE_PENDING', saved['state'])
        self.assertTrue(saved['delete_attempted'])
        self.assertEqual(self.target, saved['target'])

    def unpressed(self, page):
        self.assertFalse(any(event in {'menu-delete', 'final-delete'} for event in page.events), page.events)
        self.assertFalse(read_json(self.path)['delete_attempted'])


class BindingRegressions:
    """The same race matrix runs against both real routes with dynamic fake locators."""
    def reset_case(self):
        platform, wid = self.target['platform'], self.target['id']
        self.ready(platform, wid)
        self.page = type(self.page)(self.marked)

    def replace_node(self, old, new):
        for parent in [self.page.tree, *self.page.tree.descendants()]:
            for index, child in enumerate(parent.children):
                if child is old:
                    parent.children[index] = new
                    return
        raise AssertionError('Fixture node was not attached')

    def at_boundary(self, stage, mutate):
        original = runtime.risk_check
        fired = []

        async def risk(page):
            await original(page)
            if page is not self.page or fired:
                return
            events = page.events
            ready = (bool(page.handles) and not events if stage == 'options' else
                     read_json(self.path)['delete_attempted'] and 'menu-delete' not in events if stage == 'delete' else
                     'menu-delete' in events and 'final-delete' not in events)
            if ready:
                fired.append(True)
                mutate(page)
        return patch.object(runtime, 'risk_check', side_effect=risk), fired

    def boundary_stopped(self, stage, fired):
        self.assertTrue(fired, 'The intended boundary must actually be reached')
        self.assertNotIn('final-delete', self.page.events)
        if stage != 'final':
            self.assertNotIn('menu-delete', self.page.events)
        if stage == 'options':
            self.assertEqual([], self.page.events)
        self.assertEqual(stage != 'options', read_json(self.path)['delete_attempted'])
        self.assertTrue(all(handle.disposed for handle in self.page.handles))

    def profile_pending(self, result):
        self.assertFalse(state.verified(result, self.target))
        self.assertFalse(result['fresh'] or result['list_complete'] or result['list_absent'])
        self.assertEqual('DELETE_PENDING', state.save_result(self.path, self.target, result)['state'])
        self.unpressed(self.page)

    async def test_profile_requires_exact_https_origin_path_and_no_query_or_fragment(self):
        host = 'x.com' if self.target['platform'] == 'x' else 'www.instagram.com'
        addresses = [f'http://{host}/{OWNER}/', f'https://{host}.other.invalid/{OWNER}/',
                     f'https://other.invalid/{OWNER}/', f'https://{host}:443/{OWNER}/',
                     f'https://user@{host}/{OWNER}/', f'https://{host}/{OWNER}/?tab=other',
                     f'https://{host}/{OWNER}/#other', f'https://{host}/someone_else/']
        for address in addresses:
            with self.subTest(address=address):
                self.reset_case()
                self.page.present = False
                self.page.profile_redirect = address
                self.profile_pending(await self.execute('verify'))

    async def test_profile_requires_same_current_identity_not_only_own_url(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                self.reset_case()
                self.page.present = False
                original = self.page.goto
                async def different_identity(url, **kwargs):
                    await original(url, **kwargs)
                    if urlsplit(url).path.rstrip('/') == '/' + OWNER:
                        navigation = next(n for n in self.page.tree.descendants() if n.name == 'Profile')
                        if missing:
                            self.page.tree.children.remove(navigation)
                        else:
                            navigation.href = '/someone_else/'
                with patch.object(self.page, 'goto', side_effect=different_identity):
                    result = await self.execute('verify')
                self.profile_pending(result)

    async def test_profile_drift_after_rows_is_not_accepted(self):
        for change in ('origin', 'identity'):
            with self.subTest(change=change):
                self.reset_case()
                self.page.present = False
                fired = []
                def mutate():
                    fired.append(True)
                    if change == 'origin':
                        self.page.url = f'https://other.invalid/{OWNER}/'
                    else:
                        next(n for n in self.page.tree.descendants() if n.name == 'Profile').href = '/someone_else/'
                if self.target['platform'] == 'x':
                    original = x_pages.own_posts
                    async def rows(*args):
                        result = await original(*args)
                        mutate()
                        return result
                    with patch.object(x_pages, 'own_posts', side_effect=rows):
                        result = await self.execute('verify')
                else:
                    def after(kind, nodes):
                        if kind == 'links' and not fired and urlsplit(self.page.url).path.rstrip('/') == '/' + OWNER:
                            mutate()
                    self.page.after_read = after
                    result = await self.execute('verify')
                self.assertTrue(fired)
                self.profile_pending(result)

    async def test_profile_drift_during_empty_label_read_is_not_accepted(self):
        for change in ('origin', 'identity'):
            with self.subTest(change=change):
                self.reset_case()
                self.page.present = False
                fired = []
                def after(kind, nodes):
                    if (not fired and kind == 'text' and urlsplit(self.page.url).path.rstrip('/') == '/' + OWNER
                            and any(n.text in {'No posts yet', 'No Posts Yet'} for n in nodes)):
                        fired.append(True)
                        if change == 'origin':
                            self.page.url = f'https://other.invalid/{OWNER}/'
                        else:
                            next(n for n in self.page.tree.descendants() if n.name == 'Profile').href = '/someone_else/'
                self.page.after_read = after
                result = await self.execute('verify')
                self.assertTrue(fired)
                self.profile_pending(result)

    async def test_profile_redirect_during_identity_lookup_is_not_accepted(self):
        self.page.present = False
        fired = []
        def after(kind, nodes):
            if (not fired and kind == 'attribute' and urlsplit(self.page.url).path.rstrip('/') == '/' + OWNER
                    and any(n.name == 'Profile' for n in nodes)):
                fired.append(True)
                self.page.url = f'https://other.invalid/{OWNER}/'
        self.page.after_read = after
        result = await self.execute('verify')
        self.assertTrue(fired)
        self.profile_pending(result)

    async def test_exact_gone_read_rechecks_identity_after_label(self):
        self.page.present = False
        fired = []
        def after(kind, nodes):
            if not fired and kind == 'text' and any('has been deleted' in n.text or 'was deleted' in n.text for n in nodes):
                fired.append(True)
                next(n for n in self.page.tree.descendants() if n.name == 'Profile').href = '/someone_else/'
        self.page.after_read = after
        with self.assertRaises(VpubError) as caught:
            await self.execute('verify')
        self.assertTrue(fired)
        self.assertEqual('wrong_account', caught.exception.code)
        self.unpressed(self.page)

    async def test_replaced_target_container_blocks_every_control_boundary(self):
        for stage in ('options', 'delete', 'final'):
            with self.subTest(stage=stage):
                self.reset_case()
                def replace(page):
                    node = next(n for n in page.tree.descendants() if n.article or 'article' in n.selectors)
                    self.replace_node(node, copy(node))
                armed, fired = self.at_boundary(stage, replace)
                with armed, self.assertRaises((VpubError, TimeoutError)):
                    await self.execute()
                self.boundary_stopped(stage, fired)

    async def test_changed_exact_path_blocks_every_control_boundary(self):
        for stage in ('options', 'delete', 'final'):
            with self.subTest(stage=stage):
                self.reset_case()
                def change(page):
                    page.url = page.url.replace(self.target['id'], '2111111111111111112' if self.target['platform'] == 'x' else 'OtherOwnedPost')
                armed, fired = self.at_boundary(stage, change)
                with armed, self.assertRaises(VpubError) as caught:
                    await self.execute()
                self.assertEqual('wrong_work', caught.exception.code)
                self.boundary_stopped(stage, fired)

    async def test_changed_origin_with_same_path_blocks_every_boundary(self):
        for stage in ('options', 'delete', 'final'):
            with self.subTest(stage=stage):
                self.reset_case()
                armed, fired = self.at_boundary(stage, lambda page: setattr(
                    page, 'url', 'https://other.invalid' + urlsplit(page.url).path))
                with armed, self.assertRaises(VpubError) as caught:
                    await self.execute()
                self.assertEqual('wrong_work', caught.exception.code)
                self.boundary_stopped(stage, fired)

    async def test_changed_signed_identity_blocks_every_control_boundary(self):
        for stage in ('options', 'delete', 'final'):
            with self.subTest(stage=stage):
                self.reset_case()
                def change(page):
                    next(n for n in page.tree.descendants() if n.name == 'Profile').href = '/other_identity/'
                armed, fired = self.at_boundary(stage, change)
                with armed, self.assertRaises(VpubError) as caught:
                    await self.execute()
                self.assertEqual('wrong_account', caught.exception.code)
                self.boundary_stopped(stage, fired)

    async def test_changed_own_author_on_same_node_blocks_every_boundary(self):
        for stage in ('options', 'delete', 'final'):
            with self.subTest(stage=stage):
                self.reset_case()
                def change(page):
                    if self.target['platform'] == 'x':
                        node = next(n for n in page.tree.descendants() if n.article)
                        node.article['href'] = f"/other_owner/status/{self.target['id']}"
                    else:
                        node = next(n for n in page.tree.descendants() if n.href == f'/{OWNER}/' and n.name != 'Profile')
                        node.href = '/other_owner/'
                armed, fired = self.at_boundary(stage, change)
                with armed, self.assertRaises(VpubError) as caught:
                    await self.execute()
                self.assertEqual('owner_unverified', caught.exception.code)
                self.boundary_stopped(stage, fired)

    async def test_replaced_menu_dialog_or_delete_node_blocks_press(self):
        for stage in ('delete', 'final'):
            for replace_container in (False, True):
                with self.subTest(stage=stage, container=replace_container):
                    self.reset_case()
                    def replace(page):
                        event = 'menu-delete' if stage == 'delete' else 'final-delete'
                        control = next(n for n in page.tree.descendants() if n.event == event)
                        node = (next(n for n in page.tree.descendants() if control in list(n.descendants())
                                     and n.role in {'menu', 'dialog', 'alertdialog'}) if replace_container else control)
                        self.replace_node(node, copy(node))
                    armed, fired = self.at_boundary(stage, replace)
                    with armed, self.assertRaises((VpubError, TimeoutError)):
                        await self.execute()
                    self.boundary_stopped(stage, fired)

    async def test_existing_unbound_menu_or_dialog_is_never_used(self):
        for role in ('menu', 'dialog', 'alertdialog'):
            with self.subTest(role=role):
                self.reset_case()
                armed, fired = self.at_boundary('options', lambda page: page.tree.children.append(Node(role=role)))
                with armed, self.assertRaises(VpubError) as caught:
                    await self.execute()
                self.assertEqual('layout_changed', caught.exception.code)
                self.boundary_stopped('options', fired)

    async def test_late_unbound_overlay_blocks_destructive_controls(self):
        for stage in ('delete', 'final'):
            with self.subTest(stage=stage):
                self.reset_case()
                armed, fired = self.at_boundary(stage, lambda page: page.tree.children.append(Node(role='dialog')))
                with armed, self.assertRaises(VpubError):
                    await self.execute()
                self.boundary_stopped(stage, fired)

    async def test_direct_handles_do_not_retarget_after_all_checks(self):
        for event in ('target-more' if self.target['platform'] == 'x' else 'target-options', 'menu-delete', 'final-delete'):
            with self.subTest(event=event):
                self.reset_case()
                fired = []
                def replace(handle):
                    if handle.nodes[0].event == event and not fired:
                        fired.append(True)
                        old = handle.nodes[0]
                        new = copy(old)
                        new.event = 'WRONG-target'
                        self.replace_node(old, new)
                self.page.before_handle_click = replace
                with self.assertRaises(TimeoutError):
                    await self.execute()
                self.assertTrue(fired, 'All controls must use direct element handles')
                self.assertNotIn('WRONG-target', self.page.events)
                self.assertNotIn(event, self.page.events)
                self.assertTrue(all(handle.disposed for handle in self.page.handles))

    async def test_stop_created_after_marker_blocks_first_delete(self):
        module = inspect.getmodule(self.execute.__func__)
        target_route = module.route
        original = target_route.begin_press
        def mark(path, target):
            result = original(path, target)
            atomic_json(home() / 'STOP.json', {'reason': 'Synthetic post-marker STOP'})
            return result
        with patch.object(target_route, 'begin_press', side_effect=mark), self.assertRaises(VpubError) as caught:
            await self.execute()
        self.assertEqual('stopped', caught.exception.code)
        self.assertNotIn('menu-delete', self.page.events)
        self.assertNotIn('final-delete', self.page.events)
        self.marked()

    async def test_post_marker_risk_check_blocks_first_delete(self):
        original = runtime.risk_check
        observed = []
        async def risk(page):
            if read_json(self.path)['delete_attempted']:
                observed.append(True)
                raise VpubError('human_verification_required', 'Synthetic risk at first Delete')
            await original(page)
        with patch.object(runtime, 'risk_check', side_effect=risk), self.assertRaises(VpubError) as caught:
            await self.execute()
        self.assertEqual('human_verification_required', caught.exception.code)
        self.assertTrue(observed)
        self.assertNotIn('menu-delete', self.page.events)
        self.marked()

    async def test_stop_created_during_risk_check_is_checked_again(self):
        armed, fired = self.at_boundary('delete', lambda page: atomic_json(home() / 'STOP.json', {'reason': 'During risk check'}))
        with armed, self.assertRaises(VpubError) as caught:
            await self.execute()
        self.assertEqual('stopped', caught.exception.code)
        self.boundary_stopped('delete', fired)


class DynamicLocatorTests(OfflineCase):
    async def test_queries_and_all_retarget_but_handles_keep_original_nodes(self):
        page = FakePage()
        old = Node('Delete', role='button', event='old')
        page.tree.children = [Node(role='dialog', children=[old])]
        query = page.get_by_role('dialog').filter(has=page.get_by_role('button', name='Delete')).get_by_role('button', name='Delete')
        from_all = (await query.all())[0]
        retained = await from_all.element_handle()
        page.tree.children = [Node(role='dialog', children=[Node('Delete', role='button', event='new')])]
        await query.click()
        await from_all.click()
        self.assertEqual(['new', 'new'], page.events)
        with self.assertRaises(TimeoutError):
            await retained.click()
        await retained.dispose()

    async def test_descendants_counts_and_filters_refresh_after_replacement(self):
        page = FakePage()
        query = page.get_by_role('dialog').filter(has_text=re.compile('Delete post'))
        page.tree.children = [Node('Delete post?', role='dialog')]
        self.assertEqual(1, await query.count())
        page.tree.children = [Node('Delete account?', role='dialog')]
        self.assertEqual(0, await query.count())


def post(author=OWNER, wid=WORK, *, link=None, text='Synthetic post', ad=False, children=()):
    return Node(selectors={'main article'}, children=children, linked_ids={link or wid},
                article={'href': f'/{author}/status/{wid}', 'text': text, 'time': '2026-11-03T00:00:00Z',
                         'video': True, 'lines': ['Ad'] if ad else [text]})


class XPage(FakePage):
    def __init__(self, marked):
        super().__init__()
        self.marked = marked
        self.present, self.deleted = True, False
        self.author, self.row_id, self.ad = OWNER, WORK, False
        self.redirect = None
        self.profile_redirect = None
        self.exact_message, self.profile_message = 'This Post was deleted.', 'No posts yet'
        self.exact_message_visible, self.profile_message_visible = True, True
        self.profile_posts = []
        self.menu_count, self.final_count = 1, 1
        self.delete_available, self.final_text = True, 'Delete'
        self.stop_at_final, self.fail_final, self.stays = False, False, False
        self.hostile_comment = ''

    def menu(self):
        self.tree.children.extend(Node(role='menu', children=[Node('Delete', role='menuitem', event='menu-delete', action=self.ask)]
                                       if self.delete_available else [Node('Report', role='menuitem')]) for _ in range(self.menu_count))

    def ask(self):
        self.marked()
        self.tree.children.append(Node(role='alertdialog', children=[
            Node(self.final_text, selectors={'[data-testid="confirmationSheetConfirm"]'},
                 event='final-delete', action=self.remove) for _ in range(self.final_count)]))
        if self.stop_at_final:
            atomic_json(home() / 'STOP.json', {'reason': 'Synthetic final-boundary stop'})

    def remove(self):
        self.marked()
        if self.fail_final:
            raise TimeoutError('Synthetic final acknowledgement lost')
        self.deleted = not self.stays

    async def goto(self, url, **kwargs):
        self.visits.append(url)
        exact = '/status/' in urlsplit(url).path
        self.url = (self.redirect if exact else self.profile_redirect) or url
        nodes = []
        if exact and self.present and not self.deleted:
            more = Node(role='button', name='More', selectors={x_pages.MORE}, event='target-more', action=self.menu)
            nodes.append(post(self.author, self.row_id, link=WORK, ad=self.ad, children=[more]))
            # Same-looking control on a different post must never be selected.
            nodes.append(post('other', '2111111111111111112', children=[Node(role='button', name='More', selectors={x_pages.MORE}, event='other-more')]))
        elif exact:
            nodes.append(Node(self.exact_message, selectors={'main [data-testid="emptyState"]'}, visible=self.exact_message_visible))
        else:
            nodes.extend(self.profile_posts)
            nodes.append(Node(self.profile_message, selectors={'main [data-testid="emptyState"]'}, visible=self.profile_message_visible))
        if self.hostile_comment:
            nodes.append(Node(self.hostile_comment, user_words=True))
        self.tree = Node(children=[self.navigation()] + nodes)


class XDeletionTests(BindingRegressions, OfflineCase):
    def setUp(self):
        super().setUp()
        self.ready('x', WORK)
        self.page = XPage(self.marked)

    async def execute(self, operation='delete', actual=OWNER):
        return await route.execute(self.page, actual, self.target, operation, str(self.path))

    async def test_empty_profile_on_other_origin_is_not_verified(self):
        self.page.present = False
        self.page.profile_redirect = f'https://other.invalid/{OWNER}/'
        result = await self.execute('verify')
        self.assertFalse(state.verified(result, self.target))
        self.assertFalse(result['fresh'] or result['list_complete'])
        self.unpressed(self.page)

    async def test_recycled_row_quoting_target_rechecks_own_id_before_each_click(self):
        for stage in ('options', 'delete', 'final'):
            with self.subTest(stage=stage):
                self.reset_case()
                def change(page):
                    row = next(n for n in page.tree.descendants() if n.article)
                    # Keep the same DOM node, owner, text and target quote link.
                    row.article['href'] = f'/{OWNER}/status/2111111111111111112'
                    self.assertIn(WORK, row.linked_ids)
                armed, fired = self.at_boundary(stage, change)
                with armed, self.assertRaises(VpubError) as caught:
                    await self.execute()
                self.assertEqual('owner_unverified', caught.exception.code)
                self.boundary_stopped(stage, fired)

    async def test_wrong_scheme_or_host_with_exact_path_never_opens_menu(self):
        for address in (f'https://other.invalid/{OWNER}/status/{WORK}', f'http://x.com/{OWNER}/status/{WORK}'):
            with self.subTest(address=address):
                self.reset_case()
                self.page.redirect = address
                with self.assertRaises(VpubError) as caught:
                    await self.execute()
                self.assertEqual('wrong_work', caught.exception.code)
                self.unpressed(self.page)

    async def test_wrong_account_stops_before_navigation(self):
        with self.assertRaises(VpubError) as caught:
            await self.execute(actual='someone_else')
        self.assertEqual('wrong_account', caught.exception.code)
        self.assertEqual([], self.page.visits)
        self.unpressed(self.page)

    async def test_wrong_post_address_author_id_or_ad_is_not_owned(self):
        for field, value, code in [('redirect', f'https://x.com/{OWNER}/status/2111111111111111112', 'wrong_work'),
                                   ('author', 'other', 'owner_unverified'), ('row_id', '2111111111111111112', 'owner_unverified'),
                                   ('ad', True, 'owner_unverified')]:
            with self.subTest(field=field):
                self.page = XPage(self.marked)
                setattr(self.page, field, value)
                with self.assertRaises(VpubError) as caught:
                    await self.execute()
                self.assertEqual(code, caught.exception.code)
                self.unpressed(self.page)

    async def test_inspect_and_verify_do_not_open_delete_menu(self):
        for operation in ('inspect', 'verify'):
            result = await self.execute(operation)
            self.assertTrue(result['owned'])
            self.assertTrue(result['present'])
            self.assertEqual([], self.page.events)
            self.unpressed(self.page)

    async def test_delete_is_durable_before_both_controls_and_final_is_exactly_once(self):
        result = await self.execute()
        self.assertEqual(['target-more', 'menu-delete', 'final-delete'], self.page.events)
        self.assertEqual([f'https://x.com/{OWNER}/status/{WORK}'] * 2 + [f'https://x.com/{OWNER}'], self.page.visits)
        self.assertEqual(1, result['platform_writes'])
        self.assertTrue(state.verified(result, self.target))
        self.assertEqual('DELETED_VERIFIED', state.save_result(self.path, self.target, result)['state'])

    async def test_ambiguous_menu_or_final_and_wrong_final_label_stop(self):
        for field, value, marked in [('menu_count', 2, False), ('final_count', 2, True), ('final_text', 'Delete account', True),
                                    ('delete_available', False, False)]:
            with self.subTest(field=field):
                self.ready('x', WORK)
                self.page = XPage(self.marked)
                setattr(self.page, field, value)
                with self.assertRaises(VpubError) as caught:
                    await self.execute()
                self.assertEqual('layout_changed', caught.exception.code)
                self.assertNotIn('final-delete', self.page.events)
                self.assertEqual(marked, read_json(self.path)['delete_attempted'])

    async def test_stop_appearing_after_first_delete_blocks_final(self):
        self.page.stop_at_final = True
        with self.assertRaises(VpubError) as caught:
            await self.execute()
        self.assertEqual('stopped', caught.exception.code)
        self.assertEqual(['target-more', 'menu-delete'], self.page.events)
        self.marked()
        self.assertTrue((home() / 'STOP.json').exists())

    async def test_uncertain_press_cannot_be_repeated_and_verify_does_not_write(self):
        self.page.fail_final = True
        with self.assertRaises(TimeoutError):
            await self.execute()
        self.marked()
        self.page = XPage(self.marked)
        with self.assertRaises(VpubError) as caught:
            await self.execute()
        self.assertEqual('deletion_already_attempted', caught.exception.code)
        self.assertEqual(['target-more'], self.page.events)
        self.page.events.clear()
        self.assertFalse(state.verified(await self.execute('verify'), self.target))
        self.assertEqual([], self.page.events)

    async def test_already_gone_needs_complete_empty_profile_and_zero_presses(self):
        for message in ('This Post was deleted.', 'This Post doesn’t exist.'):
            with self.subTest(message=message):
                self.page = XPage(self.marked)
                self.page.present = False
                self.page.exact_message = message
                result = await self.execute()
                self.assertTrue(state.verified(result, self.target))
                self.assertEqual(0, result['platform_writes'])
                self.assertEqual([], self.page.events)
                self.unpressed(self.page)

    async def test_generic_unavailable_and_hostile_comment_do_not_prove_gone(self):
        self.page.present = False
        self.page.exact_message = 'Something went wrong. Try reloading.'
        self.page.hostile_comment = 'This Post was deleted.'
        result = await self.execute()
        self.assertFalse(result['page_gone'])
        self.assertFalse(state.verified(result, self.target))
        self.unpressed(self.page)

    async def test_partial_timeline_or_still_present_post_keeps_pending(self):
        for still_present in (False, True):
            with self.subTest(still_present=still_present):
                self.ready('x', WORK)
                self.page = XPage(self.marked)
                self.page.stays = still_present
                self.page.profile_message = ''
                self.page.profile_posts = [post(wid=WORK if still_present else '2111111111111111112')]
                result = await self.execute()
                self.assertFalse(state.verified(result, self.target))
                self.assertFalse(result['list_complete'])
                self.assertEqual(still_present, result['present'])
                self.assertEqual(still_present, result['owned'])
                self.assertEqual('DELETE_PENDING', state.save_result(self.path, self.target, result)['state'])
                self.assertEqual(1, self.page.events.count('final-delete'))

    async def test_hidden_empty_state_is_not_complete_deletion_proof(self):
        for field, proof in [('exact_message_visible', 'page_gone'), ('profile_message_visible', 'list_complete')]:
            with self.subTest(hidden=field):
                self.page = XPage(self.marked)
                self.page.present = False
                setattr(self.page, field, False)
                result = await self.execute()
                self.assertFalse(result[proof])
                self.assertFalse(state.verified(result, self.target))
                self.unpressed(self.page)


if __name__ == '__main__':
    unittest.main()

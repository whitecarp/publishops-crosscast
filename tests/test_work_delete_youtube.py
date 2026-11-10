"""Synthetic pages only: no browser profile, network, screenshots or platform API."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from video_publisher import browser_runtime as rt, work_delete_state as state, work_delete_youtube as route
from video_publisher.core import VpubError, atomic_json, home, read_json


CHANNEL = 'UC' + 'a' * 22
VIDEO = 'abcDEF12_-3'
OTHER = 'xyzABC45_-6'


LIST_CLEAN_RUNNER = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const os = require('node:os');
os.setPriority(process.pid, os.constants.priority.PRIORITY_BELOW_NORMAL);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
class Element {
  constructor(spec, parent = null) {
    this.tagName = spec.tag || 'div'; this.attrs = spec.attrs || {}; this.parent = parent;
    this.type = spec.type || 'text'; this.value = spec.value || '';
    this.children = (spec.children || []).map(child => new Element(child, this));
  }
  matches(selector) {
    if (/^[a-z][a-z0-9-]*$/i.test(selector)) return this.tagName === selector;
    if (/^#[\w-]+$/.test(selector)) return this.attrs.id === selector.slice(1);
    const attribute = selector.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
    if (!attribute) throw new Error('Unsupported fixture selector: ' + selector);
    return Object.hasOwn(this.attrs, attribute[1]) &&
      (attribute[2] === undefined || this.attrs[attribute[1]] === attribute[2]);
  }
  closest(selector) {
    for (let node = this; node; node = node.parent)
      if (selector.split(',').some(part => node.matches(part.trim()))) return node;
    return null;
  }
  getClientRects() { return this.closest('[hidden]') ? [] : [{}]; }
}
function descendants(root) { return root.children.flatMap(child => [child, ...descendants(child)]); }
function selected(node, selector) {
  const parts = selector.trim().split(/\s+/);
  if (!node.matches(parts.pop())) return false;
  let parent = node.parent;
  while (parts.length) {
    const part = parts.pop();
    while (parent && !parent.matches(part)) parent = parent.parent;
    if (!parent) return false;
    parent = parent.parent;
  }
  return true;
}
const results = input.fixtures.map(spec => {
  const root = new Element(spec);
  const document = {querySelectorAll: selector => [root, ...descendants(root)].filter(node =>
    selector.split(',').some(part => selected(node, part)))};
  const context = vm.createContext({document, getComputedStyle: node => ({visibility: node.attrs.visibility || 'visible'})});
  const predicate = vm.runInContext('(' + input.source + ')', context, {timeout: 1000});
  return predicate([root]);
});
process.stdout.write(JSON.stringify(results));
"""


def list_clean_script(fixtures):
    """Execute the unchanged production JS, with scoped CSS/visibility fixtures; no browser or packages."""
    node = shutil.which('node')
    if not node:
        raise unittest.SkipTest('Pure-JavaScript predicate check needs Node.js; no package install is required')
    with tempfile.TemporaryDirectory() as temp:
        env = dict(os.environ, VPUB_HOME=str(Path(temp) / 'vpub'), BILI_CLI_HOME=str(Path(temp) / 'bili'))
        result = subprocess.run([node, '-e', LIST_CLEAN_RUNNER],
                                input=json.dumps({'source': route.LIST_CLEAN, 'fixtures': fixtures}),
                                text=True, capture_output=True, timeout=10, check=True, env=env)
    return json.loads(result.stdout)


def filter_chip_fixture(hidden=False):
    return {'tag': 'body', 'children': [{'tag': 'ytcp-chip', 'attrs': {'hidden': ''} if hidden else {},
                                       'children': [{'tag': 'svg', 'attrs': {'id': 'delete-icon'}}]}]}


class ListCleanScriptTests(unittest.TestCase):
    def test_actual_javascript_rejects_known_filters_and_accepts_clean_controls(self):
        fixtures = [
            {'tag': 'body'},
            {'tag': 'body', 'children': [{'tag': 'ytcp-filter-chip'}]},
            filter_chip_fixture(),
            filter_chip_fixture(hidden=True),
            {'tag': 'body', 'children': [{'tag': 'span', 'attrs': {'id': 'delete-icon'}}]},
            {'tag': 'body', 'children': [{'attrs': {'role': 'progressbar'}}]},
            {'tag': 'body', 'children': [{'tag': 'input', 'type': 'search', 'value': 'filtered'}]},
            {'tag': 'body', 'children': [{'tag': 'input', 'type': 'search', 'value': ''}]},
            {'tag': 'body', 'children': [{'attrs': {'role': 'alert'}}]},
        ]
        self.assertEqual([True, False, False, True, True, False, False, True, False], list_clean_script(fixtures))


class Node:
    def __init__(self, kind, text='', *, attrs=None, children=None, action=None, visible=True):
        self.kind, self.text = kind, text
        self.attrs, self.children = attrs or {}, children or []
        self.action, self.visible, self.connected = action, visible, True
        self.disposed = False
        self.page = None

    async def dispose(self):
        self.disposed = True

    async def click(self, **kwargs):
        if not self.connected:
            raise TimeoutError('Detached synthetic control')
        if self.action:
            self.action()

    async def is_visible(self):
        result = self.connected and self.visible
        if self.page is not None and self.page.on_visibility:
            self.page.on_visibility(self)
        return result

    async def inner_text(self, **kwargs):
        return self.words()

    async def get_attribute(self, name):
        return self.attrs.get(name)

    async def evaluate(self, script, arg=None):
        if 'node ===' in script:
            result = self is arg and self.connected
        elif 'node.contains(child)' in script:
            result = self.connected and arg.connected and arg in list(walk(self.children))
        elif 'closest' in script:
            result = self.attrs.get('platform', True)
        else:
            raise AssertionError('Unexpected element evaluation')
        if self.page is not None and self.page.on_evaluate:
            self.page.on_evaluate(self, script)
        return result

    def words(self):
        return self.text or '\n'.join(child.words() for child in self.children)


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.children)


def says(pattern, text, exact=False):
    if hasattr(pattern, 'search'):
        return bool(pattern.search(text))
    return text == pattern if exact else pattern in text


class Locator:
    def __init__(self, page, find):
        self.page, self.find = page, find

    @property
    def first(self):
        return self.nth(0)

    def nth(self, index):
        return Locator(self.page, lambda: self.find()[index:index + 1])

    async def all(self):
        return [self.nth(i) for i in range(len(self.find()))]

    async def count(self):
        return len(self.find())

    def one(self):
        nodes = self.find()
        if len(nodes) != 1:
            raise AssertionError(f'Expected one fake node: {len(nodes)}')
        nodes[0].page = self.page
        return nodes[0]

    async def is_visible(self):
        return bool(self.find()) and await self.one().is_visible()

    async def inner_text(self, **kwargs):
        return self.one().words()

    async def all_text_contents(self):
        return [n.words() for n in self.find()]

    async def get_attribute(self, name):
        return self.one().attrs.get(name)

    async def wait_for(self, **kwargs):
        if not self.find():
            raise TimeoutError('Synthetic timeout; private page body must not escape')

    async def hover(self, **kwargs):
        pass

    async def click(self, **kwargs):
        node = self.one()
        if node.action:
            node.action()

    async def element_handle(self):
        return self.one()

    async def element_handles(self):
        nodes = list(self.find())
        for node in nodes:
            node.page = self.page
        return nodes

    async def evaluate(self, script, arg=None):
        return await self.one().evaluate(script, arg)

    async def evaluate_all(self, script):
        if script == route.yt.WORK_ROWS:
            return [{'href': n.attrs.get('href', ''), 'title': n.attrs['title']} for n in self.find()]
        if script == route.LIST_CLEAN:
            return self.page.clean
        if 'const ends' in script:  # Douyin's terminal snapshot, implemented by its fake page.
            return self.page.complete and len(self.find()) == 1
        raise AssertionError('Unexpected DOM evaluation')

    def locator(self, selector):
        if selector == 'visible=true':
            return Locator(self.page, lambda: [n for n in self.find() if n.visible])
        return Locator(self.page, lambda: self.page.select(selector, list(walk([c for n in self.find() for c in n.children]))))

    def get_by_role(self, role, *, name=None, exact=False):
        return Locator(self.page, lambda: [n for n in walk([c for p in self.find() for c in p.children])
                                         if n.attrs.get('role') == role and
                                         (name is None or says(name, n.attrs.get('name', n.words()), exact))])

    def get_by_text(self, text, exact=False):
        return Locator(self.page, lambda: [n for n in walk([c for p in self.find() for c in p.children])
                                         if says(text, n.text, exact)])

    def filter(self, *, has=None, has_text=None):
        def find():
            result = []
            for n in self.find():
                children = list(walk(n.children))
                if has is not None and not any(child in has.find() for child in children):
                    continue
                if has_text is not None and not says(has_text, n.words()):
                    continue
                result.append(n)
            return result
        return Locator(self.page, find)

    def or_(self, other):
        return Locator(self.page, lambda: self.find() + other.find())


class FakeYouTube:
    def __init__(self, target):
        self.target, self.live_owner = target, target['identity']
        self.url, self.nodes, self.events = '', [], []
        self.pages = {'upload': [[(VIDEO, 'Approved')]], 'short': [[]]}
        self.pager, self.clean, self.empty_panel = True, True, True
        self.empty_panels = [Node('empty', 'No content available')]
        self.gone_text, self.deleted = 'This video has been removed by the uploader.', False
        self.final_timeout, self.keep_target, self.navigation_timeout = False, False, False
        self.risk, self.on_delete_menu, self.on_options = False, None, None
        self.redirect_watch, self.redirect_list = None, None
        self.final_count = self.menu_count = 0
        self.deleted_ids = []
        self.on_evaluate = self.on_visibility = self.on_identity = None

    def select(self, selector, nodes):
        if selector == route.MENU:
            return [n for n in nodes if n.kind == 'menu']
        if selector == route.GONE_PANEL:
            return [n for n in nodes if n.kind == 'gone']
        if selector.startswith('a[href='):
            href = selector.split('"')[1]
            return [n for n in nodes if n.kind == 'title' and n.attrs.get('href') == href]
        mapping = {'body': 'body', 'ytcp-video-row': 'row', 'a#video-title': 'title',
                   '#navigate-after': 'next', 'ytcp-video-section-content': 'section',
                   '.no-content': 'empty', '.no-content-header': 'empty_header',
                   'ytcp-checkbox-lit#confirm-checkbox': 'tick'}
        if selector not in mapping:
            raise AssertionError('Unexpected selector: ' + selector)
        return [n for n in nodes if n.kind == mapping[selector]]

    def locator(self, selector):
        return Locator(self, lambda: self.select(selector, list(walk(self.nodes))))

    def get_by_role(self, role, *, name=None, exact=False):
        return Locator(self, lambda: [n for n in walk(self.nodes) if n.attrs.get('role') == role
                                     and (name is None or says(name, n.attrs.get('name', n.words()), exact))])

    def get_by_text(self, text, exact=False):
        return Locator(self, lambda: [n for n in walk(self.nodes) if says(text, n.text, exact)])

    async def goto(self, url, **kwargs):
        self.events.append('read')
        if self.navigation_timeout:
            raise TimeoutError('private body')
        self.url = url
        address = urlsplit(url)
        if address.path == '/watch':
            self.url = self.redirect_watch or url
            self.nodes = [Node('gone', self.gone_text)] if self.deleted else []
        else:
            self.url = self.redirect_list or url
            self.tab, self.index = address.path.rsplit('/', 1)[1], 0
            self.render()

    def render(self):
        for node in walk(self.nodes):
            node.connected = False
        rows = []
        for wid, title in self.pages[self.tab][self.index]:
            if self.deleted and wid == self.target['id'] and not self.keep_target:
                continue
            href = f'/video/{wid}/edit' if wid else ''
            rows.append(Node('row', attrs={'href': href, 'title': title}, children=[
                Node('title', title, attrs={'href': href}),
                Node('options', 'Options', attrs={'role': 'button'}, action=lambda wid=wid: self.options(wid))]))
        if not rows and self.empty_panel:
            rows.extend(deepcopy(self.empty_panels))
        children = [Node('section', children=rows)]
        if self.pager:
            children.append(Node('next', attrs={'aria-disabled': str(self.index == len(self.pages[self.tab]) - 1).lower()}, action=self.next_page))
        self.nodes = [Node('body', children=children)]

    def next_page(self):
        self.index += 1
        self.render()

    def options(self, wid=None):
        self.events.append('options')
        if self.on_options:
            self.on_options()
        self.nodes.append(Node('menu', 'Delete forever', action=lambda: self.delete_menu(wid)))

    def delete_menu(self, wid=None):
        self.events.append('delete-menu')
        self.menu_count += 1
        self.nodes = [n for n in self.nodes if n.kind != 'menu']
        box = Node('checkbox', attrs={'role': 'checkbox', 'aria-checked': 'false'})

        def tick():
            self.events.append('checkbox')
            box.attrs['aria-checked'] = 'true'

        self.nodes.append(Node('dialog', attrs={'role': 'dialog'}, children=[
            Node('label', self.target['title']), box, Node('tick', action=tick),
            Node('final', 'Delete forever', attrs={'role': 'button'}, action=lambda: self.final(wid))]))
        if self.on_delete_menu:
            self.on_delete_menu()

    def final(self, wid=None):
        self.final_count += 1
        self.events.append('final')
        wid = wid or self.target['id']
        self.deleted_ids.append(wid)
        self.deleted = wid == self.target['id']
        if self.final_timeout:
            raise TimeoutError('private body after server action')

    async def wait_for_timeout(self, ms):
        pass

    def on(self, event, callback):
        pass

    def remove_listener(self, event, callback):
        pass

    async def screenshot(self, **kwargs):
        raise AssertionError('Screenshots are forbidden')


class OfflineCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'VPUB_HOME': str(Path(self.temp.name) / 'vpub'),
                                     'BILI_CLI_HOME': str(Path(self.temp.name) / 'bili')})
        env.start()
        self.addCleanup(env.stop)
        for p in (patch.object(rt, 'open_context', side_effect=AssertionError('Live browser forbidden')),
                  patch('socket.create_connection', side_effect=AssertionError('Network forbidden')),
                  patch.object(rt, 'risk_check', side_effect=self.risk_check),
                  patch.object(route.yt, 'risk_check', side_effect=self.risk_check),
                  patch.object(rt, 'identity', side_effect=self.identity)):
            p.start()
            self.addCleanup(p.stop)
        self.after_mark = None
        self.on_risk = None
        original = state.begin_press

        def mark(path, target):
            result = original(path, target)
            self.page.events.append('mark')
            if self.after_mark:
                self.after_mark()
            return result

        p = patch.object(state, 'begin_press', side_effect=mark)
        p.start()
        self.addCleanup(p.stop)

    async def risk_check(self, page):
        page.events.append('risk')
        if self.on_risk:
            self.on_risk(page)
        if page.risk:
            raise VpubError('human_verification_required', 'Synthetic platform risk')

    async def identity(self, page, platform):
        if page.on_identity:
            page.on_identity()
        return page.live_owner

    def seed(self, platform, wid, who):
        url = f'https://www.youtube.com/watch?v={wid}' if platform == 'youtube' else f'https://www.douyin.com/video/{wid}'
        self.target = {'key': 'a' * 64, 'platform': platform, 'id': wid, 'identity': who,
                       'account': 'default', 'title': 'Approved', 'url': url,
                       'generation': 'generation-1', 'attempt': 'attempt-1'}
        self.path = state.receipt_path(self.target['key'])
        atomic_json(self.path, {'state': 'DELETE_READY', 'target': self.target, 'delete_attempted': False})

    def stopped(self):
        atomic_json(home() / 'STOP.json', {'reason': 'Synthetic stop'})

    def assert_unpressed(self):
        self.assertEqual((0, 0), (self.page.menu_count, self.page.final_count))
        self.assertFalse(read_json(self.path)['delete_attempted'])

    def assert_safe_result(self, answer):
        self.assertEqual(set(answer), {'id', 'identity', 'present', 'owned', 'page_gone', 'list_absent',
                                       'list_complete', 'fresh', 'platform_writes', 'note'})
        self.assertNotIn('private body', str(answer))

    async def refusal_or_result(self):
        try:
            return await self.run_route()
        except VpubError as exc:
            self.assertIn(exc.code, {'wrong_work', 'wrong_account', 'layout_changed'})
            return None


class YouTubeDeletionTests(OfflineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.seed('youtube', VIDEO, CHANNEL)
        self.page = FakeYouTube(self.target)

    async def run_route(self, operation='delete', **changes):
        return await route.execute(self.page, CHANNEL, self.target | changes, operation, self.path)

    async def test_options_redraw_cannot_retarget_locator(self):
        def redraw(node):
            if node.kind == 'options':
                self.page.on_visibility = None
                self.page.render()
        self.page.on_visibility = redraw
        await self.refusal_or_result()
        self.assertNotIn('options', self.page.events)
        self.assert_unpressed()

    async def test_menu_replaced_during_owner_read_cannot_delete_other_work(self):
        def replace_menu():
            menus = [node for node in self.page.nodes if node.kind == 'menu']
            if menus:
                self.page.on_identity = None
                for node in menus:
                    node.connected = False
                    self.page.nodes.remove(node)
                self.page.options(OTHER)
        self.page.on_identity = replace_menu
        await self.refusal_or_result()
        self.assertEqual([], self.page.deleted_ids)
        self.assertEqual(0, self.page.menu_count)

    async def test_dialog_replaced_after_identity_check_cannot_retarget_final(self):
        def replace_dialog(node, script):
            if node.kind == 'dialog' and 'node ===' in script:
                self.page.on_evaluate = None
                for old in walk([node]):
                    old.connected = False
                self.page.nodes.remove(node)
                # A fresh confirmation for a different work with the same title.
                self.page.delete_menu(OTHER)
        self.page.on_evaluate = replace_dialog
        await self.refusal_or_result()
        self.assertEqual([], self.page.deleted_ids)
        self.assertEqual(0, self.page.final_count)
        self.assertTrue(read_json(self.path)['delete_attempted'])

    async def test_owner_changed_during_post_marker_guard_prevents_delete(self):
        def change_owner(page):
            if 'mark' in page.events and not page.menu_count:
                page.live_owner = 'UC' + 'Z' * 22
        self.on_risk = change_owner
        await self.refusal_or_result()
        self.assertEqual((0, 0), (self.page.menu_count, self.page.final_count))
        self.assertTrue(read_json(self.path)['delete_attempted'])

    async def test_owner_changed_during_final_guard_prevents_final(self):
        def change_owner(page):
            if 'checkbox' in page.events:
                page.live_owner = 'UC' + 'Z' * 22
        self.on_risk = change_owner
        await self.refusal_or_result()
        self.assertEqual([], self.page.deleted_ids)
        self.assertEqual(0, self.page.final_count)

    async def test_stop_during_post_marker_binding_read_prevents_delete(self):
        def stop_during_read():
            if 'mark' in self.page.events:
                self.stopped()
        self.page.on_identity = stop_during_read
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assertEqual((0, 0), (self.page.menu_count, self.page.final_count))
        self.assertTrue(read_json(self.path)['delete_attempted'])

    async def test_stop_during_final_binding_read_prevents_final(self):
        def stop_during_read(node, script):
            if node.kind == 'dialog' and 'node.contains(child)' in script and 'checkbox' in self.page.events:
                self.stopped()
        self.page.on_evaluate = stop_during_read
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assertEqual([], self.page.deleted_ids)
        self.assertEqual(0, self.page.final_count)

    async def test_known_studio_chip_blocks_complete_absence_using_actual_javascript(self):
        self.page.deleted = True
        self.page.clean = list_clean_script([filter_chip_fixture()])[0]
        answer = await self.run_route('verify')
        self.assertFalse(answer['list_complete'])
        self.assertFalse(state.verified(answer, self.target))
        self.assert_unpressed()

    async def test_wrong_identity_and_id_fail_before_reads_or_writes(self):
        for changes in ({'identity': CHANNEL.upper()}, {'id': OTHER}, {'url': 'https://example.invalid/'},
                        {'id': '../bad'}, {'platform': 'douyin'}):
            with self.subTest(changes=changes), self.assertRaises(VpubError):
                await self.run_route(**changes)
        self.assertEqual([], self.page.events)
        self.assert_unpressed()

    async def test_changed_live_owner_cannot_delete(self):
        self.page.live_owner = 'UC' + 'b' * 22
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assert_unpressed()

    async def test_same_title_wrong_id_cannot_select_a_work(self):
        self.page.pages['upload'] = [[(OTHER, 'Approved')]]
        answer = await self.run_route()
        self.assertFalse(answer['present'])
        self.assertFalse(state.verified(answer, self.target))
        self.assert_unpressed()

    async def test_duplicate_exact_id_on_one_or_two_pages_refuses(self):
        for pages in ([[(VIDEO, 'Approved'), (VIDEO, 'Approved')]], [[(VIDEO, 'Approved')], [(VIDEO, 'Approved')]]):
            self.page.pages['upload'] = pages
            with self.assertRaises(VpubError):
                await self.run_route()
        self.assert_unpressed()

    async def test_inspect_and_verify_present_are_zero_write(self):
        for op in ('inspect', 'verify'):
            answer = await self.run_route(op)
            self.assertTrue(answer['present'] and answer['owned'])
            self.assert_safe_result(answer)
        self.assert_unpressed()

    async def test_already_gone_has_both_proofs_and_zero_writes(self):
        self.page.deleted = True
        answer = await self.run_route()
        self.assertTrue(state.verified(answer, self.target))
        self.assertEqual(0, answer['platform_writes'])
        self.assert_safe_result(answer)
        self.assert_unpressed()

    async def test_missing_rows_filters_or_missing_pager_remain_pending(self):
        self.page.deleted = True
        for attr, value in (('empty_panel', False), ('clean', False), ('pager', False)):
            with self.subTest(attr=attr):
                setattr(self.page, attr, value)
                # A nonempty list without Next cannot claim last-page coverage.
                self.page.pages['upload'] = [[(OTHER, 'Other')]] if attr == 'pager' else [[(VIDEO, 'Approved')]]
                answer = await self.run_route('verify')
                self.assertFalse(answer['list_complete'])
                self.assertFalse(state.verified(answer, self.target))
                setattr(self.page, attr, True)
        self.assert_unpressed()

    async def test_dropped_row_cannot_prove_complete(self):
        self.page.deleted = True
        self.page.pages['upload'] = [[(None, 'Unresolved draft')]]
        answer = await self.run_route('verify')
        self.assertFalse(answer['list_complete'])

    async def test_unavailable_access_denied_and_redirects_never_gone(self):
        self.page.deleted = True
        for text in ('Video unavailable', 'Access denied', 'This video is private', 'No content available'):
            self.page.gone_text = text
            answer = await self.run_route('verify')
            self.assertFalse(answer['page_gone'])
        self.page.gone_text = 'This video has been removed by the uploader.'
        self.page.redirect_watch = f'https://www.youtube.com/watch?v={OTHER}'
        self.assertFalse((await self.run_route('verify'))['page_gone'])

    async def test_mark_then_risk_guard_then_one_final_and_fresh_readback(self):
        answer = await self.run_route()
        self.assertTrue(state.verified(answer, self.target))
        self.assertEqual(1, answer['platform_writes'])
        self.assertEqual((1, 1), (self.page.menu_count, self.page.final_count))
        events = self.page.events
        mark = events.index('mark')
        self.assertEqual(['mark', 'risk', 'delete-menu'], events[mark:mark + 3])
        self.assertEqual('risk', events[events.index('final') - 1])
        self.assertIn('read', events[events.index('final') + 1:])
        self.assertTrue(read_json(self.path)['delete_attempted'])
        self.assert_safe_result(answer)

    async def test_target_on_second_page_or_shorts_is_bound(self):
        self.page.pages = {'upload': [[(OTHER, 'Other')]], 'short': [[(OTHER, 'Other')], [(VIDEO, 'Approved')]]}
        answer = await self.run_route()
        self.assertEqual(1, self.page.final_count)
        self.assertTrue(answer['page_gone'])

    async def test_stop_before_and_immediately_after_mark_prevents_controls(self):
        self.stopped()
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assert_unpressed()
        (home() / 'STOP.json').unlink()
        self.after_mark = self.stopped
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assertEqual((0, 0), (self.page.menu_count, self.page.final_count))
        self.assertTrue(read_json(self.path)['delete_attempted'])

    async def test_risk_after_mark_and_stop_before_final_prevent_press(self):
        self.after_mark = lambda: setattr(self.page, 'risk', True)
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assertEqual(0, self.page.menu_count)

    async def test_stop_after_menu_prevents_final(self):
        self.page.on_delete_menu = self.stopped
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assertEqual((1, 0), (self.page.menu_count, self.page.final_count))

    async def test_card_or_identity_change_after_menu_prevents_final(self):
        self.page.on_delete_menu = lambda: setattr(self.page, 'live_owner', 'UC' + 'b' * 22)
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assertEqual(0, self.page.final_count)

    async def test_final_timeout_never_retries_and_verify_is_read_only(self):
        self.page.final_timeout = True
        answer = await self.run_route()
        self.assertFalse(state.verified(answer, self.target))
        self.assertEqual(1, answer['platform_writes'])
        self.assert_safe_result(answer)
        self.page.keep_target = True
        with self.assertRaises(VpubError):
            await self.run_route()
        self.assertEqual((1, 1), (self.page.menu_count, self.page.final_count))
        self.page.keep_target = False
        self.assertTrue(state.verified(await self.run_route('verify'), self.target))
        self.assertEqual(1, self.page.final_count)

    async def test_read_timeout_is_never_gone(self):
        self.page.navigation_timeout = True
        answer = await self.run_route('verify')
        self.assertFalse(answer['fresh'] or answer['page_gone'] or answer['list_complete'])
        self.assert_unpressed()

    async def test_replaced_first_delete_control_is_not_retargeted(self):
        def swap(page):
            if 'mark' in page.events:
                page.nodes[-1].connected = False
                page.nodes[-1] = Node('menu', 'Delete forever', action=lambda: self.fail('Retargeted delete'))
        self.on_risk = swap
        with self.assertRaises(VpubError) as raised:
            await self.run_route()
        self.assertEqual('wrong_work', raised.exception.code)
        self.assertEqual((0, 0), (self.page.menu_count, self.page.final_count))
        self.assertTrue(read_json(self.path)['delete_attempted'])

    async def test_replaced_final_control_is_not_retargeted(self):
        def swap(page):
            if page.events[-2:] == ['checkbox', 'risk']:
                dialog = page.nodes[-1]
                dialog.children[-1].connected = False
                dialog.children[-1] = Node('final', 'Delete forever', attrs={'role': 'button'},
                                           action=lambda: self.fail('Retargeted final'))
        self.on_risk = swap
        with self.assertRaises(VpubError) as raised:
            await self.run_route()
        self.assertEqual('wrong_work', raised.exception.code)
        self.assertEqual(0, self.page.final_count)
        self.assertTrue(read_json(self.path)['delete_attempted'])

    async def test_foreign_attempt_or_generation_never_press(self):
        for changes in ({'attempt': 'other'}, {'generation': 'other'}, {'account': 'other'}):
            with self.subTest(changes=changes), self.assertRaises(VpubError):
                await self.run_route(**changes)
            self.page.nodes = []
        self.assert_unpressed()

    async def test_unsupported_operation_refuses(self):
        with self.assertRaises(VpubError):
            await self.run_route('remove')
        self.assert_unpressed()


if __name__ == '__main__':
    unittest.main()

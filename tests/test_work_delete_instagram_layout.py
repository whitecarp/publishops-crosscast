"""Observed headerless Instagram layout; synthetic DOM only, no accounts/browser."""
from copy import copy, deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_work_delete_instagram as instagram
import test_work_delete_x as fixtures
from video_publisher import browser_runtime as runtime, work_delete_instagram as route, work_delete_state as state
from video_publisher.core import VpubError, atomic_json, home, read_json


# Execute the unchanged production predicates against a small DOM implementation.
# No browser, dependencies, network or real profile is available in this VM.
DOM_RUNNER = r"""
const fs = require('node:fs'), vm = require('node:vm'), os = require('node:os');
os.setPriority(process.pid, os.constants.priority.PRIORITY_BELOW_NORMAL);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
class Element {
  constructor(spec, parent = null) {
    this.id = spec.id; this.tagName = (spec.tag || 'div').toUpperCase();
    this.attrs = spec.attrs || {}; this.parentElement = parent;
    this.text = spec.text || ''; this.isConnected = spec.connected !== false;
    this.children = (spec.children || []).map(child => new Element(child, this));
  }
  get innerText() { return [this.text, ...this.children.map(child => child.innerText)].join(''); }
  getAttribute(name) { return Object.hasOwn(this.attrs, name) ? this.attrs[name] : null; }
  matches(selector) {
    const match = /^([a-z]+)?(?:\[([\w-]+)(?:="([^"]*)")?\])?$/i.exec(selector);
    if (!match || (!match[1] && !match[2])) throw new Error('Unsupported fixture CSS: ' + selector);
    return (!match[1] || this.tagName === match[1].toUpperCase()) && (!match[2]
      || Object.hasOwn(this.attrs, match[2]) && (match[3] === undefined || this.attrs[match[2]] === match[3]));
  }
  closest(selector) {
    for (let node = this; node; node = node.parentElement)
      if (selector.split(',').some(part => node.matches(part.trim()))) return node;
    return null;
  }
  querySelectorAll(selector) {
    return descendants(this).filter(node => selector.split(',').some(part => node.matches(part.trim())));
  }
  contains(other) { return descendants(this).includes(other); }
  getClientRects() { return this.closest('[hidden]') || !this.isConnected ? [] : [{}]; }
}
function descendants(root) { return root.children.flatMap(child => [child, ...descendants(child)]); }
const result = input.fixtures.map(spec => {
  const root = new Element(spec), all = [root, ...descendants(root)];
  const document = {querySelectorAll: selector => all.filter(node =>
    selector.split(',').some(part => node.matches(part.trim())))};
  const context = vm.createContext({document, URL, getComputedStyle: node => ({visibility:
    node.closest('[visibility="hidden"]') ? 'hidden' : 'visible'})});
  const predicate = vm.runInContext('(' + input.source + ')', context, {timeout: 1000});
  if (input.mode === 'nav') return predicate(document.querySelectorAll('a[href]'));
  const find = id => all.find(node => node.id === id);
  return predicate(find('panel'), {author: find('author'), more: find('more'),
    permalink: find('permalink'), identity: 'test_owner'});
});
process.stdout.write(JSON.stringify(result));
"""


def dom(tag='div', *, attrs=None, children=(), text='', id=None):
    return {'tag': tag, 'attrs': attrs or {}, 'children': list(children), 'text': text, 'id': id}


def icon(label):
    return dom('svg', attrs={'aria-label': label})


def avatar(name=fixtures.OWNER):
    return dom('a', attrs={'href': f'/{name}/'}, children=[
        dom('img', attrs={'alt': f"{name}'s profile picture"})])


def sidebar(name=fixtures.OWNER):
    # Includes inert links whose href is the current post; these cannot be post identity.
    labels = ('Home', 'Reels', 'Messages', 'Search', 'Notifications')
    return dom(children=[dom(children=[dom('a', attrs={'href': f'/p/{instagram.WORK}/'}, children=[icon(label)])])
                         for label in labels] + [dom('a', attrs={'href': '#'}), avatar(name)])


def headerless_spec():
    bar = dom(children=[dom('a', id='author', text=fixtures.OWNER, attrs={'href': f'/{fixtures.OWNER}/'}),
                        dom('a', attrs={'href': '/reels/audio/synthetic/'}),
                        dom(id='more', attrs={'role': 'button'}, children=[icon('More options')])])
    caption = dom(children=[avatar(), dom('a', text=fixtures.OWNER, attrs={'href': f'/{fixtures.OWNER}/'})])
    actions = dom(children=[*[icon(label) for label in ('Like', 'Comment', 'Share', 'Save')],
                            dom('a', id='permalink', attrs={'href': f'/p/{instagram.WORK}/'}, children=[dom('time')])])
    panel = dom(id='panel', children=[bar, dom('hr'), caption, actions, dom(children=[dom('form'), dom('textarea')])])
    return dom('body', children=[sidebar(), dom('main', children=[panel])])


def find(spec, id):
    if spec.get('id') == id:
        return spec
    return next((found for child in spec.get('children', []) if (found := find(child, id)) is not None), None)


class LayoutScriptTests(unittest.TestCase):
    def setUp(self):
        fixtures.below_normal()
        scratch = tempfile.TemporaryDirectory(prefix='ig-layout-js-')
        self.addCleanup(scratch.cleanup)
        env = patch.dict(os.environ, VPUB_HOME=str(Path(scratch.name) / 'vpub'),
                         BILI_CLI_HOME=str(Path(scratch.name) / 'bili'))
        env.start()
        self.addCleanup(env.stop)
        for name in ('socket.socket.connect', 'socket.create_connection'):
            blocked = patch(name, side_effect=AssertionError('Offline only'))
            blocked.start()
            self.addCleanup(blocked.stop)

    def evaluate(self, specs, mode):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js unavailable; do not install a runtime for this test')
        source = route.NAV_IDENTITY if mode == 'nav' else route.POST_STRUCTURE
        result = subprocess.run([node, '-e', DOM_RUNNER], input=json.dumps(
            {'source': source, 'fixtures': specs, 'mode': mode}), text=True,
            capture_output=True, check=True, timeout=10, env=dict(os.environ))
        return json.loads(result.stdout)

    def test_actual_navigation_predicate_ignores_post_and_comment_avatars(self):
        good = headerless_spec()
        other = deepcopy(good)
        other['children'][0] = sidebar('someone_else')
        self.assertEqual([[fixtures.OWNER], ['someone_else']], self.evaluate([good, other], 'nav'))

    def test_actual_navigation_predicate_rejects_unscoped_hidden_or_ambiguous_controls(self):
        specs = []
        for change in ('no-nav', 'in-main', 'contains-main', 'dialog', 'hidden', 'aria-hidden',
                       'hidden-icon', 'duplicate-avatar', 'missing-icon', 'duplicate-icon',
                       'text-only', 'foreign-href', 'mismatched-alt'):
            spec = headerless_spec()
            nav = spec['children'][0]
            if change == 'no-nav':
                spec['children'].pop(0)
            elif change == 'in-main':
                nav['tag'] = 'main'
            elif change == 'contains-main':
                nav['children'].append(dom('main'))
            elif change == 'dialog':
                nav['attrs']['role'] = 'dialog'
            elif change in ('hidden', 'aria-hidden'):
                nav['attrs'][change] = 'true'
            elif change == 'hidden-icon':
                nav['children'][0]['attrs']['hidden'] = ''
            elif change == 'duplicate-avatar':
                nav['children'][-2] = avatar('someone_else')
            elif change == 'missing-icon':
                nav['children'][0]['children'][0]['children'].clear()
            elif change == 'duplicate-icon':
                nav['children'][0]['children'][0]['children'].append(icon('Home'))
            elif change == 'text-only':
                for child in nav['children'][:5]:
                    child['children'][0]['children'].clear()
                    child['children'][0]['text'] = 'Home Reels Messages Search Notifications'
            elif change == 'foreign-href':
                nav['children'][-1]['attrs']['href'] = 'https://other.invalid/test_owner/'
            else:
                nav['children'][-1]['children'][0]['attrs']['alt'] = "someone_else's profile picture"
            specs.append(spec)
        self.assertEqual([[]] * len(specs), self.evaluate(specs, 'nav'))
        duplicate = dom('body', children=[sidebar(), sidebar()])
        self.assertEqual([[fixtures.OWNER, fixtures.OWNER]], self.evaluate([duplicate], 'nav'))

    def test_actual_post_predicate_binds_author_bar_and_actions_timestamp(self):
        self.assertEqual([True], self.evaluate([headerless_spec()], 'post'))

    def test_actual_post_predicate_rejects_comment_global_author_and_structural_mutations(self):
        specs = []
        for change in ('comment-author', 'global-author', 'wrong-author-text', 'other-profile-in-bar',
                       'comment-options', 'comment-timestamp', 'missing-separator', 'second-main',
                       'comment-list-in-bar', 'duplicate-options', 'duplicate-time', 'hidden-author',
                       'missing-action', 'dialog'):
            spec = headerless_spec()
            panel = find(spec, 'panel')
            bar, separator, caption, actions, form = panel['children']
            if change in ('comment-author', 'global-author'):
                author = bar['children'].pop(0)
                (caption if change == 'comment-author' else spec)['children'].append(author)
            elif change == 'wrong-author-text':
                find(spec, 'author')['text'] = 'someone_else'
            elif change == 'other-profile-in-bar':
                bar['children'].append(dom('a', attrs={'href': '/someone_else/'}))
            elif change == 'comment-options':
                caption['children'].append(bar['children'].pop())
            elif change == 'comment-timestamp':
                caption['children'].append(actions['children'].pop())
            elif change == 'missing-separator':
                separator['tag'] = 'div'
            elif change == 'second-main':
                spec['children'].append(dom('main'))
            elif change == 'comment-list-in-bar':
                bar['children'].append(dom('ul'))
            elif change == 'duplicate-options':
                caption['children'].append(dom(attrs={'role': 'button'}, children=[icon('More options')]))
            elif change == 'duplicate-time':
                actions['children'].append(dom('a', attrs={'href': '/p/OtherPost/'}, children=[dom('time')]))
            elif change == 'hidden-author':
                find(spec, 'author')['attrs']['hidden'] = ''
            elif change == 'missing-action':
                actions['children'].pop(0)
            else:
                panel['attrs']['role'] = 'dialog'
            specs.append(spec)
        self.assertEqual([False] * len(specs), self.evaluate(specs, 'post'))


class LayoutPage(instagram.InstagramPage):
    def __init__(self, marked):
        super().__init__(marked)
        self.normal_identity = None
        self.nav_identity = fixtures.OWNER
        self.structural_match = True
        self.on_layout = None

    async def goto(self, url, **kwargs):
        await super().goto(url, **kwargs)
        if self.normal_identity is None:
            self.tree.children.pop(0)
        else:
            self.tree.children[0].href = f'/{self.normal_identity}/'
        nav = fixtures.Node(href=f'/{self.nav_identity}/', selectors={route.NAV_AVATARS})
        nav.navigation_identity = self.nav_identity
        self.tree.children.insert(0, nav)
        if '/p/' in url and self.present and not self.deleted:
            main = next(node for node in self.tree.descendants() if 'main' in node.selectors)
            self.author = fixtures.Node(fixtures.OWNER, href=f'/{self.owner}/', event='post-author')
            self.options = fixtures.Node(role='button', name='', selectors={route.LAYOUT_OPTIONS},
                                         event='target-options', action=self.menu)
            self.bar = fixtures.Node(selectors={route.AUTHOR_BAR}, children=[self.author, self.options])
            self.stamp = fixtures.Node(href=self.permalink, selectors={route.TIMESTAMP}, event='post-permalink')
            self.actions = fixtures.Node(children=[self.stamp])
            self.caption = fixtures.Node(children=[fixtures.Node(href=f'/{fixtures.OWNER}/')])
            self.panel = fixtures.Node(selectors={route.POST_PANELS}, children=[
                self.bar, fixtures.Node(), self.caption, self.actions, fixtures.Node()])
            main.children = [self.panel]
            if self.on_layout:
                self.on_layout(self)


class LayoutCase(fixtures.OfflineCase):
    replace_node = fixtures.BindingRegressions.replace_node
    at_boundary = fixtures.BindingRegressions.at_boundary
    boundary_stopped = fixtures.BindingRegressions.boundary_stopped

    def setUp(self):
        super().setUp()
        self.reset_layout()
        original_all, original_one = fixtures.Locator.evaluate_all, fixtures.Locator.evaluate

        async def evaluate_all(locator, script):
            if script != route.NAV_IDENTITY:
                return await original_all(locator, script)
            names = [node.navigation_identity for node in locator.nodes
                     if getattr(node, 'navigation_identity', None) is not None and node.visible]
            locator.page.read_complete('navigation', locator.nodes)
            return names

        async def evaluate(locator, script, *args):
            if script != route.POST_STRUCTURE:
                return await original_one(locator, script, *args)
            page, scope = locator.page, locator.nodes[0]
            supplied = args[0]
            valid = (page.structural_match and len(scope.children) == 5
                     and supplied['author'].nodes[0] in list(scope.children[0].descendants())
                     and supplied['more'].nodes[0] in list(scope.children[0].descendants())
                     and supplied['permalink'].nodes[0] in list(scope.children[3].descendants()))
            page.read_complete('layout', [scope])
            return valid

        for method, replacement in (('evaluate_all', evaluate_all), ('evaluate', evaluate)):
            patched = patch.object(fixtures.Locator, method, replacement)
            patched.start()
            self.addCleanup(patched.stop)

    def reset_layout(self):
        self.ready('instagram', instagram.WORK)
        self.page = LayoutPage(self.marked)

    async def execute(self, operation='delete'):
        return await route.execute(self.page, fixtures.OWNER, self.target, operation, str(self.path))

    def no_destructive(self):
        self.assertNotIn('menu-delete', self.page.events)
        self.assertNotIn('final-delete', self.page.events)
        self.assertTrue(all(handle.disposed for handle in self.page.handles))


class InstagramLayoutTests(LayoutCase):
    async def test_headerless_inspect_and_verify_open_only_the_bound_options(self):
        for operation in ('inspect', 'verify'):
            with self.subTest(operation=operation):
                self.reset_layout()
                result = await self.execute(operation)
                self.assertTrue(result['owned'] and result['present'])
                self.assertEqual(0, result['platform_writes'])
                self.assertEqual(['target-options', 'escape'], self.page.events)
                self.no_destructive()
                self.unpressed(self.page)

    async def test_delete_marks_before_both_controls_and_keeps_one_final_press(self):
        result = await self.execute()
        self.assertEqual(['target-options', 'menu-delete', 'final-delete'], self.page.events)
        self.assertTrue(state.verified(result, self.target))
        self.assertEqual(1, result['platform_writes'])
        self.marked()
        self.assertTrue(all(handle.disposed for handle in self.page.handles))

    async def test_normal_identity_is_preferred_and_wrong_account_never_uses_fallback(self):
        self.page.normal_identity = 'someone_else'
        with patch.object(route, 'current_identity', wraps=route.current_identity), self.assertRaises(VpubError) as error:
            await self.execute()
        self.assertEqual('wrong_account', error.exception.code)
        self.assertEqual([], self.page.events)
        self.unpressed(self.page)
        self.reset_layout()
        self.page.normal_identity = fixtures.OWNER
        with patch.object(fixtures.Locator, 'evaluate_all', side_effect=AssertionError('Fallback must not run')):
            result = await self.execute('inspect')
        self.assertTrue(result['owned'])

    async def test_missing_wrong_or_duplicate_navigation_does_not_borrow_post_author(self):
        for kind in ('missing', 'wrong', 'duplicate'):
            with self.subTest(kind=kind):
                self.reset_layout()
                if kind != 'duplicate':
                    self.page.nav_identity = None if kind == 'missing' else 'someone_else'
                else:
                    def duplicate(page):
                        page.tree.children.insert(0, copy(page.tree.children[0]))
                    self.page.on_layout = duplicate
                with self.assertRaises(VpubError) as error:
                    await self.execute()
                self.assertEqual('wrong_account', error.exception.code)
                self.assertEqual([], self.page.events)
                self.unpressed(self.page)

    async def test_headerless_replacement_of_panel_author_options_or_timestamp_blocks_each_boundary(self):
        for stage in ('options', 'delete', 'final'):
            for name in ('panel', 'author', 'options', 'stamp'):
                with self.subTest(stage=stage, name=name):
                    self.reset_layout()
                    def replace(page):
                        old = getattr(page, name)
                        self.replace_node(old, copy(old))
                    armed, fired = self.at_boundary(stage, replace)
                    with armed, self.assertRaises(VpubError):
                        await self.execute()
                    self.boundary_stopped(stage, fired)

    async def test_structure_or_permalink_drift_blocks_each_boundary(self):
        for stage in ('options', 'delete', 'final'):
            for mutation in ('shape', 'permalink', 'conflicting-link', 'navigation'):
                with self.subTest(stage=stage, mutation=mutation):
                    self.reset_layout()
                    def change(page):
                        if mutation == 'shape':
                            page.structural_match = False
                        elif mutation == 'permalink':
                            page.stamp.href = '/p/OtherPost/'
                        elif mutation == 'conflicting-link':
                            page.panel.children.append(fixtures.Node(href='/p/OtherPost/'))
                        else:
                            page.tree.children[0].navigation_identity = 'someone_else'
                    armed, fired = self.at_boundary(stage, change)
                    with armed, self.assertRaises(VpubError):
                        await self.execute()
                    self.boundary_stopped(stage, fired)

    async def test_initial_unproven_layout_or_wrong_permalink_never_opens_options(self):
        for mutation in ('shape', 'other-post', 'query', 'fragment', 'external', 'comment-author'):
            with self.subTest(mutation=mutation):
                self.reset_layout()
                if mutation == 'shape':
                    self.page.structural_match = False
                elif mutation == 'comment-author':
                    def move(page):
                        page.bar.children.remove(page.author)
                        page.caption.children.append(page.author)
                    self.page.on_layout = move
                else:
                    self.page.permalink = {'other-post': '/p/OtherPost/', 'query': f'/p/{instagram.WORK}/?other=1',
                                           'fragment': f'/p/{instagram.WORK}/#other',
                                           'external': f'https://other.invalid/p/{instagram.WORK}/'}[mutation]
                with self.assertRaises(VpubError):
                    await self.execute()
                self.assertEqual([], self.page.events)
                self.unpressed(self.page)

    async def test_navigation_read_address_drift_is_rechecked_before_options(self):
        fired = []
        def drift(kind, nodes):
            if kind == 'navigation' and not fired:
                fired.append(True)
                self.page.url += '?changed=1'
        self.page.after_read = drift
        with self.assertRaises(VpubError) as error:
            await self.execute()
        self.assertEqual('wrong_work', error.exception.code)
        self.assertEqual([True], fired)
        self.assertEqual([], self.page.events)
        self.unpressed(self.page)

    async def test_stop_during_structure_read_prevents_any_destructive_control(self):
        def stop(kind, nodes):
            if kind == 'layout':
                atomic_json(home() / 'STOP.json', {'reason': 'Synthetic layout STOP'})
        self.page.after_read = stop
        with self.assertRaises(VpubError) as error:
            await self.execute()
        self.assertEqual('stopped', error.exception.code)
        self.assertEqual([], self.page.events)
        self.unpressed(self.page)

    async def test_uncertain_final_is_durable_and_cannot_retry(self):
        self.page.fail_final = True
        with self.assertRaises(TimeoutError):
            await self.execute()
        self.marked()
        self.assertEqual(1, self.page.events.count('final-delete'))
        before = self.path.read_bytes()
        self.page = LayoutPage(self.marked)
        with self.assertRaises(VpubError) as error:
            await self.execute()
        self.assertEqual('deletion_already_attempted', error.exception.code)
        self.assertEqual(before, self.path.read_bytes())
        self.no_destructive()

    async def test_gone_without_complete_own_list_stays_pending_with_fallback_identity(self):
        for explicit_gone in (False, True):
            with self.subTest(gone=explicit_gone):
                self.reset_layout()
                self.page.present = False
                if not explicit_gone:
                    self.page.exact_message = "Sorry, this page isn't available."
                self.page.profile_message = ''
                self.page.counts = ['3 posts']
                self.page.profile_links = ['https://www.instagram.com/p/OtherPost/']
                result = await self.execute('verify')
                self.assertEqual(explicit_gone, result['page_gone'])
                self.assertFalse(result['list_complete'])
                self.assertFalse(state.verified(result, self.target))
                self.assertEqual('DELETE_PENDING', state.save_result(self.path, self.target, result)['state'])
                self.unpressed(self.page)


class DelayedLayoutPage(LayoutPage):
    def __init__(self, marked, menu_delay=1500, confirmation_delay=1500):
        super().__init__(marked)
        self.delays = {'menu': menu_delay, 'confirmation': confirmation_delay}
        self.elapsed = {'menu': 0, 'confirmation': 0}
        self.waits = {'menu': [], 'confirmation': []}
        self.rendered, self.stage_nodes = set(), {}
        self.stage = None
        self.shell = None
        self.loading_shell = False
        self.on_wait = self.on_render = None

    def menu(self):
        self.stage = 'menu'
        if self.loading_shell:
            self.shell = fixtures.Node(role='dialog')
            self.tree.children.append(self.shell)
        self.render_ready()

    def ask(self):
        self.marked()
        self.stage = 'confirmation'
        # Keep the outgoing menu visible until the confirmation is rendered.
        self.render_ready()

    def render_ready(self):
        stage = self.stage
        delay = self.delays[stage]
        if stage in self.rendered or delay is None or self.elapsed[stage] < delay:
            return
        self.rendered.add(stage)
        if stage == 'menu':
            if self.shell:
                self.tree.children.remove(self.shell)
            instagram.InstagramPage.menu(self)
        else:
            instagram.InstagramPage.ask(self)
        dialog = next(node for node in self.tree.children if node.role == 'dialog')
        self.stage_nodes[stage] = [dialog] + [node for node in dialog.descendants()
                                            if node.name in {'Edit', 'Delete'}]
        if self.on_render:
            self.on_render(self)

    async def wait_for_timeout(self, milliseconds):
        if self.stage:
            self.waits[self.stage].append(milliseconds)
            self.elapsed[self.stage] += milliseconds
            self.render_ready()
            if self.on_wait:
                self.on_wait(self)

    async def goto(self, url, **kwargs):
        self.stage = None
        await super().goto(url, **kwargs)


class InstagramReadinessTests(LayoutCase):
    def reset_delayed(self, menu_delay=1500, confirmation_delay=1500):
        self.ready('instagram', instagram.WORK)
        self.page = DelayedLayoutPage(self.marked, menu_delay, confirmation_delay)
        self.before = self.path.read_bytes()

    def stopped_before_press(self, stage):
        expected = ['target-options'] + (['menu-delete'] if stage == 'confirmation' else [])
        self.assertEqual(expected, self.page.events)
        self.assertEqual(stage == 'confirmation', read_json(self.path)['delete_attempted'])
        self.assertTrue(all(handle.disposed for handle in self.page.handles))
        if stage == 'menu':
            self.assertEqual(self.before, self.path.read_bytes())
        else:
            self.marked()

    async def rejected(self, stage, code='layout_changed'):
        with self.assertRaises(VpubError) as error:
            await self.execute()
        self.assertEqual(code, error.exception.code)
        self.stopped_before_press(stage)

    async def cannot_retry(self):
        before = self.path.read_bytes()
        self.page = DelayedLayoutPage(self.marked, 0, 0)
        with self.assertRaises(VpubError) as error:
            await self.execute()
        self.assertEqual('deletion_already_attempted', error.exception.code)
        self.assertEqual(before, self.path.read_bytes())
        self.no_destructive()

    def after_ready_capture(self, stage, change):
        original = runtime.risk_check
        fired = []

        async def risk(page):
            await original(page)
            if page is not self.page or page.stage != stage or stage not in page.rendered or fired:
                return
            captured = [handle.nodes[0] for handle in page.handles]
            self.assertTrue(all(node in captured for node in page.stage_nodes[stage]),
                            'First ready dialog and controls must be retained before the next guard')
            fired.append(page.elapsed[stage])
            change(page)

        return patch.object(runtime, 'risk_check', side_effect=risk), fired

    async def test_delayed_menu_and_confirmation_including_exact_deadlines_click_once(self):
        for menu, confirmation in ((0, 0), (1500, 1500), (10000, 10000), (500, 10000)):
            with self.subTest(menu=menu, confirmation=confirmation):
                self.reset_delayed(menu, confirmation)
                result = await self.execute()
                self.assertTrue(state.verified(result, self.target))
                self.assertEqual(1, result['platform_writes'])
                self.assertEqual(['target-options', 'menu-delete', 'final-delete'], self.page.events)
                self.assertEqual([500] * (menu // 500), self.page.waits['menu'])
                self.assertEqual([500] * (confirmation // 500), self.page.waits['confirmation'])
                self.marked()
                self.assertTrue(all(handle.disposed for handle in self.page.handles))

    async def test_delayed_inspect_and_verify_escape_without_a_marker(self):
        for operation in ('inspect', 'verify'):
            with self.subTest(operation=operation):
                self.reset_delayed(10000)
                result = await self.execute(operation)
                self.assertTrue(result['present'] and result['owned'])
                self.assertEqual(0, result['platform_writes'])
                self.assertEqual(['target-options', 'escape'], self.page.events)
                self.assertEqual([500] * 20, self.page.waits['menu'])
                self.assertEqual([], self.page.waits['confirmation'])
                self.assertEqual(self.before, self.path.read_bytes())
                self.no_destructive()

    async def test_menu_timeout_does_not_mark_or_retry_options(self):
        for delay in (None, 10500):
            with self.subTest(delay=delay):
                self.reset_delayed(delay)
                await self.rejected('menu')
                self.assertEqual([500] * 20, self.page.waits['menu'])
                self.assertEqual([], self.page.waits['confirmation'])

    async def test_confirmation_timeout_preserves_pending_and_never_retries_delete(self):
        for delay in (None, 10500):
            with self.subTest(delay=delay):
                self.reset_delayed(0, delay)
                await self.rejected('confirmation')
                self.assertEqual([500] * 20, self.page.waits['confirmation'])
                state.save_result(self.path, self.target, error={'code': 'layout_changed'})
                await self.cannot_retry()

    async def test_empty_loading_shell_and_outgoing_menu_may_wait_without_a_click(self):
        self.reset_delayed()
        self.page.loading_shell = True
        result = await self.execute()
        self.assertTrue(state.verified(result, self.target))
        self.assertEqual(['target-options', 'menu-delete', 'final-delete'], self.page.events)
        self.assertEqual({'menu': [500] * 3, 'confirmation': [500] * 3}, self.page.waits)

    async def test_ready_duplicate_dialog_edit_or_delete_refuses_immediately(self):
        for stage, which in (('menu', 'dialog'), ('menu', 'Edit'), ('menu', 'Delete'),
                             ('confirmation', 'dialog'), ('confirmation', 'Delete')):
            with self.subTest(stage=stage, which=which):
                self.reset_delayed(500, 500)
                def duplicate(page):
                    if page.stage != stage:
                        return
                    dialog = page.stage_nodes[stage][0]
                    if which == 'dialog':
                        page.tree.children.append(copy(dialog))
                    else:
                        dialog.children.append(copy(next(node for node in dialog.descendants() if node.name == which)))
                self.page.on_render = duplicate
                await self.rejected(stage)
                self.assertEqual([500], self.page.waits[stage])

    async def test_ambiguous_or_unbound_overlays_during_wait_refuse_immediately(self):
        for stage in ('menu', 'confirmation'):
            for role in ('dialog', 'menu', 'alertdialog'):
                with self.subTest(stage=stage, role=role):
                    self.reset_delayed(1500, 1500)
                    def overlay(page):
                        if page.stage == stage and page.elapsed[stage] == 500:
                            # Two dialogs are ambiguous even before the menu shell exists.
                            page.tree.children.extend(fixtures.Node(role=role) for _ in range(2 if role == 'dialog' else 1))
                    self.page.on_wait = overlay
                    await self.rejected(stage)
                    self.assertEqual([500], self.page.waits[stage])

    async def test_first_ready_dialog_and_controls_cannot_be_replaced_at_guard(self):
        for stage, index in (('menu', 0), ('menu', 1), ('menu', 2), ('confirmation', 0), ('confirmation', 1)):
            for delay in (0, 1500, 10000):
                with self.subTest(stage=stage, index=index, delay=delay):
                    self.reset_delayed(delay, delay)
                    def replace(page):
                        old = page.stage_nodes[stage][index]
                        self.replace_node(old, copy(old))
                    armed, fired = self.after_ready_capture(stage, replace)
                    with armed:
                        await self.rejected(stage, 'wrong_work')
                    self.assertEqual([delay], fired)

    async def test_first_visible_dialog_cannot_be_reacquired_after_replacement(self):
        original = fixtures.Handle.is_visible
        for stage in ('menu', 'confirmation'):
            for delay in (0, 1500, 10000):
                with self.subTest(stage=stage, delay=delay):
                    self.reset_delayed(delay, delay)
                    fired = []

                    async def replace_after_visible(handle):
                        visible = await original(handle)
                        if (visible and not fired and self.page.stage == stage
                                and stage in self.page.rendered
                                and handle.nodes[0] is self.page.stage_nodes[stage][0]):
                            old = handle.nodes[0]
                            self.replace_node(old, copy(old))
                            fired.append(self.page.elapsed[stage])
                        return visible

                    with patch.object(fixtures.Handle, 'is_visible', replace_after_visible):
                        await self.rejected(stage, 'wrong_work')
                    self.assertEqual([delay], fired)
                    if stage == 'confirmation':
                        await self.cannot_retry()

    async def test_wrong_account_or_url_during_wait_even_at_deadline_refuses(self):
        for stage in ('menu', 'confirmation'):
            for drift in ('account', 'url'):
                for when in (500, 10000):
                    with self.subTest(stage=stage, drift=drift, when=when):
                        self.reset_delayed(10000, 10000)
                        def change(page):
                            if page.stage == stage and page.elapsed[stage] == when:
                                if drift == 'account':
                                    page.tree.children[0].navigation_identity = 'someone_else'
                                else:
                                    page.url += '?changed=1'
                        self.page.on_wait = change
                        await self.rejected(stage, 'wrong_account' if drift == 'account' else 'wrong_work')
                        self.assertEqual(when, self.page.elapsed[stage])

    async def test_stop_during_each_wait_even_at_deadline_never_presses_next_control(self):
        for stage in ('menu', 'confirmation'):
            for when in (500, 10000):
                with self.subTest(stage=stage, when=when):
                    self.reset_delayed(10000, 10000)
                    def stop(page):
                        if page.stage == stage and page.elapsed[stage] == when:
                            atomic_json(home() / 'STOP.json', {'reason': 'Synthetic control readiness STOP'})
                    self.page.on_wait = stop
                    try:
                        await self.rejected(stage, 'stopped')
                        self.assertEqual(when, self.page.elapsed[stage])
                        self.assertEqual('Synthetic control readiness STOP', read_json(home() / 'STOP.json')['reason'])
                    finally:
                        (home() / 'STOP.json').unlink(missing_ok=True)

    async def test_retained_post_replacement_during_each_wait_refuses(self):
        for stage in ('menu', 'confirmation'):
            with self.subTest(stage=stage):
                self.reset_delayed()
                def replace(page):
                    if page.stage == stage and page.elapsed[stage] == 500:
                        self.replace_node(page.panel, copy(page.panel))
                self.page.on_wait = replace
                await self.rejected(stage, 'wrong_work')
                self.assertEqual([500], self.page.waits[stage])

    async def test_delayed_final_acknowledgement_loss_keeps_one_press_and_durable_no_retry(self):
        self.reset_delayed()
        self.page.fail_final = True
        with self.assertRaises(TimeoutError):
            await self.execute()
        self.assertEqual(['target-options', 'menu-delete', 'final-delete'], self.page.events)
        self.marked()
        state.save_result(self.path, self.target, error={'code': 'TimeoutError'})
        await self.cannot_retry()


if __name__ == '__main__':
    unittest.main()

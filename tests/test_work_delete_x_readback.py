"""Observed X error-detail absence control; offline reads, never account calls."""
from urllib.parse import urlsplit
from unittest.mock import patch
import re
import unittest

import test_work_delete_x as fixtures
from video_publisher import browser_runtime as runtime, work_delete_x as route, work_delete_state as state
from video_publisher.core import VpubError, atomic_json, home, read_json


MESSAGE = "Hmm...this page doesn’t exist. Try searching for something else."


def element(tag='div', *, testid=None, **kwargs):
    node = fixtures.Node(**kwargs)
    node.tag, node.testid = tag, testid
    if tag == 'span':
        node.selectors.add('span')
    return node


def detail(text=MESSAGE, *, visible=True, label_visible=True, nested=False):
    label = element('span', text=text, visible=label_visible)
    if nested:
        label = element('span', children=[label])
    return element(testid='error-detail', visible=visible, children=[label])


def ancestors(tree, node):
    for child in tree.children:
        if child is node:
            return [tree]
        path = ancestors(child, node)
        if path:
            return [tree, *path]
    return []


BASE_LOCATOR = fixtures.Locator.locator


def scoped_locator(locator, selector):
    """Model only the CSS subset used here, including hostile ancestor contexts.

    This interprets the *production selector* rather than assigning its constant
    to fixture nodes: removing main/article/tweet bounds must change the result.
    The shared dynamic locator still handles visibility, text, identity and rows.
    """
    marker = '[data-testid="error-detail"]'
    if marker not in selector:
        return BASE_LOCATOR(locator, selector)
    terms = re.findall(r':not\(([^()]*)\)', selector)
    base = re.sub(r':not\([^()]*\)', '', selector).strip()
    if base not in {marker, 'main ' + marker}:
        raise AssertionError('Unsupported error-detail fixture selector: ' + selector)
    allowed = {'article ' + marker, '[data-testid="tweet"] ' + marker}
    if any(term not in allowed for term in terms):
        raise AssertionError('Unsupported error-detail exclusion: ' + selector)

    def selected():
        for node in locator._below():
            if getattr(node, 'testid', None) != 'error-detail':
                continue
            parents = ancestors(locator.page.tree, node)
            if base.startswith('main ') and not any(getattr(p, 'tag', None) == 'main' for p in parents):
                continue
            if 'article ' + marker in terms and any(getattr(p, 'tag', None) == 'article' for p in parents):
                continue
            if '[data-testid="tweet"] ' + marker in terms and any(getattr(p, 'testid', None) == 'tweet' for p in parents):
                continue
            yield node
    return fixtures.Locator(locator.page, selected)


class ReadbackPage(fixtures.XPage):
    def __init__(self, marked):
        super().__init__(marked)
        self.present = False
        self.payload = lambda: [element('main', children=[detail()])]
        self.appear_at = 2000
        self.elapsed = 0
        self.waits = []
        self.on_wait = None
        self.exact_read = False

    async def goto(self, url, **kwargs):
        await super().goto(url, **kwargs)
        self.exact_read = '/status/' in urlsplit(url).path
        if self.exact_read:
            self.loaded = fixtures.Node(children=[self.navigation(), *self.payload()])
            self.tree = fixtures.Node(children=[self.navigation()])
            self.elapsed = 0
            self.waits = []

    async def wait_for_timeout(self, milliseconds):
        if not self.exact_read:
            return
        self.waits.append(milliseconds)
        self.elapsed += milliseconds
        if self.appear_at is not None and self.elapsed >= self.appear_at:
            self.tree = self.loaded
        if self.on_wait:
            self.on_wait(self)


class XReadbackTests(fixtures.OfflineCase):
    def setUp(self):
        super().setUp()  # BelowNormal, both disposable homes, network/process forbidden.
        replacement = patch.object(fixtures.Locator, 'locator', scoped_locator)
        replacement.start()
        self.addCleanup(replacement.stop)
        self.reset_page()

    def reset_page(self):
        self.ready('x', fixtures.WORK)
        self.page = ReadbackPage(self.marked)
        self.original_receipt = self.path.read_bytes()

    def untouched(self):
        self.assertEqual([], self.page.events)
        self.assertEqual(self.original_receipt, self.path.read_bytes())
        self.unpressed(self.page)

    async def read(self):
        result = await route.exact(self.page, self.target)
        self.untouched()
        return result

    async def refused(self, code):
        with self.assertRaises(VpubError) as caught:
            await self.read()
        self.assertEqual(code, caught.exception.code)
        self.assertEqual(1, len(self.page.visits))
        self.untouched()

    async def test_observed_fixed_message_and_whitespace_are_read_only_gone(self):
        for message in (MESSAGE, MESSAGE.replace('’', "'"),
                        '  Hmm...this page doesn’t exist.\n\tTry searching  for something else.  '):
            with self.subTest(message=message):
                self.reset_page()
                self.page.payload = lambda: [element('main', children=[detail(message)])]
                self.assertEqual((None, True), await self.read())
                self.assertEqual([2000], self.page.waits)

    async def test_nested_or_repeated_spans_within_one_panel_are_not_two_panels(self):
        for nested in (True, False):
            with self.subTest(nested=nested):
                self.reset_page()
                panel = detail(nested=nested)
                if not nested:
                    panel.children.append(element('span', text=MESSAGE))
                self.page.payload = lambda: [element('main', children=[panel])]
                self.assertEqual((None, True), await self.read())

    async def test_hidden_panel_hidden_parent_or_hidden_label_is_not_gone(self):
        for mode in ('panel', 'parent', 'label'):
            with self.subTest(mode=mode):
                self.reset_page()
                panel = detail(visible=mode != 'panel', label_visible=mode != 'label')
                self.page.payload = lambda: [element('main', visible=mode != 'parent', children=[panel])]
                self.assertEqual((None, False), await self.read())
                self.assertEqual(30000, sum(self.page.waits))

    async def test_two_visible_panels_are_ambiguous_even_if_only_one_matches(self):
        for second_text in (MESSAGE, 'Something went wrong.'):
            with self.subTest(second_text=second_text):
                self.reset_page()
                self.page.payload = lambda: [element('main', children=[detail(), detail(second_text)])]
                self.assertEqual((None, False), await self.read())

    async def test_hidden_duplicate_panel_does_not_make_visible_panel_ambiguous(self):
        self.page.payload = lambda: [element('main', children=[detail(), detail(visible=False)])]
        self.assertEqual((None, True), await self.read())

    async def test_fixed_words_outside_platform_control_are_not_gone(self):
        for mode in ('body', 'main-text', 'outside-main', 'article', 'tweet', 'caption', 'reply'):
            with self.subTest(mode=mode):
                self.reset_page()
                if mode == 'body':
                    payload = [element('span', text=MESSAGE)]
                elif mode == 'main-text':
                    payload = [element('main', children=[element('span', text=MESSAGE)])]
                elif mode == 'outside-main':
                    payload = [detail()]
                elif mode in {'article', 'tweet'}:
                    wrapper = element('article' if mode == 'article' else 'div',
                                      testid='tweet' if mode == 'tweet' else None, children=[detail()])
                    payload = [element('main', children=[wrapper])]
                else:
                    wrapper = element('article', children=[element('div', testid='tweetText',
                                                                  text=MESSAGE, user_words=True)])
                    payload = [element('main', children=[wrapper])]
                self.page.payload = lambda: payload
                self.assertEqual((None, False), await self.read())

    async def test_legacy_empty_state_is_preserved_but_new_words_are_not_added_to_it(self):
        for words, gone in [('This Post was deleted.', True), (MESSAGE, False)]:
            with self.subTest(words=words):
                self.reset_page()
                self.page.payload = lambda: [fixtures.Node(words, selectors={'main [data-testid="emptyState"]'})]
                self.assertEqual((None, gone), await self.read())

    async def test_generic_partial_or_extended_message_does_not_prove_gone(self):
        for message in ('Something went wrong. Try reloading.', 'This page is unavailable.',
                        'Hmm...this page doesn’t exist.', MESSAGE + ' Extra user text.',
                        'Someone said: ' + MESSAGE):
            with self.subTest(message=message):
                self.reset_page()
                self.page.payload = lambda: [element('main', children=[detail(message)])]
                self.assertEqual((None, False), await self.read())

    async def test_delayed_panel_accepts_at_deadline_but_not_after_or_on_timeout(self):
        for appear_at, gone in ((2500, True), (30000, True), (30500, False), (None, False)):
            with self.subTest(appear_at=appear_at):
                self.reset_page()
                self.page.appear_at = appear_at
                self.assertEqual((None, gone), await self.read())
                self.assertEqual(appear_at if gone else 30000, sum(self.page.waits))

    async def test_redirects_and_wrong_exact_id_cannot_borrow_error_control(self):
        exact = f'https://x.com/{fixtures.OWNER}/status/{fixtures.WORK}'
        for address in (exact.replace('https:', 'http:'), exact.replace('x.com', 'other.invalid'),
                        exact.replace(fixtures.OWNER, 'another_owner'),
                        exact.replace(fixtures.WORK, '2111111111111111112'),
                        exact + '?other=1', exact + '#other', 'https://x.com/i/flow/login'):
            with self.subTest(address=address):
                self.reset_page()
                self.page.redirect = address
                await self.refused('wrong_work')

    async def test_missing_or_changed_current_account_refuses_before_label_read(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                self.reset_page()
                self.page.signed_identity = 'other_owner'
                if missing:
                    self.page.on_wait = lambda page: setattr(page.tree, 'children', [
                        node for node in page.tree.children if node.name != 'Profile'])
                await self.refused('wrong_account')

    async def test_identity_and_url_are_rechecked_after_fixed_label_read(self):
        for mode in ('identity', 'origin', 'id', 'query'):
            with self.subTest(mode=mode):
                self.reset_page()
                fired = []
                def after(kind, nodes):
                    if fired or kind != 'text' or not any(node.words() == MESSAGE for node in nodes):
                        return
                    fired.append(True)
                    if mode == 'identity':
                        self.page.tree.children[0].href = '/someone_else/'
                    elif mode == 'origin':
                        self.page.url = self.page.url.replace('x.com', 'other.invalid')
                    elif mode == 'id':
                        self.page.url = self.page.url.replace(fixtures.WORK, '2111111111111111112')
                    else:
                        self.page.url += '?changed=1'
                self.page.after_read = after
                await self.refused('wrong_account' if mode == 'identity' else 'wrong_work')
                self.assertEqual([True], fired)

    async def test_challenge_url_overrides_a_matching_error_control(self):
        self.page.redirect = 'https://x.com/account/access'
        await self.refused('human_verification_required')
        self.assertEqual('human_verification_required', read_json(home() / 'STOP.json')['code'])

    async def test_platform_challenge_after_label_read_stops_and_latches_stop(self):
        fired = []
        def after(kind, nodes):
            if kind == 'text' and not fired and any(node.words() == MESSAGE for node in nodes):
                fired.append(True)
                self.page.tree.children.append(fixtures.Node('Verify your identity'))
        self.page.after_read = after
        await self.refused('human_verification_required')
        self.assertEqual([True], fired)
        self.assertEqual('human_verification_required', read_json(home() / 'STOP.json')['code'])

    async def test_operator_stop_after_label_read_is_not_gone_success(self):
        fired = []
        def after(kind, nodes):
            if kind == 'text' and not fired and any(node.words() == MESSAGE for node in nodes):
                fired.append(True)
                atomic_json(home() / 'STOP.json', {'reason': 'Synthetic read-back stop'})
        self.page.after_read = after
        await self.refused('stopped')
        self.assertEqual([True], fired)

    async def test_present_exact_post_takes_precedence_over_absence_panel(self):
        target = fixtures.post()
        self.page.payload = lambda: [element('main', children=[detail(), target])]
        row, gone = await self.read()
        self.assertIsNotNone(row)
        self.assertFalse(gone)

    async def test_new_gone_control_does_not_make_nonempty_profile_complete(self):
        self.page.profile_message = ''
        self.page.profile_posts = [fixtures.post(wid='2111111111111111112')]
        result = await route.execute(self.page, fixtures.OWNER, self.target, 'verify', str(self.path))
        self.assertTrue(result['page_gone'] and result['fresh'] and result['list_absent'])
        self.assertFalse(result['list_complete'])
        self.assertFalse(state.verified(result, self.target))
        self.assertEqual(0, result['platform_writes'])
        self.untouched()


class IdentityReadbackPage(ReadbackPage):
    def __init__(self, marked, identity_at):
        super().__init__(marked)
        self.identity_at = identity_at
        self.profile_elapsed, self.profile_waits = 0, []
        self.content_reads = []
        self.after_read = self.track_read

    def track_read(self, kind, nodes):
        if kind == 'post' or kind == 'text' and any(node.words() in {MESSAGE, 'No posts yet'} for node in nodes):
            elapsed = self.elapsed if self.exact_read else self.profile_elapsed
            self.content_reads.append(elapsed)
            if self.identity_at is None or elapsed < self.identity_at:
                raise AssertionError('Post/absence content read before live identity became available')

    async def wait_for_timeout(self, milliseconds):
        await super().wait_for_timeout(milliseconds)
        if self.exact_read:
            elapsed = self.elapsed
        else:
            self.profile_elapsed += milliseconds
            self.profile_waits.append(milliseconds)
            elapsed = self.profile_elapsed
        self.tree.children = [node for node in self.tree.children if node.name != 'Profile']
        if self.identity_at is not None and elapsed >= self.identity_at:
            self.tree.children.insert(0, self.navigation())


class XIdentityReadinessTests(fixtures.OfflineCase):
    def setUp(self):
        super().setUp()
        replacement = patch.object(fixtures.Locator, 'locator', scoped_locator)
        replacement.start()
        self.addCleanup(replacement.stop)
        self.ready('x', fixtures.WORK)
        self.original_receipt = self.path.read_bytes()

    def untouched(self, page):
        self.assertEqual([], page.events)
        self.assertEqual(self.original_receipt, self.path.read_bytes())

    async def test_exact_delayed_identity_including_deadline_precedes_any_content_read(self):
        for ready_at in (2500, 5000, 30000):
            with self.subTest(ready_at=ready_at):
                page = IdentityReadbackPage(self.marked, ready_at)
                self.assertEqual((None, True), await route.exact(page, self.target))
                self.assertEqual(ready_at, page.elapsed)
                self.assertTrue(page.content_reads)
                self.assertTrue(all(t >= ready_at for t in page.content_reads))
                self.untouched(page)

    async def test_exact_missing_identity_or_after_deadline_stops_without_content_read(self):
        for ready_at in (None, 30500):
            with self.subTest(ready_at=ready_at):
                page = IdentityReadbackPage(self.marked, ready_at)
                with self.assertRaises(VpubError) as caught:
                    await route.exact(page, self.target)
                self.assertEqual('wrong_account', caught.exception.code)
                self.assertEqual(30000, page.elapsed)
                self.assertEqual([], page.content_reads)
                self.untouched(page)

    async def test_explicit_wrong_identity_is_refused_on_first_observation(self):
        for ready_at in (2000, 5000):
            with self.subTest(ready_at=ready_at):
                page = IdentityReadbackPage(self.marked, ready_at)
                page.signed_identity = 'other_owner'
                with self.assertRaises(VpubError) as caught:
                    await route.exact(page, self.target)
                self.assertEqual('wrong_account', caught.exception.code)
                self.assertEqual(ready_at, page.elapsed)
                self.assertEqual([], page.content_reads)
                self.untouched(page)

    async def test_observed_wrong_identity_cannot_be_discarded_by_immediate_second_read(self):
        page = IdentityReadbackPage(self.marked, 2000)
        identities = iter(['other_owner', fixtures.OWNER, fixtures.OWNER])
        async def identity(*args):
            return next(identities, fixtures.OWNER)
        with patch.object(runtime, 'identity', side_effect=identity), self.assertRaises(VpubError) as caught:
            await route.exact(page, self.target)
        self.assertEqual('wrong_account', caught.exception.code)
        self.assertEqual([], page.content_reads)
        self.untouched(page)

    async def test_profile_delayed_identity_precedes_empty_state_and_remains_bounded(self):
        for ready_at in (4000, 7000, 49000):
            with self.subTest(ready_at=ready_at):
                page = IdentityReadbackPage(self.marked, ready_at)
                result = await route.absence(page, fixtures.OWNER, self.target, True, 0)
                self.assertTrue(state.verified(result, self.target))
                self.assertEqual(ready_at, page.profile_elapsed)
                self.assertTrue(page.content_reads)
                self.assertTrue(all(t >= ready_at for t in page.content_reads))
                self.untouched(page)

    async def test_profile_missing_identity_returns_pending_without_empty_state_read(self):
        page = IdentityReadbackPage(self.marked, None)
        result = await route.absence(page, fixtures.OWNER, self.target, True, 0)
        self.assertFalse(state.verified(result, self.target))
        self.assertFalse(result['fresh'] or result['list_complete'] or result['list_absent'])
        self.assertLessEqual(page.profile_elapsed, 50500)
        self.assertEqual([], page.content_reads)
        self.untouched(page)

    async def test_profile_explicit_wrong_identity_does_not_wait_or_read_content(self):
        page = IdentityReadbackPage(self.marked, 2500)
        page.signed_identity = 'other_owner'
        result = await route.absence(page, fixtures.OWNER, self.target, True, 0)
        self.assertFalse(state.verified(result, self.target))
        self.assertFalse(result['fresh'])
        self.assertEqual([2500], page.profile_waits)
        self.assertEqual([], page.content_reads)
        self.untouched(page)

    async def test_profile_wrong_identity_cannot_be_ignored_before_profile_current(self):
        page = IdentityReadbackPage(self.marked, 2500)
        identities = iter(['other_owner', fixtures.OWNER, fixtures.OWNER])
        async def identity(*args):
            return next(identities, fixtures.OWNER)
        with patch.object(runtime, 'identity', side_effect=identity):
            result = await route.absence(page, fixtures.OWNER, self.target, True, 0)
        self.assertFalse(state.verified(result, self.target))
        self.assertFalse(result['fresh'])
        self.assertEqual([], page.content_reads)
        self.untouched(page)


if __name__ == '__main__':
    unittest.main()

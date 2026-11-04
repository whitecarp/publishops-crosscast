"""Offline loading races for the native X deletion route; no real waits or pages."""
from urllib.parse import urlsplit
import unittest

import test_work_delete_x as fixtures
from video_publisher import work_delete_x as route, work_delete_state as state
from video_publisher.core import VpubError, atomic_json, home, read_json, stop_check


class LoadingPage(fixtures.XPage):
    """Reuse the dynamic DOM and real route, advancing only requested wait time."""

    def __init__(self, marked, appear_at=5000):
        super().__init__(marked)
        self.appear_at = appear_at
        self.exact_waits = []
        self.elapsed = 0
        self.on_wait = None
        self.edit_loaded = None
        self.loading_exact = False

    async def goto(self, url, **kwargs):
        await super().goto(url, **kwargs)
        self.loading_exact = '/status/' in urlsplit(url).path
        if self.loading_exact:
            self.loaded = self.tree
            if self.edit_loaded:
                self.edit_loaded(self.loaded)
            self.tree = fixtures.Node()
            self.elapsed = 0
            self.exact_waits.append([])

    async def wait_for_timeout(self, milliseconds):
        if not self.loading_exact:
            return
        self.exact_waits[-1].append(milliseconds)
        self.elapsed += milliseconds
        # Even the account navigation is initially loading. Keep the existing
        # initial two-second allowance; subsequent identity checks stay real.
        if self.elapsed >= 2000 and not self.tree.children:
            self.tree.children.append(self.navigation())
        if self.appear_at is not None and self.elapsed >= self.appear_at:
            self.tree = self.loaded
        if self.on_wait:
            self.on_wait(self)


class XLoadingTests(fixtures.OfflineCase):
    def setUp(self):
        super().setUp()
        self.reset_loading()

    def reset_loading(self, appear_at=5000):
        self.ready('x', fixtures.WORK)
        self.page = LoadingPage(self.marked, appear_at)
        self.original_receipt = self.path.read_bytes()

    async def execute(self, operation='delete'):
        return await route.execute(self.page, fixtures.OWNER, self.target, operation, str(self.path))

    def untouched(self):
        self.assertEqual([], self.page.events)
        self.unpressed(self.page)
        self.assertEqual(self.original_receipt, self.path.read_bytes())

    async def rejected(self, code):
        with self.assertRaises(VpubError) as caught:
            await self.execute()
        self.assertEqual(code, caught.exception.code)
        self.assertEqual(1, len(self.page.visits))
        self.untouched()

    async def test_delayed_exact_owned_article_is_accepted_including_deadline(self):
        for appear_at in (2500, 5000, 30000):
            with self.subTest(appear_at=appear_at):
                self.reset_loading(appear_at)
                result = await self.execute('inspect')
                self.assertTrue(result['present'] and result['owned'])
                self.assertEqual((fixtures.WORK, fixtures.OWNER), (result['id'], result['identity']))
                self.assertFalse(result['page_gone'])
                self.assertEqual(0, result['platform_writes'])
                self.assertEqual([2000] + [500] * ((appear_at - 2000) // 500), self.page.exact_waits[0])
                self.assertEqual(1, len(self.page.visits))
                self.untouched()

    async def test_delayed_delete_keeps_durable_gate_and_one_final_press(self):
        result = await self.execute()
        self.assertEqual(['target-more', 'menu-delete', 'final-delete'], self.page.events)
        self.assertEqual(1, result['platform_writes'])
        self.assertEqual([5000, 5000], [sum(waits) for waits in self.page.exact_waits])
        self.assertTrue(state.verified(result, self.target))
        self.marked()

    async def test_timeout_is_bounded_and_never_claims_exact_absence_or_clicks(self):
        self.reset_loading(None)
        row, gone = await route.exact(self.page, self.target)
        self.assertIsNone(row)
        self.assertFalse(gone)
        self.assertEqual(30000, sum(self.page.exact_waits[0]))
        self.untouched()
        self.reset_loading(None)
        result = await self.execute()
        self.assertEqual(30000, sum(self.page.exact_waits[0]))
        self.assertFalse(result['page_gone'])
        self.assertFalse(state.verified(result, self.target))
        self.assertEqual(0, result['platform_writes'])
        self.untouched()

    async def test_wrong_or_missing_account_during_wait_rejects(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                self.reset_loading()
                def change(page):
                    if page.elapsed >= 2500:
                        page.signed_identity = 'someone_else'
                        page.tree.children = [] if missing else [page.navigation()]
                self.page.on_wait = change
                await self.rejected('wrong_account')
                # A sidebar still loading may be absent until the bounded wait;
                # a positively different account must stop immediately.
                self.assertEqual(route.EXACT_WAIT_MILLISECONDS if missing else 2500, self.page.elapsed)
                self.untouched()

    async def test_address_drift_during_wait_rejects(self):
        exact = f'https://x.com/{fixtures.OWNER}/status/{fixtures.WORK}'
        for address in (exact.replace('x.com', 'other.invalid'), exact.replace('https:', 'http:'),
                        exact.replace(fixtures.OWNER, 'someone_else'), exact + '?other=1',
                        exact + '#other', exact.replace(fixtures.WORK, '2111111111111111112')):
            with self.subTest(address=address):
                self.reset_loading()
                def change(page):
                    if page.elapsed >= 2500:
                        page.url = address
                self.page.on_wait = change
                await self.rejected('wrong_work')
                self.assertEqual(2500, self.page.elapsed)

    async def test_multiple_delayed_target_articles_reject(self):
        self.page.edit_loaded = lambda tree: tree.children.append(fixtures.post())
        await self.rejected('wrong_work')
        self.assertEqual(5000, self.page.elapsed)

    async def test_delayed_foreign_id_author_or_ad_is_not_owned(self):
        for field, value in (('author', 'someone_else'), ('row_id', '2111111111111111112'), ('ad', True)):
            with self.subTest(field=field):
                self.reset_loading()
                setattr(self.page, field, value)
                await self.rejected('owner_unverified')
                self.assertEqual(5000, self.page.elapsed)

    async def test_operator_stop_during_wait_blocks_later_article(self):
        def stop(page):
            if page.elapsed == 2500:
                atomic_json(home() / 'STOP.json', {'reason': 'Synthetic stop while loading'})
        self.page.on_wait = stop
        await self.rejected('stopped')
        self.assertEqual(2500, self.page.elapsed)
        self.assertEqual('Synthetic stop while loading', read_json(home() / 'STOP.json')['reason'])

    async def test_platform_check_during_wait_latches_global_stop(self):
        def challenge(page):
            if page.elapsed == 2500:
                page.tree.children.append(fixtures.Node('Verify your identity'))
        self.page.on_wait = challenge
        await self.rejected('human_verification_required')
        self.assertEqual(2500, self.page.elapsed)
        saved = read_json(home() / 'STOP.json')
        self.assertEqual(('x', 'human_verification_required'), (saved['platform'], saved['code']))
        with self.assertRaises(VpubError) as caught:
            stop_check('youtube')
        self.assertEqual('stopped', caught.exception.code)
        self.assertEqual(saved, read_json(home() / 'STOP.json'))

    async def test_delayed_explicit_gone_and_complete_empty_profile_need_no_click(self):
        self.page.present = False
        result = await self.execute()
        self.assertEqual(5000, sum(self.page.exact_waits[0]))
        self.assertTrue(state.verified(result, self.target))
        self.assertEqual(0, result['platform_writes'])
        self.untouched()

    async def test_delayed_hidden_unscoped_reply_or_generic_words_do_not_prove_gone(self):
        for kind in ('hidden', 'unscoped', 'reply', 'generic'):
            with self.subTest(kind=kind):
                self.reset_loading()
                self.page.present = False
                if kind == 'hidden':
                    self.page.exact_message_visible = False
                elif kind == 'generic':
                    self.page.exact_message = 'Something went wrong. Try reloading.'
                else:
                    def replace_label(tree):
                        if kind == 'unscoped':
                            tree.children[1].selectors.clear()
                        else:
                            tree.children[1:] = [fixtures.post(wid='2111111111111111112', text='This Post was deleted.')]
                    self.page.edit_loaded = replace_label
                result = await self.execute()
                self.assertEqual(30000, sum(self.page.exact_waits[0]))
                self.assertFalse(result['page_gone'])
                self.assertFalse(state.verified(result, self.target))
                self.assertEqual(0, result['platform_writes'])
                self.untouched()

    async def test_delayed_gone_with_nonempty_profile_still_cannot_certify_completion(self):
        self.page.present = False
        self.page.profile_message = ''
        self.page.profile_posts = [fixtures.post(wid='2111111111111111112')]
        result = await self.execute()
        self.assertTrue(result['page_gone'] and result['fresh'] and result['list_absent'])
        self.assertFalse(result['list_complete'])
        self.assertFalse(state.verified(result, self.target))
        self.assertEqual(0, result['platform_writes'])
        self.untouched()
        self.assertEqual('DELETE_PENDING', state.save_result(self.path, self.target, result)['state'])

    async def test_identity_or_address_changed_during_delayed_read_rejects_before_return(self):
        for gone in (False, True):
            for change_account in (False, True):
                with self.subTest(gone=gone, change_account=change_account):
                    self.reset_loading()
                    self.page.present = not gone
                    fired = []
                    def change(kind, nodes):
                        if fired or kind != ('text' if gone else 'post'):
                            return
                        fired.append(kind)
                        if change_account:
                            self.page.signed_identity = 'someone_else'
                            self.page.tree.children[0] = self.page.navigation()
                        else:
                            self.page.url += '?changed=1'
                    self.page.after_read = change
                    await self.rejected('wrong_account' if change_account else 'wrong_work')
                    self.assertEqual(['text' if gone else 'post'], fired)


if __name__ == '__main__':
    unittest.main()

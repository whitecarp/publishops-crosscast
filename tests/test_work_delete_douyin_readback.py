"""Read-only deletion recovery regressions; synthetic pages, no live accounts."""
import unittest
from unittest.mock import AsyncMock, patch

from test_work_delete_douyin import FakeDouyin, OWNER, OTHER, WORK
from test_work_delete_youtube import Node, OfflineCase
from video_publisher import work_delete_douyin as route, work_delete_state as state
from video_publisher.core import VpubError


class DouyinReadbackTests(OfflineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.seed('douyin', WORK, OWNER)
        self.page = FakeDouyin(self.target)

    async def verify(self):
        return await route.execute(self.page, OWNER, self.target, 'verify', self.path)

    async def test_absent_target_opens_each_other_card_once_and_keeps_zero_writes(self):
        self.page.deleted = True
        self.page.cards = [(OTHER, 'Other')]
        with patch.object(self.page, 'goto', wraps=self.page.goto) as visits:
            answer = await self.verify()
        self.assertEqual([self.target['url'], route.bb.MANAGE],
                         [call.args[0] for call in visits.call_args_list])
        self.assertEqual(1, len(self.page.opened))
        self.assertTrue(state.verified(answer, self.target))
        self.assertEqual(0, answer['platform_writes'])
        self.assert_unpressed()

    async def test_present_target_still_reports_owned_without_delete_controls(self):
        answer = await self.verify()
        self.assertTrue(answer['present'] and answer['owned'])
        self.assertFalse(state.verified(answer, self.target))
        self.assertEqual(1, len(self.page.opened))
        self.assert_unpressed()

    async def test_delayed_deleted_notice_is_read_within_one_command(self):
        self.page.deleted = True
        self.page.gone_text = ''
        waits = []

        async def render(ms):
            waits.append(ms)
            if len(waits) == 3:
                self.page.nodes = [Node('alert', '该作品已被删除')]

        self.page.wait_for_timeout = render
        answer = await self.verify()
        self.assertEqual([500] * 3, waits)
        self.assertTrue(state.verified(answer, self.target))
        self.assertIn('exact_page=deleted_notice', answer['note'])
        self.assert_unpressed()

    async def test_missing_notice_has_a_finite_wait_and_no_success(self):
        self.page.deleted = True
        self.page.gone_text = 'Private unrecognized page text'
        self.page.wait_for_timeout = AsyncMock()
        answer = await self.verify()
        self.assertEqual(route.GONE_WAIT_SECONDS * 2,
                         self.page.wait_for_timeout.await_count)
        self.assertFalse(state.verified(answer, self.target))
        self.assertIn('exact_page=unrecognized_alert', answer['note'])
        self.assertNotIn('Private unrecognized', str(answer))
        self.assert_unpressed()

    async def test_query_and_redirect_refuse_without_exposing_url_or_waiting(self):
        cases = ((self.target['url'] + '?token=private-secret', 'query_or_fragment'),
                 (self.target['url'] + '#private-secret', 'query_or_fragment'),
                 ('https://other.invalid/private-secret', 'wrong_origin'),
                 ('https://www.douyin.com/video/' + OTHER, 'wrong_path'))
        for address, reason in cases:
            with self.subTest(reason=reason, address=address):
                self.page.deleted = True
                self.page.redirect_watch = address
                self.page.wait_for_timeout = AsyncMock()
                answer = await self.verify()
                self.assertIn('exact_page=' + reason, answer['note'])
                self.assertNotIn('private-secret', str(answer))
                self.assertFalse(answer['page_gone'])
                self.page.wait_for_timeout.assert_not_awaited()
                self.assert_unpressed()

    async def test_redirect_during_notice_wait_never_confirms_another_work(self):
        self.page.deleted = True
        self.page.gone_text = ''

        async def redirect(ms):
            self.page.url = 'https://www.douyin.com/video/' + OTHER
            self.page.nodes = [Node('alert', '该作品已被删除')]

        self.page.wait_for_timeout = redirect
        answer = await self.verify()
        self.assertFalse(answer['page_gone'])
        self.assertIn('exact_page=wrong_path', answer['note'])
        self.assert_unpressed()

    async def test_stop_during_notice_wait_is_not_swallowed(self):
        self.page.deleted = True
        self.page.gone_text = ''

        async def stop(ms):
            self.stopped()

        self.page.wait_for_timeout = stop
        with self.assertRaises(VpubError):
            await self.verify()
        self.assert_unpressed()

    async def test_risk_during_notice_wait_is_not_swallowed(self):
        self.page.deleted = True
        self.page.gone_text = ''

        async def challenge(ms):
            self.page.risk = True

        self.page.wait_for_timeout = challenge
        with self.assertRaises(VpubError) as caught:
            await self.verify()
        self.assertEqual('human_verification_required', caught.exception.code)
        self.assert_unpressed()

    async def test_private_unknown_id_keeps_absence_pending_and_explains_why(self):
        self.page.deleted = True
        self.page.cards = [(OTHER, 'Private unrelated title')]
        self.page.private = {OTHER}
        answer = await self.verify()
        self.assertTrue(answer['page_gone'])
        self.assertFalse(answer['list_absent'] or answer['list_complete'])
        self.assertIn('list=unresolved_id', answer['note'])
        self.assertIn('private_unresolved=1', answer['note'])
        self.assertNotIn('Private unrelated title', str(answer))
        self.assertEqual([], self.page.opened)
        self.assert_unpressed()

    async def test_missing_terminal_is_distinct_from_unresolved_ids(self):
        self.page.deleted = True
        self.page.show_terminal = False
        self.page.cards = [(OTHER, 'Known')]
        answer = await self.verify()
        self.assertIn('list=terminal_or_filter_unconfirmed', answer['note'])
        self.assertIn('resolved=1; private_unresolved=0', answer['note'])
        self.assertFalse(state.verified(answer, self.target))
        self.assert_unpressed()


if __name__ == '__main__':
    unittest.main()

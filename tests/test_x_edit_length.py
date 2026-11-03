"""Final X edit text is checked before opening the editor; readbacks remain reads."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import browser_edits, edits, operations_browser
from video_publisher.core import VpubError, atomic_json, home, read_json, x_weighted_length


class XEditLengthTests(unittest.IsolatedAsyncioTestCase):
    def patched(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.patched(patch.dict(os.environ, {
            'VPUB_HOME': str(self.root / 'vpub'),
            'BILI_CLI_HOME': str(self.root / 'bili'),
        }))
        self.receipt_path = self.root / 'edit.json'
        self.current_text = 'Old caption\n\nAI-generated'
        self.filled_text = None
        self.page = MagicMock()
        self.page.url = 'https://x.com/owner/status/123'
        self.page.goto = AsyncMock()
        self.page.reload = AsyncMock()
        self.page.wait_for_timeout = AsyncMock()
        self.post = MagicMock()
        self.post.first = self.post
        self.post.wait_for = AsyncMock()
        self.post.count = AsyncMock(return_value=1)
        self.edit = MagicMock()
        self.edit.click = AsyncMock()
        self.page.get_by_role.return_value = self.edit
        self.window = MagicMock()
        self.window.first = self.window
        self.window.wait_for = AsyncMock()
        self.page.locator.return_value = self.window
        self.editor = MagicMock()
        self.editor.wait_for = AsyncMock()
        self.window.locator.return_value = self.editor
        self.update = MagicMock()
        self.update.is_enabled = AsyncMock(return_value=True)
        self.update.click = AsyncMock(side_effect=self.press_update)
        self.window.get_by_role.return_value = self.update
        self.patched(patch.object(browser_edits.x_pages, 'post_row', return_value=self.post))
        self.fields = self.patched(patch.object(browser_edits.x_pages, 'post_fields', new=AsyncMock(
            side_effect=lambda post: {'text': self.current_text, 'id': '123', 'author': 'owner', 'is_ad': False})))
        self.open_menu = self.patched(patch.object(browser_edits.x_pages, 'open_menu', new_callable=AsyncMock))
        self.patched(patch.object(browser_edits, 'unique_visible', new=AsyncMock(
            side_effect=lambda locator, label: locator)))
        self.patched(patch.object(browser_edits, 'visible', new=AsyncMock(
            side_effect=lambda locator: locator)))
        self.risk = self.patched(patch.object(browser_edits, 'risk_check', new_callable=AsyncMock))
        self.patched(patch.object(browser_edits, 'stage'))
        self.fill = self.patched(patch.object(operations_browser, 'fill_x_text', new=AsyncMock(
            side_effect=self.fill_text)))

    async def fill_text(self, page, editor, text):
        self.filled_text = text

    async def press_update(self, **kwargs):
        self.current_text = self.filled_text

    def adopted_patch(self, length):
        return edits.validate_patch('x', {
            'expected_identity': 'owner', 'title': 'T', 'description': 'a' * (length - 3),
            'content_language': 'en', 'metadata_language': 'en', 'metadata_mode': 'target_only',
        }, False, None)

    async def execute(self, proposed, verify_only=False):
        return await browser_edits.execute(self.page, 'x', 'owner', {
            'patch': proposed, 'receipt': {'id': '123'},
            'edit_receipt_path': str(self.receipt_path), 'verify_only': verify_only,
        })

    def assert_no_edit(self):
        self.open_menu.assert_not_awaited()
        self.edit.click.assert_not_awaited()
        self.fill.assert_not_awaited()
        self.update.click.assert_not_awaited()

    async def test_preserved_ai_line_pushes_an_accepted_edit_over_the_limit(self):
        proposed = self.adopted_patch(280)
        with self.assertRaises(VpubError) as caught:
            await self.execute(proposed)
        self.assertEqual('x_text_too_long', caught.exception.code)
        self.assertIn('294', caught.exception.message)
        self.assert_no_edit()
        self.assertFalse(self.receipt_path.exists())

    async def test_one_over_the_final_boundary_stops_before_opening_the_editor(self):
        with self.assertRaises(VpubError) as caught:
            await self.execute(self.adopted_patch(267))
        self.assertEqual('x_text_too_long', caught.exception.code)
        self.assertIn('281', caught.exception.message)
        self.assert_no_edit()

    async def test_exact_final_boundary_saves_once_with_the_ai_line_intact(self):
        proposed = self.adopted_patch(266)
        result = await self.execute(proposed)
        self.assertEqual(proposed['post_text'] + '\n\nAI-generated', self.current_text)
        self.assertEqual(280, x_weighted_length(self.current_text))
        self.open_menu.assert_awaited_once()
        self.edit.click.assert_awaited_once()
        self.update.click.assert_awaited_once()
        self.assertEqual(('EDIT_VERIFIED', 1), (result['state'], result['platform_writes']))
        self.assertEqual('EDIT_VERIFIED', read_json(self.receipt_path)['state'])

    async def test_unchanged_oversized_post_does_not_count_or_press(self):
        proposed = {'post_text': 'a' * 300, 'keep_ai_line': True}
        self.current_text = proposed['post_text'] + '\n\nAI-generated'
        with patch.object(browser_edits, 'x_weighted_length', side_effect=AssertionError('Read only')):
            result = await self.execute(proposed)
        self.assertEqual(('EDIT_VERIFIED', True, 0),
                         (result['state'], result['unchanged'], result['platform_writes']))
        self.assert_no_edit()

    async def test_matching_oversized_verification_does_not_count_or_write(self):
        proposed = {'post_text': 'a' * 300, 'keep_ai_line': True}
        self.current_text = proposed['post_text'] + '\n\nAI-generated'
        with patch.object(browser_edits, 'x_weighted_length', side_effect=AssertionError('Read only')):
            result = await self.execute(proposed, verify_only=True)
        self.assertEqual(('EDIT_VERIFIED', 0), (result['state'], result['platform_writes']))
        self.assert_no_edit()
        self.assertFalse(self.receipt_path.exists())

    async def test_mismatching_oversized_verification_retains_the_existing_receipt(self):
        original = b'{"state":"EDIT_SUBMITTING"}'
        self.receipt_path.write_bytes(original)
        with patch.object(browser_edits, 'x_weighted_length', side_effect=AssertionError('Read only')):
            result = await self.execute({'post_text': 'a' * 300, 'keep_ai_line': True}, verify_only=True)
        self.assertEqual(('EDIT_SUBMITTED_UNVERIFIED', 0), (result['state'], result['platform_writes']))
        self.assert_no_edit()
        self.assertEqual(original, self.receipt_path.read_bytes())

    async def test_existing_hashtags_and_bilingual_ai_survive_a_replacement(self):
        self.current_text = 'Old title\n\nOld text\n\n#Game #游戏\n\nAI 生成内容 / AI-generated'
        proposed = self.adopted_patch(12)
        result = await self.execute(proposed)
        expected = proposed['post_text'] + '\n\n#Game #游戏\n\nAI 生成内容 / AI-generated'
        self.assertEqual(expected, self.filled_text)
        self.assertEqual(expected, result['expected_text'])
        self.assertEqual('EDIT_VERIFIED', result['state'])
        self.update.click.assert_awaited_once()

    async def test_explicit_tags_replace_the_block_but_keep_the_existing_ai_wording(self):
        self.current_text = 'Old\n\n#Old\n\nAI 生成內容 / AI-generated'
        proposed = self.adopted_patch(12)
        proposed['tags'] = ['New']
        result = await self.execute(proposed)
        self.assertEqual(proposed['post_text'] + '\n\n#New\n\nAI 生成內容 / AI-generated', result['expected_text'])

    async def test_preserved_hashtags_count_before_any_editor_or_final_press(self):
        self.current_text = 'Old\n\n#' + 'g' * 20
        with self.assertRaises(VpubError) as caught:
            await self.execute(self.adopted_patch(260))
        self.assertEqual('x_text_too_long', caught.exception.code)
        self.assert_no_edit()

    async def test_matching_foreign_or_quoted_post_cannot_verify_or_edit(self):
        proposed = self.adopted_patch(12)
        for changes in ({'author': 'other'}, {'id': '456'}, {'is_ad': True}, {'text': None}):
            with self.subTest(changes=changes):
                self.fields.side_effect = None
                self.fields.return_value = {'text': proposed['post_text'], 'id': '123', 'author': 'owner', 'is_ad': False, **changes}
                result = await self.execute(proposed)
                self.assertEqual(('EDIT_SUBMITTED_UNVERIFIED', 0), (result['state'], result['platform_writes']))
                self.assert_no_edit()

    async def test_ambiguous_owned_post_is_not_accepted(self):
        self.post.count.return_value = 2
        result = await self.execute(self.adopted_patch(12))
        self.assertEqual('ambiguous_post', result['reason'])
        self.assert_no_edit()

    async def test_redirect_to_matching_new_id_is_not_a_version_chain(self):
        self.page.url = 'https://x.com/owner/status/456'
        self.current_text = self.adopted_patch(12)['post_text']
        result = await self.execute(self.adopted_patch(12), verify_only=True)
        self.assertEqual(('EDIT_SUBMITTED_UNVERIFIED', 'version_chain_unavailable', 0),
                         (result['state'], result['reason'], result['platform_writes']))
        self.fields.assert_not_awaited()
        self.assert_no_edit()
        self.assertFalse(self.receipt_path.exists())

    async def test_foreign_host_owner_or_path_never_verifies_matching_words(self):
        for url in ('https://example.com/owner/status/123', 'https://x.com/other/status/123',
                    'https://x.com/owner/status/123/history', 'https://x.com/home'):
            with self.subTest(url=url):
                self.page.url = url
                result = await self.execute(self.adopted_patch(12), verify_only=True)
                self.assertEqual('wrong_work_address', result['reason'])
                self.assert_no_edit()

    async def test_new_id_after_one_update_stays_unverified_without_more_presses(self):
        async def new_version(**kwargs):
            self.current_text = self.filled_text
            self.page.url = 'https://x.com/owner/status/456'
        self.update.click.side_effect = new_version
        result = await self.execute(self.adopted_patch(12))
        self.assertEqual(('EDIT_SUBMITTED_UNVERIFIED', 'version_chain_unavailable', 1),
                         (result['state'], result['reason'], result['platform_writes']))
        self.update.click.assert_awaited_once()
        self.assertEqual('123', read_json(self.receipt_path)['id'])

    async def test_unchanged_old_version_after_update_remains_unverified(self):
        self.update.click.side_effect = None
        result = await self.execute(self.adopted_patch(12))
        self.assertEqual(('EDIT_SUBMITTED_UNVERIFIED', 'post_text_mismatch'), (result['state'], result['reason']))
        self.update.click.assert_awaited_once()
        self.assertEqual(browser_edits.READ_BACKS, self.page.reload.await_count)

    async def test_missing_post_parser_evidence_after_update_remains_unverified(self):
        async def fields(post):
            if self.update.click.await_count:
                raise ValueError('Unrecognized fake page')
            return {'text': self.current_text, 'id': '123', 'author': 'owner', 'is_ad': False}
        self.fields.side_effect = fields
        result = await self.execute(self.adopted_patch(12))
        self.assertEqual(('EDIT_SUBMITTED_UNVERIFIED', 'post_readback_unavailable'), (result['state'], result['reason']))
        self.update.click.assert_awaited_once()

    async def test_post_changes_during_edit_prevents_the_final_press(self):
        async def fields(post):
            return {'text': 'External edit' if self.fill.await_count else self.current_text,
                    'id': '123', 'author': 'owner', 'is_ad': False}
        self.fields.side_effect = fields
        with self.assertRaises(VpubError) as caught:
            await self.execute(self.adopted_patch(12))
        self.assertEqual('wrong_work', caught.exception.code)
        self.update.click.assert_not_awaited()

    async def test_editor_modal_route_does_not_replace_the_owned_post_binding(self):
        async def opened():
            self.page.url = 'https://x.com/compose/post'
        async def updated(**kwargs):
            self.current_text = self.filled_text
            self.page.url = 'https://x.com/owner/status/123'
        self.edit.click.side_effect = opened
        self.update.click.side_effect = updated
        result = await self.execute(self.adopted_patch(12))
        self.assertEqual('EDIT_VERIFIED', result['state'])
        self.update.click.assert_awaited_once()

    async def test_navigation_during_identity_read_cannot_verify_stale_matching_fields(self):
        proposed = self.adopted_patch(12)
        async def fields(post):
            self.page.url = 'https://x.com/owner/status/456'
            return {'text': proposed['post_text'], 'id': '123', 'author': 'owner', 'is_ad': False}
        self.fields.side_effect = fields
        result = await self.execute(proposed, verify_only=True)
        self.assertEqual('version_chain_unavailable', result['reason'])
        self.assert_no_edit()

    async def test_stop_during_last_ownership_read_prevents_the_final_press(self):
        async def fields(post):
            if self.fill.await_count:
                atomic_json(home() / 'STOP.json', {'reason': 'platform_warning'})
            return {'text': self.current_text, 'id': '123', 'author': 'owner', 'is_ad': False}
        self.fields.side_effect = fields
        with self.assertRaises(VpubError):
            await self.execute(self.adopted_patch(12))
        self.update.click.assert_not_awaited()
        self.assertEqual('platform_warning', read_json(home() / 'STOP.json')['reason'])

    async def test_warning_readback_keeps_the_pending_press_and_does_not_repeat(self):
        async def warning(page):
            if self.update.click.await_count:
                atomic_json(home() / 'STOP.json', {'reason': 'platform_warning'})
                raise VpubError('human_verification_required', 'Fake warning')
        self.risk.side_effect = warning
        with self.assertRaises(VpubError):
            await self.execute(self.adopted_patch(12))
        self.update.click.assert_awaited_once()
        self.assertEqual('EDIT_SUBMITTING', read_json(self.receipt_path)['state'])
        self.assertTrue((home() / 'STOP.json').exists())

    async def test_verify_uses_frozen_attempt_instead_of_changed_hashtag_block(self):
        proposed = self.adopted_patch(12)
        attempted = proposed['post_text'] + '\n\n#Original'
        atomic_json(self.receipt_path, {'id': '123', 'identity': 'owner', 'platform': 'x',
                                      'state': 'EDIT_SUBMITTING', 'expected_text': attempted})
        original = self.receipt_path.read_bytes()
        self.current_text = proposed['post_text'] + '\n\n#ExternalChange'
        result = await self.execute(proposed, verify_only=True)
        self.assertEqual('EDIT_SUBMITTED_UNVERIFIED', result['state'])
        self.assertEqual(attempted, result['expected_text'])
        self.assertEqual(original, self.receipt_path.read_bytes())
        self.assert_no_edit()


if __name__ == '__main__':
    unittest.main()

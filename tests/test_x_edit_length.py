"""Final X edit text is checked before opening the editor; readbacks remain reads."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import browser_edits, edits, operations_browser
from video_publisher.core import VpubError, read_json, x_weighted_length


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
        self.page.goto = AsyncMock()
        self.page.reload = AsyncMock()
        self.page.wait_for_timeout = AsyncMock()
        self.post = MagicMock()
        self.post.first = self.post
        self.post.wait_for = AsyncMock()
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
        self.patched(patch.object(browser_edits.x_pages, 'post_fields', new=AsyncMock(
            side_effect=lambda post: {'text': self.current_text})))
        self.open_menu = self.patched(patch.object(browser_edits.x_pages, 'open_menu', new_callable=AsyncMock))
        self.patched(patch.object(browser_edits, 'unique_visible', new=AsyncMock(
            side_effect=lambda locator, label: locator)))
        self.patched(patch.object(browser_edits, 'visible', new=AsyncMock(
            side_effect=lambda locator: locator)))
        self.patched(patch.object(browser_edits, 'risk_check', new_callable=AsyncMock))
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


if __name__ == '__main__':
    unittest.main()

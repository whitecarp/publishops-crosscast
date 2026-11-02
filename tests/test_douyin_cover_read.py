"""Offline cover-editor regressions: real field/URL guards, fake page, no browser."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import browser_backends, browser_edits
from video_publisher.core import VpubError, file_sha, read_json


class DouyinCoverReadTests(unittest.IsolatedAsyncioTestCase):
    WORK = "7300000000000000001"
    TITLE = "Original title"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.use(patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")}))
        portrait = self.root / "portrait.jpg"
        portrait.write_bytes(b"synthetic cover")
        self.output = self.root / "edit.json"
        self.payload = {
            "patch": {"portrait": str(portrait), "portrait_sha256": file_sha(portrait)},
            "receipt": {"id": self.WORK, "title": self.TITLE},
            "edit_receipt_path": str(self.output),
        }
        self.loads = [(self.TITLE, None), (self.TITLE, None)]
        self.redirects = {}
        self.visits, self.waits, self.captured = 0, [], []
        self.held = False
        self.page = MagicMock()
        self.page.goto = AsyncMock(side_effect=self.goto)
        self.page.wait_for_timeout = AsyncMock(side_effect=self.wait)
        self.title, self.description, self.control = (self.node() for _ in range(3))
        self.title.input_value = AsyncMock(side_effect=lambda: self.value)
        tips = MagicMock()
        tip = self.node()
        tip.inner_text.return_value = "作品修改审核中，暂不支持修改"
        tips.all = AsyncMock(side_effect=lambda: [tip] if self.held else [])
        self.page.locator.side_effect = lambda selector: {
            'input[placeholder*="填写作品标题"]': self.title,
            '[contenteditable=true]': self.description,
            '[class^="modify-tip-"]': tips,
        }.get(selector, self.control)
        self.page.get_by_role.return_value = self.control
        self.use(patch.object(browser_backends, "risk_check", new=AsyncMock()))
        self.safety = self.use(patch.object(browser_edits, "risk_check", new=AsyncMock()))
        self.choose = self.use(patch.object(browser_edits, "choose_file", new=AsyncMock()))
        self.use(patch.object(browser_edits, "douyin_moved_on", new=AsyncMock(return_value=None)))
        self.capture = self.use(patch.object(browser_edits, "capture_cover", new=AsyncMock(side_effect=self.capture_now)))

    def use(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    @staticmethod
    def node():
        node = MagicMock()
        node.first = node
        node.locator.return_value = node.filter.return_value = node.get_by_role.return_value = node
        node.all = AsyncMock(return_value=[node])
        node.is_visible = AsyncMock(return_value=True)
        node.wait_for = AsyncMock()
        node.click = AsyncMock()
        node.inner_text = AsyncMock(return_value="保存")
        node.get_attribute = AsyncMock(return_value="")
        return node

    async def goto(self, url, **kwargs):
        self.visits += 1
        self.page.url = self.redirects.get(self.visits, url)
        self.value, self.pending = self.loads[self.visits - 1]

    async def wait(self, milliseconds):
        if milliseconds == 500:
            self.waits.append(self.visits)
            if self.pending is not None:
                self.value, self.pending = self.pending, None

    async def capture_now(self, page, control, output, value, save, url):
        self.captured.append(self.value)
        return save(state="EDIT_SUBMITTED_UNVERIFIED", url=url, readback_ready=True)

    async def edit(self):
        return await browser_edits.execute(self.page, "douyin", "owner", self.payload)

    async def assert_rejected_before_file(self, code):
        with self.assertRaises(VpubError) as caught:
            await self.edit()
        self.assertEqual(code, caught.exception.code)
        self.choose.assert_not_awaited()
        self.control.click.assert_not_awaited()
        self.capture.assert_not_awaited()
        self.assertFalse(self.output.exists())

    async def test_initially_empty_title_waits_for_the_receipt_title(self):
        self.loads[0] = ("", self.TITLE)
        result = await self.edit()
        self.assertEqual("EDIT_SUBMITTED_UNVERIFIED", result["state"])
        self.assertEqual([1], self.waits)
        self.choose.assert_awaited_once()
        self.assertEqual([self.TITLE], self.captured)

    async def test_actually_wrong_title_rejects_before_file_selection(self):
        self.loads[0] = ("Another title", None)
        await self.assert_rejected_before_file("wrong_work")

    async def test_title_equality_remains_exact(self):
        self.loads[0] = (self.TITLE + " ", None)
        await self.assert_rejected_before_file("wrong_work")

    async def test_wrong_work_url_rejects_even_with_the_right_title(self):
        self.redirects[1] = browser_backends.douyin_editor_address("7300000000000000002")
        await self.assert_rejected_before_file("wrong_work")

    async def test_empty_post_save_title_is_ready_before_capture(self):
        self.loads[1] = ("", self.TITLE)
        await self.edit()
        self.assertEqual([2], self.waits)
        self.assertEqual([self.TITLE], self.captured)
        self.choose.assert_awaited_once()

    async def test_post_save_wrong_url_is_not_captured_as_this_cover(self):
        self.redirects[2] = browser_backends.douyin_editor_address("7300000000000000002")
        with self.assertRaises(VpubError) as caught:
            await self.edit()
        self.assertEqual("wrong_work", caught.exception.code)
        self.choose.assert_awaited_once()
        self.capture.assert_not_awaited()
        self.assertEqual("EDIT_SUBMITTING", read_json(self.output)["state"])

    async def test_review_hold_still_rejects_before_file_selection(self):
        self.held = True
        await self.assert_rejected_before_file("edit_held_by_review")

    async def test_hold_arriving_during_final_safety_check_prevents_save(self):
        async def hold(page):
            self.held = True
        self.safety.side_effect = hold
        with self.assertRaises(VpubError) as caught:
            await self.edit()
        self.assertEqual("edit_held_by_review", caught.exception.code)
        self.choose.assert_awaited_once()
        # Only open the picker and finish the local choice; never press Save.
        self.assertEqual(2, self.control.click.await_count)
        self.capture.assert_not_awaited()
        self.assertFalse(self.output.exists())

    async def test_verify_only_waits_for_title_without_selecting_a_file(self):
        self.loads[0] = ("", self.TITLE)
        self.payload["verify_only"] = True
        await self.edit()
        self.assertEqual([self.TITLE], self.captured)
        self.choose.assert_not_awaited()
        self.control.click.assert_not_awaited()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()

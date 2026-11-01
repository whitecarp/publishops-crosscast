"""What a failed browser command keeps on this computer: the step and the page's controls, never what the page or its boxes held."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from video_publisher import browser_backends, browser_runtime as rt, browser_worker
from video_publisher.core import atomic_json, file_sha, home, read_json

SECRETS = ("PRIVATE-BODY-WORDS", "FIELD-VALUE-SECRET", "EDITOR-WORDS", "QUERY-VALUE-SECRET", "FRAGMENT-SECRET")
PAGE = """<!doctype html><body><p>PRIVATE-BODY-WORDS</p>
<input type="password" name="pass" aria-label="Password" value="FIELD-VALUE-SECRET"><textarea id="note">FIELD-VALUE-SECRET</textarea>
<div contenteditable="true" data-testid="box">EDITOR-WORDS</div><button aria-label="Send" id="go">Send</button>"""
ADDRESS = "https://x.com/some/path?token=QUERY-VALUE-SECRET#FRAGMENT-SECRET"


class AddressTests(unittest.TestCase):
    def test_only_the_place_is_kept(self):
        self.assertEqual("https://x.com/some/path", rt.plain_address(ADDRESS))
        self.assertEqual("https://x.com/a", rt.plain_address("https://user:pass@x.com/a"))
        self.assertEqual("", rt.plain_address("about:blank?x=1".replace("about:blank", "")))


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()

        async def answer(route):
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=PAGE)
        await self.page.route("**/*", answer)
        await self.page.goto(ADDRESS)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    def assert_nothing_private_in(self, path):
        text = Path(path).read_text(encoding="utf-8")
        for secret in SECRETS:
            self.assertNotIn(secret, text)

    async def fail(self, action, **payload):
        with patch("video_publisher.operations_browser.execute", new=AsyncMock(side_effect=RuntimeError("Locator.click: Timeout\n  - waiting for locator('#go') to be visible"))), \
                patch("video_publisher.collections_browser.execute", new=AsyncMock(side_effect=RuntimeError("Locator.click: Timeout"))), \
                patch("video_publisher.browser_backends.execute", new=AsyncMock(side_effect=RuntimeError("Locator.click: Timeout"))), \
                self.assertRaises(RuntimeError):
            await browser_worker.dispatch(self.page, "x", "me", {"action": action, **payload})


class FailureRecordTests(Base):
    async def test_a_failed_read_keeps_the_step_and_the_controls_and_nothing_they_held(self):
        await self.fail("operations", operation="comments")
        path = home() / "operations/errors/x-last-error.json"
        self.assert_nothing_private_in(path)
        saved = read_json(path)
        self.assertEqual(("comments", "https://x.com/some/path"), (saved["operation"], saved["url"]))
        self.assertIn("waiting for locator('#go')", saved["error"])
        self.assertTrue(any(control["testid"] == "box" for control in saved["controls"]))
        self.assertTrue(any(control["id"] == "go" for control in saved["controls"]))
        self.assertFalse(any("label" in control or "placeholder" in control for control in saved["controls"]))
        self.assertNotIn("text", saved)

    async def test_a_failed_edit_keeps_no_picture(self):
        receipt = self.root / "edit.json"
        await self.fail("edit", edit_receipt_path=str(receipt))
        self.assert_nothing_private_in(receipt.with_suffix(".error.json"))
        self.assertFalse(receipt.with_suffix(".error.png").exists())

    async def test_a_failed_publish_keeps_no_picture_and_its_receipt_keeps_a_plain_address(self):
        video = self.root / "video.mp4"
        video.write_bytes(b"fixture")
        plan = {"video": str(video), "video_sha256": file_sha(video), "confirmation": "a" * 64, "options": {"title": "T", "submission_mode": "publish"}}
        with patch("video_publisher.browser_forms.publish_x", new=AsyncMock(side_effect=RuntimeError("Locator.click: Timeout"))):
            result = await browser_backends.execute(self.page, "x", "me", {"action": "publish", "plan": plan})
        folder = home() / "browser-debug" / ("a" * 64 + "-x")
        self.assert_nothing_private_in(folder / "failure.json")
        self.assertFalse((folder / "last-page.png").exists())
        self.assertEqual("https://x.com/some/path", result["review_url"])
        self.assert_nothing_private_in(home() / "browser-receipts" / ("a" * 64 + "-x.json"))

    async def test_page_text_and_a_picture_only_if_the_person_turned_them_on_and_never_field_contents(self):
        atomic_json(home() / "config.json", {"page_diagnostics": True})
        receipt = self.root / "edit.json"
        await self.fail("edit", edit_receipt_path=str(receipt))
        saved = receipt.with_suffix(".error.json").read_text(encoding="utf-8")
        self.assertIn("PRIVATE-BODY-WORDS", saved)
        for secret in ("FIELD-VALUE-SECRET", "EDITOR-WORDS", "QUERY-VALUE-SECRET", "FRAGMENT-SECRET"):
            self.assertNotIn(secret, saved)
        self.assertTrue(receipt.with_suffix(".error.png").exists())
        self.assertIn("Password", saved)

    async def test_the_picture_has_form_fields_covered_in_every_frame(self):
        atomic_json(home() / "config.json", {"page_diagnostics": True})
        receipt = self.root / "edit.json"
        with patch.object(self.page, "screenshot", new=AsyncMock()) as picture:
            await self.fail("edit", edit_receipt_path=str(receipt))
        self.assertEqual(len(self.page.frames), len(picture.await_args.kwargs["mask"]))

    async def test_only_a_real_yes_turns_them_on(self):
        atomic_json(home() / "config.json", {"page_diagnostics": "yes"})
        await self.fail("operations", operation="comments")
        self.assert_nothing_private_in(home() / "operations/errors/x-last-error.json")

    async def test_a_page_that_has_gone_does_not_replace_the_real_error(self):
        await self.page.close()
        await self.fail("operations", operation="comments")


class ReceiptTests(unittest.TestCase):
    def test_every_saved_address_loses_its_query(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"VPUB_HOME": folder}):
            video = Path(folder) / "video.mp4"
            video.write_bytes(b"fixture")
            tx = browser_backends.Transaction("x", "me", {"video": str(video), "video_sha256": file_sha(video), "confirmation": "b" * 64, "options": {"title": "T"}})
            saved = tx.save(review_url=ADDRESS, draft_url=ADDRESS, media_url="https://studio.x.com/library/13_77/edit?x=QUERY-VALUE-SECRET")
            self.assertEqual(("https://x.com/some/path", "https://x.com/some/path", "https://studio.x.com/library/13_77/edit"), (saved["review_url"], saved["draft_url"], saved["media_url"]))
            self.assertNotIn("SECRET", tx.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

"""A field run's Schedule button was covered by Studio's backdrop three times running, and each attempt was recorded as pressed
though no click had happened. In a real browser against a small copy: the backdrop is pressed only when it is what refused the press,
the list it covers for closes, the press then goes once, and a cover that won't close leaves nothing recorded as pressed."""
import os
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, patch

from video_publisher import browser_backends, browser_forms as forms
from video_publisher.browser_backends import Transaction
from video_publisher.core import VpubError, file_sha, read_json
from shared_chrome import ChromeCase, async_playwright

# Like Studio, a list opened over the upload window brings the backdrop up with it, and a press on the backdrop closes the list.
PAGE = """<!doctype html><body style="margin:0">
<ytcp-uploads-dialog style="display:block;position:fixed;top:100px;left:100px;width:400px;height:300px;background:#eee;z-index:5">
  <button id="done-button" onclick="document.body.dataset.pressed = Number(document.body.dataset.pressed || 0) + 1" style="margin:120px">Schedule</button>
</ytcp-uploads-dialog>
<tp-yt-iron-overlay-backdrop class="opened" style="display:block;position:fixed;top:0;left:0;right:0;bottom:0;z-index:10;background:rgba(0,0,0,.3)"
  onclick="%s"></tp-yt-iron-overlay-backdrop>
<tp-yt-iron-dropdown id="list" opened style="display:block;position:fixed;top:200px;left:600px;z-index:11;background:#fff">12:00 PM</tp-yt-iron-dropdown>
</body>"""
CLOSES = "this.classList.remove('opened'); this.style.display = 'none'; document.getElementById('list').remove()"


class CoveredPressTests(ChromeCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)
        video = Path(temp.name) / "video.mp4"
        video.write_bytes(b"fixture-video")
        self.tx = Transaction("youtube", "UC" + "a" * 22, {"video": str(video), "video_sha256": file_sha(video), "confirmation": "c" * 64,
                                                            "options": {"title": "T", "submission_mode": "publish"}})
        for name, value in (("PRESS_TRIAL_MS", 1500), ("PRESS_RETRIAL_MS", 1500)):
            quick = patch.object(browser_backends, name, value)
            quick.start()
            self.addCleanup(quick.stop)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(5000)
        self.addAsyncCleanup(self.close)

    async def close(self):
        await self.browser.close()
        await self.pw.stop()

    async def press(self, closes=CLOSES):
        await self.page.set_content(PAGE % closes)
        done = self.page.locator("ytcp-uploads-dialog #done-button")
        with patch("video_publisher.browser_backends.risk_check", new=AsyncMock()):
            await self.tx.submit(self.page, done, uncover=lambda refused: forms.yt_uncover(self.page, refused))

    async def pressed(self):
        """The page's own record of presses. Patchright reads scripts' variables in a world of its own, so the record is on the page."""
        return await self.page.locator("body").get_attribute("data-pressed")

    async def test_the_list_over_the_button_is_closed_and_the_one_press_goes(self):
        await self.press()
        self.assertEqual("1", await self.pressed())
        saved = read_json(self.tx.path)
        self.assertEqual((True, 1, True), (saved["final_submit_attempted"], saved["final_presses"], saved["press_covered"]["pressed_backdrop"]))
        self.assertIn("tp-yt-iron-dropdown", [shown["tag"] for shown in saved["press_covered"]["on_top"]])

    async def test_a_cover_that_will_not_close_leaves_nothing_pressed_or_recorded(self):
        with self.assertRaises(VpubError) as caught:
            await self.press(closes="")
        self.assertEqual("press_blocked", caught.exception.code)
        self.assertIsNone(await self.pressed())
        saved = read_json(self.tx.path)
        self.assertEqual((False, True), (saved["final_submit_attempted"], saved["press_covered"]["pressed_backdrop"]))

    async def test_the_backdrop_is_left_alone_when_something_else_refused_the_press(self):
        await self.page.set_content(PAGE % CLOSES)
        self.assertIsNone(await forms.yt_uncover(self.page, "Browser command failed (TimeoutError): Locator.click: Timeout 1500ms exceeded. [element is not enabled]"))
        self.assertEqual(1, await self.page.locator("#list").count())

"""A field run read the comments of an Instagram post deleted the day before and got only a 45-second timeout. In a real browser
against a small copy of the page: a post Instagram shows as not available says so, and any other page that never shows the post
still fails as it did. Nothing here contacts Instagram."""
import os
import tempfile
from unittest.mock import patch

from patchright.async_api import Error as BrowserError
from video_publisher import operations_browser
from video_publisher.core import VpubError
from shared_chrome import ChromeCase, async_playwright


class UnavailablePostTests(ChromeCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)
        quick = patch.object(operations_browser, "INSTAGRAM_POST_MS", 800)
        quick.start()
        self.addCleanup(quick.stop)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(5000)
        self.body = ""
        await self.page.route("**/*", self.handle)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def handle(self, route):
        await route.fulfill(status=200, content_type="text/html", body=f"<!doctype html><body><main>{self.body}</main></body>")

    async def test_a_post_instagram_shows_as_not_available_says_so(self):
        self.body = "<h2>Sorry, this page isn't available.</h2><p>The link you followed may be broken, or the page may have been removed.</p>"
        with self.assertRaises(VpubError) as caught:
            await operations_browser.open_work(self.page, "instagram", "owner", "GONE1")
        self.assertEqual("work_unavailable", caught.exception.code)
        self.assertIn("vpub works instagram", caught.exception.hint)

    async def test_a_page_that_never_shows_the_post_fails_as_before(self):
        self.body = "<p>Loading</p>"
        with self.assertRaises(BrowserError):
            await operations_browser.open_work(self.page, "instagram", "owner", "SLOW1")

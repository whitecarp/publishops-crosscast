"""Douyin's creator list fills in some time after its page shows, so an early look is not an answer. In a real browser against a small page."""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import work_discovery_browser as discovery
from video_publisher.core import VpubError


class WaitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()

        async def answer(route):
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body="<!doctype html><body>Loading")
        await self.page.route("**/*", answer)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def scan(self, fill_on_tick=None, fill="暂无作品"):
        ticks = []

        async def tick(ms):
            ticks.append(ms)
            if len(ticks) == fill_on_tick:
                await self.page.evaluate("words => { document.body.innerText = words; }", fill)
        with patch.object(self.page, "wait_for_timeout", tick):
            return await discovery.scan(self.page, "douyin", "me", 5, 1), ticks

    async def test_it_waits_for_the_page_to_say_what_it_holds(self):
        result, ticks = await self.scan(fill_on_tick=6)
        self.assertEqual([], result["items"])
        self.assertEqual(6, len(ticks))

    async def test_a_page_that_never_says_stops_after_its_limit(self):
        with self.assertRaises(VpubError) as caught:
            await self.scan()
        self.assertEqual("works_layout_unverified", caught.exception.code)


class DetailPageTests(unittest.IsolatedAsyncioTestCase):
    """A work's detail page opens in its own tab, and its address is all that's needed. A field run saw it not finish loading in 15 seconds."""
    CARD = '<div class="video-card-content-1"><div class="info-title-text-1" onclick="window.open(\'/creator-micro/work-management/work-detail/123\')">My work</div></div>'

    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()

        async def answer(route):
            if route.request.url.endswith("/never.png"):
                return  # Never answered, so the detail page never finishes loading.
            body = '<!doctype html><body><img src="/never.png">Detail' if "/work-detail/" in route.request.url else "<!doctype html><body>" + self.CARD
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
        await self.context.route("**/*", answer)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_listing_works_takes_the_address_without_waiting_for_the_page_to_finish(self):
        from video_publisher.browser_backends import works
        started = asyncio.get_running_loop().time()
        found = await works(self.page, "douyin", "me")
        self.assertEqual(["123"], list(found))
        self.assertLess(asyncio.get_running_loop().time() - started, 12)

    async def test_discovery_does_too(self):
        started = asyncio.get_running_loop().time()
        result = await discovery.scan(self.page, "douyin", "me", 5, 1)
        self.assertEqual(["123"], [item["id"] for item in result["items"]])
        self.assertLess(asyncio.get_running_loop().time() - started, 14)


if __name__ == "__main__":
    unittest.main()

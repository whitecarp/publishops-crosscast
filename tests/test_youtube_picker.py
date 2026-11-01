"""A box in Studio's playlist picker, in a real browser against a small page. A real channel's first box refused a click for 15 seconds."""
import unittest
from unittest.mock import patch

from video_publisher import browser_forms

BOX = """<!doctype html><body><div id="box" role="checkbox" tabindex="0" aria-checked="false" style="%s"></div>
<script>
const box = document.getElementById('box');
window.clicks = 0; window.keys = 0;
const flip = () => box.setAttribute('aria-checked', box.getAttribute('aria-checked') === 'true' ? 'false' : 'true');
box.addEventListener('click', () => { window.clicks++; flip(); });
box.addEventListener('keydown', event => { if (event.key === ' ') { window.keys++; flip(); } });
</script>"""


class TickTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def tick(self, style):
        await self.page.set_content(BOX % style)
        box = self.page.locator('[role="checkbox"]')
        with patch.object(browser_forms, "YT_TICK_MS", 500):
            await browser_forms.yt_tick(self.page, box)
        return await box.get_attribute("aria-checked"), await self.page.evaluate("[window.clicks, window.keys]", isolated_context=False)

    async def test_a_box_that_can_be_clicked_gets_one_click_and_no_key(self):
        self.assertEqual(("true", [1, 0]), await self.tick("width: 20px; height: 20px"))

    async def test_a_box_with_no_size_is_ticked_from_the_keyboard(self):
        self.assertEqual(("true", [0, 1]), await self.tick("width: 0; height: 0; overflow: hidden"))

    async def test_a_box_covered_by_something_else_is_ticked_from_the_keyboard(self):
        await self.page.set_content(BOX % "width: 20px; height: 20px" + '<div style="position: fixed; inset: 0"></div>')
        box = self.page.locator('[role="checkbox"]')
        with patch.object(browser_forms, "YT_TICK_MS", 500):
            await browser_forms.yt_tick(self.page, box)
        self.assertEqual(("true", [0, 1]), (await box.get_attribute("aria-checked"), await self.page.evaluate("[window.clicks, window.keys]", isolated_context=False)))


if __name__ == "__main__":
    unittest.main()

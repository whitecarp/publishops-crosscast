"""X's reply box, in a real browser against small pages. A field run timed out waiting for it, before anything was typed or sent."""
import unittest
from unittest.mock import patch

from video_publisher import operations_browser as ob
from video_publisher.core import VpubError

PAGE = """<!doctype html><body>
<article id="row"><div role="group"><button data-testid="reply" id="reply">Reply</button></div></article>
%(outer)s<div role="dialog" id="inner" hidden style="position: fixed; width: 300px; height: 200px">
<div contenteditable="true" data-testid="tweetTextarea_0"></div><button data-testid="tweetButton">Reply</button></div>%(close)s
<script>
const open_ = () => { document.getElementById('inner').hidden = false; };
const reply = document.getElementById('reply');
window.clicks = 0; window.keys = 0;
reply.addEventListener('click', event => { if (event.detail) { window.clicks++; if (%(click_opens)s) open_(); } });
reply.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); window.keys++; if (%(key_opens)s) open_(); } });
</script>"""


class ComposerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def compose(self, click_opens="true", key_opens="true", nested=False):
        await self.page.set_content(PAGE % {"outer": '<div role="dialog" style="width: 0; height: 0">' if nested else "", "close": "</div>" if nested else "", "click_opens": click_opens, "key_opens": key_opens})
        with patch.object(ob, "COMPOSER_WAITS", (500, 500)):
            return await ob.x_composer(self.page, self.page.locator("#row"), "reply")

    async def pressed(self):
        return await self.page.evaluate("[window.clicks, window.keys]", isolated_context=False)

    async def test_a_click_that_opens_it_is_enough(self):
        editor, button = await self.compose()
        self.assertEqual("tweetTextarea_0", await editor.get_attribute("data-testid"))
        self.assertEqual("tweetButton", await button.get_attribute("data-testid"))
        self.assertEqual([1, 0], await self.pressed())

    async def test_a_click_that_opens_nothing_gets_the_keyboard_once(self):
        await self.compose(click_opens="false")
        self.assertEqual([1, 1], await self.pressed())

    async def test_an_outer_window_with_no_size_does_not_hide_the_box_inside_it(self):
        editor, button = await self.compose(nested=True)
        self.assertTrue(await editor.is_visible())
        self.assertTrue(await button.is_visible())

    async def test_a_box_that_never_opens_says_nothing_was_sent(self):
        with self.assertRaises(VpubError) as caught:
            await self.compose(click_opens="false", key_opens="false")
        self.assertEqual("layout_changed", caught.exception.code)
        self.assertIn("nothing was sent", caught.exception.message)
        self.assertEqual([1, 1], await self.pressed())


if __name__ == "__main__":
    unittest.main()

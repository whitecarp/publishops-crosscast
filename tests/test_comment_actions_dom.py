import unittest
from unittest.mock import AsyncMock, patch
from video_publisher.comment_actions_browser import execute
from video_publisher.core import VpubError

class CommentDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        self.pw=await async_playwright().start()
        self.browser=await self.pw.chromium.launch(channel='chrome',headless=True)
        self.page=await self.browser.new_page();await self.page.route('**/*',lambda route:route.abort())
    async def asyncTearDown(self):
        await self.browser.close();await self.pw.stop()
    async def read(self,platform='instagram'):
        c={'id':'123456789','text':'Hello','ui_index':0,'author':'owner'}
        r={'comment':c,'work_id':'987654321','identity':'owner'}
        with patch('video_publisher.comment_actions_browser.open_work',new=AsyncMock()),patch('video_publisher.comment_actions_browser.comments',new=AsyncMock(return_value={'items':[c]})):
            return await execute(self.page,platform,'owner',{'request':r,'operation':'status'})
    async def test_instagram_comment_sibling_heart_not_post_heart(self):
        await self.page.set_content('''<main><section><button><svg width="20" height="20" aria-label="Unlike"></svg></button><svg aria-label="Save"></svg></section>
        <div><div data-crosscast-comment-row="0">Hello</div><button><svg width="20" height="20" aria-label="Like"></svg></button></div></main>''')
        self.assertFalse((await self.read())['liked'])
    async def test_two_comment_hearts_are_ambiguous(self):
        await self.page.set_content('''<div data-crosscast-comment-row="0">Hello<button><svg width="20" height="20" aria-label="Like"></svg></button><button><svg width="20" height="20" aria-label="Unlike"></svg></button></div>''')
        with self.assertRaises(VpubError):await self.read()
    async def test_missing_comment_heart_does_not_climb_into_post_toolbar(self):
        await self.page.set_content('''<main><section><svg width="20" height="20" aria-label="Like"></svg><svg aria-label="Save"></svg></section><div data-crosscast-comment-row="0">Hello</div></main>''')
        with self.assertRaises(VpubError):await self.read()
    async def test_x_comment_is_scoped_separately_from_original_post(self):
        await self.page.set_content('''<main><article><a href="/owner/status/987654321"><time>now</time></a><button data-testid="unlike">Unlike</button></article>
        <article><a href="/owner/status/123456789"><time>now</time></a><button data-testid="like">Like</button></article></main>''')
        self.assertFalse((await self.read('x'))['liked'])

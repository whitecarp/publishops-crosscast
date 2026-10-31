"""Isolated controls: no platform navigation or account profile access."""
import unittest
from video_publisher.engagement_browser import controls
from video_publisher.core import VpubError


class EngagementDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        self.pw=await async_playwright().start()
        try:self.browser=await self.pw.chromium.launch(channel='chrome',headless=True)
        except Exception:
            await self.pw.stop();self.skipTest('Chrome required for isolated DOM tests.')
        self.page=await self.browser.new_page()
        await self.page.route('**/*',lambda route:route.abort())
    async def asyncTearDown(self):
        if hasattr(self,'browser'):await self.browser.close();await self.pw.stop()
    async def test_instagram_nested_sections_do_not_target_comment_likes(self):
        await self.page.set_content('''<section><section><button><svg width="20" height="20" aria-label="Unlike"></svg></button>
        <svg width="20" height="20" aria-label="Comment"></svg><button><svg width="20" height="20" aria-label="Save"></svg></button>
        </section><section><svg width="20" height="20" aria-label="Like"></svg>comment</section></section>''')
        values,nodes=await controls(self.page,'instagram','abc')
        self.assertTrue(values['liked']);self.assertFalse(values['saved'])
        self.assertEqual('Unlike',await nodes['like'].get_attribute('aria-label'))
    async def test_instagram_two_independent_toolbars_refuse_to_choose(self):
        toolbar='<section><svg width="20" height="20" aria-label="Like"></svg><svg width="20" height="20" aria-label="Comment"></svg><svg width="20" height="20" aria-label="Save"></svg></section>'
        await self.page.set_content(toolbar*2)
        with self.assertRaises(VpubError):await controls(self.page,'instagram','abc')
    async def test_x_scopes_actions_to_exact_post(self):
        await self.page.set_content('''<article><a href="/author/status/111"><time>today</time></a><button data-testid="unlike">Unlike</button><button data-testid="removeBookmark">Remove</button></article>
        <article><a href="/other/status/222"><time>today</time></a><button data-testid="like">Like</button><button data-testid="bookmark">Bookmark</button></article>''')
        values,nodes=await controls(self.page,'x','111')
        self.assertEqual({'liked':True,'saved':True},values)
        self.assertEqual('unlike',await nodes['like'].get_attribute('data-testid'))
    async def test_x_missing_target_refuses_to_select_neighbour(self):
        await self.page.set_content('<article><a href="/author/status/222"><time>today</time></a><button data-testid="like">Like</button><button data-testid="bookmark">Bookmark</button></article>')
        with self.assertRaises(VpubError):await controls(self.page,'x','111')
    async def test_x_waits_for_delayed_action_toolbar(self):
        await self.page.set_content('''<article><a href="/author/status/111"><time>today</time></a></article>
        <script>setTimeout(()=>document.querySelector('article').insertAdjacentHTML('beforeend',
        '<button data-testid="like">Like</button><button data-testid="bookmark">Bookmark</button>'),300)</script>''')
        values,_=await controls(self.page,'x','111')
        self.assertEqual({'liked':False,'saved':False},values)
    async def test_douyin_tooltips_only_identify_controls_not_state(self):
        await self.page.set_content('''<div data-e2e="detail-video-info" data-e2e-aweme-id="123">
        <div tabindex="0" aria-describedby="tip_like">heart</div><div tabindex="0" aria-describedby="tip_save">star</div></div>
        <div id="tip_like">取消点赞</div><div id="tip_save">收藏</div>''')
        values,_=await controls(self.page,'douyin','123')
        self.assertEqual({'liked':None,'saved':None},values)
    async def test_douyin_unknown_tooltip_is_not_guessed_from_icon(self):
        await self.page.set_content('''<div data-e2e="detail-video-info" data-e2e-aweme-id="123">
        <div tabindex="0" aria-describedby="tip_like">red heart</div><div tabindex="0" aria-describedby="tip_save">star</div></div>
        <div id="tip_like">Unknown</div><div id="tip_save">收藏</div>''')
        with self.assertRaises(VpubError):await controls(self.page,'douyin','123')

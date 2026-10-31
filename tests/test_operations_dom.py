"""Synthetic DOM regressions; never open a platform, account or network URL."""
import unittest
from video_publisher.operations_browser import COMMENT_ROWS, DOUYIN_PUBLIC_ROWS


class CommentDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        self.pw=await async_playwright().start()
        try:
            self.browser=await self.pw.chromium.launch(channel='chrome',headless=True)
        except Exception:
            await self.pw.stop()
            self.skipTest('Chrome is unavailable; synthetic DOM tests require the browser backend.')
        self.page=await self.browser.new_page()
        await self.page.route('**/*',lambda route:route.abort())

    async def asyncTearDown(self):
        if hasattr(self,'browser'):
            await self.browser.close();await self.pw.stop()

    async def test_instagram_text_and_id_survive_changing_relative_time(self):
        await self.page.set_content('''<div><header><a href="/creator/">creator</a>
        <a href="/p/sample/c/123456/"><time datetime="2026-10-30T00:00:00Z">9s</time></a></header>
        <div><span dir="auto">你好</span></div><div role="button"><span>Reply</span></div></div>''')
        first=await self.page.evaluate(COMMENT_ROWS,{'platform':'instagram','limit':10})
        await self.page.locator('time').evaluate("e=>e.textContent='2m'")
        second=await self.page.evaluate(COMMENT_ROWS,{'platform':'instagram','limit':10})
        self.assertEqual('你好',first[0]['text']);self.assertEqual(first[0]['text'],second[0]['text'])
        self.assertEqual(first[0]['time'],second[0]['time']);self.assertEqual(first[0]['permalink'],second[0]['permalink'])

    async def test_douyin_ids_and_author_badge_are_not_inferred_from_display_name(self):
        await self.page.set_content('''<section data-e2e="comment-list"><div data-e2e="comment-item">
        <div class="comment-item-info-wrap"><a href="/user/creator"><span data-click-from="title">Creator</span>
        <span class="comment-item-tag-text">作者</span></a><div data-e2e="video-comment-more"><div id="tooltip_123456789"></div></div></div>
        <div>你好</div><div>刚刚</div></div><div data-e2e="comment-item">
        <div class="comment-item-info-wrap"><a href="/user/viewer"><span data-click-from="title">Creator</span></a>
        <div data-e2e="video-comment-more"><div id="tooltip_987654321"></div></div></div><div>Question</div><div>1分钟前</div></div></section>''')
        rows=await self.page.evaluate(DOUYIN_PUBLIC_ROWS,20)
        self.assertEqual('123456789',rows[0]['id']);self.assertTrue(rows[0]['is_owner'])
        self.assertFalse(rows[1]['is_owner']);self.assertEqual('Question',rows[1]['text'])

    async def test_douyin_nested_reply_keeps_its_parent_comment_id(self):
        def node(cid):
            return f'<div class="comment-item-info-wrap"><a href="/user/creator">Creator<span class="comment-item-tag-text">作者</span></a><div data-e2e="video-comment-more"><i id="tooltip_{cid}"></i></div></div><div>你好</div><div>刚刚</div>'
        await self.page.set_content('<div data-e2e="comment-item">'+node('123456789')+
            '<div class="replyContainer"><div data-e2e="comment-item">'+node('987654321')+'</div></div></div>')
        rows=await self.page.evaluate(DOUYIN_PUBLIC_ROWS,20)
        self.assertIsNone(rows[0]['parent_id']);self.assertEqual('123456789',rows[1]['parent_id'])
        self.assertEqual('dom_nested_thread',rows[1]['parent_relationship'])

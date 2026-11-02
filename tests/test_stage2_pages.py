"""Safety test stage 2: what a viewer can put on a page, in an invisible browser against small copies. Whose words are whose decides whether the whole tool stops. Nothing is contacted."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from stage_browser import SharedBrowser

from video_publisher import browser_runtime as rt, operations_browser
from video_publisher.core import atomic_json, home

OWNER = "owner"
WORK = "Cabcde12345"


class Shown(SharedBrowser):
    def prepare(self):
        self.body = ""

    async def handle(self, route):
        await self.page_at(route, self.body)

    async def shown(self, body, address):
        self.body = body
        await self.page.goto(address)
        return await rt.challenge_shown(self.page)


class ViewerWordsTests(Shown):
    """A viewer cannot stop the whole tool by writing the words a platform uses for its checks."""

    DOUYIN_PANEL = '<div data-e2e="listDlgTest-container"><div><span data-e2e="user-name-card">{}</span><pre>{}</pre><span>提到了你</span></div></div>'
    DOUYIN_CHAT = '<div data-stack-layer="chat"><div class="StackLayoutStackChatHeadertitle">{}</div><div class="messageMessageListlist"><div class="messageMessageBoxcontentBox"><div class="TextMessageTexttextInnerContent">{}</div></div></div></div>'

    async def test_words_in_a_douyin_notification_are_the_viewers(self):
        for name, words in (("viewer", "安全验证"), ("安全验证", "你好"), ("viewer", "Confirm it's you")):
            with self.subTest(name=name, words=words):
                self.assertFalse(await self.shown(self.DOUYIN_PANEL.format(name, words), "https://www.douyin.com/user/self"))

    async def test_words_in_a_douyin_conversation_are_the_viewers(self):
        for name, words in (("viewer", "请完成验证"), ("安全验证", "你好")):
            with self.subTest(name=name, words=words):
                self.assertFalse(await self.shown(self.DOUYIN_CHAT.format(name, words), "https://www.douyin.com/user/MS4wLjABAAAAviewer12345"))

    async def test_a_viewers_profile_is_the_viewers(self):
        for body, address in (('<main><header><section><h2>viewer</h2><span dir="auto">Confirm it\'s you</span></section></header><button>Message</button></main>', "https://www.instagram.com/viewer/"),
                              ('<main><header><section><h2>Action Blocked</h2></section></header><button>Message</button></main>', "https://www.instagram.com/viewer/"),
                              ('<main><header><section><div><h1 dir="auto">Unusual activity</h1></div></section></header></main>', "https://www.instagram.com/viewer/"),
                              ('<div data-e2e="user-info"><h1>安全验证</h1><p>Unusual activity</p></div><button>私信</button>', "https://www.douyin.com/user/MS4wLjABAAAAviewer12345")):
            with self.subTest(address=address):
                self.assertFalse(await self.shown(body, address))

    async def test_the_tool_keeps_going_after_all_of_these(self):
        await self.shown(self.DOUYIN_CHAT.format("安全验证", "请完成验证"), "https://www.douyin.com/user/MS4wLjABAAAAviewer12345")
        await rt.risk_check(self.page)
        self.assertFalse((home() / "STOP.json").exists())


class PlatformWordsTests(Shown):
    """The platform asking still counts, on those same pages and in the words it uses when it pushes back."""

    async def test_a_check_over_a_conversation_still_counts(self):
        chat = ViewerWordsTests.DOUYIN_CHAT.format("viewer", "hello")
        self.assertTrue(await self.shown(chat + '<div role="dialog"><h2>安全验证</h2></div>', "https://www.douyin.com/user/MS4wLjABAAAAviewer12345"))

    async def test_a_check_on_a_profile_page_still_counts(self):
        self.assertTrue(await self.shown("<main><header>viewer</header><section><h1>Confirm it's you</h1></section></main>", "https://www.instagram.com/viewer/"))

    async def test_an_alert_or_window_belongs_to_the_platform_wherever_it_is_put(self):
        """From a review: an alert inside a profile's header passed as the viewer's own words."""
        for body in ('<main><header><div role="alert">Action Blocked</div></header></main>',
                     '<main><header><section><h2>viewer</h2><div role="alert"><span dir="auto">Action Blocked</span></div></section></header></main>',
                     '<main><header><section><div role="alertdialog"><h1 dir="auto">Confirm it\'s you</h1></div></section></header></main>',
                     '<main><header><section><div role="dialog"><h2 dir="auto">Confirm it\'s you</h2></div></section></header></main>',
                     '<article><div role="alert">Action Blocked</div></article>',
                     '<div role="alert"><span class="comment-note">Action Blocked</span></div>',
                     '<div data-e2e="user-info"><div role="alert">操作过于频繁</div></div>',
                     '<div><a href="/viewer/">viewer</a><time>1h</time><div role="dialog">Confirm it\'s you</div></div>',
                     '<div><a href="/viewer/">viewer</a><time>1h</time><div role="alert">Action Blocked</div></div>'):
            with self.subTest(body=body):
                self.assertTrue(await self.shown(body, "https://www.instagram.com/viewer/"))

    async def test_a_banner_of_the_sites_own_in_a_profile_header_still_counts(self):
        self.assertTrue(await self.shown("<main><header><div>Action Blocked</div></header></main>", "https://www.instagram.com/viewer/"))
        self.assertTrue(await self.shown("<main><header><section><h2>viewer</h2><div>Confirm it's you</div></section></header></main>", "https://www.instagram.com/viewer/"))

    async def test_a_viewers_words_in_a_window_are_still_the_viewers(self):
        for body in ('<div role="dialog"><article>Confirm it\'s you</article></div>',
                     '<div role="dialog"><div><a href="/viewer/">viewer</a><time>1h</time><span>Action Blocked</span></div></div>',
                     '<div role="dialog"><div data-e2e="comment-list"><p>Unusual activity</p></div></div>'):
            with self.subTest(body=body):
                self.assertFalse(await self.shown(body, "https://www.instagram.com/p/x/"))

    async def test_a_platform_saying_too_much_too_fast_counts_however_it_says_it(self):
        for words, address in (("操作过于频繁，请稍后再试", "https://creator.douyin.com/x"), ("操作太频繁了", "https://www.douyin.com/video/1"), ("评论太频繁", "https://www.douyin.com/video/1"), ("發送過於頻繁", "https://www.douyin.com/video/1"),
                               ("Action Blocked", "https://www.instagram.com/p/x/"), ("We limit how often you can do certain things on Instagram", "https://www.instagram.com/p/x/"), ("We restrict certain activity to protect our community", "https://www.instagram.com/p/x/"),
                               ("Rate limit exceeded", "https://x.com/home"), ("You are over the daily limit for sending posts.", "https://x.com/home"), ("Your account has been temporarily restricted", "https://x.com/home")):
            with self.subTest(words=words):
                self.assertTrue(await self.shown(f'<div role="dialog"><p>{words}</p></div>', address))

    async def test_a_viewer_writing_those_words_is_still_only_a_viewer(self):
        self.assertFalse(await self.shown('<article><span>Action Blocked</span></article>', "https://x.com/home"))
        self.assertFalse(await self.shown('<div data-e2e="comment-list"><div data-e2e="comment-item">操作过于频繁</div></div>', "https://www.douyin.com/video/1"))

    async def test_ordinary_words_near_them_do_not_count(self):
        for words in ("Blocked accounts", "Try again", "Something went wrong", "频道", "操作说明", "Weekly limit on nothing"):
            with self.subTest(words=words):
                self.assertFalse(await self.shown(f"<p>{words}</p>", "https://x.com/home"))


del Shown


class RowsTests(SharedBrowser):
    """A viewer's comment is read as words, whatever it looks like."""

    def prepare(self):
        self.rows = ""

    async def handle(self, route):
        await self.page_at(route, f'<main><svg aria-label="More options" width="10" height="10"></svg>{self.rows}<textarea aria-label="Add a comment…"></textarea></main>')

    def row(self, author, number, words):
        return f"""<div><a href="/{author}/">{author}</a><a href="/p/{WORK}/c/{number}/"><time datetime="2026-10-30T00:00:00Z">1h</time></a><span dir="auto">{words}</span><div role="button"><span>Reply</span></div></div>"""

    async def read(self):
        await self.page.goto(f"https://www.instagram.com/p/{WORK}/")
        return (await operations_browser.comments(self.page, "instagram", OWNER, WORK, 20))["items"]

    async def test_markup_in_a_comment_stays_words_and_runs_nothing(self):
        words = "&lt;img src=x onerror=&quot;window.ran = 1&quot;&gt; &lt;script&gt;window.ran = 1&lt;/script&gt;"
        self.rows = self.row("viewer", 111, words)
        items = await self.read()
        self.assertEqual('<img src=x onerror="window.ran = 1"> <script>window.ran = 1</script>', items[0]["text"])
        self.assertIsNone(await self.page.evaluate("window.ran ?? null"))

    async def test_naming_the_owner_in_a_comment_does_not_make_it_the_owners(self):
        self.rows = self.row("viewer", 111, f'Thanks <a href="/{OWNER}/">@{OWNER}</a>')
        items = await self.read()
        self.assertEqual(("/viewer/", False), (items[0]["author_id"], items[0]["is_owner"]))

    async def test_a_display_name_copying_the_owner_does_not_make_it_the_owners(self):
        self.rows = f"""<div><a href="/viewer/">{OWNER}</a><a href="/p/{WORK}/c/111/"><time datetime="2026-10-30T00:00:00Z">1h</time></a><span dir="auto">Hi</span><div role="button"><span>Reply</span></div></div>"""
        items = await self.read()
        self.assertEqual(("/viewer/", False), (items[0]["author_id"], items[0]["is_owner"]))

    async def test_the_owners_own_comment_is_still_the_owners(self):
        self.rows = self.row(OWNER, 111, "My note")
        self.assertTrue((await self.read())[0]["is_owner"])

    async def test_a_comment_written_to_look_like_another_row_is_one_row(self):
        self.rows = self.row("viewer", 111, "viewer2 1h Reply Thanks for the video Reply")
        items = await self.read()
        self.assertEqual(["111"], [row["id"] for row in items])


if __name__ == "__main__":
    unittest.main()

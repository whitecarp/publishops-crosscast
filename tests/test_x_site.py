"""X's two sites, driven in a real browser against small copies of each.

The rebuilt site's copy follows x.com's public post pages and page code, in Chinese as the site served them, and has not been
tried on a signed-in account. The earlier site's copy follows what a signed-in account showed on 2026-10-31. Nothing here
contacts X, and each test below runs against both.
"""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from video_publisher import browser_edits, browser_runtime as rt, comment_actions_browser, comment_actions_x, direct_messages_x, engagement, engagement_browser, message_sources, operations_browser as ops, work_discovery_browser as discovery, x_pages
from video_publisher.core import VpubError, atomic_json, read_json
from shared_chrome import ChromeCase, async_playwright

OWNER = "owner"
POST = "2115117485020135559"
REPLY = "2115117485020135600"
MINE = "2115117485020135700"
HIDDEN = "2115117485020135800"
AD = "2115117485020135900"


def post_id_now():
    return str((int(time.time() * 1000) - 1288834974657) << 22)


class FakeX:
    update_label = "更新"
    delete_label = "删除"

    def __init__(self):
        self.liked = {}
        self.saved = {}
        self.replies = [{"id": REPLY, "author": "viewer", "text": "这首歌叫什么？"}, {"id": MINE, "author": OWNER, "text": "My own note"}]
        self.hidden = [{"id": HIDDEN, "author": "viewer", "text": "Hidden words"}]
        self.mentions = [{"id": REPLY, "author": "viewer", "text": "@owner hello"}]
        self.writes = []
        self.post_text = "ty for 4 years of bangers"
        self.keeps_edits = True
        self.confirm_hide = True
        self.chat_ready = True
        self.gone_words = "Hmm...this page doesn't exist. Try searching for something else."

    def actions(self, post, likes="9839", label=None):
        liked = "true" if self.liked.get(post) else "false"
        saved = "true" if self.saved.get(post) else "false"
        heart = "heart-fill" if self.liked.get(post) else "heart"
        mark = "bookmark-fill" if self.saved.get(post) else "bookmark"
        return f"""<div role="group" aria-label="{label or f'25 回复、26 转帖、{likes} 点赞、21 书签、140000 次观看'}">
          <a aria-label="25 回复. 回复" href="/compose/post?in_reply_to={post}" onclick="compose('{post}'); return false"><svg role="img" width="16" height="16"><use href="#icon-reply"></use></svg><span dir="ltr">25</span></a>
          <button type="button" aria-label="26 转帖. 转帖" aria-pressed="false" aria-haspopup="menu" data-slot="xds-menu-trigger"><svg role="img" width="16" height="16"><use href="#icon-repost"></use></svg><span dir="ltr">26</span></button>
          <button type="button" aria-label="{likes} 点赞. 点赞" aria-pressed="{liked}" onclick="act(this, 'like/{post}')"><svg role="img" width="16" height="16"><use href="#icon-{heart}"></use></svg><span dir="ltr">{likes}</span></button>
          <button type="button" aria-label="书签" aria-pressed="{saved}" onclick="act(this, 'save/{post}')"><svg role="img" width="16" height="16"><use href="#icon-{mark}"></use></svg></button>
          <button type="button" aria-label="分享" aria-haspopup="menu" data-slot="xds-menu-trigger"><svg role="img" width="16" height="16"><use href="#icon-share-ios"></use></svg></button></div>"""

    def more(self, post, hidden=False):
        item = "取消隐藏回复" if hidden else "隐藏回复"
        return f"""<button type="button" aria-label="更多" aria-haspopup="menu" data-slot="xds-menu-trigger" onclick="document.getElementById('menu-{post}').hidden = false"><svg role="img" width="16" height="16"><use href="#icon-more-dots-hor"></use></svg></button>
          <div role="menu" id="menu-{post}" hidden><div role="menuitem" tabindex="0" onclick="hideReply('{post}', {str(hidden).lower()})">{item}</div><div role="menuitem" tabindex="0" onclick="editPost('{post}')">编辑</div><div role="menuitem" tabindex="0" onclick="askDelete('{post}')">{self.delete_label}</div></div>"""

    def reply_row(self, row, hidden=False, extra=""):
        return f"""<article class="flex flex-col gap-1"><a href="/{row['author']}"><img alt="" width="10" height="10"></a><a href="/{row['author']}"><div>Name</div></a><a href="/{row['author']}"><span>@{row['author']}</span></a>
          <a href="/{row['author']}/status/{row['id']}">10月27日</a>{self.more(row['id'], hidden)}<div dir="auto"><span>{row['text']}</span></div>{extra}{self.actions(row['id'])}</article>"""

    def post_page(self):
        focal = f"""<article class="flex flex-col gap-1"><a href="/{OWNER}"><div>Owner</div></a>{self.more(POST)}<div dir="auto"><span>{self.post_text}</span></div>
          <div dir="auto"><a href="/{OWNER}/status/{POST}">12:24 · 2026年10月27日</a></div><a href="/{OWNER}/status/{POST}"><div>14万</div><div>浏览量</div></a>
          {self.actions(POST, '35049', '2899 回复、2900 转帖、35049 点赞、796 书签、4800484 次观看').replace('aria-label="书签"', 'aria-label="796 书签. 加入书签"').replace('25 回复. 回复', '2899 回复. 回复').replace('26 转帖. 转帖', '2900 转帖. 转帖')}</article>"""
        quote = '<div role="link"><a href="/someone/status/1111111111"><div dir="auto">Quoted words</div></a><div dir="auto"><span>Quoted words</span></div></div>'
        rows = "".join(self.reply_row(row, extra=quote if row["id"] == REPLY else "") for row in self.replies)
        ad = f'<article><a href="/brand/status/{AD}">广告</a><div dir="auto"><span>Buy things</span></div><p>Ad</p>{self.actions(AD)}</article>'
        return f"<main>{focal}{rows}{ad}</main>{self.composer()}"

    def composer(self):
        return """<div role="dialog" aria-modal="true" id="composer" data-modal-slot-surface="" hidden><form onsubmit="return false"><div contenteditable="true" role="textbox" aria-multiline="true" aria-label="发布你的回复" oninput="send.disabled = !this.innerText.trim()"></div>
          <button type="button" id="send" disabled>回复</button></form></div>
          <div role="alertdialog" id="sure" hidden><h2>Hiding replies</h2><button type="button" id="sure-yes">隐藏回复</button></div>
          <div role="alertdialog" id="gone" hidden><h2>Delete?</h2><button type="button" id="gone-yes">DELETE_LABEL</button></div>
          <script>
          function askDelete(post) { document.getElementById('menu-' + post).hidden = true; document.getElementById('gone').hidden = false;
            document.getElementById('gone-yes').onclick = () => { fetch('/i/api/graphql/abc/DeleteTweet?write=delete/' + post, {method: 'POST', body: '{}'}); document.getElementById('gone').hidden = true; }; }
          const send = document.getElementById('send');
          const box = document.querySelector('#composer [contenteditable=true]');
          let write = 'reply';
          let target = '';
          function compose(post) { write = 'reply'; target = post; document.getElementById('composer').hidden = false; }
          function editPost(post) { write = 'edit'; target = post; box.innerText = 'POST_TEXT'; send.textContent = 'UPDATE_LABEL'; send.disabled = false; document.getElementById('composer').hidden = false; }
          send.onclick = () => { const text = box.innerText; fetch('/i/api/graphql/abc/CreateTweet?write=' + write + '/' + target, {method: 'POST', body: JSON.stringify({text})}).then(() => { document.getElementById('composer').hidden = true; }); };
          function act(button, write) { fetch('/i/api/graphql/abc/act?write=' + write, {method: 'POST', body: '{}'});
            if (button.hasAttribute('aria-pressed')) button.setAttribute('aria-pressed', button.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
            const swap = {like: 'unlike', unlike: 'like', bookmark: 'removeBookmark', removeBookmark: 'bookmark'}[button.dataset.testid];
            if (swap) button.dataset.testid = swap; }
          let pendingHide = '';
          function send_hide(post) { fetch('/i/api/graphql/abc/hide?write=hide/' + post, {method: 'POST', body: '{}'}); document.getElementById('sure').hidden = true; }
          function hideReply(post, hidden) { document.getElementById('menu-' + post).hidden = true; if (hidden) { fetch('/i/api/graphql/abc/unhide?write=unhide/' + post, {method: 'POST', body: '{}'}); return; }
            if (CONFIRM) { pendingHide = post; document.getElementById('sure').hidden = false; document.getElementById('sure-yes').onclick = () => send_hide(post); } else send_hide(post); }
          </script>""".replace("CONFIRM", "true" if self.confirm_hide else "false").replace("POST_TEXT", self.post_text).replace("UPDATE_LABEL", self.update_label).replace("DELETE_LABEL", self.delete_label)

    async def handle(self, route):
        url = urlsplit(route.request.url)
        if url.hostname != "x.com":
            return await route.abort()
        if route.request.method == "POST":
            write = parse_qs(url.query).get("write")
            if write:
                self.apply(write[0].split("/"), json.loads(route.request.post_data or "{}"))
            return await route.fulfill(status=200, content_type="application/json", body="{}")
        path = url.path
        if path == "/notifications/mentions":
            rows = "".join(self.reply_row(row) for row in self.mentions)
            return await self.page_at(route, f'<main>{rows}{"" if self.mentions else "<p>Nothing to see here</p>"}</main>{self.composer()}')
        if path.endswith("/hidden"):
            return await self.page_at(route, f'<main>{"".join(self.reply_row(row, True) for row in self.hidden)}</main>{self.composer()}')
        if path == "/i/chat":
            return await self.page_at(route, '<button data-testid="dm-new-chat-button">New</button>' if self.chat_ready else '<input aria-label="Digit 1 of 4">')
        if path == f"/{OWNER}/status/{POST}" or path == f"/i/status/{POST}" or path == f"/{OWNER}/status/{REPLY}" or path == f"/i/status/{REPLY}":
            return await self.page_at(route, self.post_page())
        if path == f"/{OWNER}/status/{MINE}":
            mine = any(row["id"] == MINE for row in self.replies)
            return await self.page_at(route, self.post_page() if mine else f"<main><p>{self.gone_words}</p></main>")
        await self.page_at(route, "<main></main>")

    async def page_at(self, route, body):
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body="<!doctype html><title>X</title><body>" + body)

    def apply(self, parts, data):
        self.writes.append((parts[0], parts[1], *data.values()))
        if parts[0] == "like":
            self.liked[parts[1]] = not self.liked.get(parts[1])
        elif parts[0] == "save":
            self.saved[parts[1]] = not self.saved.get(parts[1])
        elif parts[0] == "reply":
            self.replies.append({"id": post_id_now(), "author": OWNER, "text": data["text"]})
        elif parts[0] == "edit":
            self.post_text = data["text"] if self.keeps_edits else self.post_text
        elif parts[0] == "delete":
            self.replies = [row for row in self.replies if row["id"] != parts[1]]
        elif parts[0] == "hide":
            row = next(row for row in self.replies if row["id"] == parts[1])
            self.replies.remove(row)
            self.hidden.append(row)
        elif parts[0] == "unhide":
            row = next(row for row in self.hidden if row["id"] == parts[1])
            self.hidden.remove(row)
            self.replies.append(row)


class FakeEarlierX(FakeX):
    """What a signed-in account showed: test labels, time links and a second reply box on the post's own page."""
    update_label = "Update"
    delete_label = "Delete"

    def actions(self, post, likes="9839", label=None):
        like = "unlike" if self.liked.get(post) else "like"
        mark = "removeBookmark" if self.saved.get(post) else "bookmark"
        return f"""<div role="group" aria-label="{label or f'25 replies, 26 reposts, {likes} likes, 21 bookmarks, 140000 views'}">
          <button type="button" data-testid="reply" aria-label="25 Replies. Reply" onclick="compose('{post}')">25</button>
          <button type="button" data-testid="retweet" aria-label="26 reposts. Repost">26</button>
          <button type="button" data-testid="{like}" aria-label="{likes} Likes. Like" onclick="act(this, 'like/{post}')">{likes}</button>
          <button type="button" data-testid="{mark}" aria-label="21 Bookmarks. Bookmark" onclick="act(this, 'save/{post}')">21</button></div>"""

    def more(self, post, hidden=False):
        item = "Unhide reply" if hidden else "Hide reply"
        return f"""<button type="button" data-testid="caret" aria-label="More" aria-haspopup="menu" onclick="document.getElementById('menu-{post}').hidden = false">...</button>
          <div role="menu" id="menu-{post}" hidden><div role="menuitem" tabindex="0" onclick="hideReply('{post}', {str(hidden).lower()})">{item}</div><div role="menuitem" tabindex="0" onclick="editPost('{post}')">Edit post</div><div role="menuitem" tabindex="0" onclick="askDelete('{post}')">{self.delete_label}</div></div>"""

    def author(self, name, handle, row):
        return f"""<div data-testid="User-Name"><a href="/{handle}"><span>{name}</span></a><a href="/{handle}"><span>@{handle}</span></a>
          <a href="/{handle}/status/{row}"><time datetime="{x_pages.post_time(row)}">Oct 27</time></a></div>"""

    def reply_row(self, row, hidden=False, extra=""):
        return f"""<article data-testid="tweet">{self.author('Name', row['author'], row['id'])}{self.more(row['id'], hidden)}
          <div data-testid="tweetText" dir="auto"><span>{row['text']}</span></div>{extra}{self.actions(row['id'])}</article>"""

    def post_page(self):
        focal = f"""<article data-testid="tweet">{self.author('Owner', OWNER, POST)}{self.more(POST)}<div data-testid="tweetText" dir="auto"><span>{self.post_text}</span></div>
          <a href="/{OWNER}/status/{POST}/analytics"><span>4.8M</span> Views</a>
          {self.actions(POST, '35049', '2899 replies, 2900 reposts, 35049 likes, 796 bookmarks, 4800484 views').replace('25 Replies', '2899 Replies').replace('26 reposts', '2900 reposts').replace('21 Bookmarks', '796 Bookmarks')}</article>
          <div contenteditable="true" data-testid="tweetTextarea_1"></div>"""
        quote = '<div role="link" tabindex="0"><div data-testid="User-Name"><span>@someone</span></div><time datetime="2020-01-01T00:00:00.000Z">Jan 1</time><div data-testid="tweetText" dir="auto"><span>Quoted words</span></div></div>'
        rows = "".join(self.reply_row(row, extra=quote if row["id"] == REPLY else "") for row in self.replies)
        ad = f'<article data-testid="tweet"><div data-testid="User-Name"><a href="/brand"><span>Brand</span></a></div><div data-testid="tweetText" dir="auto"><span>Buy things</span></div><span>Ad</span>{self.actions(AD)}</article>'
        return f"<main>{focal}{rows}{ad}</main>{self.composer()}"

    def composer(self):
        # A signed-in account's reply window nests one dialog inside another, with every control in the inner one.
        return (super().composer().replace('contenteditable="true" role="textbox"', 'contenteditable="true" data-testid="tweetTextarea_0" role="textbox"')
                .replace('id="send"', 'id="send" data-testid="tweetButton"').replace(">回复<", ">Reply<").replace('role="alertdialog"', 'role="dialog"')
                .replace(">隐藏回复<", ">Hide reply<").replace('<form onsubmit="return false">', '<div role="dialog"><form onsubmit="return false">').replace("</form>", "</form></div>"))


class Site(ChromeCase):
    quick_pauses = True  # This copy of the site has no timers of its own, so a pause can end once the page is quiet.
    site = FakeX

    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(8000)
        self.x = self.site()
        await self.page.route("**/*", self.x.handle)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    def receipt(self, name, **fields):
        path = self.root / (name + ".json")
        atomic_json(path, {"state": "READY", **fields})
        return str(path)

    async def open_post(self):
        await self.page.goto(f"https://x.com/{OWNER}/status/{POST}")


class ReadingTests(Site):
    async def test_a_post_describes_itself(self):
        await self.open_post()
        focal = await x_pages.post_fields(self.page.locator("article").nth(0))
        self.assertEqual((OWNER, POST, "ty for 4 years of bangers"), (focal["author"], focal["id"], focal["text"]))

    async def test_a_quoted_post_never_takes_over_the_row(self):
        await self.open_post()
        reply = await x_pages.post_fields(x_pages.post_row(self.page, REPLY))
        self.assertEqual(("viewer", REPLY, "这首歌叫什么？"), (reply["author"], reply["id"], reply["text"]))

    async def test_the_time_comes_from_the_post_id(self):
        await self.open_post()
        focal = await x_pages.post_fields(self.page.locator("article").nth(0))
        self.assertTrue(focal["published_at"].startswith("2026-10-27"), focal["published_at"])

    async def test_figures_come_from_the_action_row_in_any_language(self):
        result = await ops.execute(self.page, "x", OWNER, {"work_id": POST, "operation": "stats"})
        metrics = result["metrics"]
        self.assertEqual((2899, 2900, 35049, 796, 4800484), tuple(metrics[name]["value"] for name in ("comments", "reposts", "likes", "bookmarks", "post_views")))

    async def test_replies_leave_out_the_post_itself_and_ads(self):
        result = await ops.execute(self.page, "x", OWNER, {"work_id": POST, "operation": "comments", "limit": 20})
        self.assertEqual([REPLY, MINE], [row["id"] for row in result["items"]])
        self.assertEqual([False, True], [row["is_owner"] for row in result["items"]])
        self.assertEqual("这首歌叫什么？", result["items"][0]["text"])
        self.assertTrue(all(row["published_at"].startswith("2026-10-27") for row in result["items"]))

    async def test_mentions_open_by_address_and_read_each_row(self):
        await self.page.goto("https://x.com/home")
        result = await message_sources.x_mentions(self.page, OWNER, 10)
        self.assertEqual([REPLY], [row["id"] for row in result["items"]])
        self.assertEqual("@owner hello", result["items"][0]["text"])

    async def test_empty_mentions_need_the_sites_own_empty_message(self):
        self.x.mentions = []
        await self.page.goto("https://x.com/home")
        with patch.object(self.page, "wait_for_timeout", side_effect=self.no_wait):
            result = await message_sources.x_mentions(self.page, OWNER, 10)
        self.assertEqual([], result["items"])

    async def no_wait(self, ms):
        return None

    async def test_adopting_checks_the_post_is_this_accounts(self):
        result = await discovery.execute(self.page, "x", OWNER, {"action": "adopt_work", "work_id": POST})
        self.assertEqual(("exact_post_permalink_and_author", 0), (result["ownership_evidence"], result["platform_writes"]))

    async def test_chat_opens_by_address_and_asks_for_the_pin_when_locked(self):
        await self.page.goto("https://x.com/home")
        await direct_messages_x.inbox(self.page)
        self.assertEqual("/i/chat", urlsplit(self.page.url).path)
        self.x.chat_ready = False
        with self.assertRaises(VpubError) as caught:
            await direct_messages_x.inbox(self.page)
        self.assertEqual("chat_unlock_required", caught.exception.code)

    async def test_a_quote_with_no_words_of_its_own_never_takes_the_quoted_words(self):
        await self.page.route("**/*", lambda route: route.abort())
        await self.page.set_content("""<article><a href="/author/status/333"><time datetime="2026-01-01T00:00:00.000Z">now</time></a><div role="link"><div data-testid="tweetText">Quoted words</div></div></article>""")
        post = await x_pages.post_fields(x_pages.post_row(self.page, "333"))
        self.assertIsNone(post["text"])

    async def test_a_row_with_no_time_and_no_action_row_is_not_credited_to_a_post_it_links(self):
        await self.page.route("**/*", lambda route: route.abort())
        await self.page.set_content("""<article><a href="/someone/status/444">a link</a><div dir="auto">Words</div></article>""")
        self.assertIsNone(await x_pages.post_fields(self.page.locator("article")))


class ChangeTests(Site):
    async def test_status_reads_like_and_bookmark_by_their_icons(self):
        self.x.liked[POST] = True
        result = await engagement_browser.execute(self.page, "x", OWNER, {"operation": "status", "work_id": POST})
        self.assertEqual((True, False), (result["liked"], result["saved"]))

    async def test_a_like_presses_only_this_posts_button(self):
        path = self.receipt("like", state="ACTION_READY")
        result = await engagement_browser.execute(self.page, "x", OWNER, {"operation": "set", "work_id": POST, "kind": "like", "desired": True, "receipt_path": path})
        await self.page.wait_for_timeout(200)
        self.assertEqual(1, result["platform_writes"])
        self.assertEqual([("like", POST)], self.x.writes)
        self.assertEqual("ACTION_SUBMITTING", read_json(path)["state"])

    async def test_a_bookmark_already_set_sends_nothing(self):
        self.x.saved[POST] = True
        result = await engagement_browser.execute(self.page, "x", OWNER, {"operation": "set", "work_id": POST, "kind": "save", "desired": True, "receipt_path": "unused"})
        self.assertEqual("NO_CHANGE", result["state"])
        self.assertEqual([], self.x.writes)

    async def test_a_comment_like_is_kept_apart_from_the_post(self):
        self.x.liked[POST] = True
        comment = {"id": REPLY, "text": "这首歌叫什么？", "author": "viewer"}
        request = {"comment": comment, "work_id": POST, "identity": OWNER, "kind": "like", "desired": True}
        status = await comment_actions_browser.execute(self.page, "x", OWNER, {"request": request, "operation": "status"})
        self.assertIs(False, status["liked"])
        path = self.receipt("comment", state="COMMENT_READY")
        await comment_actions_browser.execute(self.page, "x", OWNER, {"request": request, "operation": "set", "receipt_path": path})
        self.assertEqual([("like", REPLY)], self.x.writes)

    async def reply_payload(self, path):
        return {"work_id": POST, "operation": "reply", "reply_receipt_path": path,
                "reply_request": {"comment": {"id": REPLY, "text": "这首歌叫什么？"}, "text": "测试曲目。", "expected_identity": OWNER, "attempt": "this-attempt"}}

    async def test_a_reply_goes_to_the_exact_comment_once_and_stays_until_sent(self):
        path = self.receipt("reply", state="REPLY_READY", request={"attempt": "this-attempt"})
        result = await ops.execute(self.page, "x", OWNER, await self.reply_payload(path))
        self.assertEqual(("REPLY_UNVERIFIED", 1), (result["state"], result["platform_writes"]))
        self.assertEqual([("reply", REPLY, "测试曲目。")], self.x.writes)
        saved = read_json(path)
        self.assertEqual(("REPLY_UNVERIFIED", "测试曲目。"), (saved["state"], saved["submitted_text"]))
        self.assertIn(REPLY, saved["before_reply_ids"])

    async def test_the_reply_reads_back_by_its_new_id_and_time(self):
        path = self.receipt("reply", state="REPLY_READY", request={"attempt": "this-attempt"})
        await ops.execute(self.page, "x", OWNER, await self.reply_payload(path))
        saved = {**read_json(path), "at": "2026-01-01T00:00:00+00:00"}
        payload = {**(await self.reply_payload(path)), "operation": "verify_reply", "existing_reply": saved}
        result = await ops.execute(self.page, "x", OWNER, payload)
        self.assertEqual("REPLY_VERIFIED", result["state"])
        self.assertEqual(1, len(self.x.writes))

    async def test_a_changed_comment_opens_no_reply_box(self):
        path = self.receipt("reply")
        payload = await self.reply_payload(path)
        payload["reply_request"]["comment"]["text"] = "Different words"
        with self.assertRaises(VpubError) as caught:
            await ops.execute(self.page, "x", OWNER, payload)
        self.assertEqual("comment_changed", caught.exception.code)
        self.assertEqual([], self.x.writes)

    def hide_request(self, comment=REPLY, text="这首歌叫什么？", desired=True):
        return {"comment": {"id": comment, "text": text, "author": "viewer", "author_id": "viewer", "conversation_work_id": POST}, "work_id": POST, "identity": OWNER, "kind": "hide", "desired": desired}

    async def test_hiding_a_reply_goes_through_the_sites_confirmation(self):
        path = self.receipt("hide", state="COMMENT_READY")
        result = await comment_actions_x.execute(self.page, OWNER, {"request": self.hide_request(), "operation": "set", "receipt_path": path})
        self.assertEqual("COMMENT_UNVERIFIED", result["state"])
        self.assertEqual([("hide", REPLY)], self.x.writes)

    async def test_a_reply_already_hidden_is_found_on_its_own_page(self):
        status = await comment_actions_x.execute(self.page, OWNER, {"request": self.hide_request(HIDDEN, "Hidden words"), "operation": "status"})
        self.assertEqual("/owner/status/" + POST + "/hidden", urlsplit(self.page.url).path)
        self.assertIs(True, status["hidden"])
        path = self.receipt("unhide", state="COMMENT_READY")
        await comment_actions_x.execute(self.page, OWNER, {"request": self.hide_request(HIDDEN, "Hidden words", False), "operation": "set", "receipt_path": path})
        self.assertEqual([("unhide", HIDDEN)], self.x.writes)

    async def test_a_reply_on_neither_page_is_reported_as_missing(self):
        with patch.object(self.page, "wait_for_timeout", side_effect=self.no_wait):
            original = self.page.set_default_timeout
            original(1500)
            with self.assertRaises(VpubError) as caught:
                await comment_actions_x.execute(self.page, OWNER, {"request": self.hide_request("2115117485020135999", "Gone"), "operation": "status"})
        self.assertIn(caught.exception.code, {"hidden_replies_unavailable", "layout_changed"})
        self.assertEqual([], self.x.writes)

    async def no_wait(self, ms):
        return None

    def delete_payload(self, operation="set", comment=MINE, text="My own note", is_owner=True, receipt=None):
        return {"operation": operation, "receipt_path": receipt, "request": {"identity": OWNER, "work_id": POST, "kind": "delete", "desired": True,
                                                                             "comment": {"id": comment, "text": text, "is_owner": is_owner}}}

    async def test_deleting_your_own_reply_confirms_once_and_reads_back_as_gone(self):
        path = self.receipt("delete", state="COMMENT_READY")
        before = await comment_actions_x.delete_own(self.page, OWNER, self.delete_payload("status"))
        self.assertEqual((False, "My own note"), (before["deleted"], before["text"]))
        result = await comment_actions_x.delete_own(self.page, OWNER, self.delete_payload(receipt=path))
        self.assertEqual(("COMMENT_UNVERIFIED", 1), (result["state"], result["platform_writes"]))
        self.assertEqual([("delete", MINE)], self.x.writes)
        self.assertEqual("COMMENT_UNVERIFIED", read_json(path)["state"])
        self.assertIs(True, (await comment_actions_x.delete_own(self.page, OWNER, self.delete_payload("status")))["deleted"])

    async def test_an_unknown_page_does_not_count_as_deleted(self):
        self.x.replies = [row for row in self.x.replies if row["id"] != MINE]
        self.x.gone_words = "Something went wrong."
        result = await comment_actions_x.delete_own(self.page, OWNER, self.delete_payload("status"))
        self.assertIsNone(result["deleted"])

    async def test_someone_elses_reply_is_refused_before_anything(self):
        with self.assertRaises(VpubError) as caught:
            await comment_actions_x.delete_own(self.page, OWNER, self.delete_payload(comment=REPLY, text="这首歌叫什么？", is_owner=False))
        self.assertEqual("own_comment_required", caught.exception.code)
        self.assertEqual([], self.x.writes)

    async def test_a_reply_that_changed_is_not_deleted(self):
        with self.assertRaises(VpubError) as caught:
            await comment_actions_x.delete_own(self.page, OWNER, self.delete_payload(text="Different words", receipt=self.receipt("delete", state="COMMENT_READY")))
        self.assertEqual("comment_changed", caught.exception.code)
        self.assertEqual([], self.x.writes)

    def edit_payload(self, text="New words"):
        return {"patch": {"post_text": text, "title": text}, "receipt": {"id": POST}, "edit_receipt_path": str(self.root / "edit.json")}

    async def test_an_edit_replaces_the_posts_words_through_its_own_menu(self):
        result = await browser_edits.execute(self.page, "x", OWNER, self.edit_payload())
        self.assertEqual("EDIT_VERIFIED", result["state"])
        self.assertEqual([("edit", POST, "New words")], self.x.writes)
        self.assertEqual("New words", self.x.post_text)

    async def test_words_already_on_the_post_are_not_saved_again(self):
        """From a review: the words were already there, and it still filled the box and pressed Update."""
        result = await browser_edits.execute(self.page, "x", OWNER, self.edit_payload(self.x.post_text))
        self.assertEqual(("EDIT_VERIFIED", True, 0), (result["state"], result["unchanged"], result["platform_writes"]))
        self.assertEqual([], self.x.writes)

    async def test_the_same_words_elsewhere_on_the_page_do_not_confirm_an_edit(self):
        """From a review: the read-back looked at the whole page, so a reply saying the same words passed for this post."""
        self.x.keeps_edits = False
        words = self.x.replies[0]["text"]
        with patch.object(browser_edits, "READ_BACK_WAIT", 10):
            result = await browser_edits.execute(self.page, "x", OWNER, self.edit_payload(words))
        self.assertEqual("EDIT_SUBMITTED_UNVERIFIED", result["state"])

    async def test_checking_an_edit_changes_nothing(self):
        payload = {**self.edit_payload(self.x.post_text), "verify_only": True}
        result = await browser_edits.execute(self.page, "x", OWNER, payload)
        self.assertEqual(("EDIT_VERIFIED", 0), (result["state"], result["platform_writes"]))
        self.assertEqual([], self.x.writes)


class EarlierSiteReadingTests(ReadingTests):
    site = FakeEarlierX


class EarlierSiteChangeTests(ChangeTests):
    site = FakeEarlierX


class AccountTests(Site):
    async def test_a_signed_out_page_names_no_account(self):
        await self.page.goto("https://x.com/home")
        self.assertIsNone(await rt.identity(self.page, "x"))


class WatchedRouteTests(unittest.TestCase):
    def test_the_route_still_asks_the_browser_for_x(self):
        with patch.object(engagement.adapters, "browser_call", return_value={"identity": OWNER}) as call:
            engagement.dispatch("x", "status", POST)
        self.assertEqual(("x", "engagement", "default"), call.call_args.args)


if __name__ == "__main__":
    unittest.main()

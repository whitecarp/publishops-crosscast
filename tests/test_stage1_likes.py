"""Likes, saves and comment likes on the platforms the other tests only read: Instagram and Douyin in an invisible browser against small page copies, Bilibili through a fake bili-cli. Nothing is contacted."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).parent))
from stage_browser import SharedBrowser

from video_publisher import adapters, comment_actions, comment_actions_browser, comment_actions_douyin, engagement, engagement_browser, safety
from video_publisher.core import VpubError, atomic_json, digest, home, read_json

OWNER = "owner"
POST = "Cabcde12345"
VIDEO = "7300000000000000001"
BVID = "BV1xx411c7mD"


class Pages(SharedBrowser):
    def prepare(self):
        self.pressed = []
        self.check = ""
        self.receipt = self.root / "action.json"
        atomic_json(self.receipt, {"state": "ACTION_READY"})

    def note(self, route):
        self.pressed.append((parse_qs(urlsplit(route.request.url).query)["what"][0], read_json(self.receipt)["state"]))

    async def refused(self, code, call):
        with self.assertRaises(VpubError) as caught:
            await call()
        self.assertEqual(code, caught.exception.code)
        await self.page.evaluate("1")
        self.assertEqual([], self.pressed)


class InstagramLikeTests(Pages):
    def prepare(self):
        super().prepare()
        self.liked = False
        self.saved = False
        self.path = f"/p/{POST}/"

    async def handle(self, route):
        url = urlsplit(route.request.url)
        if route.request.method == "POST":
            self.note(route)
            return await self.json_at(route, {})
        if url.path != self.path:
            return await self.page_at(route, f"<script>location.replace('{self.path}')</script>")
        press = "fetch('/act?what=WHAT', {method: 'POST'})"
        await self.page_at(route, f"""{self.check}<section><section><button onclick="{press.replace('WHAT', 'post-like')}"><svg width="20" height="20" aria-label="{'Unlike' if self.liked else 'Like'}"></svg></button>
          <svg width="20" height="20" aria-label="Comment"></svg><button onclick="{press.replace('WHAT', 'post-save')}"><svg width="20" height="20" aria-label="{'Remove' if self.saved else 'Save'}"></svg></button></section>
          <section><button onclick="{press.replace('WHAT', 'comment-like')}"><svg width="20" height="20" aria-label="Like"></svg></button>a viewer's comment</section></section>""")

    async def set_it(self, kind="like", desired=True):
        return await engagement_browser.execute(self.page, "instagram", OWNER, {"operation": "set", "work_id": POST, "kind": kind, "desired": desired, "receipt_path": str(self.receipt)})

    async def test_a_like_marks_the_receipt_then_presses_the_posts_own_heart_once(self):
        result = await self.set_it()
        await self.page.evaluate("1")
        self.assertEqual(("ACTION_UNVERIFIED", 1), (result["state"], result["platform_writes"]))
        self.assertEqual([("post-like", "ACTION_SUBMITTING")], self.pressed)

    async def test_a_save_presses_only_save(self):
        await self.set_it("save")
        await self.page.evaluate("1")
        self.assertEqual([("post-save", "ACTION_SUBMITTING")], self.pressed)

    async def test_what_is_already_so_presses_nothing(self):
        self.liked = True
        self.assertEqual("NO_CHANGE", (await self.set_it())["state"])
        self.assertEqual([], self.pressed)
        self.assertEqual("ACTION_READY", read_json(self.receipt)["state"])

    async def test_undoing_presses_the_same_heart_once(self):
        self.liked = True
        await self.set_it(desired=False)
        await self.page.evaluate("1")
        self.assertEqual([("post-like", "ACTION_SUBMITTING")], self.pressed)

    async def test_a_stop_holds_it_at_the_last_moment(self):
        self.stop()
        await self.refused("stopped", self.set_it)

    async def test_the_platform_asking_for_a_check_stops_it(self):
        self.check = "<div role='dialog'><h2>Confirm it's you</h2></div>"
        await self.refused("human_verification_required", self.set_it)
        self.assertTrue((home() / "STOP.json").exists())

    async def test_a_page_that_moved_to_another_post_stops_it(self):
        self.path = "/p/Cother00000/"
        await self.refused("wrong_work", self.set_it)


class InstagramCommentLikeTests(Pages):
    COMMENT = "111"

    def prepare(self):
        super().prepare()
        self.liked = False
        self.write_delivery = None
        atomic_json(self.receipt, {"state": "COMMENT_READY"})

    async def handle(self, route):
        if route.request.method == "POST":
            if self.write_delivery is not None:
                await self.write_delivery.wait()
            self.note(route)
            self.liked = not self.liked
            return await self.json_at(route, {})
        press = "fetch('/act?what=WHAT', {method: 'POST'}); this.querySelector('svg').setAttribute('aria-label', 'Unlike')"
        await self.page_at(route, f"""<main><svg aria-label="More options" width="10" height="10"></svg>
          <section><svg width="20" height="20" aria-label="Comment"></svg><svg width="20" height="20" aria-label="Save"></svg><div role="button" onclick="{press.replace('WHAT', 'post')}"><svg width="20" height="20" aria-label="Like"></svg></div></section>
          <div><div><a href="/viewer/">viewer</a><a href="/p/{POST}/c/{self.COMMENT}/"><time datetime="2026-10-30T00:00:00Z">1h</time></a><span dir="auto">Nice</span><div role="button"><span>Reply</span></div></div>
            <div role="button" onclick="{press.replace('WHAT', 'comment')}"><svg width="20" height="20" aria-label="{'Unlike' if self.liked else 'Like'}"></svg></div></div></main>""")

    def request(self, text="Nice"):
        return {"comment": {"id": self.COMMENT, "text": text}, "work_id": POST, "identity": OWNER, "kind": "like", "desired": True}

    async def run_it(self, operation="set", text="Nice"):
        return await comment_actions_browser.execute(self.page, "instagram", OWNER, {"request": self.request(text), "operation": operation, "receipt_path": str(self.receipt)})

    async def test_asking_only_reads(self):
        self.assertIs(False, (await self.run_it("status"))["liked"])
        self.assertEqual([], self.pressed)

    async def test_a_like_marks_the_receipt_then_presses_this_comments_heart_once(self):
        from patchright.async_api import Locator
        click, at_press = Locator.click, []

        async def observe(control, **kwargs):
            if not kwargs.get("trial"):
                at_press.append(read_json(self.receipt)["state"])
            return await click(control, **kwargs)

        # A routed request can arrive after click() returns and the receipt
        # advances. Observe the actual press boundary, then deliberately delay
        # request delivery to prove the assertion does not depend on its timing.
        self.write_delivery = asyncio.Event()
        async with self.page.expect_response(lambda r: r.request.method == "POST") as response:
            with patch.object(Locator, "click", observe):
                result = await self.run_it()
            self.write_delivery.set()
        await response.value
        self.assertEqual(("COMMENT_UNVERIFIED", 1), (result["state"], result["platform_writes"]))
        self.assertEqual(["COMMENT_SUBMITTING"], at_press)
        self.assertEqual([("comment", "COMMENT_UNVERIFIED")], self.pressed)

    async def test_words_that_changed_stop_it(self):
        await self.refused("comment_changed", lambda: self.run_it(text="Other words"))

    async def test_a_stop_holds_it_at_the_last_moment(self):
        self.stop()
        await self.refused("stopped", self.run_it)


class DouyinLikeTests(Pages):
    def prepare(self):
        super().prepare()
        self.detail = {"aweme_id": VIDEO, "user_digged": 0, "collect_stat": 0}
        self.status_code = 0

    async def handle(self, route):
        url = urlsplit(route.request.url)
        if url.path == "/aweme/v1/web/aweme/detail/":
            return await self.json_at(route, {"status_code": self.status_code, "aweme_detail": self.detail})
        if route.request.method == "POST":
            self.note(route)
            return await self.json_at(route, {})
        press = "fetch('/act?what=WHAT', {method: 'POST'})"
        show = "document.getElementById(this.getAttribute('aria-describedby')).hidden = false"
        await self.page_at(route, f"""{self.check}<script>fetch('/aweme/v1/web/aweme/detail/')</script><div data-e2e="detail-video-info" data-e2e-aweme-id="{VIDEO}">
          <div tabindex="0" aria-describedby="tip-like" onmouseenter="{show}" onclick="{press.replace('WHAT', 'like')}">heart</div>
          <div tabindex="0" aria-describedby="tip-save" onmouseenter="{show}" onclick="{press.replace('WHAT', 'save')}">star</div></div>
          <div id="tip-like" hidden>点赞</div><div id="tip-save" hidden>收藏</div>""")

    async def run_it(self, operation="set", kind="like", desired=True):
        return await engagement_browser.execute(self.page, "douyin", OWNER, {"operation": operation, "work_id": VIDEO, "kind": kind, "desired": desired, "receipt_path": str(self.receipt)})

    async def test_the_state_comes_from_the_pages_own_answer_and_not_the_tooltip(self):
        self.detail.update(user_digged=1, collect_stat=0)
        result = await self.run_it("status")
        self.assertEqual((True, False, "official_page_loaded_aweme_detail"), (result["liked"], result["saved"], result["source"]))

    async def test_a_like_marks_the_receipt_then_presses_once(self):
        result = await self.run_it()
        await self.page.evaluate("1")
        self.assertEqual(("ACTION_UNVERIFIED", 1), (result["state"], result["platform_writes"]))
        self.assertEqual([("like", "ACTION_SUBMITTING")], self.pressed)

    async def test_a_save_presses_only_save(self):
        await self.run_it(kind="save")
        await self.page.evaluate("1")
        self.assertEqual([("save", "ACTION_SUBMITTING")], self.pressed)

    async def test_what_is_already_so_presses_nothing(self):
        self.detail["user_digged"] = 1
        self.assertEqual("NO_CHANGE", (await self.run_it())["state"])
        self.assertEqual([], self.pressed)

    async def test_an_answer_about_another_video_stops_it(self):
        self.detail["aweme_id"] = "7300000000000000002"
        await self.refused("state_unavailable", self.run_it)

    async def test_an_answer_without_a_clear_state_stops_it(self):
        self.detail["user_digged"] = "maybe"
        await self.refused("state_unavailable", self.run_it)

    async def test_a_stop_holds_it_at_the_last_moment(self):
        self.stop()
        await self.refused("stopped", self.run_it)


class DouyinCommentTests(Pages):
    COMMENT = "111"

    def prepare(self):
        super().prepare()
        self.row = {"cid": self.COMMENT, "aweme_id": VIDEO, "text": "Nice", "user_digged": 0, "user": {"nickname": "Viewer", "unique_id": "viewer"}, "reply_comment_total": 0}
        self.confirm_words = "确定要删除吗"
        atomic_json(self.receipt, {"state": "COMMENT_READY"})

    async def handle(self, route):
        url = urlsplit(route.request.url)
        if url.path.endswith("/comment/list/select/"):
            return await self.json_at(route, {"status_code": 0, "comments": [self.row], "has_more": False})
        if route.request.method == "POST":
            self.note(route)
            return await self.json_at(route, {})
        press = "fetch('/act?what=WHAT', {method: 'POST'})"
        await self.page_at(route, f"""<div onclick="fetch('/aweme/v1/creator/comment/list/select/')">评论管理</div>
          <div class="comment-item-a"><div class="comment-main-a"><span class="username-a">{self.row['user']['nickname']}</span><div class="text-a">{self.row['text']}</div></div>
            <div class="operations-a"><button onclick="{press.replace('WHAT', 'like')}">12</button><button>down</button><button>回复</button><button onclick="document.getElementById('sure').hidden = false">删除</button><button>report</button></div></div>
          <div role="dialog" id="sure" hidden>{self.confirm_words}<button onclick="{press.replace('WHAT', 'delete')}">confirm</button></div>""")

    async def run_it(self, kind="like", operation="set", verification=False):
        request = {"comment": {"id": self.COMMENT, "text": "Nice"}, "work_id": VIDEO, "identity": OWNER, "kind": kind, "desired": True, "verification": verification}
        return await comment_actions_douyin.execute(self.page, OWNER, {"request": request, "operation": operation, "receipt_path": str(self.receipt)})

    async def test_asking_only_reads(self):
        self.assertIs(False, (await self.run_it(operation="status"))["liked"])
        self.assertEqual([], self.pressed)

    async def test_a_like_marks_the_receipt_then_presses_once(self):
        result = await self.run_it()
        await self.page.evaluate("1")
        self.assertEqual(("COMMENT_UNVERIFIED", 1), (result["state"], result["platform_writes"]))
        self.assertEqual([("like", "COMMENT_SUBMITTING")], self.pressed)

    async def test_what_is_already_so_presses_nothing(self):
        self.row["user_digged"] = 1
        self.assertEqual("NO_CHANGE", (await self.run_it())["state"])
        self.assertEqual([], self.pressed)

    async def test_deleting_your_own_comment_goes_through_the_confirmation_once(self):
        self.row["user"]["unique_id"] = OWNER
        await self.run_it("delete")
        await self.page.evaluate("1")
        self.assertEqual([("delete", "COMMENT_SUBMITTING")], self.pressed)

    async def test_a_viewers_comment_is_never_deleted(self):
        await self.refused("own_comment_required", lambda: self.run_it("delete"))

    async def test_a_comment_with_replies_is_never_deleted(self):
        self.row["user"]["unique_id"] = OWNER
        self.row["reply_comment_total"] = 2
        await self.refused("comment_has_replies", lambda: self.run_it("delete"))

    async def test_a_confirmation_with_other_words_stops_the_delete(self):
        self.row["user"]["unique_id"] = OWNER
        self.confirm_words = "确定要拉黑吗"
        await self.refused("layout_changed", lambda: self.run_it("delete"))

    async def test_a_stop_holds_it_at_the_last_moment(self):
        self.stop()
        await self.refused("stopped", self.run_it)

    async def test_another_account_stops_before_anything(self):
        request = {"comment": {"id": self.COMMENT, "text": "Nice"}, "work_id": VIDEO, "identity": "someone_else", "kind": "like", "desired": True}
        await self.refused("wrong_account", lambda: comment_actions_douyin.execute(self.page, OWNER, {"request": request, "operation": "set", "receipt_path": str(self.receipt)}))


del Pages


class BilibiliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": str(Path(temp.name) / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        self.liked = False
        self.folders = [{"id": 7, "default": True, "has_video": False}, {"id": 8, "default": False, "has_video": False}]
        self.writes = []
        self.lost = False
        for target in (patch.object(adapters, "bili", self.bili), patch.object(adapters, "auth_status", return_value={"identity": "42"}), patch("video_publisher.engagement_api.bili_folders", side_effect=lambda work: [dict(row) for row in self.folders])):
            target.start()
            self.addCleanup(target.stop)

    def bili(self, args, timeout=210, program=None):
        if args[:2] == ["video", "relation"]:
            return {"liked": self.liked}
        self.writes.append(list(args))
        receipts = list((home() / "engagement/receipts").glob("*.json"))
        self.states = [read_json(path)["state"] for path in receipts]
        if self.lost:
            raise VpubError("backend_timeout", "No answer.")
        if args[:2] == ["video", "like"]:
            self.liked = "--undo" not in args
        elif args[:2] == ["video", "fav"]:
            folder = next(row for row in self.folders if str(row["id"]) == args[args.index("--folder") + 1])
            folder["has_video"] = "--remove" not in args
        return {}

    def test_a_like_marks_the_receipt_then_sends_once_and_reads_back(self):
        result = engagement.set_action("bilibili", BVID, "like")
        self.assertEqual("ACTION_VERIFIED", result["state"])
        self.assertEqual([["video", "like", BVID]], self.writes)
        self.assertEqual(["ACTION_SUBMITTING"], self.states)

    def test_undoing_says_so_and_does_not_count(self):
        self.liked = True
        result = engagement.set_action("bilibili", BVID, "like", False)
        self.assertEqual("ACTION_VERIFIED", result["state"])
        self.assertEqual([["video", "like", BVID, "--undo"]], self.writes)
        self.assertEqual({}, safety.status()["used_today"])

    def test_a_save_goes_to_the_chosen_folder(self):
        result = engagement.set_action("bilibili", BVID, "save", True, "8")
        self.assertEqual("ACTION_VERIFIED", result["state"])
        self.assertEqual([["video", "fav", BVID, "--folder", "8"]], self.writes)

    def test_a_folder_that_is_not_listed_is_never_tried(self):
        with self.assertRaises(VpubError) as caught:
            engagement.set_action("bilibili", BVID, "save", True, "9")
        self.assertEqual("collection_required", caught.exception.code)
        self.assertEqual([], self.writes)

    def test_an_unclear_ending_is_never_pressed_again_and_never_undone(self):
        self.lost = True
        first = engagement.set_action("bilibili", BVID, "like")
        self.assertEqual("ACTION_UNVERIFIED", first["state"])
        self.lost = False
        for desired in (True, False):
            with self.subTest(desired=desired):
                self.assertTrue(engagement.set_action("bilibili", BVID, "like", desired)["reused"])
        self.assertEqual(1, len(self.writes))
        self.assertEqual(1, safety.status()["used_today"]["bilibili"]["likes_per_day"])

    def test_another_account_cannot_finish_a_like(self):
        first = engagement.set_action("bilibili", BVID, "like")
        with patch.object(adapters, "auth_status", return_value={"identity": "99"}), self.assertRaises(VpubError) as caught:
            engagement.verify(first["key"])
        self.assertEqual("wrong_account", caught.exception.code)


class AccountTests(unittest.TestCase):
    """The browser platforms remember whose profile this is, and a page showing anyone else stops a like before it is pressed."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": str(Path(temp.name) / "runtime")})
        env.start()
        self.addCleanup(env.stop)

    def expected_on_first_read(self, platform, work):
        with patch.object(adapters, "browser_call", return_value={"identity": "me", "liked": True, "saved": True, "collection": None}) as call, patch.object(adapters, "addon_installed", return_value=False):
            engagement.set_action(platform, work, "like", True)
        return call.call_args_list[0].kwargs.get("expected_identity")

    def test_the_first_read_names_the_account_on_record(self):
        for platform, work in (("x", "1234567890"), ("instagram", "Cabcde12345"), ("douyin", "7300000000000000001"), ("youtube", "abcdefghijk")):
            with self.subTest(platform=platform):
                atomic_json(home() / "accounts/default" / (platform + "-identity.json"), {"identity": "me"})
                self.assertEqual("me", self.expected_on_first_read(platform, work))

    def test_with_no_record_the_page_decides_as_before(self):
        self.assertIsNone(self.expected_on_first_read("x", "1234567890"))

    def test_the_answer_from_the_browser_stops_it_before_anything_is_saved(self):
        atomic_json(home() / "accounts/default/x-identity.json", {"identity": "me"})
        with patch.object(adapters, "browser_call", side_effect=VpubError("wrong_account", "Another account.")), self.assertRaises(VpubError) as caught:
            engagement.set_action("x", "1234567890", "like")
        self.assertEqual("wrong_account", caught.exception.code)
        self.assertFalse((home() / "engagement").exists())
        self.assertEqual({}, safety.status()["used_today"])


if __name__ == "__main__":
    unittest.main()

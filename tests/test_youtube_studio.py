"""Free YouTube works, stats, comments, replies, likes and edits on YouTube's own pages. A fake page; nothing is contacted."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import adapters, cli, edits, engagement, operations as ops, operations_sync, work_catalog, youtube_studio as yt
from video_publisher.core import VpubError, atomic_json, digest, file_sha, home, read_json

CHANNEL = "UC" + "a" * 22
VIDEO = "dQw4w9WgXcQ"
OWN = "mychannel"


def row(comment, text, handle="viewer", author="Viewer", time="1 day ago"):
    return {"id": comment, "text": text, "author": author, "href": f"http://www.youtube.com/@{handle}", "time": time}


class Element:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    @property
    def first(self):
        return self

    def locator(self, selector):
        return Element(self.page, self.selector + " " + selector)

    def exists(self):
        return "verifycenter" not in self.selector and not any(part in self.selector for part in self.page.missing)

    async def all(self):
        return [self] if self.exists() else []

    async def count(self):
        if self.selector == "ytcp-comment-thread":
            return len(self.page.threads)
        if self.selector.endswith("ytcp-comment[comment-id]"):
            return len(self.page.threads[0])
        return 1 if self.exists() else 0

    async def is_visible(self):
        return self.exists()

    async def is_enabled(self):
        return True

    async def click(self, timeout=None):
        self.page.log.append(("click", self.selector))
        if "#submit-button" in self.selector:
            self.page.at_submit = dict(read_json(self.page.receipt)) if self.page.receipt else None
            if self.page.post_reply:
                self.page.threads[0].append(row("new-reply", self.page.typed, OWN, "Me"))
        if "like-button" in self.selector:
            self.page.pressed = "true" if self.page.pressed == "false" else "false"
        if "ytcp-button#save" in self.selector:
            self.page.saved_fields = dict(self.page.fields)
            self.page.save_ready = False

    async def fill(self, value, **options):
        self.page.log.append(("fill", self.selector, value))
        self.page.typed = value
        if "#title-textarea" in self.selector:
            self.page.fields["title"] = value
        if "#description-textarea" in self.selector:
            self.page.fields["description"] = value
        self.page.save_ready = True

    async def input_value(self):
        return "" if self.page.drop_text else self.page.typed

    async def inner_text(self, **options):
        if "#title-textarea" in self.selector:
            return self.page.fields["title"]
        if "#description-textarea" in self.selector:
            return self.page.fields["description"]
        if "#metric-label" in self.selector:
            return "观看次数"
        if "#metric-total" in self.selector:
            return "19"
        return ""

    async def get_attribute(self, name):
        return self.page.pressed if name == "aria-pressed" else None

    async def evaluate(self, script):
        if script == "e => e.hasAttribute('disabled')":
            return self.page.no_replies
        return self.page.save_ready

    async def evaluate_all(self, script):
        if script == yt.COMMENT_ROWS:
            return [list(thread) for thread in self.page.threads]
        return self.page.rows

    async def wait_for(self, **options):
        if not self.exists():
            raise TimeoutError(self.selector)

    async def set_input_files(self, path, **options):
        self.page.log.append(("files", self.selector, str(path)))
        self.page.save_ready = True


class FakePage:
    def __init__(self, **options):
        self.log, self.missing, self.typed, self.at_submit, self.receipt = [], set(), "", None, None
        self.threads = [[row("c1", "这首歌叫什么？")]]
        self.rows = [{"href": f"/video/{VIDEO}/edit", "title": " My title ", "visibility": "公开", "date": "2026年10月30日", "views": "19", "comments": "3", "likes": ""}]
        self.fields, self.saved_fields = {"title": "Old title", "description": "Old text"}, None
        self.url, self.pressed, self.post_reply, self.drop_text, self.save_ready = "https://studio.youtube.com/", "false", True, False, False
        self.no_replies = True
        self.mouse = MagicMock(wheel=AsyncMock())
        for name, value in options.items():
            setattr(self, name, value)

    def locator(self, selector):
        return Element(self, selector)

    def on(self, event, handler):
        pass

    def remove_listener(self, event, handler):
        pass

    def get_by_text(self, pattern, **options):
        return MagicMock(all=AsyncMock(return_value=[]))

    async def goto(self, url, **options):
        self.log.append(("goto", url))
        self.url = url

    async def wait_for_timeout(self, ms):
        return None

    async def content(self):
        return f'"vanityChannelUrl":"http://www.youtube.com/@{OWN}"'

    def clicks(self, part):
        return [entry for entry in self.log if entry[0] == "click" and part in entry[1]]


class Temporary(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        env.start()
        self.addCleanup(env.stop)

    def run_it(self, coroutine):
        return asyncio.run(coroutine)


class ReadingTests(Temporary):
    def test_comments_become_rows_with_their_authors_and_replies(self):
        items = yt.comment_items([[row("c1", "Question", "Viewer"), row("r1", "Answer", OWN, "Me")], [row("c2", "Another", "Other%E5%90%8D")], []], OWN)
        first, reply, second = items
        self.assertEqual(("c1", None, "@viewer", False, True, 1), (first["id"], first["parent_id"], first["author_id"], first["is_owner"], first["owner_replied"], first["reply_count"]))
        self.assertEqual(("r1", "c1", True), (reply["id"], reply["parent_id"], reply["is_owner"]))
        self.assertEqual(("@other名", False), (second["author_id"], second["owner_replied"]))

    def test_an_unknown_own_handle_never_claims_a_comment_or_an_answer(self):
        first = yt.comment_items([[row("c1", "Question", OWN)]], None)[0]
        self.assertFalse(first["is_owner"])
        self.assertIsNone(first["owner_replied"])

    def test_works_come_from_the_content_list_and_ignore_other_rows(self):
        items = yt.work_items([{"href": f"/video/{VIDEO}/edit", "title": " My title ", "visibility": "私享\n", "date": "x", "views": "1.2K", "comments": "—", "likes": ""},
                               {"href": "/channel/x", "title": "Not a video"}], CHANNEL)
        self.assertEqual([VIDEO], list(items))
        self.assertEqual(("My title", "private", 1200, None, f"https://www.youtube.com/watch?v={VIDEO}"), (items[VIDEO]["title"], items[VIDEO]["visibility"], items[VIDEO]["views"], items[VIDEO]["comments"], items[VIDEO]["url"]))

    def test_key_figures_are_read_by_label_in_either_language(self):
        chinese = yt.studio_metrics([{"label": "观看次数", "total": "19\n"}, {"label": "观看时长（小时）", "total": "0.1"}, {"label": "订阅人数", "total": "—"}])
        self.assertEqual((19, 0.1, None), (chinese["views"]["value"], chinese["watch_time_hours"]["value"], chinese["subscribers"]["value"]))
        self.assertEqual("hours", chinese["watch_time_hours"]["unit"])
        english = yt.studio_metrics([{"label": "Views", "total": "1.5K"}])
        self.assertEqual(1500, english["views"]["value"])
        self.assertTrue(english["views"]["approximate"])
        self.assertIsNone(english["subscribers"]["value"])

    def test_the_channels_own_handle_is_read_once_and_kept(self):
        page = FakePage()
        self.assertEqual(OWN, self.run_it(yt.own_handle(page, CHANNEL)))
        self.assertEqual(OWN, self.run_it(yt.own_handle(page, CHANNEL)))
        self.assertEqual(1, len([entry for entry in page.log if entry[0] == "goto"]))
        self.assertNotEqual(OWN, self.run_it(yt.own_handle(FakePage(content=AsyncMock(return_value="")), "UC" + "b" * 22)))

    def test_the_default_filter_that_hides_answered_threads_is_removed(self):
        page = FakePage()
        result = self.run_it(yt.read_comments(page, CHANNEL, VIDEO, 20))
        self.assertEqual(1, len(page.clicks("ytcp-chip #delete-icon")))
        self.assertEqual(["c1"], [c["id"] for c in result["items"]])
        self.assertEqual([], [entry for entry in page.log if entry[0] == "fill"])

    def test_replies_already_showing_are_never_hidden(self):
        # The same button hides replies that are already open, and is disabled on a thread with none.
        for threads, no_replies, clicks in (([[row("c1", "Q"), row("r1", "A", OWN)]], False, 0), ([[row("c1", "Q")]], True, 0), ([[row("c1", "Q")]], False, 1)):
            with self.subTest(rows=len(threads[0]), no_replies=no_replies):
                page = FakePage(threads=threads, no_replies=no_replies)
                self.run_it(yt.read_comments(page, CHANNEL, VIDEO, 20))
                self.assertEqual(clicks, len(page.clicks("#show-replies-button")))

    def test_reading_comments_refuses_a_page_that_left_the_video(self):
        page = FakePage()
        original = page.goto
        async def elsewhere(url, **options):
            await original(url, **options)
            page.url = "https://studio.youtube.com/channel/x"
        page.goto = elsewhere
        with self.assertRaises(VpubError) as caught:
            self.run_it(yt.read_comments(page, CHANNEL, VIDEO, 20))
        self.assertEqual("owner_unverified", caught.exception.code)

    def test_stats_need_studio_to_open_the_video(self):
        page = FakePage(missing={"yta-key-metric-block"})
        with self.assertRaises(VpubError) as caught:
            self.run_it(yt.stats(page, CHANNEL, VIDEO))
        self.assertEqual("owner_unverified", caught.exception.code)

    def test_stats_report_figures_and_what_is_missing(self):
        with patch.object(yt, "visible", new=AsyncMock()):
            result = self.run_it(yt.stats(FakePage(), CHANNEL, VIDEO))
        self.assertEqual("studio_analytics_opened", result["ownership_evidence"])
        self.assertEqual(19, result["metrics"]["views"]["value"])
        self.assertEqual(3, result["metrics"]["comments"]["value"])
        self.assertIsNone(result["metrics"]["likes"]["value"])
        self.assertEqual("unavailable", result["metrics"]["likes"]["availability"])

    def test_operations_the_route_does_not_have_say_so(self):
        for payload in ({"operation": "test_seed", "work_id": VIDEO}, {"operation": "comments", "work_id": VIDEO, "moderation_status": "heldForReview"}):
            with self.subTest(payload=payload), self.assertRaises(VpubError) as caught:
                self.run_it(yt.operations(FakePage(), CHANNEL, payload))
            self.assertEqual("unsupported_operation", caught.exception.code)
        with self.assertRaises(VpubError) as caught:
            self.run_it(yt.operations(FakePage(), CHANNEL, {"operation": "comments", "work_id": VIDEO, "cursor": "x"}))
        self.assertEqual("cursor_unsupported", caught.exception.code)


class ReplyTests(Temporary):
    def payload(self, text="歌名是测试曲目。（AI 回复）", operation="reply", **extra):
        path = self.root / "reply.json"
        atomic_json(path, {"state": "REPLY_READY"})
        request = {"text": text, "language": "zh", "expected_identity": CHANNEL, "comment": {"id": "c1", "text": "这首歌叫什么？"}}
        return {"operation": operation, "work_id": VIDEO, "reply_request": request, "reply_receipt_path": str(path), **extra}

    def test_one_click_saved_first_then_read_back(self):
        payload = self.payload()
        page = FakePage(receipt=payload["reply_receipt_path"])
        result = self.run_it(yt.operations(page, CHANNEL, payload))
        self.assertEqual(("REPLY_VERIFIED", "new-reply", 1), (result["state"], result["reply_id"], result["platform_writes"]))
        self.assertEqual(1, len(page.clicks("#submit-button")))
        self.assertEqual("REPLY_SUBMITTING", page.at_submit["state"])
        self.assertEqual(["c1"], page.at_submit["before_reply_ids"])

    def test_no_read_back_stays_unverified_and_never_sends_again(self):
        payload = self.payload()
        page = FakePage(receipt=payload["reply_receipt_path"], post_reply=False)
        result = self.run_it(yt.operations(page, CHANNEL, payload))
        self.assertEqual("REPLY_UNVERIFIED", result["state"])
        self.assertEqual(1, len(page.clicks("#submit-button")))
        self.assertEqual("REPLY_UNVERIFIED", read_json(payload["reply_receipt_path"])["state"])

    def test_a_changed_comment_or_another_account_stops_before_the_reply_box(self):
        page = FakePage(threads=[[row("c1", "Edited after reading")]])
        with self.assertRaises(VpubError) as caught:
            self.run_it(yt.operations(page, CHANNEL, self.payload()))
        self.assertEqual("comment_changed", caught.exception.code)
        self.assertEqual([], page.clicks("#reply-button"))
        with self.assertRaises(VpubError) as caught:
            self.run_it(yt.operations(FakePage(), "UC" + "b" * 22, self.payload()))
        self.assertEqual("wrong_account", caught.exception.code)

    def test_text_the_box_did_not_keep_is_not_sent(self):
        page = FakePage(drop_text=True)
        with self.assertRaises(VpubError) as caught:
            self.run_it(yt.operations(page, CHANNEL, self.payload()))
        self.assertEqual("metadata_mismatch", caught.exception.code)
        self.assertEqual([], page.clicks("#submit-button"))

    def test_a_stop_file_stops_it_at_the_last_moment(self):
        atomic_json(home() / "STOP.json", {"reason": "test"})
        page = FakePage()
        with self.assertRaises(VpubError):
            self.run_it(yt.operations(page, CHANNEL, self.payload()))
        self.assertEqual([], page.clicks("#submit-button"))

    def test_verify_only_reads(self):
        text = "歌名是测试曲目。（AI 回复）"
        page = FakePage(threads=[[row("c1", "这首歌叫什么？"), row("r9", text, OWN)]])
        payload = self.payload(text, "verify_reply", existing_reply={"before_reply_ids": ["c1"], "submitted_text": text})
        result = self.run_it(yt.operations(page, CHANNEL, payload))
        self.assertEqual(("REPLY_VERIFIED", "r9", 0), (result["state"], result["reply_id"], result["platform_writes"]))
        self.assertEqual([], page.clicks("#reply-button"))
        self.assertEqual("REPLY_UNVERIFIED", self.run_it(yt.operations(FakePage(), CHANNEL, payload))["state"])

    def test_only_a_new_reply_by_this_channel_under_this_comment_counts(self):
        items = [{"id": "old", "is_owner": True, "text": "Hi", "parent_id": "c1"}, {"id": "other", "is_owner": False, "text": "Hi", "parent_id": "c1"},
                 {"id": "elsewhere", "is_owner": True, "text": "Hi", "parent_id": "c2"}, {"id": "words", "is_owner": True, "text": "Bye", "parent_id": "c1"}]
        self.assertEqual([], yt.find_reply(items, "Hi", {"old"}, "c1"))
        self.assertEqual(["new"], [c["id"] for c in yt.find_reply([*items, {"id": "new", "is_owner": True, "text": " Hi ", "parent_id": "c1"}], "Hi", {"old"}, "c1")])


class LikeTests(Temporary):
    def payload(self, operation="status", **extra):
        path = self.root / "like.json"
        atomic_json(path, {"state": "ACTION_READY"})
        return {"operation": operation, "work_id": VIDEO, "receipt_path": str(path), **extra}

    def test_status_reads_the_button_and_a_like_clicks_once(self):
        page = FakePage()
        page.goto = AsyncMock(side_effect=lambda url, **options: setattr(page, "url", "https://www.youtube.com/watch?v=" + VIDEO))
        self.assertFalse(self.run_it(yt.engagement(page, CHANNEL, self.payload()))["liked"])
        payload = self.payload("set", kind="like", desired=True)
        self.assertEqual("ACTION_UNVERIFIED", self.run_it(yt.engagement(page, CHANNEL, payload))["state"])
        self.assertEqual("ACTION_SUBMITTING", read_json(payload["receipt_path"])["state"])
        self.assertEqual(1, len(page.clicks("like-button")))
        self.assertEqual("NO_CHANGE", self.run_it(yt.engagement(page, CHANNEL, self.payload("set", kind="like", desired=True)))["state"])

    def test_an_unknown_state_or_a_save_changes_nothing(self):
        page = FakePage(pressed=None)
        page.goto = AsyncMock(side_effect=lambda url, **options: setattr(page, "url", "https://www.youtube.com/watch?v=" + VIDEO))
        with self.assertRaises(VpubError) as caught:
            self.run_it(yt.engagement(page, CHANNEL, self.payload("set", kind="like", desired=True)))
        self.assertEqual("state_unavailable", caught.exception.code)
        with self.assertRaises(VpubError) as caught:
            self.run_it(yt.engagement(page, CHANNEL, self.payload("set", kind="save", desired=True)))
        self.assertEqual("state_unavailable", caught.exception.code)
        self.assertEqual([], page.clicks("like-button"))


class EditTests(Temporary):
    def run_edit(self, page, patch_value, **options):
        output = self.root / "edit.json"

        def save(**fields):
            atomic_json(output, fields)
            return fields

        async def submit(button):
            save(state="EDIT_SUBMITTING")
            await button.click(timeout=20000)
            page.at_save = read_json(output)
        payload = {"patch": patch_value, "receipt": {"id": VIDEO}, **options}
        return self.run_it(yt.edit(page, payload, save, submit))

    def test_new_text_is_saved_once_and_read_back(self):
        page = FakePage()
        result = self.run_edit(page, {"title": "New title", "description": "New text"})
        self.assertEqual(("EDIT_VERIFIED", 1), (result["state"], result["platform_writes"]))
        self.assertEqual(1, len(page.clicks("ytcp-button#save")))
        self.assertEqual("EDIT_SUBMITTING", page.at_save["state"])
        self.assertEqual({"title": "New title", "description": "New text"}, page.saved_fields)

    def test_matching_text_writes_nothing(self):
        page = FakePage(fields={"title": "Same", "description": "Text"})
        result = self.run_edit(page, {"title": "Same", "description": "Text"})
        self.assertEqual(("EDIT_VERIFIED", True, 0), (result["state"], result["unchanged"], result["platform_writes"]))
        self.assertEqual([], page.clicks("ytcp-button#save"))

    def test_verify_only_never_saves(self):
        page = FakePage()
        self.assertEqual("EDIT_MISMATCH", self.run_edit(page, {"title": "New", "description": "Text"}, verify_only=True)["state"])
        self.assertEqual([], page.clicks("ytcp-button#save"))

    def test_a_save_that_never_enables_stops(self):
        page = FakePage()
        original = Element.fill
        async def keep_disabled(self, value, **options):
            await original(self, value, **options)
            self.page.save_ready = False
        with patch.object(Element, "fill", new=keep_disabled), self.assertRaises(VpubError) as caught:
            self.run_edit(page, {"title": "New", "description": "Text"})
        self.assertEqual("edit_not_ready", caught.exception.code)
        self.assertEqual([], page.clicks("ytcp-button#save"))

    def test_a_video_studio_will_not_open_is_refused(self):
        with self.assertRaises(VpubError) as caught:
            self.run_edit(FakePage(missing={"#title-textarea"}), {"title": "New", "description": "Text"})
        self.assertEqual("owner_unverified", caught.exception.code)

    def test_a_thumbnail_is_sent_and_says_its_pixels_are_unverified(self):
        cover = self.root / "cover.jpg"
        cover.write_bytes(b"cover")
        page = FakePage()
        result = self.run_edit(page, {"cover": str(cover), "cover_sha256": file_sha(cover)})
        self.assertIn(("files", "input#file-loader", str(cover)), page.log)
        self.assertFalse(result["cover"]["remote_pixels_verified"])
        cover.write_bytes(b"changed")
        with self.assertRaises(VpubError) as caught:
            self.run_edit(FakePage(), {"cover": str(cover), "cover_sha256": "old"})
        self.assertEqual("source_changed", caught.exception.code)

    def test_chapters_replace_only_the_old_list_in_the_description(self):
        page = FakePage(fields={"title": "Same", "description": "Intro\n\n0:00 Old\n1:00 Older\n\nBye"})
        chapters = [{"start_seconds": 0, "title": "One"}, {"start_seconds": 60, "title": "Two"}, {"start_seconds": 120, "title": "Three"}]
        result = self.run_edit(page, {"chapters": chapters, "duration_seconds": 300})
        self.assertEqual("EDIT_VERIFIED", result["state"])
        self.assertEqual({"title": "Same", "description": "Intro\n\nBye\n\n0:00 One\n1:00 Two\n2:00 Three"}, page.saved_fields)

    def test_a_cover_edit_is_its_own_request_on_youtube_and_bilibili_only(self):
        cover = self.root / "cover.jpg"
        cover.write_bytes(b"cover")
        checked = edits.validate_patch("youtube", {"expected_identity": CHANNEL, "cover": str(cover)})
        self.assertEqual(file_sha(cover), checked["cover_sha256"])
        for platform, value in (("x", {"expected_identity": CHANNEL, "cover": str(cover)}), ("youtube", {"expected_identity": CHANNEL, "cover": str(self.root / "missing.jpg")}),
                                ("youtube", {"expected_identity": CHANNEL, "cover": str(cover), "title": "T"})):
            with self.subTest(platform=platform, value=value), self.assertRaises(VpubError):
                edits.validate_patch(platform, value)


class RouteTests(Temporary):
    def setUp(self):
        super().setUp()
        for active in (patch.object(adapters.importlib.util, "find_spec", return_value=None), patch.dict(sys.modules, {"crosscast_api": None})):
            active.start()
            self.addCleanup(active.stop)

    def test_operations_use_the_browser_without_a_receipt(self):
        with patch.object(adapters, "browser_call", return_value={"identity": CHANNEL}) as browser:
            ops.backend("youtube", "stats", VIDEO)
        self.assertEqual(("youtube", "operations", "default"), browser.call_args.args)
        self.assertEqual({"operation": "stats", "work_id": VIDEO}, browser.call_args.kwargs)

    def test_the_add_on_keeps_the_api_route(self):
        with patch.object(adapters.importlib.util, "find_spec", return_value=object()), patch.object(ops, "backend", wraps=ops.backend), \
                patch("video_publisher.operations_api.execute", return_value={"identity": CHANNEL}) as api, patch.object(adapters, "browser_call") as browser:
            ops.backend("youtube", "stats", VIDEO)
        api.assert_called_once()
        browser.assert_not_called()

    def test_the_works_list_uses_the_browser_and_has_no_cursor(self):
        items = [{"id": VIDEO}]
        with patch.object(adapters, "browser_call", return_value={"identity": CHANNEL, "items": items}) as browser:
            result = operations_sync.works("youtube", 5)
        self.assertEqual(("youtube", "works", "default"), browser.call_args.args)
        self.assertEqual((items, None, False), (result["items"], result["next_cursor"], result["complete"]))
        with self.assertRaises(VpubError) as caught:
            operations_sync.works("youtube", 5, "cursor")
        self.assertEqual("unsupported_cursor", caught.exception.code)

    def test_adopting_records_that_studio_opened_the_video(self):
        with patch.object(ops, "backend", return_value={"identity": CHANNEL, "title": "T", "url": "u", "ownership_evidence": "studio_analytics_opened"}):
            result = work_catalog.adopt("youtube", VIDEO)
        self.assertEqual(("ADOPTED", "studio_analytics_opened"), (result["state"], result["ownership_evidence"]))

    def test_likes_use_the_browser(self):
        with patch.object(adapters, "browser_call", return_value={"identity": CHANNEL, "liked": False}) as browser:
            engagement.status("youtube", VIDEO)
        self.assertEqual("engagement", browser.call_args.args[1])

    def test_edits_use_the_browser(self):
        from video_publisher.core import Ledger
        ledger = Ledger()
        key = ledger.claim("youtube", "default", "sha", {"identity": {"identity": CHANNEL}, "receipt": {"id": VIDEO, "title": "Old"}, "options": {}})
        ledger.save(key, "SUBMISSION_VERIFIED", {"identity": {"identity": CHANNEL}, "receipt": {"id": VIDEO, "title": "Old"}, "options": {}})
        ledger.close()
        path = self.root / "patch.json"
        atomic_json(path, {"expected_identity": CHANNEL, "title": "New", "description": "Text", "content_language": "en", "metadata_language": "en", "metadata_mode": "target_only"})
        with patch.object(adapters, "browser_call", return_value={"state": "EDIT_VERIFIED"}) as browser:
            result = edits.run(key, path)
        self.assertEqual("EDIT_VERIFIED", result["state"])
        self.assertEqual(("youtube", "edit", "default"), browser.call_args.args)

    def test_the_capabilities_describe_the_route_and_what_is_untried(self):
        self.assertIn("not been tried", cli.capabilities()["youtube_studio_operations"])


if __name__ == "__main__":
    unittest.main()

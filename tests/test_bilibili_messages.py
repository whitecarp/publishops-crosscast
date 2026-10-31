"""Bilibili in the unified inbox. Rows go through bili-cli's real conversion code; no platform is contacted."""
import asyncio
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from bili_cli.commands import dm as bili_dm, feed as bili_feed
from video_publisher import adapters, messages, operations_api
from video_publisher.core import VpubError


def mention(event_id, text, mid=7, name="Viewer"):
    """A mention exactly as `bili feed mentions` builds it from Bilibili's response."""
    raw = {"id": event_id, "at_time": 1800000000, "user": {"mid": mid, "nickname": name},
           "item": {"source_content": text, "uri": "https://www.bilibili.com/video/BV1ab411c7mD", "business_id": 1,
                    "source_id": 55, "subject_id": 66, "title": "My video"}}
    notice = bili_feed._notice(raw, "at_time", None, True)
    return notice


def conversations(*sessions, named=True):
    """Conversations exactly as `bili dm list` builds them from Bilibili's response."""
    names = {session["talker_id"]: f"Name{session['talker_id']}" for session in sessions} if named else {}
    with patch.object(bili_dm, "_sessions", new=AsyncMock(return_value=list(sessions))), patch.object(bili_dm, "_names", new=AsyncMock(return_value=names)):
        return asyncio.run(bili_dm.conversations(SimpleNamespace(dedeuserid="1"), False, 20))


def session(talker, text, sender, source=None):
    last = {"sender_uid": sender, "msg_type": 1, "content": '{"content": "%s"}' % text, "timestamp": 1800000000}
    if source is not None:
        last["msg_source"] = source
    return {"talker_id": talker, "unread_count": 1, "last_msg": last}


class SourceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"BILI_CLI_HOME": str(Path(temp.name) / "bili"), "VPUB_HOME": str(Path(temp.name) / "vpub")})
        env.start()
        self.addCleanup(env.stop)

    def test_mentions_map_to_stable_inbox_rows(self):
        with patch.object(adapters, "bili", return_value={"items": [mention(9, "@me look"), mention(10, "@me", mid=1)], "next_cursor": None}) as bili:
            value = operations_api.bilibili_source("mentions", "1", 20)
        bili.assert_called_once_with(["feed", "mentions", "--limit", "20"])
        self.assertEqual("1", value["identity"])
        self.assertTrue(value["complete"])
        first, own = value["items"]
        self.assertEqual({"id": "9", "text": "Viewer\n@me look", "url": "https://www.bilibili.com/video/BV1ab411c7mD", "is_owner": False}, first)
        self.assertTrue(own["is_owner"])

    def test_a_mention_without_text_says_who_and_where(self):
        # Seen on a real account: a mention in a post has no text, title or comment ID.
        raw = {"id": 11, "at_time": 1800000000, "user": {"mid": 7, "nickname": "Viewer"},
               "item": {"source_content": "", "uri": "https://t.bilibili.com/1", "business": "动态", "subject_id": 66}}
        notice = bili_feed._notice(raw, "at_time", None, False)
        with patch.object(adapters, "bili", return_value={"items": [notice], "next_cursor": None}):
            item = operations_api.bilibili_source("mentions", "1", 20)["items"][0]
        self.assertEqual("Viewer\n[动态]", item["text"])
        self.assertEqual("https://t.bilibili.com/1", item["url"])

    def test_a_conversation_is_never_blank(self):
        # Seen on a real account: no name for some accounts, and no text for some message types.
        rows = conversations({"talker_id": 5, "unread_count": 0}, session(6, "hello", 6), named=False)
        with patch.object(adapters, "bili", return_value={"items": rows}):
            value = operations_api.bilibili_source("dm_preview", "1", 20)
        self.assertEqual(["UID 5", "UID 6\nhello"], [item["preview"] for item in value["items"]])

    def test_a_next_page_means_the_read_is_not_complete(self):
        with patch.object(adapters, "bili", return_value={"items": [], "next_cursor": "9,1"}):
            self.assertFalse(operations_api.bilibili_source("mentions", "1", 20)["complete"])

    def test_previews_use_the_uid_and_show_who_wrote(self):
        rows = conversations(session(2, "hello", 2))
        with patch.object(adapters, "bili", return_value={"items": rows}) as bili:
            value = operations_api.bilibili_source("dm_preview", "1", 20)
        bili.assert_called_once_with(["dm", "list", "--limit", "20"])
        self.assertFalse(value["complete"])
        self.assertEqual([{"conversation_id": "2", "name": "Name2", "preview": "Name2\nhello", "is_owner": False}], value["items"])

    def test_only_a_real_answer_takes_a_conversation_out_of_pending(self):
        rows = conversations(session(2, "their question", 2), session(3, "my answer", 1), session(4, "auto reply", 1, bili_dm.AUTO_REPLY_SOURCE))
        with patch.object(adapters, "bili", return_value={"items": rows}):
            value = operations_api.bilibili_source("dm_preview", "1", 20)
        self.assertEqual({"2": False, "3": True, "4": False}, {row["conversation_id"]: row["is_owner"] for row in value["items"]})

    def test_a_conversation_without_a_readable_last_message_still_lists(self):
        rows = conversations({"talker_id": 5, "unread_count": 0})
        with patch.object(adapters, "bili", return_value={"items": rows}):
            value = operations_api.bilibili_source("dm_preview", "1", 20)
        self.assertEqual("Name5", value["items"][0]["preview"])


class SyncTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"BILI_CLI_HOME": str(Path(temp.name) / "bili"), "VPUB_HOME": str(Path(temp.name) / "vpub")})
        env.start()
        self.addCleanup(env.stop)
        status = patch.object(adapters, "auth_status", return_value={"identity": "1"})
        status.start()
        self.addCleanup(status.stop)
        self.mentions = [mention(9, "private mention text")]
        self.sessions = [session(2, "private dm text", 2), session(3, "my answer", 1), session(4, "auto reply", 1, bili_dm.AUTO_REPLY_SOURCE)]

    def fake_bili(self, args, timeout=210):
        if args[:2] == ["feed", "mentions"]:
            return {"items": self.mentions, "next_cursor": None}
        self.assertEqual(["dm", "list"], args[:2])
        return {"items": conversations(*self.sessions)}

    def sync(self, **options):
        with patch.object(adapters, "bili", side_effect=self.fake_bili):
            return messages.sync("bilibili", "mentions,dm_preview", **options)

    def test_first_read_sets_a_baseline_and_keeps_private_text_out_of_the_report(self):
        run = self.sync()
        self.assertEqual("COLLECTED", run["state"])
        self.assertEqual(["OBSERVED", "OBSERVED"], [source["state"] for source in run["sources"]])
        self.assertTrue(all(source["baseline"] for source in run["sources"]))
        self.assertEqual(0, run["platform_writes"])
        self.assertNotIn("private mention text", str(run))
        self.assertNotIn("private dm text", str(run))

    def test_pending_list_shows_mentions_and_conversations_still_waiting(self):
        self.sync()
        pending = messages.listing(platform="bilibili")["items"]
        self.assertEqual({("mentions", "9"), ("dm_preview", "2"), ("dm_preview", "4")}, {(row["kind"], row["source_id"]) for row in pending})
        everything = messages.listing(platform="bilibili", include_own=True)["items"]
        self.assertEqual(4, len(everything))

    def test_later_read_reports_only_what_is_new_and_keeps_local_marks(self):
        self.sync()
        key = next(row["key"] for row in messages.listing(kind="mentions")["items"])
        messages.mark(key, "handled")
        self.mentions = [*self.mentions, mention(10, "another mention")]
        self.sessions[0] = session(2, "a new question", 2)
        run = self.sync(full=True)
        counts = {source["kind"]: source for source in run["sources"]}
        self.assertEqual(1, counts["mentions"]["newly_observed"])
        self.assertEqual(1, counts["dm_preview"]["changed"])
        rows = {row["source_id"]: row for row in messages.listing(status="all", include_own=True)["items"]}
        self.assertEqual("handled", rows["9"]["status"])
        self.assertEqual("unread", rows["2"]["status"])
        self.assertIn("a new question", rows["2"]["text"])

    def test_a_repeat_within_the_freshness_window_makes_no_request(self):
        self.sync()
        with patch.object(adapters, "bili") as bili:
            run = messages.sync("bilibili", "mentions,dm_preview")
        bili.assert_not_called()
        self.assertEqual(2, run["cached_reads"])

    def test_signed_out_account_is_reported_not_treated_as_empty(self):
        with patch.object(adapters, "auth_status", side_effect=VpubError("not_logged_in", "Sign in.")), patch.object(adapters, "bili") as bili:
            run = messages.sync("bilibili", "mentions,dm_preview")
        bili.assert_not_called()
        self.assertEqual("PARTIAL", run["state"])
        self.assertEqual(["not_logged_in", "not_logged_in"], [source["code"] for source in run["sources"]])

    def test_a_failed_read_makes_the_run_partial_and_can_resume(self):
        with patch.object(adapters, "bili", side_effect=VpubError("risk_control", "Blocked.")):
            first = messages.sync("bilibili", "mentions,dm_preview")
        self.assertEqual("PARTIAL", first["state"])
        self.assertEqual([], messages.listing(platform="bilibili", status="all", include_own=True)["items"])
        with patch.object(adapters, "bili", side_effect=self.fake_bili):
            second = messages.resume(first["run_id"])
        self.assertEqual("COLLECTED", second["state"])

    def test_youtube_is_still_an_explicit_gap(self):
        run = messages.sync("youtube", "mentions,dm_preview")
        self.assertEqual("PARTIAL", run["state"])
        self.assertEqual(["UNSUPPORTED", "UNSUPPORTED"], [source["state"] for source in run["sources"]])


if __name__ == "__main__":
    unittest.main()

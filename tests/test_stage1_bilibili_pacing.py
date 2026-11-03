"""bili-cli's own spacing between writes. Bilibili never sees a request it holds back, so that is neither Bilibili objecting nor an unclear ending. Fakes only; nothing is contacted."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from video_publisher import adapters, cli, collections, comment_actions, direct_messages as dm, edits, engagement, operations as ops, safety
from video_publisher.core import Ledger, VpubError, atomic_json, digest, home, read_json

WORK = "BV1xx411c7mD"
OWNER = "42"
HELD_BACK = "write_spacing"
# What bili-cli's own code answers: a real process, its real limiter and its real error output, with a throwaway settings folder.
# This never reaches its network library, which needs a short install path on Windows, so that library stays out of it.
SPACED_BY_THE_REAL_LIMITER = """import sys, types
sys.modules["bilibili_api"] = types.SimpleNamespace(Credential=object)
from bili_cli import guard
from bili_cli.runtime import run
async def body():
    guard.record("comment", {}, "ok")
    guard.check("comment")
run(body)
"""


class Temporary(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime"), "BILI_CLI_HOME": str(self.root / "bili")})
        env.start()
        self.addCleanup(env.stop)

    def stopped(self):
        return (home() / "STOP.json").exists()

    def used(self, name):
        return safety.status()["used_today"].get("bilibili", {}).get(name, 0)


class WhoSaidItTests(Temporary):
    def test_the_real_limiter_answers_in_the_shape_this_is_about(self):
        with self.assertRaises(VpubError) as caught:
            adapters.bili([], program=SPACED_BY_THE_REAL_LIMITER)
        self.assertEqual(HELD_BACK, caught.exception.code)
        self.assertFalse(self.stopped())

    def test_it_says_when_to_try_again(self):
        with self.assertRaises(VpubError) as caught:
            adapters.bili([], program=SPACED_BY_THE_REAL_LIMITER)
        self.assertTrue(caught.exception.hint)

    def test_a_short_gap_is_slept_through_by_bili_cli_itself(self):
        answer = {"ok": True, "data": {}}
        with patch.object(adapters.subprocess, "run", return_value=MagicMock(stdout=json.dumps(answer))) as run:
            adapters.bili(["comment", "reply"])
        self.assertEqual(["--wait", "comment", "reply"], run.call_args.args[0][3:])

    def test_the_real_command_line_takes_the_switch(self):
        self.assertIn("version", adapters.bili(["schema"]))

    def test_its_own_auth_wait_is_left_to_it(self):
        from bili_cli.main import GLOBAL_FLAGS
        self.assertIn("--wait", GLOBAL_FLAGS)
        answer = {"ok": True, "data": {}}
        with patch.object(adapters.subprocess, "run", return_value=MagicMock(stdout=json.dumps(answer))) as run:
            adapters.bili(["auth", "status", "--wait", "120"], signing_in=True)
        self.assertEqual(["--wait", "auth", "status", "--wait", "120"], run.call_args.args[0][3:])

    def test_a_gap_longer_than_it_will_sleep_still_comes_back_held(self):
        spaced = SPACED_BY_THE_REAL_LIMITER.replace('guard.check("comment")', 'import bili_cli.output as out; out.settings.wait = True; guard.LIMITS["comment"] = (3600, 20); guard.check("comment")')
        with self.assertRaises(VpubError) as caught:
            adapters.bili([], program=spaced)
        self.assertEqual(HELD_BACK, caught.exception.code)

    def test_a_short_gap_goes_through_once_it_has_passed(self):
        spaced = SPACED_BY_THE_REAL_LIMITER.replace('guard.check("comment")', 'import bili_cli.output as out; out.settings.wait = True; guard.LIMITS["comment"] = (1, 20); guard.check("comment"); out.success({"waited": True})')
        self.assertEqual({"waited": True}, adapters.bili([], program=spaced))

    def test_bilibili_itself_saying_slow_down_still_stops_everything(self):
        answer = {"ok": False, "error": {"code": "rate_limited", "message": "B 站返回错误 12014", "detail": {"bili_code": 12014}}}
        with patch.object(adapters.subprocess, "run", return_value=MagicMock(stdout=json.dumps(answer))), self.assertRaises(VpubError) as caught:
            adapters.bili(["comment", "reply"])
        self.assertEqual("rate_limited", caught.exception.code)
        self.assertTrue(self.stopped())

    def test_an_answer_with_no_detail_counts_as_bilibili_objecting(self):
        answer = {"ok": False, "error": {"code": "rate_limited", "message": "No."}}
        with patch.object(adapters.subprocess, "run", return_value=MagicMock(stdout=json.dumps(answer))), self.assertRaises(VpubError):
            adapters.bili(["comment", "reply"])
        self.assertTrue(self.stopped())


class Spaced(Temporary):
    """A fake bili-cli that holds back the write and, once told, lets it through."""

    def setUp(self):
        super().setUp()
        self.holding = True
        self.sent = []
        for target in (patch.object(adapters, "bili", self.bili), patch.object(adapters, "auth_status", return_value={"identity": OWNER})):
            target.start()
            self.addCleanup(target.stop)

    def bili(self, args, timeout=210, program=None):
        answer = self.read(args)
        if answer is not None:
            return answer
        if self.holding:
            raise VpubError(HELD_BACK, "comment 操作间隔至少 10 秒。", "等待 7 秒后重试。")
        self.sent.append(list(args))
        return self.written(args)

    def read(self, args):
        if args[:2] == ["video", "info"]:
            return {"owner": {"mid": 42}}
        if args[:3] == ["danmaku", "interact", "list"]:
            return {"items": []}
        return None

    def written(self, args):
        return {}


class ReplyTests(Spaced):
    def setUp(self):
        super().setUp()
        atomic_json(home() / "operations/comments" / (digest(["bilibili", WORK]) + ".json"), {"identity": OWNER, "items": [
            {"id": "7", "text": "Question", "author": "V", "author_id": "99", "is_owner": False, "owner_replied": False, "language_hint": "und"}]})

    def read(self, args):
        if args[:2] == ["comment", "replies"]:
            mine = [{"rpid": 8, "parent": "7", "content": "Answer (AI reply)", "user": {"mid": 42}}] if self.sent else []
            return {"root": {"rpid": 7, "content": "Question"}, "items": mine}
        return super().read(args)

    def written(self, args):
        return {"rpid": 8}

    def reply(self):
        return ops.reply("bilibili", WORK, "7", "Answer", "en")

    def test_a_reply_held_back_gives_its_count_back_and_can_be_sent_again(self):
        with self.assertRaises(VpubError) as caught:
            self.reply()
        self.assertEqual(HELD_BACK, caught.exception.code)
        self.assertEqual(0, self.used("replies_per_day"))
        self.assertFalse(self.stopped())
        self.holding = False
        self.assertEqual("REPLY_VERIFIED", self.reply()["state"])
        self.assertEqual(1, len(self.sent))
        self.assertEqual(1, self.used("replies_per_day"))

    def test_a_reply_that_may_have_gone_is_still_never_sent_again(self):
        def lost(args, timeout=210, program=None):
            if args[:2] == ["comment", "reply"]:
                self.sent.append(list(args))
                raise VpubError("backend_timeout", "No answer.")
            return self.read(args) or {}
        with patch.object(adapters, "bili", lost), self.assertRaises(VpubError):
            self.reply()
        with patch.object(adapters, "bili", lost), patch.object(ops, "backend") as backend:
            self.assertTrue(self.reply()["reused"])
        backend.assert_not_called()
        self.assertEqual(1, len(self.sent))


class MessageTests(Spaced):
    def read(self, args):
        if args[:2] == ["dm", "read"]:
            mine = [{"from": "me", "seqno": 2, "text": "Hello (AI reply)"}] if self.sent else []
            return {"messages": [{"from": "them", "seqno": 1, "text": "Hi"}, *mine]}
        return super().read(args)

    def written(self, args):
        return {"sent": True, "msg_keys": ["k"]}

    def test_a_message_held_back_is_not_called_unverified_and_can_be_sent_again(self):
        first = dm.send("bilibili", "99", "Hello", "en")
        self.assertEqual(("DM_FAILED", HELD_BACK, 0), (first["state"], first["error_code"], first["platform_writes"]))
        self.assertEqual(0, self.used("messages_per_day"))
        self.assertFalse(self.stopped())
        self.holding = False
        self.assertEqual("DM_VISIBLE", dm.send("bilibili", "99", "Hello", "en")["state"])
        self.assertEqual(1, len(self.sent))
        self.assertEqual(1, self.used("messages_per_day"))


class LikeTests(Spaced):
    def setUp(self):
        super().setUp()
        self.liked = False
        for target in (patch("video_publisher.engagement_api.bili_folders", return_value=[{"id": 1, "default": True, "has_video": False}]),):
            target.start()
            self.addCleanup(target.stop)

    def read(self, args):
        if args[:2] == ["video", "relation"]:
            return {"liked": self.liked}
        return super().read(args)

    def written(self, args):
        self.liked = True
        return {}

    def test_a_like_held_back_gives_its_count_back_and_can_be_pressed_again(self):
        first = engagement.set_action("bilibili", WORK, "like")
        self.assertEqual(("ACTION_FAILED", HELD_BACK, 0), (first["state"], first["error_code"], first["platform_writes"]))
        self.assertEqual(0, self.used("likes_per_day"))
        self.assertFalse(self.stopped())
        self.holding = False
        self.assertEqual("ACTION_VERIFIED", engagement.set_action("bilibili", WORK, "like")["state"])
        self.assertEqual(1, self.used("likes_per_day"))


class CommentLikeTests(Spaced):
    def setUp(self):
        super().setUp()
        self.liked = 0
        atomic_json(home() / "operations/comments" / (digest(["bilibili", WORK]) + ".json"), {"identity": OWNER, "items": [{"id": "7", "text": "Question", "is_owner": False}]})

    def read(self, args):
        if args[:2] == ["comment", "replies"]:
            return {"root": {"rpid": 7, "content": {"message": "Question"}, "action": self.liked}, "replies": []}
        return super().read(args)

    def written(self, args):
        self.liked = 1
        return {}

    def test_a_comment_like_held_back_gives_its_count_back_and_can_be_pressed_again(self):
        first = comment_actions.mutate("bilibili", WORK, "7", "like")
        self.assertEqual(("COMMENT_FAILED", HELD_BACK, 0), (first["state"], first["error_code"], first["platform_writes"]))
        self.assertEqual(0, self.used("likes_per_day"))
        self.assertFalse(self.stopped())
        self.holding = False
        self.assertEqual("COMMENT_VERIFIED", comment_actions.mutate("bilibili", WORK, "7", "like")["state"])
        self.assertEqual(1, self.used("likes_per_day"))


class CollectionTests(Spaced):
    def setUp(self):
        super().setUp()
        self.title = "Old"

    def read(self, args):
        if args[:2] == ["season", "list"]:
            return {"seasons": [{"id": 5, "title": self.title}]}
        return super().read(args)

    def written(self, args):
        self.title = args[2]
        return {}

    def test_a_rename_held_back_says_nothing_went_out(self):
        result = collections.change("bilibili", "rename", "5", "New", "request-1")
        self.assertEqual(("FAILED_BEFORE_SUBMIT", 0, HELD_BACK), (result["state"], result["platform_writes"], result["error"]))
        self.assertFalse(self.stopped())


class UploadTests(Spaced):
    def setUp(self):
        super().setUp()
        tool = patch("video_publisher.rehearsal.shutil.which", return_value="ffmpeg.exe")
        tool.start()
        self.addCleanup(tool.stop)

    def plan(self):
        return {"confirmation": "hash", "video_sha256": "a" * 64, "platforms": {"bilibili": {"account": "default", "title": "T", "description": "D", "tags": ["a"], "visibility": "public", "ai_generated": True, "method": "cli"}}, "video": "v.mp4"}

    def read(self, args):
        if args[:2] == ["upload", "status"]:
            return {"archive": {"bvid": WORK}}
        return super().read(args)

    def written(self, args):
        return {"bvid": WORK}

    def publish(self):
        with patch.object(cli, "snapshot_plan", side_effect=lambda plan, platform: plan):
            return cli.publish_plan(self.plan(), "hash", reviewed=True)

    def test_a_second_upload_held_back_keeps_nothing_and_can_run_again(self):
        first = self.publish()["results"]["bilibili"]
        self.assertEqual(("NOT_STARTED", HELD_BACK), (first["state"], first["code"]))
        self.assertFalse(self.stopped())
        ledger = Ledger()
        self.assertEqual([], ledger.rows())
        ledger.close()
        self.holding = False
        self.assertEqual("SUBMISSION_VERIFIED", self.publish()["results"]["bilibili"]["state"])
        self.assertEqual(1, len(self.sent))

    def test_a_draft_held_back_keeps_nothing_and_can_run_again(self):
        plan = self.plan()
        plan["platforms"]["bilibili"]["submission_mode"] = "draft_only"

        def bili(args, timeout=210, program=None):
            if args == ["list"]:
                return {"drafts": []}
            raise VpubError(HELD_BACK, "upload 操作间隔至少 300 秒。", "等待。")
        with patch.object(adapters, "bili", bili), patch.object(cli, "snapshot_plan", side_effect=lambda plan, platform: plan):
            result = cli.publish_plan(plan, "hash", reviewed=True)["results"]["bilibili"]
        self.assertEqual(("NOT_STARTED", HELD_BACK), (result["state"], result["code"]))
        ledger = Ledger()
        self.assertEqual([], ledger.rows())
        ledger.close()


class EditTests(Spaced):
    TEXT = {"expected_identity": OWNER, "title": "New title", "description": "New text", "content_language": "zh-Hans", "metadata_language": "zh-Hans", "metadata_mode": "target_only"}

    def read(self, args):
        if args[:2] == ["upload", "status"]:
            return {"archive": {"bvid": WORK, "title": "New title" if self.sent else "Old", "desc": "New text" if self.sent else "Old"}}
        if args[:3] == ["upload", "chapters", "show"]:
            return {"chapters": []}
        return super().read(args)

    def test_an_edit_held_back_can_be_tried_again_and_stops_nothing(self):
        atomic_json(home() / "operations/adopted" / (digest(["bilibili", WORK]) + ".json"), {"platform": "bilibili", "work_id": WORK, "identity": OWNER, "title": "Old"})
        patch_path = self.root / "patch.json"
        atomic_json(patch_path, self.TEXT)
        with self.assertRaises(VpubError) as caught:
            edits.run_adopted("bilibili", WORK, patch_path)
        self.assertEqual(HELD_BACK, caught.exception.code)
        self.assertFalse(self.stopped())
        self.holding = False
        self.assertEqual("EDIT_VERIFIED", edits.run_adopted("bilibili", WORK, patch_path)["state"])
        self.assertEqual(1, len(self.sent))

    def test_chapters_held_back_after_an_upload_are_tried_again_by_verify(self):
        from video_publisher.edits_bilibili import finish_chapters
        wanted = [{"start_seconds": 0, "title": "One"}]
        result = finish_chapters({"id": WORK, "state": "SUBMISSION_VERIFIED", "pending_chapters": wanted, "chapters_state": "PENDING"}, lambda mark: None)
        self.assertEqual(("NOT_APPLIED", HELD_BACK), (result["chapters_state"], result["chapters_error"]))
        self.assertEqual([], self.sent)


if __name__ == "__main__":
    unittest.main()

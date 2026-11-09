"""Field findings: a publish that worked read as a failure while its chapters waited, the next step for chapters in review
didn't show in vpub status, and a person's own words got the AI label with nothing saying why."""
import os
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import cli, direct_messages as dm, operations as ops, overview
from video_publisher.core import atomic_json, digest, home

KEY = "a" * 64
IN_REVIEW = "Run vpub verify for this receipt after platform review; chapters are not yet applied."


class Temporary(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)


class LeftToFinishTests(Temporary):
    def test_a_verified_work_waiting_for_its_chapters_is_published_with_what_is_left(self):
        results = {"bilibili": {"key": KEY, "state": "SUBMISSION_VERIFIED", "id": "BV1test", "chapters_state": "PENDING"},
                   "youtube": {"key": "b" * 64, "state": "SUBMISSION_VERIFIED", "id": "abcdefghijk"}}
        left = cli.left_to_finish(results)
        self.assertEqual(["bilibili"], list(left))
        self.assertEqual((True, ["chapters"], f"vpub verify {KEY}"),
                         (left["bilibili"]["published"], left["bilibili"]["parts"], left["bilibili"]["next_command"]))
        self.assertIn("Never upload it again", left["bilibili"]["note"])
        # ok keeps its meaning: the run as a whole still isn't finished.
        self.assertFalse(cli.finished({"all_submissions_verified": True, "results": results}))

    def test_a_douyin_work_in_review_gives_its_own_reason(self):
        left = cli.left_to_finish({"douyin": {"key": KEY, "state": "SUBMISSION_VERIFIED", "id": "7300000000000000001",
                                              "chapters_state": "PENDING_WORK_ID", "next": IN_REVIEW}})
        self.assertEqual(IN_REVIEW, left["douyin"]["note"])

    def test_widgets_and_a_collection_are_named_too(self):
        left = cli.left_to_finish({"bilibili": {"key": KEY, "state": "SUBMISSION_VERIFIED", "widgets_state": "PARTIAL", "season": {"added": False}}})
        self.assertEqual(["widgets", "collection"], left["bilibili"]["parts"])

    def test_nothing_is_said_for_a_work_that_did_not_publish_or_has_nothing_left(self):
        self.assertEqual({}, cli.left_to_finish({"bilibili": {"key": KEY, "state": "SUBMITTED_UNVERIFIED", "chapters_state": "PENDING"},
                                                 "youtube": {"key": KEY, "state": "SUBMISSION_VERIFIED", "chapters_state": "VERIFIED"},
                                                 "x": {"state": "NOT_STARTED", "code": "stopped"}}))

    def test_a_publish_result_carries_it(self):
        receipt = {"key": KEY, "state": "SUBMISSION_VERIFIED", "id": "BV1test", "chapters_state": "PENDING"}
        with patch.object(cli, "_publish_platform", return_value=receipt), patch("video_publisher.publication_review.check", return_value=None), \
                patch.object(cli, "platform_check"), patch.object(cli, "stop_check"):
            answer = cli.publish_plan({"confirmation": "c", "platforms": {"bilibili": {"method": "cli"}}}, "c")
        self.assertIs(True, answer["all_submissions_verified"])
        self.assertEqual(["chapters"], answer["left_to_finish"]["bilibili"]["parts"])

    def test_a_publish_with_nothing_left_has_no_such_field(self):
        receipt = {"key": KEY, "state": "SUBMISSION_VERIFIED", "id": "BV1test", "chapters_state": "VERIFIED"}
        with patch.object(cli, "_publish_platform", return_value=receipt), patch("video_publisher.publication_review.check", return_value=None), \
                patch.object(cli, "platform_check"), patch.object(cli, "stop_check"):
            answer = cli.publish_plan({"confirmation": "c", "platforms": {"bilibili": {"method": "cli"}}}, "c")
        self.assertNotIn("left_to_finish", answer)


class StatusNoteTests(Temporary):
    def row(self, receipt):
        return {"key": KEY, "platform": "douyin", "state": "SUBMISSION_VERIFIED", "updated_at": "2026-11-04T08:00:00Z",
                "data": {"title": "Test", "options": {"method": "browser"}, "plan_hash": "p", "receipt": receipt}}

    def test_a_part_waiting_for_review_shows_the_receipts_own_reason(self):
        item = overview.attempt(self.row({"id": "7300000000000000001", "chapters_state": "PENDING_WORK_ID", "next": IN_REVIEW}), False)
        self.assertEqual((IN_REVIEW, f"vpub verify {KEY}", "PENDING_WORK_ID"), (item["note"], item["next_command"], item["chapters_state"]))

    def test_without_a_reason_the_general_note_stays(self):
        item = overview.attempt(self.row({"id": "7300000000000000001", "chapters_state": "PENDING"}), False)
        self.assertIn("finish chapters or widgets", item["note"])


class LabelNoteTests(Temporary):
    def test_a_message_dry_run_says_why_the_label_is_there_and_how_to_leave_it_off(self):
        before = {"identity": "owner", "recipient": "recipient", "can_send": True, "items": []}
        with patch.object(dm, "identity", return_value="owner"), patch.object(dm, "dispatch", return_value=before):
            labelled = dm.send("instagram", "recipient", "Hello", "en", dry_run=True)
            own = dm.send("instagram", "recipient", "Hello", "en", dry_run=True, written_by="person")
        self.assertEqual(("Hello (AI reply)", "(AI reply)"), (labelled["request"]["text"], labelled["label_added"]))
        self.assertIn("--written-by person", labelled["label_note"])
        self.assertEqual("Hello", own["request"]["text"])
        self.assertNotIn("label_note", own)

    def test_a_reply_dry_run_says_it_too(self):
        comment = {"id": "comment123", "text": "请问歌名", "author_id": "viewer", "is_owner": False, "language_hint": "zh"}
        atomic_json(home() / "operations/comments" / (digest(["youtube", "abcdefghijk"]) + ".json"),
                    {"identity": "channel", "items": [comment], "state": "OBSERVED"})
        labelled = ops.reply("youtube", "abcdefghijk", "comment123", "谢谢。", dry_run=True)
        own = ops.reply("youtube", "abcdefghijk", "comment123", "谢谢。", dry_run=True, written_by="person")
        self.assertEqual(("谢谢。（AI 回复）", "（AI 回复）"), (labelled["text"], labelled["label_added"]))
        self.assertIn("--written-by person", labelled["label_note"])
        self.assertNotIn("label_note", own)


if __name__ == "__main__":
    unittest.main()

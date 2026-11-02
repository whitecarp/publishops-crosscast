"""Bilibili edits, chapters and collections at upload, deleting comments, and Douyin text edits. Stand-ins only; nothing is contacted."""
import contextlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from video_publisher import adapters, browser_backends, browser_edits, chapters, cli, comment_actions, comment_actions_api as api, edits, edits_bilibili
from video_publisher.core import Ledger, VpubError, atomic_json, digest, home, load_plan, publication_lock, read_json
from shared_chrome import ChromeCase, async_playwright

WORK = "BV1xx411c7mD"
OWNER = "42"
TEXT = {"expected_identity": OWNER, "title": "New title", "description": "New text", "content_language": "zh-Hans", "metadata_language": "zh-Hans", "metadata_mode": "target_only"}
LONG = [{"start_seconds": 0, "title": "开场"}, {"start_seconds": 90, "title": "正片"}, {"start_seconds": 3700, "title": "结尾"}]


class FakeBili:
    def __init__(self):
        self.archive = {"bvid": WORK, "title": "Old title", "desc": "Old text", "tag": "a,b", "cover": "https://i0.hdslb.com/old.jpg"}
        self.chapters = []
        self.calls = []
        self.fail = None
        self.fail_after = None  # The platform took it, and then the answer never came.
        self.read_fail = None
        self.keep = True
        self.drop_tags = set()
        self.upload = {"bvid": WORK, "season_added": True}

    def __call__(self, args, timeout=210, program=None):
        self.calls.append(list(args))
        if args[:2] == ["upload", "status"]:
            return {"archive": dict(self.archive)}
        if args[:3] == ["upload", "chapters", "show"]:
            if self.read_fail:
                raise VpubError(self.read_fail, "Could not read.")
            return {"chapters": [{"from": row["start_seconds"], "to": 0, "title": row["title"]} for row in self.chapters]}
        if args[:2] == ["upload", "video"]:
            return dict(self.upload)
        if self.fail:
            raise VpubError(self.fail, "No.")
        if self.keep:
            self.apply(args)
        if self.fail_after:
            raise VpubError(self.fail_after, "No answer.")
        return {}

    def apply(self, args):
        if args[:3] == ["upload", "chapters", "set"]:
            self.chapters = []
            for flag, line in zip(args[4::2], args[5::2]):
                stamp, title = line.split(" ", 1)
                parts = [int(part) for part in stamp.split(":")]
                self.chapters.append({"start_seconds": sum(part * 60 ** i for i, part in enumerate(reversed(parts))), "title": title})
            return
        tags = []
        for flag, value in zip(args[3::2], args[4::2]):
            if flag == "--title":
                self.archive["title"] = value
            elif flag == "--desc":
                self.archive["desc"] = value
            elif flag == "--cover":
                self.archive["cover"] = "https://i0.hdslb.com/new.jpg"
            elif flag == "--tag":
                tags.append(value)
        if tags:
            self.archive["tag"] = ",".join(t for t in tags if t not in self.drop_tags)

    def writes(self):
        return [call for call in self.calls if call[:2] == ["upload", "edit"] or call[:3] == ["upload", "chapters", "set"]]


class Temporary(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for target in (patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")}),):
            target.start()
            self.addCleanup(target.stop)
        self.bili = FakeBili()
        for target in (patch.object(adapters, "bili", self.bili), patch.object(adapters, "auth_status", return_value={"identity": OWNER})):
            target.start()
            self.addCleanup(target.stop)


class BilibiliTextEditTests(Temporary):
    def edit(self, value=None, verify_only=False):
        marks = []
        result = edits_bilibili.run("default", OWNER, {"id": WORK}, edits.validate_patch("bilibili", value or TEXT), marks.append, verify_only)
        return result, marks

    def test_new_text_is_saved_once_and_read_back(self):
        result, marks = self.edit()
        self.assertEqual(("EDIT_VERIFIED", 1), (result["state"], result["platform_writes"]))
        self.assertEqual([["upload", "edit", WORK, "--title", "New title", "--desc", "New text"]], self.bili.writes())
        self.assertEqual("EDIT_SUBMITTING", marks[0]["state"])

    def test_text_that_did_not_keep_is_not_called_done(self):
        self.bili.keep = False
        self.assertEqual("EDIT_SUBMITTED_UNVERIFIED", self.edit()[0]["state"])

    def test_matching_text_writes_nothing(self):
        self.bili.archive.update(title="New title", desc="New text")
        result, _ = self.edit()
        self.assertEqual(("EDIT_VERIFIED", True, 0), (result["state"], result["unchanged"], result["platform_writes"]))
        self.assertEqual([], self.bili.writes())

    def test_verify_only_never_saves(self):
        self.assertEqual("EDIT_MISMATCH", self.edit(verify_only=True)[0]["state"])
        self.assertEqual([], self.bili.writes())

    def test_a_tag_bilibili_drops_is_reported(self):
        self.bili.drop_tags = {"banned"}
        result, _ = self.edit({**TEXT, "tags": ["ok", "banned"]})
        self.assertEqual(("EDIT_VERIFIED", ["banned"]), (result["state"], result["tags_dropped"]))

    def test_a_refusal_is_recorded_so_it_can_be_tried_again(self):
        self.bili.fail = "invalid_argument"
        marks = []
        with self.assertRaises(VpubError):
            edits_bilibili.run("default", OWNER, {"id": WORK}, edits.validate_patch("bilibili", TEXT), marks.append, False)
        self.assertEqual("EDIT_FAILED", marks[-1]["state"])

    def test_an_unclear_ending_is_not_marked_as_failed(self):
        self.bili.fail = "backend_timeout"
        marks = []
        with self.assertRaises(VpubError):
            edits_bilibili.run("default", OWNER, {"id": WORK}, edits.validate_patch("bilibili", TEXT), marks.append, False)
        self.assertEqual("EDIT_SUBMITTING", marks[-1]["state"])

    def test_another_account_stops_before_anything(self):
        adapters.auth_status.return_value = {"identity": "99"}
        with self.assertRaises(VpubError) as caught:
            self.edit()
        self.assertEqual("wrong_account", caught.exception.code)
        self.assertEqual([], self.bili.calls)

    def test_a_different_work_answering_stops_it(self):
        self.bili.archive["bvid"] = "BV1other"
        with self.assertRaises(VpubError) as caught:
            self.edit()
        self.assertEqual("wrong_work", caught.exception.code)
        self.assertEqual([], self.bili.writes())


class BilibiliOtherEditTests(Temporary):
    def test_a_new_cover_is_sent_and_says_its_pixels_are_unverified(self):
        cover = self.root / "cover.jpg"
        cover.write_bytes(b"cover")
        value = edits.validate_patch("bilibili", {"expected_identity": OWNER, "cover": str(cover)})
        result = edits_bilibili.run("default", OWNER, {"id": WORK}, value, lambda mark: None, False)
        self.assertEqual("EDIT_VERIFIED", result["state"])
        self.assertFalse(result["cover"]["remote_pixels_verified"])
        self.assertEqual([["upload", "edit", WORK, "--cover", str(cover.resolve())]], self.bili.writes())

    def test_a_cover_that_changed_since_the_check_is_not_sent(self):
        cover = self.root / "cover.jpg"
        cover.write_bytes(b"cover")
        value = edits.validate_patch("bilibili", {"expected_identity": OWNER, "cover": str(cover)})
        cover.write_bytes(b"changed")
        with self.assertRaises(VpubError) as caught:
            edits_bilibili.run("default", OWNER, {"id": WORK}, value, lambda mark: None, False)
        self.assertEqual("source_changed", caught.exception.code)
        self.assertEqual([], self.bili.writes())

    def test_chapters_are_sent_as_lines_and_read_back(self):
        value = edits.validate_patch("bilibili", {"expected_identity": OWNER, "chapters": LONG, "duration_seconds": 4000})
        result = edits_bilibili.run("default", OWNER, {"id": WORK}, value, lambda mark: None, False)
        self.assertEqual("EDIT_VERIFIED", result["state"])
        self.assertEqual(["--chapter", "0:00 开场", "--chapter", "1:30 正片", "--chapter", "1:01:40 结尾"], self.bili.writes()[0][4:])

    def test_chapters_already_saved_write_nothing(self):
        self.bili.chapters = list(LONG)
        value = edits.validate_patch("bilibili", {"expected_identity": OWNER, "chapters": LONG, "duration_seconds": 4000})
        result = edits_bilibili.run("default", OWNER, {"id": WORK}, value, lambda mark: None, False)
        self.assertEqual(("EDIT_VERIFIED", 0), (result["state"], result["platform_writes"]))
        self.assertEqual([], self.bili.writes())


class EditRequestTests(Temporary):
    def run_edit(self, value):
        patch_path = self.root / "patch.json"
        atomic_json(patch_path, value)
        ledger = MagicMock()
        ledger.get.return_value = {"platform": "bilibili", "account": "default", "state": "SUBMISSION_VERIFIED",
                                   "data": {"identity": {"identity": OWNER}, "receipt": {"id": WORK}, "options": {}}}
        with patch.object(edits, "Ledger", return_value=ledger):
            return edits.run("work-key", patch_path)

    def test_after_a_refusal_the_same_edit_can_be_tried_again(self):
        self.bili.fail = "invalid_argument"
        with self.assertRaises(VpubError):
            self.run_edit(TEXT)
        self.bili.fail = None
        self.assertEqual("EDIT_VERIFIED", self.run_edit(TEXT)["state"])

    def test_after_an_unclear_ending_the_same_edit_is_never_sent_again(self):
        self.bili.fail = "backend_timeout"
        with self.assertRaises(VpubError):
            self.run_edit(TEXT)
        self.bili.fail = None
        before = len(self.bili.writes())
        with self.assertRaises(VpubError) as caught:
            self.run_edit(TEXT)
        self.assertEqual("inspect_existing_edit", caught.exception.code)
        self.assertEqual(before, len(self.bili.writes()))

    def test_requests_each_platform_cannot_take_are_refused(self):
        for platform, value in (("x", {**TEXT, "tags": ["a"]}), ("bilibili", {**TEXT, "title": "x" * 81}), ("douyin", {**TEXT, "title": "x" * 31}),
                                ("bilibili", {**TEXT, "description": "x" * 2001}), ("bilibili", {**TEXT, "tags": []}), ("bilibili", {**TEXT, "tags": ["a,b"]}),
                                ("instagram", {"expected_identity": OWNER, "chapters": LONG, "duration_seconds": 4000}),
                                ("youtube", {"expected_identity": OWNER, "chapters": LONG[:2], "duration_seconds": 4000})):
            with self.subTest(platform=platform, value=value), self.assertRaises(VpubError):
                edits.validate_patch(platform, value)

    def test_douyin_text_includes_its_hashtags(self):
        checked = edits.validate_patch("douyin", {**TEXT, "tags": ["one", "#two"]})
        self.assertEqual("New text\n\n#one #two", checked["caption"])


class AdoptedWorkTests(Temporary):
    def adopt(self, title="Old title"):
        atomic_json(home() / "operations/adopted" / (digest(["bilibili", WORK]) + ".json"), {"platform": "bilibili", "work_id": WORK, "identity": OWNER, "title": title})

    def run_it(self, value=None, **options):
        patch_path = self.root / "patch.json"
        atomic_json(patch_path, value or TEXT)
        return edits.run_adopted("bilibili", WORK, patch_path, **options)

    def test_a_work_not_adopted_is_refused_and_says_how(self):
        with self.assertRaises(VpubError) as caught:
            self.run_it()
        self.assertEqual("work_identity_required", caught.exception.code)
        self.assertIn(f"vpub ops adopt bilibili {WORK}", caught.exception.hint)
        self.assertEqual([], self.bili.calls)

    def test_an_adopted_work_is_edited_and_its_recorded_title_follows(self):
        self.adopt()
        result = self.run_it()
        self.assertEqual(("EDIT_VERIFIED", WORK), (result["state"], result["work_id"]))
        self.assertEqual(1, len(self.bili.writes()))
        self.assertEqual("New title", read_json(home() / "operations/adopted" / (digest(["bilibili", WORK]) + ".json"))["title"])

    def test_the_named_account_must_be_the_one_adoption_verified(self):
        self.adopt()
        with self.assertRaises(VpubError) as caught:
            self.run_it({**TEXT, "expected_identity": "99"})
        self.assertEqual("wrong_account", caught.exception.code)
        self.assertEqual([], self.bili.calls)

    def test_a_dry_run_writes_nothing(self):
        self.adopt()
        self.assertEqual(0, self.run_it(dry_run=True)["platform_writes"])
        self.assertEqual([], self.bili.calls)

    def test_an_unclear_ending_blocks_a_repeat_here_too(self):
        self.adopt()
        self.bili.fail = "backend_timeout"
        with self.assertRaises(VpubError):
            self.run_it()
        self.bili.fail = None
        with self.assertRaises(VpubError) as caught:
            self.run_it()
        self.assertEqual("inspect_existing_edit", caught.exception.code)

    def test_douyin_covers_and_chapters_need_the_title_adoption_recorded(self):
        atomic_json(home() / "operations/adopted" / (digest(["douyin", "123"]) + ".json"), {"platform": "douyin", "work_id": "123", "identity": OWNER, "title": None})
        patch_path = self.root / "patch.json"
        atomic_json(patch_path, {"expected_identity": OWNER, "chapters": [{"start_seconds": 0, "title": "One"}], "duration_seconds": 30})
        with self.assertRaises(VpubError) as caught:
            edits.run_adopted("douyin", "123", patch_path)
        self.assertEqual("unsupported_edit", caught.exception.code)

    def test_checking_text_that_already_holds_a_link_reads_it_and_writes_nothing(self):
        self.adopt()
        linked = {**TEXT, "description": "More at https://example.com/a"}
        self.bili.archive.update(title=linked["title"], desc=linked["description"])
        result = self.run_it(linked, verify_only=True)
        self.assertEqual(("EDIT_VERIFIED", 0), (result["state"], result["platform_writes"]))
        self.assertEqual([], self.bili.writes())

    def test_checking_different_text_with_a_link_still_writes_nothing(self):
        self.adopt()
        result = self.run_it({**TEXT, "description": "More at https://example.com/a"}, verify_only=True)
        self.assertEqual(("EDIT_MISMATCH", 0), (result["state"], result["platform_writes"]))
        self.assertEqual([], self.bili.writes())

    def test_saving_new_text_with_a_link_is_still_refused(self):
        self.adopt()
        with self.assertRaises(VpubError) as caught:
            self.run_it({**TEXT, "description": "More at https://example.com/a"})
        self.assertEqual("links_disabled", caught.exception.code)
        self.assertEqual([], self.bili.calls)

    def test_the_command_line_reaches_it(self):
        args = cli.parser().parse_args(["edit-work", "bilibili", WORK, "patch.json", "--verify-only"])
        self.assertEqual(("bilibili", WORK, "patch.json", True), (args.platform, args.work, args.patch, args.verify_only))


class BilibiliUploadTests(Temporary):
    def publish(self, **options):
        marks = []
        settings = {"title": "T", "description": "D", "ai_generated": True, "tags": ["a"], "visibility": "public", "account": "default", **options}
        return adapters.publish_bilibili({"video": "v.mp4"}, settings, marks.append), marks

    def test_a_collection_is_passed_to_the_upload(self):
        result, _ = self.publish(season_id=7)
        self.assertIn(["--season", "7"], [self.bili.calls[0][i:i + 2] for i in range(len(self.bili.calls[0]))])
        self.assertEqual({"id": 7, "added": True, "error": None}, result["season"])

    def test_an_upload_stands_even_when_the_collection_refused_it(self):
        self.bili.upload = {"bvid": WORK, "season_added": False, "season_error": "No."}
        result, _ = self.publish(season_id=7)
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertEqual({"id": 7, "added": False, "error": "No."}, result["season"])

    def verify(self, receipt):
        marks = []
        result = adapters.verify("bilibili", "default", receipt, marks.append)
        return result, marks

    def sent(self):
        return len([call for call in self.bili.writes() if call[:3] == ["upload", "chapters", "set"]])

    def test_chapters_follow_the_upload_and_are_read_back(self):
        result, marks = self.publish(chapters=LONG)
        self.assertEqual("VERIFIED", result["chapters_state"])
        self.assertEqual(["PENDING", "SUBMITTING"], [mark["chapters_state"] for mark in marks])
        self.assertEqual(1, self.sent())

    def test_verifying_reads_before_anything_could_be_sent(self):
        self.verify({"id": WORK, "state": "SUBMITTED", "pending_chapters": LONG, "chapters_state": "PENDING"})
        chapters = [call[2] for call in self.bili.calls if call[1] == "chapters"]
        self.assertEqual(["show", "set", "show"], chapters)

    def test_the_attempt_is_recorded_before_anything_is_sent(self):
        seen = []

        def mark(value):
            seen.append((value["chapters_state"], self.sent()))
        receipt = {"id": WORK, "state": "SUBMITTED", "pending_chapters": LONG, "chapters_state": "PENDING"}
        adapters.verify("bilibili", "default", receipt, mark)
        self.assertEqual([("SUBMITTING", 0)], seen)

    def test_chapters_bilibili_refuses_leave_the_work_say_why_and_can_be_tried_again(self):
        self.bili.fail = "invalid_argument"
        result, _ = self.publish(chapters=LONG)
        self.assertEqual(("SUBMISSION_VERIFIED", "NOT_APPLIED", "invalid_argument"), (result["state"], result["chapters_state"], result["chapters_error"]))
        self.bili.fail = None
        again, _ = self.verify(result)
        self.assertEqual("VERIFIED", again["chapters_state"])
        self.assertNotIn("chapters_error", again)
        self.assertEqual(2, self.sent())  # The one Bilibili refused, and the one it took.

    def test_an_unclear_ending_is_only_ever_read_again(self):
        self.bili.fail_after = "backend_timeout"
        result, _ = self.publish(chapters=LONG)
        self.assertEqual(("SAVE_UNVERIFIED", "backend_timeout"), (result["chapters_state"], result["chapters_error"]))
        self.assertEqual(1, self.sent())
        self.bili.fail_after = None
        again, marks = self.verify(result)
        self.assertEqual("VERIFIED", again["chapters_state"])
        self.assertEqual(1, self.sent())
        self.assertEqual([], marks)

    def test_a_read_back_that_is_slow_is_waited_for_without_sending_again(self):
        self.bili.keep = False
        result, _ = self.publish(chapters=LONG)
        self.assertEqual("SAVE_UNVERIFIED", result["chapters_state"])
        for _ in range(2):
            result, _ = self.verify(result)
            self.assertEqual("SAVE_UNVERIFIED", result["chapters_state"])
        self.assertEqual(1, self.sent())
        self.bili.chapters = list(LONG)
        self.assertEqual("VERIFIED", self.verify(result)[0]["chapters_state"])
        self.assertEqual(1, self.sent())

    def test_a_process_that_died_after_recording_the_attempt_never_sends_again(self):
        receipt = {"id": WORK, "state": "SUBMISSION_VERIFIED", "pending_chapters": LONG, "chapters_state": "SUBMITTING"}
        result, _ = self.verify(receipt)
        self.assertEqual("SAVE_UNVERIFIED", result["chapters_state"])
        self.assertEqual(0, self.sent())

    def test_a_list_someone_else_set_is_left_alone(self):
        other = [{"start_seconds": 0, "title": "Mine"}]
        self.bili.chapters = list(other)
        for state, expected in (("PENDING", "CONFLICT"), ("NOT_APPLIED", "CONFLICT"), ("SUBMITTING", "SAVE_UNVERIFIED"), ("SAVE_UNVERIFIED", "SAVE_UNVERIFIED")):
            with self.subTest(state=state):
                result, _ = self.verify({"id": WORK, "state": "SUBMISSION_VERIFIED", "pending_chapters": LONG, "chapters_state": state, "chapters_error": "invalid_argument"})
                self.assertEqual(expected, result["chapters_state"])
        self.assertEqual(0, self.sent())
        self.assertEqual(other, self.bili.chapters)

    def test_a_read_that_fails_sends_nothing_and_leaves_the_state_as_it_was(self):
        self.bili.read_fail = "backend_timeout"
        result, marks = self.verify({"id": WORK, "state": "SUBMISSION_VERIFIED", "pending_chapters": LONG, "chapters_state": "PENDING"})
        self.assertEqual(("PENDING", "backend_timeout"), (result["chapters_state"], result["chapters_error"]))
        self.assertEqual(0, self.sent())
        self.assertEqual([], marks)

    def test_an_older_receipt_that_says_not_applied_after_a_timeout_is_only_read(self):
        for error in ("backend_timeout", "backend_error", None):
            with self.subTest(error=error):
                result, marks = self.verify({"id": WORK, "state": "SUBMISSION_VERIFIED", "pending_chapters": LONG, "chapters_state": "NOT_APPLIED", "chapters_error": error})
                self.assertEqual("SAVE_UNVERIFIED", result["chapters_state"])
                self.assertEqual(0, self.sent())
                self.assertEqual([], marks)

    def test_a_clear_refusal_can_still_be_tried_again(self):
        result, _ = self.verify({"id": WORK, "state": "SUBMISSION_VERIFIED", "pending_chapters": LONG, "chapters_state": "NOT_APPLIED", "chapters_error": "invalid_argument"})
        self.assertEqual("VERIFIED", result["chapters_state"])
        self.assertEqual(1, self.sent())

    def test_a_stop_during_the_send_counts_as_not_sent(self):
        self.bili.fail = "stopped"
        result, _ = self.verify({"id": WORK, "state": "SUBMISSION_VERIFIED", "pending_chapters": LONG, "chapters_state": "PENDING"})
        self.assertEqual("NOT_APPLIED", result["chapters_state"])

    def test_with_nowhere_to_record_the_attempt_it_only_reads(self):
        receipt = {"id": WORK, "state": "SUBMITTED", "pending_chapters": LONG, "chapters_state": "PENDING"}
        self.assertEqual("PENDING", adapters.verify("bilibili", "default", receipt)["chapters_state"])
        self.assertEqual(0, self.sent())

    def test_chapters_already_verified_are_not_sent_again(self):
        receipt = {"id": WORK, "pending_chapters": LONG, "chapters_state": "VERIFIED"}
        self.verify(receipt)
        self.assertEqual([], self.bili.writes())

    def test_the_verify_command_saves_the_attempt_in_the_ledger_before_sending(self):
        ledger = Ledger()
        key = ledger.claim("bilibili", "default", "a" * 64, {})
        ledger.save(key, "SUBMITTED", {"receipt": {"id": WORK, "state": "SUBMITTED", "pending_chapters": LONG, "chapters_state": "PENDING"}})
        ledger.close()
        seen = []

        def write(args, timeout=210, program=None):
            if args[:3] == ["upload", "chapters", "set"]:
                check = Ledger()
                seen.append(check.get(key)["data"]["receipt"]["chapters_state"])
                check.close()
            return self.bili(args, timeout, program)
        with patch.object(adapters, "bili", write):
            result = cli.run_command(cli.parser().parse_args(["verify", key]))
        self.assertEqual(["SUBMITTING"], seen)
        self.assertEqual("VERIFIED", result["chapters_state"])


class TwoVerifiesTests(Temporary):
    """From a review: two at once could put back each other's older state, after which a third sent again what had ended unclear."""

    def recorded(self, **receipt):
        ledger = Ledger()
        key = ledger.claim("bilibili", "default", "a" * 64, {})
        ledger.save(key, "SUBMITTED", {"receipt": {"id": WORK, "state": "SUBMITTED", **receipt}})
        ledger.close()
        return key

    @staticmethod
    def stored(key):
        ledger = Ledger()
        row = ledger.get(key)
        ledger.close()
        return row

    def verify_command(self, key):
        return cli.run_command(cli.parser().parse_args(["verify", key]))

    def test_one_that_can_still_send_waits_its_turn_and_sends_nothing_meanwhile(self):
        key = self.recorded(pending_chapters=LONG, chapters_state="PENDING")
        with publication_lock(), self.assertRaises(VpubError) as caught:
            self.verify_command(key)
        self.assertEqual("busy", caught.exception.code)
        self.assertEqual([], self.bili.writes())
        self.assertEqual("PENDING", self.stored(key)["data"]["receipt"]["chapters_state"])

    def test_once_it_has_its_turn_it_goes_by_the_record_as_it_is_then(self):
        key = self.recorded(pending_chapters=LONG, chapters_state="PENDING")

        @contextlib.contextmanager
        def another_got_there_first():
            row = self.stored(key)
            ledger = Ledger()
            ledger.save(key, "SUBMITTED", {**row["data"], "receipt": {**row["data"]["receipt"], "chapters_state": "SAVE_UNVERIFIED"}})
            ledger.close()
            yield
        with patch.object(cli, "publication_lock", another_got_there_first):
            result = self.verify_command(key)
        self.assertEqual("SAVE_UNVERIFIED", result["chapters_state"])
        self.assertEqual([], self.bili.writes())
        self.assertEqual("SAVE_UNVERIFIED", self.stored(key)["data"]["receipt"]["chapters_state"])

    def test_one_that_read_before_the_upload_was_saved_sends_nothing(self):
        """From a review: its first read found no receipt, the publisher then saved one with chapters to send, and a second read here sent them, and the publisher sent them too."""
        ledger = Ledger()
        key = ledger.claim("bilibili", "default", "a" * 64, {})
        ledger.close()
        real, reads = Ledger.get, []

        def publisher_saves_just_after(ledger, wanted):
            row = real(ledger, wanted)
            if not reads:
                reads.append(True)
                ledger.save(key, "SUBMITTED", {"receipt": {"id": WORK, "state": "SUBMITTED", "pending_chapters": LONG, "chapters_state": "PENDING"}})
            return row
        with patch.object(Ledger, "get", publisher_saves_just_after), self.assertRaises(VpubError) as caught:
            self.verify_command(key)
        self.assertEqual("receipt_missing", caught.exception.code)
        self.assertEqual([], self.bili.writes())
        self.assertEqual("PENDING", self.stored(key)["data"]["receipt"]["chapters_state"])

    def test_without_the_lock_it_has_nowhere_to_record_a_write(self):
        key = self.recorded()
        with patch.object(adapters, "verify", return_value={"id": WORK, "state": "SUBMISSION_VERIFIED"}) as verify:
            self.verify_command(key)
        self.assertIsNone(verify.call_args.args[3])

    def test_with_the_lock_it_has(self):
        key = self.recorded(pending_chapters=LONG, chapters_state="PENDING")
        with patch.object(adapters, "verify", return_value={"id": WORK, "state": "SUBMISSION_VERIFIED"}) as verify:
            self.verify_command(key)
        self.assertTrue(callable(verify.call_args.args[3]))

    def test_one_that_only_reads_never_writes_over_a_newer_record(self):
        key = self.recorded()

        def read_while_another_saves(platform, account, receipt, checkpoint=None):
            row = self.stored(key)
            ledger = Ledger()
            ledger.save(key, "SUBMITTED", {**row["data"], "edits": ["kept"]})
            ledger.close()
            return {**receipt, "state": "SUBMISSION_VERIFIED", "older": True}
        with patch.object(adapters, "verify", read_while_another_saves):
            result = self.verify_command(key)
        self.assertIn("Another command", result["not_saved"])
        row = self.stored(key)
        self.assertEqual(("SUBMITTED", ["kept"]), (row["state"], row["data"]["edits"]))
        self.assertNotIn("older", row["data"]["receipt"])

    def test_with_nothing_else_going_on_one_that_only_reads_is_saved(self):
        key = self.recorded()
        with patch.object(adapters, "verify", return_value={"id": WORK, "state": "SUBMISSION_VERIFIED"}):
            result = self.verify_command(key)
        self.assertNotIn("not_saved", result)
        self.assertEqual("SUBMISSION_VERIFIED", self.stored(key)["state"])

    def test_one_that_only_reads_can_run_during_another_command_and_saves_nothing_then(self):
        key = self.recorded()
        with publication_lock(), patch.object(adapters, "verify", return_value={"id": WORK, "state": "SUBMISSION_VERIFIED"}):
            result = self.verify_command(key)
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertIn("not saved", result["not_saved"])
        self.assertEqual("SUBMITTED", self.stored(key)["state"])

    def browser_attempt(self, ledger_says, file_says):
        ledger = Ledger()
        key = ledger.claim("x", "default", "b" * 64, {})
        ledger.save(key, "SUBMITTED_UNVERIFIED", {"plan_hash": "plan", "receipt": {"method": "browser", "state": "SUBMITTED_UNVERIFIED", "identity": "me", **ledger_says}})
        ledger.close()
        path = home() / "browser-receipts" / "plan-x.json"
        atomic_json(path, {"method": "browser", "state": "SUBMITTED_UNVERIFIED", "identity": "me", **file_says})
        return key, path

    def test_what_a_workers_own_copy_says_about_a_press_stays_said(self):
        """A killed command's worker finishes by itself, so its copy can be newer than the ledger's, and a verify then read the older one and saved it over the newer."""
        key, path = self.browser_attempt({"final_submit_attempted": False}, {"final_submit_attempted": True, "final_presses": 1, "media_selected": True})
        with patch.object(adapters, "verify", side_effect=lambda platform, account, receipt, checkpoint=None: {**receipt, "state": "SUBMITTED_UNVERIFIED"}):
            result = self.verify_command(key)
        for receipt in (result, read_json(path), self.stored(key)["data"]["receipt"]):
            self.assertEqual((True, 1, True), (receipt["final_submit_attempted"], receipt["final_presses"], receipt["media_selected"]))

    def test_what_the_verify_didnt_look_at_stays_as_the_workers_copy_has_it(self):
        """From a review: replacing the file lost upload_filename, so resume then failed before it reached the saved file."""
        key, path = self.browser_attempt({"phase": "preview", "still_a_draft": True}, {"phase": "media_ready", "media_selected": True, "upload_filename": "crosscast-abc.mp4", "still_a_draft": True})

        def found_something(platform, account, receipt, checkpoint=None):
            answer = {**receipt, "state": "SUBMISSION_VERIFIED", "id": "123"}
            del answer["still_a_draft"]
            return answer
        with patch.object(adapters, "verify", found_something):
            result = self.verify_command(key)
        for receipt in (result, read_json(path), self.stored(key)["data"]["receipt"]):
            self.assertEqual(("SUBMISSION_VERIFIED", "123", "media_ready", True, "crosscast-abc.mp4"),
                             (receipt["state"], receipt["id"], receipt["phase"], receipt["media_selected"], receipt["upload_filename"]))
            self.assertNotIn("still_a_draft", receipt)

    def test_a_second_press_on_record_is_not_taken_back_by_an_older_count(self):
        key, path = self.browser_attempt({"final_submit_attempted": True, "final_presses": 2}, {"final_submit_attempted": True, "final_presses": 1})
        with patch.object(adapters, "verify", side_effect=lambda platform, account, receipt, checkpoint=None: {**receipt, "state": "SUBMITTED_UNVERIFIED"}):
            self.verify_command(key)
        self.assertEqual(2, read_json(path)["final_presses"])

    def test_a_writer_that_gets_in_between_is_not_overwritten_in_either_place(self):
        key, path = self.browser_attempt({"final_submit_attempted": False}, {"final_submit_attempted": False})

        def read_while_a_resume_presses(platform, account, receipt, checkpoint=None):
            row = self.stored(key)
            newer = {**row["data"]["receipt"], "final_submit_attempted": True}
            atomic_json(path, newer)
            ledger = Ledger()
            ledger.save(key, "SUBMITTED_UNVERIFIED", {**row["data"], "receipt": newer})
            ledger.close()
            return {**receipt, "state": "SUBMITTED_UNVERIFIED"}
        with patch.object(adapters, "verify", read_while_a_resume_presses):
            result = self.verify_command(key)
        self.assertIn("not_saved", result)
        self.assertTrue(read_json(path)["final_submit_attempted"])
        self.assertTrue(self.stored(key)["data"]["receipt"]["final_submit_attempted"])

    def test_with_the_receipts_own_turn_taken_it_saves_nothing_and_says_so(self):
        key, path = self.browser_attempt({}, {})
        with patch.object(adapters, "verify", return_value={"method": "browser", "state": "SUBMISSION_VERIFIED"}), \
                patch.object(cli, "receipt_turn", side_effect=VpubError("attempt_busy", "Another step is moving this attempt on, so nothing was sent.")):
            result = self.verify_command(key)
        self.assertIn("not_saved", result)
        self.assertEqual("SUBMITTED_UNVERIFIED", self.stored(key)["state"])

    def test_resume_trusts_whichever_copy_says_the_press_happened(self):
        plan = {"confirmation": "plan", "video_sha256": "s", "options": {"title": "T"}}
        path = home() / "browser-receipts" / "plan-x.json"
        atomic_json(path, {"video_sha256": "s", "identity": "me", "final_submit_attempted": False})
        kept = browser_backends.Transaction("x", "me", plan, resume=True, prior_receipt={"final_submit_attempted": True, "final_presses": 1, "media_selected": True, "upload_filename": "crosscast-abc.mp4"})
        self.assertEqual((True, 1, True, "crosscast-abc.mp4"), (kept.receipt["final_submit_attempted"], kept.receipt["final_presses"], kept.receipt["media_selected"], kept.receipt["upload_filename"]))
        atomic_json(path, {"video_sha256": "s", "identity": "me", "final_submit_attempted": True, "final_presses": 1})
        kept = browser_backends.Transaction("x", "me", plan, resume=True, prior_receipt={"final_submit_attempted": False})
        self.assertEqual((True, 1), (kept.receipt["final_submit_attempted"], kept.receipt["final_presses"]))


class PlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        target = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        target.start()
        self.addCleanup(target.stop)
        (self.root / "clip.mp4").write_bytes(b"synthetic")
        (self.root / "cover.jpg").write_bytes(b"synthetic")
        self.value = cli.template(self.root / "clip.mp4", "en")
        self.value.update(cover="cover.jpg", tags=["tag"], covers={"douyin": {"portrait": "cover.jpg"}})
        self.value["platforms"]["youtube"].update(category_id="22", method="browser")
        self.value["platforms"]["bilibili"]["method"] = "cli"
        self.path = self.root / "publish.json"

    def plan(self):
        atomic_json(self.path, self.value)
        return load_plan(self.path)

    def refused(self, code):
        with self.assertRaises(VpubError) as caught:
            self.plan()
        self.assertEqual(code, caught.exception.code)

    def test_youtube_chapters_become_lines_in_the_description(self):
        self.value["platforms"]["youtube"].update(description="About this.", chapters=LONG, duration_seconds=4000)
        description = self.plan()["platforms"]["youtube"]["description"]
        self.assertEqual("About this.\n\n0:00 开场\n1:30 正片\n1:01:40 结尾", description)

    def test_youtube_chapters_must_fit_the_description(self):
        self.value["platforms"]["youtube"].update(description="x" * 4995, chapters=LONG, duration_seconds=4000)
        self.refused("invalid_description")

    def test_youtube_needs_three_chapters_of_ten_seconds(self):
        self.value["platforms"]["youtube"].update(chapters=LONG[:2], duration_seconds=4000)
        self.refused("invalid_chapters")
        self.value["platforms"]["youtube"].update(chapters=[{"start_seconds": 0, "title": "a"}, {"start_seconds": 5, "title": "b"}, {"start_seconds": 30, "title": "c"}])
        self.refused("invalid_chapters")

    def test_bilibili_takes_chapters_and_a_collection(self):
        self.value["platforms"]["bilibili"].update(chapters=LONG, duration_seconds=4000, season_id=7)
        self.assertEqual(7, self.plan()["platforms"]["bilibili"]["season_id"])

    def test_a_collection_needs_a_real_id(self):
        for value in ("7", 0, -1, True):
            self.value["platforms"]["bilibili"]["season_id"] = value
            with self.subTest(value=value):
                self.refused("invalid_collection")

    def test_other_platforms_have_no_collection_at_upload(self):
        self.value["platforms"]["youtube"]["season_id"] = 7
        self.refused("invalid_manifest")

    def test_making_a_missing_playlist_needs_titles_and_the_browser_route(self):
        self.value["platforms"]["youtube"]["create_missing_playlists"] = True
        self.refused("invalid_playlist")
        self.value["platforms"]["youtube"]["playlist_titles"] = ["New"]
        self.assertTrue(self.plan()["platforms"]["youtube"]["create_missing_playlists"])
        self.value["platforms"]["youtube"]["create_missing_playlists"] = "yes"
        self.refused("invalid_playlist")


class ChapterTextTests(unittest.TestCase):
    def test_times_read_the_way_people_write_them(self):
        self.assertEqual(["0:05", "1:30", "1:01:40"], [chapters.stamp(s) for s in (5, 90, 3700)])

    def test_a_description_keeps_everything_but_its_old_chapter_list(self):
        old = "Intro\n\n0:00 One\n1:00 Two\n2:00 Three\n\nThanks at 3:15"
        self.assertEqual("Intro\n\nThanks at 3:15\n\n0:00 开场\n1:30 正片\n1:01:40 结尾", chapters.youtube_description(old, LONG, 4000))

    def test_a_timestamp_that_does_not_start_at_zero_is_left_alone(self):
        self.assertEqual("3:15 my favourite part", chapters.without_chapters("3:15 my favourite part"))

    def test_a_list_written_with_two_digit_minutes_is_found_too(self):
        self.assertEqual("", chapters.without_chapters("00:00 One\n01:00 Two"))


class CommentDeleteTests(Temporary):
    def request(self, kind="delete", **comment):
        return {"platform": "bilibili", "identity": OWNER, "work_id": WORK, "kind": kind, "desired": True, "comment": {"id": "7", "text": "Hi", **comment}}

    def stand_in(self, replies):
        def run(args, timeout=210, program=None):
            self.calls.append(list(args))
            if args[:2] == ["video", "info"]:
                return {"owner": {"mid": 42}}
            if args[:2] == ["comment", "replies"]:
                if isinstance(replies, VpubError):
                    raise replies
                return replies
            return {}
        self.calls = []
        return patch.object(adapters, "bili", run)

    def receipt(self):
        path = home() / "comment.json"
        atomic_json(path, {"state": "COMMENT_READY"})
        return path

    def test_a_delete_marks_the_receipt_and_sends_once(self):
        with self.stand_in({"root": {"rpid": 7, "content": {"message": "Hi"}, "action": 0}}):
            path = self.receipt()
            result = api.execute(self.request(), "set", path)
        self.assertEqual(("COMMENT_UNVERIFIED", 1), (result["state"], result["platform_writes"]))
        self.assertIn(["comment", "delete", WORK, "7"], self.calls)
        self.assertEqual("COMMENT_UNVERIFIED", read_json(path)["state"])

    def test_a_comment_that_changed_is_not_deleted(self):
        with self.stand_in({"root": {"rpid": 7, "content": {"message": "Other"}, "action": 0}}), self.assertRaises(VpubError) as caught:
            api.execute(self.request(), "set", self.receipt())
        self.assertEqual("comment_changed", caught.exception.code)
        self.assertNotIn(["comment", "delete", WORK, "7"], self.calls)

    def test_bilibilis_own_no_such_comment_answer_is_the_read_back(self):
        with self.stand_in(VpubError("not_found", "No such comment.")):
            self.assertIs(True, api.execute({**self.request(), "verification": True}, "status")["deleted"])

    def test_before_a_delete_a_missing_comment_is_an_error_not_a_success(self):
        with self.stand_in(VpubError("not_found", "No such comment.")), self.assertRaises(VpubError):
            api.execute(self.request(), "status")

    def checked(self, **data):
        with self.stand_in({"root": {"rpid": 1, "content": {"message": "Root"}}, **data}):
            return api.execute({**self.request(parent_id="1"), "verification": True}, "status")

    def test_a_reply_missing_from_a_page_that_holds_them_all_reads_as_deleted(self):
        self.assertIs(True, self.checked(replies=[], page={"count": 0})["deleted"])
        self.assertIs(True, self.checked(replies=[{"rpid": 8}], page={"count": 1})["deleted"])

    def test_a_reply_missing_from_a_page_that_holds_only_some_proves_nothing(self):
        result = self.checked(replies=[{"rpid": 8}], page={"count": 25})
        self.assertIsNone(result["deleted"])
        self.assertIs(True, result["absent_from_loaded_list"])

    def test_a_reply_page_that_does_not_say_how_many_there_are_proves_nothing(self):
        self.assertIsNone(self.checked(replies=[])["deleted"])

    def snapshot(self, **comment):
        atomic_json(home() / "operations/comments" / (digest(["bilibili", WORK]) + ".json"), {"identity": OWNER, "items": [{"id": "7", "text": "Hi", **comment}]})

    def test_delete_is_for_your_own_comment_and_remove_for_a_viewers(self):
        self.snapshot(is_owner=False)
        with self.assertRaises(VpubError) as caught:
            comment_actions.mutate("bilibili", WORK, "7", "delete")
        self.assertEqual("own_comment_required", caught.exception.code)
        self.snapshot(is_owner=True)
        with self.assertRaises(VpubError) as caught:
            comment_actions.mutate("bilibili", WORK, "7", "remove")
        self.assertEqual("other_author_required", caught.exception.code)

    def test_a_removal_is_read_back_as_gone(self):
        self.snapshot(is_owner=False)
        answers = [{"root": {"rpid": 7, "content": {"message": "Hi"}, "action": 0}}, {"root": {"rpid": 7, "content": {"message": "Hi"}, "action": 0}}, VpubError("not_found", "Gone.")]
        calls = []

        def run(args, timeout=210, program=None):
            calls.append(list(args))
            if args[:2] == ["video", "info"]:
                return {"owner": {"mid": 42}}
            if args[:2] == ["comment", "replies"]:
                answer = answers.pop(0)
                if isinstance(answer, VpubError):
                    raise answer
                return answer
            return {}
        with patch.object(adapters, "bili", run), patch.object(comment_actions.ops, "record", side_effect=lambda kind, platform, work, value: value):
            result = comment_actions.mutate("bilibili", WORK, "7", "remove")
        self.assertEqual("COMMENT_VERIFIED", result["state"])
        self.assertIn(["comment", "delete", WORK, "7"], calls)

    def test_platforms_without_it_refuse(self):
        with self.assertRaises(VpubError) as caught:
            comment_actions.mutate("instagram", "abc", "7", "remove")
        self.assertEqual("unsupported_operation", caught.exception.code)


class FakeDouyin:
    def __init__(self):
        self.title = "Old title"
        self.caption = "Old words"
        self.saves = []
        self.keep = True
        self.elsewhere = False

    async def handle(self, route):
        url = urlsplit(route.request.url)
        if route.request.method == "POST":
            import json
            data = json.loads(route.request.post_data)
            self.saves.append(data)
            if self.keep:
                self.title, self.caption = data["title"], data["caption"]
            return await route.fulfill(status=200, content_type="application/json", body="{}")
        if url.path == "/creator-micro/content/post/video" and not self.elsewhere:
            return await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=f"""<!doctype html><body>
              <input placeholder="填写作品标题，为作品获得更多流量" value="{self.title}"><div contenteditable="true" id="c">{self.caption}</div>
              <button id="go">发布</button><script>document.getElementById('go').onclick = async () => {{
                await fetch('/save', {{method: 'POST', body: JSON.stringify({{title: document.querySelector('input').value, caption: document.getElementById('c').innerText}})}});
                location.href = '/creator-micro/content/manage'; }};</script>""")
        await route.fulfill(status=200, content_type="text/html", body="<!doctype html><body>manage")


class DouyinTextTests(ChromeCase):
    quick_pauses = True  # This copy of the site has no timers of its own, so a pause can end once the page is quiet.
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        target = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        target.start()
        self.addCleanup(target.stop)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(8000)
        self.site = FakeDouyin()
        await self.page.route("**/*", self.site.handle)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def edit(self, **extra):
        checked = edits.validate_patch("douyin", {**TEXT, "tags": ["one"]})
        return await browser_edits.execute(self.page, "douyin", OWNER, {"patch": checked, "receipt": {"id": "123", "title": "Old title"}, "edit_receipt_path": str(self.root / "edit.json"), **extra})

    async def test_new_text_is_saved_once_and_read_back(self):
        result = await self.edit()
        self.assertEqual(("EDIT_VERIFIED", 1), (result["state"], result["platform_writes"]))
        self.assertEqual(1, len(self.site.saves))
        self.assertEqual(("New title", "New text #one"), (self.site.saves[0]["title"], " ".join(self.site.saves[0]["caption"].split())))

    async def test_text_that_did_not_keep_is_not_called_done(self):
        self.site.keep = False
        self.assertEqual("EDIT_SUBMITTED_UNVERIFIED", (await self.edit())["state"])

    async def test_matching_text_writes_nothing(self):
        self.site.title, self.site.caption = "New title", "New text #one"
        result = await self.edit()
        self.assertEqual(("EDIT_VERIFIED", 0), (result["state"], result["platform_writes"]))
        self.assertEqual([], self.site.saves)

    async def test_verify_only_never_saves(self):
        self.assertEqual("EDIT_MISMATCH", (await self.edit(verify_only=True))["state"])
        self.assertEqual([], self.site.saves)


if __name__ == "__main__":
    unittest.main()

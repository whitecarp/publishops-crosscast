"""What the customer's agent is told: nothing may read as done unless it was shown to be. Fakes only; nothing is contacted."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import adapters, cli, collections, overview
from video_publisher.core import Ledger, atomic_json, home

WORK = "BV1xx411c7mD"
LONG = [{"start_seconds": 0, "title": "One"}]


class Temporary(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        env.start()
        self.addCleanup(env.stop)


class ExitTests(Temporary):
    def answer(self, value):
        """What main() prints and how it exits for a command whose result is this."""
        out = io.StringIO()
        with patch("sys.argv", ["vpub", "doctor"]), patch.object(cli, "execute", return_value=value), contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as ended:
            cli.main()
        return json.loads(out.getvalue())["ok"], ended.exception.code

    def test_a_state_that_proves_nothing_is_not_reported_as_success(self):
        for state in ("READY", "SUBMITTING", "PLATFORM_REJECTED", "BROWSER_VERIFICATION_REQUIRED", "TEST_READY", "TEST_SEED_SUBMITTING", "DM_REPLACED"):
            with self.subTest(state=state):
                self.assertEqual((False, 4), self.answer({"state": state, "reused": True}))

    def test_a_confirmed_or_harmless_ending_still_is(self):
        for state in ("CONFIRMED", "NO_CHANGE", "DRY_RUN", "SUBMISSION_VERIFIED", "EXISTING_VERIFIED", "EDIT_VERIFIED", "REPLY_VERIFIED", "COMMENT_VERIFIED", "ACTION_VERIFIED", "DM_VISIBLE", "COLLECTED", "DRAFT_SAVED", "OBSERVED", "ADOPTED"):
            with self.subTest(state=state):
                self.assertEqual((True, 0), self.answer({"state": state}))

    def test_a_verified_upload_whose_chapters_did_not_land_is_not_reported_as_done(self):
        for chapters in ("PENDING", "SUBMITTING", "NOT_APPLIED", "SAVE_UNVERIFIED", "CONFLICT", "PENDING_WORK_ID", "INCOMPLETE"):
            with self.subTest(chapters=chapters):
                self.assertEqual((False, 4), self.answer({"state": "SUBMISSION_VERIFIED", "chapters_state": chapters}))
        self.assertEqual((True, 0), self.answer({"state": "SUBMISSION_VERIFIED", "chapters_state": "VERIFIED"}))

    def test_a_verified_upload_that_did_not_join_its_collection_is_not_reported_as_done(self):
        self.assertEqual((False, 4), self.answer({"state": "SUBMISSION_VERIFIED", "season": {"id": 7, "added": False, "error": "No."}}))
        self.assertEqual((True, 0), self.answer({"state": "SUBMISSION_VERIFIED", "season": {"id": 7, "added": True, "error": None}}))

    def test_a_publish_is_looked_through_for_the_same_things(self):
        good = {"state": "SUBMISSION_VERIFIED"}
        self.assertEqual((True, 0), self.answer({"all_submissions_verified": True, "results": {"bilibili": good, "x": good}}))
        self.assertEqual((False, 4), self.answer({"all_submissions_verified": True, "results": {"bilibili": {**good, "chapters_state": "NOT_APPLIED"}, "x": good}}))
        self.assertEqual((False, 4), self.answer({"all_submissions_verified": True, "results": {"bilibili": {**good, "season": {"added": False}}}}))
        self.assertEqual((False, 4), self.answer({"all_submissions_verified": False, "results": {"x": {"state": "UNKNOWN"}}}))

    def test_a_collection_request_interrupted_before_or_during_its_click_says_so_when_repeated(self):
        for state, writes in (("READY", 0), ("SUBMITTING", 1)):
            with self.subTest(state=state), patch.object(collections, "identity", return_value="me"):
                key = collections.digest(["x", "me", "request-" + state])
                atomic_json(collections.receipt_path(key), {"key": key, "state": state, "intent": {"action": "delete", "collection": "5", "title": None}, "platform_writes": writes})
                repeated = collections.change("x", "delete", "5", request_id="request-" + state)
                self.assertEqual((state, True), (repeated["state"], repeated["reused"]))
                self.assertEqual((False, 4), self.answer(repeated))


class StatusTests(Temporary):
    def row(self, state="SUBMISSION_VERIFIED", **receipt):
        ledger = Ledger()
        key = ledger.claim("bilibili", "default", "a" * 64, {})
        ledger.save(key, state, {"title": "T", "options": {"method": "cli"}, "receipt": {"id": WORK, "state": state, **receipt}})
        ledger.close()
        return key

    def view(self):
        with patch.object(adapters, "addon_installed", return_value=False):
            return overview.status()

    def test_a_verified_upload_with_its_chapters_still_outstanding_needs_attention(self):
        for chapters in ("PENDING", "NOT_APPLIED", "SAVE_UNVERIFIED", "CONFLICT", "INCOMPLETE"):
            with self.subTest(chapters=chapters):
                key = self.row(chapters_state=chapters, pending_chapters=LONG)
                seen = self.view()["publications"]
                self.assertEqual(1, seen["needs_attention_total"])
                self.assertEqual((key, f"vpub verify {key}", chapters), (seen["needs_attention"][0]["key"], seen["needs_attention"][0]["next_command"], seen["needs_attention"][0]["chapters_state"]))
                (home() / "publications.sqlite3").unlink()

    def test_a_verified_upload_that_did_not_join_its_collection_needs_attention(self):
        self.row(season={"id": 7, "added": False, "error": "No."})
        seen = self.view()["publications"]
        self.assertEqual(1, seen["needs_attention_total"])
        self.assertEqual({"id": 7, "added": False, "error": "No."}, seen["needs_attention"][0]["season"])

    def test_a_finished_upload_does_not(self):
        self.row(chapters_state="VERIFIED", season={"id": 7, "added": True, "error": None})
        self.assertEqual(0, self.view()["publications"]["needs_attention_total"])


if __name__ == "__main__":
    unittest.main()

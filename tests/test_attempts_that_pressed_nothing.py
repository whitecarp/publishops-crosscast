"""Field findings: an Instagram attempt that stopped before its final press was called SUBMITTED_UNVERIFIED by verify, and after that
running it again skipped it, delete needed a verified post, and verify could never find one. A Douyin post under review read as a failure,
and a YouTube playlist change pointed to vpub collection verify. No browser, network or account is used here."""
import asyncio
from contextlib import nullcontext
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from video_publisher import adapters, browser_backends, cli, collections, core
from video_publisher.core import VpubError, atomic_json


class Home(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        for patcher in (patch.dict(os.environ, VPUB_HOME=str(self.home)), patch.object(cli, "publication_lock", lambda: nullcontext()),
                        patch.object(cli, "snapshot_plan", lambda plan, platform: deepcopy(plan)),
                        patch.object(adapters, "preflight", lambda *args: {"logged_in": True, "identity": "owner", "account": "default"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.options = {"account": "default", "method": "browser", "title": "fixture", "submission_mode": "publish",
                        "content_language": "und", "expected_identity": "owner"}
        self.plan = {"confirmation": "fixture_confirmation", "video_sha256": "fixture_hash",
                     "video": str(self.home / "never-opened.mp4"), "platforms": {"x": self.options}}
        self.key = core.Ledger.key("x", "default", "fixture_hash")

    def publish(self, adapter):
        with patch.object(adapters, "publish", adapter):
            return cli.publish_plan(deepcopy(self.plan), self.plan["confirmation"], reviewed=True)["results"]["x"]

    def stops_before_the_press(self, *args):
        atomic_json(self.home / "browser-receipts" / "fixture_confirmation-x.json",
                    {"platform": "x", "state": "PREPARATION_INCOMPLETE", "phase": "blocked", "final_submit_attempted": False, "media_selected": False})
        raise VpubError("browser_step_failed", "Browser command failed (TimeoutError): Locator.click: Timeout 10000ms exceeded.")


class NeverPressedTests(Home):
    def test_an_attempt_that_pressed_nothing_points_to_resume_and_so_does_running_it_again(self):
        first = self.publish(self.stops_before_the_press)
        self.assertEqual(("PREPARATION_INCOMPLETE", f"vpub resume {self.key}"), (first["state"], first["next_command"]))
        self.assertIn("nothing was submitted", first["hint"])
        again = self.publish(self.stops_before_the_press)
        self.assertEqual(("SKIPPED_EXISTING", "PREPARATION_INCOMPLETE", f"vpub resume {self.key}"),
                         (again["state"], again["previous_state"], again["next_command"]))
        self.assertIn("isn't sent again", again["hint"])

    def test_verify_leaves_an_attempt_that_pressed_nothing_resumable(self):
        receipt = {"identity": "owner", "state": "SUBMITTED_UNVERIFIED", "final_submit_attempted": False, "media_selected": False}
        result = asyncio.run(browser_backends.verify(AsyncMock(), "instagram", "owner", receipt))
        self.assertEqual(("PREPARATION_INCOMPLETE", 0), (result["state"], result["platform_writes"]))
        self.assertIn("vpub resume", result["note"])

    def test_a_pressed_attempt_with_no_id_stays_unconfirmed(self):
        receipt = {"identity": "owner", "state": "UNKNOWN", "final_submit_attempted": True}
        self.assertEqual("SUBMITTED_UNVERIFIED", asyncio.run(browser_backends.verify(AsyncMock(), "instagram", "owner", receipt))["state"])

    def test_a_press_the_workers_own_copy_records_is_never_called_unpressed(self):
        ledger = core.Ledger()
        try:
            ledger.claim("x", "default", "fixture_hash", {"plan_hash": "p", "receipt": {"state": "SUBMITTED_UNVERIFIED"}})
            atomic_json(self.home / "browser-receipts" / "p-x.json", {"final_submit_attempted": True, "final_presses": 1})
            result = cli.keep_verified(ledger, ledger.get(self.key), {"state": "SUBMITTED_UNVERIFIED"},
                                       {"state": "PREPARATION_INCOMPLETE", "method": "browser", "final_submit_attempted": False})
            self.assertEqual("SUBMITTED_UNVERIFIED", ledger.get(self.key)["state"])
        finally:
            ledger.close()
        self.assertEqual(("SUBMITTED_UNVERIFIED", True), (result["state"], result["final_submit_attempted"]))


class UnderReviewTests(Home):
    def test_a_post_the_platform_is_reviewing_is_named_as_submitted(self):
        results = {"douyin": {"key": "k" * 64, "state": "SUBMITTED_UNVERIFIED", "platform_status": "UNDER_REVIEW"},
                   "x": {"key": "x" * 64, "state": "SUBMITTED_UNVERIFIED"}}
        reviewing = cli.under_review(results)
        self.assertEqual(["douyin"], list(reviewing))
        self.assertEqual((True, "vpub verify " + "k" * 64), (reviewing["douyin"]["submitted"], reviewing["douyin"]["next_command"]))
        self.assertIn("never upload it again", reviewing["douyin"]["note"])

    def test_a_publish_result_carries_it(self):
        def reviewing(*args):
            return {"state": "SUBMITTED_UNVERIFIED", "platform_status": "UNDER_REVIEW", "method": "browser", "identity": "owner"}
        with patch.object(adapters, "publish", reviewing):
            answer = cli.publish_plan(deepcopy(self.plan), self.plan["confirmation"], reviewed=True)
        self.assertTrue(answer["under_review"]["x"]["submitted"])


class ChapterReasonTests(Home):
    def test_a_chapter_step_that_failed_keeps_its_words_and_a_review_says_when_to_come_back(self):
        receipt = {"id": "7300000000000000001", "state": "SUBMISSION_VERIFIED", "pending_chapters": {"chapters": []}, "title": "T"}
        with patch("video_publisher.browser_edits.execute", new=AsyncMock(side_effect=TimeoutError("Locator.wait_for: Timeout 30000ms exceeded."))):
            failed = asyncio.run(browser_backends.complete_pending_chapters(AsyncMock(), "douyin", "owner", receipt))
        self.assertEqual(("INCOMPLETE", "TimeoutError"), (failed["chapters_state"], failed["chapters_error"]))
        self.assertIn("Locator.wait_for", failed["chapters_error_message"])
        reviewing = VpubError("not_public_yet", "Douyin is still reviewing this video, so its chapters can't be set yet.")
        with patch("video_publisher.browser_edits.execute", new=AsyncMock(side_effect=reviewing)):
            waiting = asyncio.run(browser_backends.complete_pending_chapters(AsyncMock(), "douyin", "owner", receipt))
        self.assertEqual("not_public_yet", waiting["chapters_error"])
        self.assertIn("still reviewing", waiting["next"])


class PlaylistCommandTests(unittest.TestCase):
    def test_a_youtube_playlist_change_points_to_playlist_verify(self):
        self.assertEqual(("vpub playlist verify ", "vpub collection verify "), (collections.verify_command("youtube"), collections.verify_command("douyin")))


if __name__ == "__main__":
    unittest.main()

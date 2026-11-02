"""A platform's warning or a person's stop holds everything that follows, on the routes the main tests do not reach. Fakes only; nothing is contacted."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import adapters, engagement_api, messages, operations as ops, operations_runs as runs, operations_sync, safety, youtube_studio
from video_publisher.core import VpubError, atomic_json, home, stop_check

WORK = "BV1xx411c7mD"
OTHER = "BV1xx411c7mE"
VIDEO = "abcdefghijk"


class Temporary(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        env.start()
        self.addCleanup(env.stop)

    def stop(self):
        atomic_json(home() / "STOP.json", {"reason": "A person paused it."})

    def stopped(self):
        return (home() / "STOP.json").exists()


class FolderReadTests(Temporary):
    """Bilibili saves ask a separate process which folders hold the video, so it needs the same checks as every other bili-cli call."""

    def answer(self, code):
        return MagicMock(stdout=json.dumps({"ok": False, "error": {"code": code, "message": "x"}}))

    def test_bilibilis_warning_during_the_read_stops_everything(self):
        for code in sorted(safety.PUSHBACK - {"human_verification_required"}):
            with self.subTest(code=code):
                with patch.object(engagement_api.subprocess, "run", return_value=self.answer(code)), self.assertRaises(VpubError) as caught:
                    engagement_api.bili_folders(WORK)
                self.assertEqual(code, caught.exception.code)
                self.assertTrue(self.stopped())
                safety.clear_stop()

    def test_an_ordinary_failure_stops_nothing(self):
        with patch.object(engagement_api.subprocess, "run", return_value=self.answer("not_found")), self.assertRaises(VpubError):
            engagement_api.bili_folders(WORK)
        self.assertFalse(self.stopped())

    def test_it_does_not_start_during_a_stop(self):
        self.stop()
        with patch.object(engagement_api.subprocess, "run") as started, self.assertRaises(VpubError) as caught:
            engagement_api.bili_folders(WORK)
        self.assertEqual("stopped", caught.exception.code)
        started.assert_not_called()


class PatrolTests(Temporary):
    """A multi-platform patrol that meets a stop part way reads nothing more and says what it did not read."""

    def setUp(self):
        super().setUp()
        atomic_json(home() / "operations/watchlist.json", {"schema_version": 1, "items": [
            {"platform": "bilibili", "work_id": WORK, "identity": "42"}, {"platform": "bilibili", "work_id": OTHER, "identity": "42"}, {"platform": "youtube", "work_id": VIDEO, "identity": "UC1"}]})
        self.reads = []

    def backend(self, platform, action, work, **options):
        self.reads.append((platform, action, work))
        self.stop()
        return {"identity": "42" if platform == "bilibili" else "UC1", "items": [], "state": "OBSERVED", "metrics": {}, "complete": True}

    def patrol(self, workers):
        with patch.object(ops, "backend", side_effect=self.backend), patch.object(adapters, "addon_installed", return_value=False):
            return runs.sync(limit=5, pages=1, min_interval=0, workers=workers)

    def test_nothing_more_is_read_after_the_stop_appears(self):
        result = self.patrol(1)
        self.assertEqual(1, len(self.reads))
        self.assertEqual("PARTIAL", result["state"])
        self.assertEqual(5, result["report"]["failed_reads"])
        self.assertLessEqual({error["code"] for error in result["errors"]}, {"stopped", "not_started"})

    def test_the_same_holds_with_several_platforms_read_side_by_side(self):
        result = self.patrol(3)
        self.assertEqual("PARTIAL", result["state"])
        self.assertLessEqual(len(self.reads), 2)  # At most one read per platform was already going when the stop appeared.
        self.assertEqual(6 - len(self.reads), result["report"]["failed_reads"])

    def test_a_message_sync_during_a_stop_reads_nothing_and_says_so(self):
        self.stop()
        with patch.object(ops, "backend") as backend, patch.object(adapters, "bili") as bili, patch.object(adapters, "auth_status", side_effect=VpubError("stopped", "Stopped.")):
            result = messages.sync("bilibili", "mentions", min_interval=0, workers=1)
        backend.assert_not_called()
        bili.assert_not_called()
        self.assertEqual("PARTIAL", result["state"])
        self.assertEqual("stopped", result["source_status"][0]["code"])

    def test_a_stop_does_not_get_in_the_way_of_reading_what_is_already_saved(self):
        self.stop()
        self.assertEqual([], messages.listing()["items"])
        self.assertTrue(safety.status()["stopped"])
        self.assertEqual(3, len(operations_sync.watch("list")["items"]))


class UploadPlaylistTests(Temporary, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Temporary.setUp(self)

    async def test_a_stop_holds_the_playlists_an_upload_would_make_first(self):
        self.stop()
        made = []

        async def create(page, identity, payload, before_submit, known=None):
            before_submit(payload, identity)
            made.append(payload["title"])
            return {"collection_id": "PLnew"}
        with patch.object(youtube_studio, "read_playlists", AsyncMock(return_value=([{"id": "PLold", "title": "Old"}], True))), patch.object(youtube_studio, "create_playlist", create), self.assertRaises(VpubError) as caught:
            await youtube_studio.ensure_playlists(MagicMock(), "UC1", ["New"])
        self.assertEqual("stopped", caught.exception.code)
        self.assertEqual([], made)


if __name__ == "__main__":
    unittest.main()

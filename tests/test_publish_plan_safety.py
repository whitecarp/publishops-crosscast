"""Publish-plan scheduling and automatic playlist creation; local files and fake transport only."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from video_publisher import core, youtube_studio as studio


def immediate(coroutine):
    """These stand-ins never suspend; no browser, network or event loop is needed."""
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coroutine.close()
    raise AssertionError("Unexpected asynchronous suspension")


class TemporaryHomes(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "vpub"), "BILI_CLI_HOME": str(self.root / "bili")})
        env.start()
        self.addCleanup(env.stop)


class SchedulePrecisionTests(TemporaryHomes):
    def setUp(self):
        super().setUp()
        self.media = self.root / "fixture.mp4"
        self.media.write_bytes(b"synthetic local plan fixture, not a real video")
        self.path = self.root / "plan.json"
        self.when = (datetime.now(timezone.utc) + timedelta(days=3)).replace(second=0, microsecond=0)

    def plan(self, platform, schedule, method=None):
        options = {"visibility": "private" if platform == "youtube" else "account" if platform == "x" else "public",
                   "ai_generated": False, "schedule": schedule}
        if method is not None:
            options["method"] = method
        if platform == "youtube":
            options.update(category_id="22", made_for_kids=False)
        core.atomic_json(self.path, {"schema_version": 1, "video": str(self.media), "title": "Fixture",
                                    "description": "Offline fixture", "tags": ["test"], "platforms": {platform: options}})
        return core.load_plan(self.path)["platforms"][platform]

    def test_browser_schedulers_refuse_seconds_and_microseconds_before_later_preflight(self):
        for platform in ("youtube", "douyin", "x"):
            for method in (None, "browser"):
                for second, microsecond in ((59, 0), (0, 1), (1, 500000)):
                    with self.subTest(platform=platform, method=method, second=second, microsecond=microsecond):
                        stamp = self.when.replace(second=second, microsecond=microsecond).isoformat()
                        with patch.object(core, "x_weighted_length", side_effect=AssertionError("Reached later X preflight")), self.assertRaises(core.VpubError) as caught:
                            self.plan(platform, stamp, method)
                        self.assertEqual("invalid_schedule", caught.exception.code)
                        self.assertIn("whole minutes", caught.exception.message)
                        self.assertIn("Nothing was uploaded", caught.exception.hint)

    def test_whole_minutes_keep_the_exact_approved_timestamp_and_offset(self):
        for platform in ("youtube", "douyin", "x"):
            for method in (None, "browser"):
                for moment in (self.when, self.when.astimezone(timezone(timedelta(hours=5, minutes=30)))):
                    stamp = moment.isoformat()
                    with self.subTest(platform=platform, method=method, stamp=stamp):
                        # This fixture has only plain ASCII, no links or emoji. Text counting has its own tests.
                        with patch.object(core, "x_weighted_length", side_effect=len):
                            self.assertEqual(stamp, self.plan(platform, stamp, method)["schedule"])

    def test_subminute_timezone_offset_cannot_shift_a_whole_minute_input(self):
        for platform in ("youtube", "douyin", "x"):
            stamp = self.when.replace(tzinfo=timezone(timedelta(seconds=30))).isoformat()
            with self.subTest(platform=platform), self.assertRaises(core.VpubError) as caught:
                self.plan(platform, stamp)
            self.assertEqual("invalid_schedule", caught.exception.code)

    def test_bilibili_cli_and_youtube_api_keep_seconds_and_fractional_seconds(self):
        stamp = self.when.replace(second=59, microsecond=123456).isoformat()
        for platform, method in (("bilibili", None), ("bilibili", "cli"), ("youtube", "api")):
            with self.subTest(platform=platform, method=method):
                self.assertEqual(stamp, self.plan(platform, stamp, method)["schedule"])

    def test_naive_past_and_public_youtube_schedules_still_refuse(self):
        for stamp in (self.when.replace(tzinfo=None).isoformat(), (self.when - timedelta(days=4)).isoformat()):
            with self.subTest(stamp=stamp), self.assertRaises(core.VpubError) as caught:
                self.plan("youtube", stamp)
            self.assertEqual("invalid_schedule", caught.exception.code)
        self.plan("youtube", self.when.isoformat())
        manifest = core.read_json(self.path)
        manifest["platforms"]["youtube"]["visibility"] = "public"
        core.atomic_json(self.path, manifest)
        with self.assertRaises(core.VpubError) as caught:
            core.load_plan(self.path)
        self.assertEqual("invalid_schedule", caught.exception.code)


class PlaylistCreationTests(TemporaryHomes):
    def setUp(self):
        super().setUp()
        self.page = type("Page", (), {"wait_for_timeout": AsyncMock()})()
        self.sent = []

    def records(self):
        return [core.read_json(path) for path in (self.root / "vpub/youtube-auto-playlists").glob("*.json")]

    async def create(self, page, identity, payload, before_submit, known):
        before_submit(payload, identity)
        records = self.records()
        self.assertTrue(any(r["identity"] == identity and r["title"] == payload["title"] and r["state"] == "SUBMITTING" for r in records))
        self.sent.append((identity, dict(payload), list(known)))
        return {"collection_id": "PLfixture" + str(len(self.sent))}

    def ensure(self, rows, complete, titles=("Wanted",), create=None, identity="fixture-owner"):
        with patch.object(studio, "read_playlists", new=AsyncMock(return_value=(rows, complete))), \
                patch.object(studio, "create_playlist", side_effect=create or self.create) as dispatch:
            result = immediate(studio.ensure_playlists(self.page, identity, list(titles)))
        return result, dispatch.call_count

    def test_empty_incomplete_read_never_creates_even_after_retry(self):
        for _ in range(2):
            with self.assertRaises(core.VpubError) as caught:
                self.ensure([], False)
            self.assertEqual("read_incomplete", caught.exception.code)
        self.assertEqual([], self.sent)
        self.assertEqual([], self.records())

    def test_nonempty_incomplete_read_never_creates_missing_title(self):
        with self.assertRaises(core.VpubError) as caught:
            self.ensure([{"id": "PLold", "title": "Other"}], False)
        self.assertEqual("read_incomplete", caught.exception.code)
        self.assertEqual([], self.sent)

    def test_complete_empty_creates_once_private_and_records_before_send(self):
        made, calls = self.ensure([], True)
        self.assertEqual([{"id": "PLfixture1", "title": "Wanted"}], made)
        self.assertEqual(1, calls)
        self.assertEqual(("fixture-owner", {"title": "Wanted", "privacy": "private"}, []), self.sent[0])
        self.assertEqual("CREATED", self.records()[0]["state"])
        self.assertEqual("PLfixture1", self.records()[0]["collection_id"])

    def test_existing_title_needs_no_creation_even_with_incomplete_list(self):
        self.assertEqual(([], 0), self.ensure([{"id": "PLold", "title": "Wanted"}], False))
        self.assertEqual([], self.records())

    def test_late_list_read_can_find_the_playlist_without_creating(self):
        with patch.object(studio, "read_playlists", new=AsyncMock(side_effect=[([], False), ([{"id": "PLold", "title": "Wanted"}], True)])), \
                patch.object(studio, "create_playlist", new=AsyncMock()) as create:
            self.assertEqual([], immediate(studio.ensure_playlists(self.page, "fixture-owner", ["Wanted"])))
            create.assert_not_called()

    def test_uncertain_creation_persists_and_blocks_reentry_even_with_complete_empty(self):
        async def uncertain(*args, **kwargs):
            await self.create(*args, **kwargs)
            raise TimeoutError("Synthetic uncertain readback")

        with self.assertRaises(TimeoutError):
            self.ensure([], True, create=uncertain)
        self.assertEqual("SUBMITTING", self.records()[0]["state"])
        for _ in range(2):
            with self.assertRaises(core.VpubError) as caught:
                self.ensure([], True)
            self.assertEqual("playlist_unverified", caught.exception.code)
        self.assertEqual(1, len(self.sent))
        # A later visible match can be used; no second Create is needed.
        self.assertEqual(([], 0), self.ensure([{"id": "PLoriginal", "title": "Wanted"}], True))

    def test_no_returned_id_keeps_attempt_uncertain(self):
        async def missing_id(*args, **kwargs):
            await self.create(*args, **kwargs)
            return {"collection_id": None}

        with self.assertRaises(core.VpubError):
            self.ensure([], True, create=missing_id)
        with self.assertRaises(core.VpubError):
            self.ensure([], True)
        self.assertEqual(1, len(self.sent))

    def test_failure_before_final_create_does_not_mark_or_prevent_retry(self):
        async def stopped_before(*args, **kwargs):
            raise core.VpubError("layout_changed", "Synthetic: Create was not reached")

        with self.assertRaises(core.VpubError):
            self.ensure([], True, create=stopped_before)
        self.assertEqual([], self.records())
        self.assertEqual(1, self.ensure([], True)[1])

    def test_failed_durable_marker_prevents_send(self):
        with patch.object(studio, "atomic_json", side_effect=OSError("Synthetic storage failure")), self.assertRaises(OSError):
            self.ensure([], True)
        self.assertEqual([], self.sent)

    def test_stop_arriving_during_durable_save_prevents_send(self):
        def save_then_stop(path, value):
            core.atomic_json(path, value)
            core.atomic_json(self.root / "vpub/STOP.json", {"reason": "Synthetic pause during save"})

        with patch.object(studio, "atomic_json", side_effect=save_then_stop), self.assertRaises(core.VpubError) as caught:
            self.ensure([], True)
        self.assertEqual("stopped", caught.exception.code)
        self.assertEqual([], self.sent)
        self.assertEqual("SUBMITTING", self.records()[0]["state"])

    def test_completed_creation_that_disappears_is_not_recreated(self):
        self.ensure([], True)
        with self.assertRaises(core.VpubError) as caught:
            self.ensure([], True)
        self.assertEqual("playlist_unverified", caught.exception.code)
        self.assertEqual(1, len(self.sent))

    def test_attempts_are_bound_to_owner_and_normalized_title(self):
        self.ensure([], True, titles=("Wanted  list",))
        with self.assertRaises(core.VpubError):
            self.ensure([], True, titles=("Wanted list",))
        self.ensure([], True, titles=("Wanted list",), identity="other-owner")
        self.ensure([], True, titles=("Another title",))
        self.assertEqual(3, len(self.sent))

    def test_last_check_refuses_another_attempt_recorded_while_form_opened(self):
        async def race(page, identity, payload, before_submit, known):
            self.ensure([], True)
            return await self.create(page, identity, payload, before_submit, known)

        with self.assertRaises(core.VpubError) as caught:
            self.ensure([], True, create=race)
        self.assertEqual("playlist_unverified", caught.exception.code)
        self.assertEqual(1, len(self.sent))


class PlaylistEmptyReadTests(TemporaryHomes):
    class Page:
        def locator(self, selector):
            assert selector == "#navigate-after"
            return "next-control"

        def get_by_text(self, pattern):
            raise AssertionError("Unscoped text cannot prove the playlist list is empty")

    def read(self, raw, next_control):
        rows = type("Rows", (), {"evaluate_all": AsyncMock(return_value=raw)})()
        with patch.object(studio, "open_playlist_list", new=AsyncMock(return_value=rows)), \
                patch.object(studio, "visible", new=AsyncMock(return_value=next_control)):
            return immediate(studio.read_playlists(self.Page(), "fixture-owner"))

    def test_missing_next_never_proves_absence_from_empty_partial_or_unparsed_rows(self):
        for raw, expected_count in (([], 0), ([{"href": "/playlist/PLold/edit", "title": "Other"}], 1),
                                    ([{"href": "", "title": "No content available"}], 0)):
            with self.subTest(raw=raw):
                rows, complete = self.read(raw, None)
                self.assertEqual(expected_count, len(rows))
                self.assertFalse(complete)

    def test_explicit_last_page_keeps_complete_empty_and_nonempty_reads(self):
        next_control = type("Next", (), {"get_attribute": AsyncMock(return_value="true")})()
        for raw in ([], [{"href": "/playlist/PLold/edit", "title": "Other"}]):
            with self.subTest(raw=raw):
                rows, complete = self.read(raw, next_control)
                self.assertEqual(len(raw), len(rows))
                self.assertTrue(complete)

    def test_other_table_consumers_keep_their_existing_missing_next_behavior(self):
        rows = type("Rows", (), {"evaluate_all": AsyncMock(return_value=[{"id": "old"}])})()
        with patch.object(studio, "visible", new=AsyncMock(return_value=None)):
            result = immediate(studio.studio_table(self.Page(), rows, "unused", lambda raw: {item["id"]: item for item in raw}))
        self.assertEqual(([{"id": "old"}], True), result)


if __name__ == "__main__":
    unittest.main()

"""Named-zone display fixtures are synthetic; no live Studio schema is asserted."""
from datetime import datetime
import unittest
from unittest.mock import AsyncMock, patch

from video_publisher import browser_backends, browser_forms
from video_publisher.core import read_json
import test_youtube_browser as yt


def immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coroutine.close()
    raise AssertionError("Unexpected asynchronous suspension")


class ScheduleZoneTests(yt.FormCase):
    def setUp(self):
        super().setUp()
        runner = patch.object(yt.asyncio, "run", side_effect=immediate)
        runner.start()
        self.addCleanup(runner.stop)

    def refused(self, text, when="2030-11-04T15:00:00+00:00"):
        page = yt.FakeStudio(scheduler_text=text)
        self.fails_before_saving(page, self.plan(schedule=when), "schedule_unverified")
        self.assertFalse(read_json(page.receipt_path)["final_submit_attempted"])
        return page

    def test_named_zone_uses_target_rules_across_spring_and_fall_and_readback(self):
        for current, when, offset, hour in (
                ("2030-01-15T15:00:00+00:00", "2030-03-11T15:00:00+00:00", "-05:00", 11),
                ("2030-07-15T15:00:00+00:00", "2030-11-04T15:00:00+00:00", "-04:00", 10)):
            class Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime.fromisoformat(current).astimezone(tz)

            target = datetime.fromisoformat(when)
            shown = f"Scheduled\n{target.year}年{target.month}月{target.day}日 {hour}:00"
            page = yt.FakeStudio(scheduler_text=f"Time zone GMT{offset} America/New_York", visibility_text=shown)
            plan = self.plan(schedule=when)
            plan["confirmation"] = str(hour % 10) * 64
            with self.subTest(when=when), patch.object(browser_forms, "datetime", Clock):
                _, result = self.run_form(page, plan)
            self.assertEqual("SUBMISSION_VERIFIED", result["state"])
            self.assertEqual("America/New_York", result["schedule"]["timezone"])
            self.assertEqual([hour, 0], result["schedule"]["time"])
            self.assertEqual(target, datetime.fromisoformat(result["schedule"]["local"]))
            self.assertEqual([], result["readback_differs"])
            self.assertEqual(1, len(page.clicks("#done-button")))

    def test_explicit_utc_can_publish_and_read_back_exactly(self):
        page = yt.FakeStudio(scheduler_text="Time zone UTC", visibility_text="Scheduled\n2030年11月4日 15:00")
        _, result = self.run_form(page, self.plan(schedule="2030-11-04T15:00:00+00:00"))
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertEqual("UTC", result["schedule"]["timezone"])
        self.assertEqual(1, len(page.clicks("#done-button")))

    def test_old_offset_wall_time_in_readback_cannot_verify_new_named_zone_schedule(self):
        page = yt.FakeStudio(scheduler_text="America/New_York", visibility_text="Scheduled\n2030年11月4日 11:00")
        _, result = self.run_form(page, self.plan(schedule="2030-11-04T15:00:00+00:00"))
        self.assertEqual([10, 0], result["schedule"]["time"])
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
        self.assertIn("visibility", result["readback_differs"])
        self.assertEqual(1, len(page.clicks("#done-button")))

    def test_postsave_explicit_offset_or_named_zone_conflict_stays_unverified(self):
        for index, suffix in enumerate(("GMT+08:00", "UTC+08:00", "Asia/Shanghai", "UTC\nAsia/Shanghai")):
            with self.subTest(suffix=suffix):
                page = yt.FakeStudio(scheduler_text="Time zone UTC", visibility_text="Scheduled\n2030年11月4日 15:00 " + suffix)
                plan = self.plan(schedule="2030-11-04T15:00:00+00:00")
                plan["confirmation"] = str(index) * 64
                _, result = self.run_form(page, plan)
                self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
                self.assertIn("schedule_timezone", result["readback_differs"])
                self.assertEqual(1, len(page.clicks("#done-button")))

    def test_postsave_matching_explicit_zone_and_target_offset_verify(self):
        for index, suffix in enumerate(("GMT+00:00", "UTC", "UTC GMT+00:00")):
            with self.subTest(suffix=suffix):
                page = yt.FakeStudio(scheduler_text="UTC", visibility_text="Scheduled\n2030年11月4日 15:00 " + suffix)
                plan = self.plan(schedule="2030-11-04T15:00:00+00:00")
                plan["confirmation"] = str(index) * 64
                _, result = self.run_form(page, plan)
                self.assertEqual("SUBMISSION_VERIFIED", result["state"])
                self.assertEqual([], result["readback_differs"])

    def test_postsave_named_dst_zone_refuses_offset_neither_current_nor_target(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromisoformat("2030-01-15T12:00:00+00:00").astimezone(tz)

        schedule = {"at": "2030-11-04T15:00:00+00:00", "local": "2030-11-04T10:00:00-05:00", "timezone": "America/New_York"}
        for text, expected in (("America/New_York GMT-05:00", True), ("America/New_York GMT-04:00", False),
                               ("America/Lima GMT-05:00", False), ("GMT-05:00", True)):
            with self.subTest(text=text), patch.object(browser_backends, "datetime", Clock):
                self.assertIs(expected, browser_backends.youtube_schedule_zone_agrees(text, schedule))

    def test_current_offset_label_survives_dst_in_native_readback(self):
        for index, (current, when, display_offset, target_offset, hour) in enumerate((
                ("2030-11-15T12:00:00+00:00", "2031-04-15T15:00:00+00:00", "+00:00", "+01:00", 16),
                ("2030-07-15T12:00:00+00:00", "2030-11-15T15:00:00+00:00", "+01:00", "+00:00", 15))):
            class Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime.fromisoformat(current).astimezone(tz)

            target = datetime.fromisoformat(when)
            for number, suffix in enumerate((f"Europe/London GMT{display_offset}", f"GMT{display_offset}",
                                             f"UTC{display_offset}", f"Europe/London GMT{target_offset}")):
                with self.subTest(when=when, suffix=suffix), patch.object(browser_forms, "datetime", Clock), \
                        patch.object(browser_backends, "datetime", Clock):
                    shown = f"Scheduled\n{target.year}年{target.month}月{target.day}日 {hour}:00 {suffix}"
                    page = yt.FakeStudio(scheduler_text=f"Time zone GMT{display_offset} Europe/London", visibility_text=shown)
                    plan = self.plan(schedule=when)
                    plan["confirmation"] = f"{index * 10 + number:064x}"
                    tx, result = self.run_form(page, plan)
                    self.assertEqual("SUBMISSION_VERIFIED", result["state"])
                    self.assertEqual([], result["readback_differs"])
                    self.assertEqual(target, datetime.fromisoformat(result["schedule"]["local"]))
                    self.assertEqual([hour, 0], result["schedule"]["time"])
                    self.assertEqual(1, len(page.clicks("#done-button")))
                    self.assertEqual(result, read_json(tx.path))

    def test_current_offset_label_does_not_excuse_wrong_wall_time(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromisoformat("2030-11-15T12:00:00+00:00").astimezone(tz)

        page = yt.FakeStudio(scheduler_text="Time zone Europe/London GMT+00:00",
                             visibility_text="Scheduled\n2031年4月15日 15:00 Europe/London GMT+00:00")
        with patch.object(browser_forms, "datetime", Clock), patch.object(browser_backends, "datetime", Clock):
            _, result = self.run_form(page, self.plan(schedule="2031-04-15T15:00:00+00:00"))
        self.assertEqual([16, 0], result["schedule"]["time"])
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
        self.assertIn("visibility", result["readback_differs"])
        self.assertEqual(1, len(page.clicks("#done-button")))

    def test_current_offset_acceptance_requires_consistent_instant_and_named_zone(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromisoformat("2030-11-15T12:00:00+00:00").astimezone(tz)

        schedule = {"at": "2031-04-15T15:00:00+00:00", "local": "2031-04-15T16:00:00+01:00", "timezone": "Europe/London"}
        cases = (("Europe/London GMT+02:00", schedule),
                 ("Europe/London GMT+00:00 GMT+01:00", schedule),
                 ("Europe/Paris GMT+01:00", schedule),
                 ("UTC GMT+00:00", schedule),
                 ("GMT+00:00", {**schedule, "timezone": None}),
                 ("GMT+00:00", {**schedule, "local": "2031-04-15T16:00:00+00:00"}),
                 ("GMT+00:00", {**schedule, "local": "2031-04-15T15:00:00+00:00"}),
                 ("GMT+00:00", {**schedule, "at": "2031-04-15T15:00:00"}),
                 ("Europe/London", {**schedule, "at": "2030-10-27T00:30:00+00:00", "local": "2030-10-27T01:30:00+01:00"}),
                 ("Europe/London", {**schedule, "at": "2030-10-27T01:30:00+00:00", "local": "2030-10-27T01:30:00+00:00"}))
        for shown, record in cases:
            with self.subTest(shown=shown, record=record), patch.object(browser_backends, "datetime", Clock):
                self.assertFalse(browser_backends.youtube_schedule_zone_agrees(shown, record))

    def test_offset_only_missing_abbreviated_ambiguous_and_conflicting_zones_never_submit(self):
        for index, text in enumerate(("", "GMT-04:00", "GMT+00:00", "EST", "CST", "Time zone China Standard Time",
                                      "America/New_York or America/Lima", "America/New_York\nAmerica/Lima",
                                      "Unknown/City", "Time zone UTC GMT+05:00", "America/New_York GMT+08:00",
                                      "UTC\nTime zone EST", "UTC\nAmerica/New_York or America/Lima")):
            with self.subTest(text=text):
                page = yt.FakeStudio(scheduler_text=text)
                plan = self.plan(schedule="2030-11-04T15:00:00+00:00")
                plan["confirmation"] = f"{index:064x}"
                self.fails_before_saving(page, plan, "schedule_unverified")
                self.assertFalse(read_json(page.receipt_path)["final_submit_attempted"])

    def test_both_occurrences_of_repeated_fall_minute_are_refused(self):
        for hour in (5, 6):
            with self.subTest(hour=hour):
                page = yt.FakeStudio(scheduler_text="America/New_York")
                plan = self.plan(schedule=f"2030-11-03T{hour:02}:30:00+00:00")
                plan["confirmation"] = str(hour) * 64
                self.fails_before_saving(page, plan, "schedule_unverified")
                self.assertFalse(read_json(page.receipt_path)["final_submit_attempted"])

    def test_missing_zone_data_never_submits(self):
        with patch.object(browser_forms, "ZoneInfo", side_effect=browser_forms.ZoneInfoNotFoundError("fixture data unavailable")):
            self.refused("America/New_York")

    def test_zone_change_during_final_risk_check_never_submits(self):
        page = yt.FakeStudio(scheduler_text="America/New_York")

        async def changed(_):
            if page.receipt_path and read_json(page.receipt_path).get("phase") == "preview":
                page.scheduler_text = "UTC"

        with patch.object(browser_backends, "risk_check", new=AsyncMock(side_effect=changed)):
            self.fails_before_saving(page, self.plan(schedule="2030-11-04T15:00:00+00:00"), "schedule_unverified")
        self.assertFalse(read_json(page.receipt_path)["final_submit_attempted"])

    def test_wall_time_change_during_final_risk_check_never_submits(self):
        page = yt.FakeStudio(scheduler_text="UTC")

        async def changed(_):
            if page.receipt_path and read_json(page.receipt_path).get("phase") == "preview":
                page.time_text = "16:00"

        with patch.object(browser_backends, "risk_check", new=AsyncMock(side_effect=changed)):
            self.fails_before_saving(page, self.plan(schedule="2030-11-04T15:00:00+00:00"), "schedule_unverified")
        self.assertFalse(read_json(page.receipt_path)["final_submit_attempted"])

    def test_same_offset_named_zones_keep_ordinary_schedules(self):
        for name, local in (("Asia/Shanghai", "2030-11-04T23:00:00+08:00"), ("Etc/GMT+5", "2030-11-04T10:00:00-05:00")):
            with self.subTest(name=name):
                moment, selected = browser_forms.yt_schedule_zone(name, datetime.fromisoformat("2030-11-04T15:00:00+00:00"))
                self.assertEqual((local, name), (moment.isoformat(), selected))


if __name__ == "__main__":
    unittest.main()

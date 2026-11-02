"""The free YouTube route through Studio in the dedicated browser. A fake page; no platform is contacted."""
import asyncio
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import adapters, browser_forms, browser_runtime, cli, hidden_desktop, youtube_studio
from video_publisher.browser_backends import Transaction, execute, studio_visibility, work_url
from video_publisher.browser_forms import youtube_link_id
from video_publisher.core import VpubError, atomic_json, file_sha, home, load_plan, read_json

CHANNEL = "UC" + "a" * 22
VIDEO = "dQw4w9WgXcQ"
DIALOG = browser_forms.YT_DIALOG + " "
RADIO = 'tp-yt-paper-radio-button[name="%s"]'


class Element:
    """One thing on the fake page. Writes are logged in order, the way Studio would see them."""

    def __init__(self, page, selector, index=None):
        self.page, self.selector, self.index = page, selector, index
        self.key = selector[len(DIALOG):] if selector.startswith(DIALOG) else selector

    @property
    def first(self):
        return self

    def filter(self, **options):
        return self

    def get_by_text(self, pattern, **options):
        return Element(self.page, self.selector + " edit-draft")

    def locator(self, selector):
        return Element(self.page, self.selector + " " + selector)

    def nth(self, index):
        return Element(self.page, self.selector, index)

    def exists(self):
        if "verifycenter" in self.key or any(part in self.key for part in self.page.missing):
            return False
        if browser_forms.YT_CHECKS_WARNING in self.key:
            return self.page.warning_open
        if "#done-button" in self.key and self.page.published and not self.page.form_stays:
            return False
        if self.key == "edit-draft":
            return self.page.is_draft
        return self.page.picker_open if self.key.startswith(browser_forms.YT_PLAYLISTS) else True

    async def all(self):
        return [self] if self.exists() else []

    async def count(self):
        if "ancestor-or-self" in self.key:
            return 0
        if "ytcp-chip" in self.key:
            self.page.chip_reads += 1
            return self.page.chips if self.page.chip_reads > self.page.chips_late else 0
        return 1 if self.exists() else 0

    async def is_visible(self):
        # The picker's outer element has no size on the real page, so it never counts as visible.
        return self.exists() and self.key != browser_forms.YT_PLAYLISTS

    async def click(self, timeout=None):
        self.page.log.append(("click", self.key))
        if "#done-button" in self.key:
            self.page.at_final_click = dict(read_json(self.page.receipt_path)) if self.page.receipt_path else {}
            self.page.polls_at_final_click = self.page.polls
            self.page.saved = True
            if self.page.warnings:
                self.page.warnings -= 1
                self.page.warning_open = True
            elif not self.page.press_does_nothing:
                self.page.published = True
                self.page.is_draft = False
        if browser_forms.YT_CHECKS_WARNING in self.key:
            self.page.warning_open = False
        if "#next-button" in self.key:
            self.page.steps += 1
        if "tp-yt-paper-item" in self.key:
            self.page.category = "Education"
        if "ytcp-video-metadata-playlists" in self.key:
            self.page.picker_open = True
        if self.key.endswith('[role="checkbox"]') and not self.page.refuse_tick:
            self.page.ticked ^= {self.index}
        if self.key.endswith(".done-button"):
            self.page.picker_open = False

    async def get_attribute(self, name):
        if name == "aria-checked":
            return "true" if ("click", self.key) in self.page.log and not self.page.refuse_radio else "false"
        if name == "uploading":
            self.page.polls += 1
            return "" if self.page.polls <= self.page.uploading_polls else None

    async def evaluate(self, script):
        return True

    async def evaluate_all(self, script):
        if ".progress-label" in self.key:
            return [self.page.checks_label]
        if self.key.endswith('[role="checkbox"]'):
            return [{"lines": [title, "Private"], "checked": i in self.page.ticked} for i, title in enumerate(self.page.playlists)]
        return [self.page.link] if self.page.has_link else []

    async def fill(self, value, **options):
        self.page.log.append(("fill", self.key, value))
        if "ytcp-date-picker" in self.key:
            self.page.date_text = self.page.date_text if self.page.refuse_schedule else value
        elif "#time-of-day-container" in self.key:
            self.page.time_text = self.page.time_text if self.page.refuse_schedule else value
        elif not self.page.ignore_fill:
            self.page.text[self.key] = value

    async def press(self, key, **options):
        self.page.log.append(("press", self.key, key))

    async def input_value(self, **options):
        return self.page.time_text if "#time-of-day-container" in self.key else self.page.text.get(self.key, "")

    async def inner_text(self, **options):
        if "ytcp-video-metadata-visibility" in self.key:
            if self.page.visibility_blank_reads:
                self.page.visibility_blank_reads -= 1
                return ""
            return self.page.visibility_text if self.page.visibility_text is not None else {"PUBLIC": "Public", "UNLISTED": "Unlisted", "PRIVATE": "Private"}.get(self.page.chosen_visibility(), "")
        if "#category" in self.key:
            return self.page.category or "Choose"
        if "tp-yt-paper-item" in self.key:
            return "Education"
        if "ytcp-video-metadata-playlists" in self.key:
            return ", ".join(title for i, title in enumerate(self.page.playlists) if i in self.page.ticked) or "Select"
        if "#datepicker-trigger" in self.key:
            return self.page.date_text
        if "ytcp-visibility-scheduler" in self.key:
            return self.page.scheduler_text
        return self.page.text.get(self.key, "")

    async def press_sequentially(self, text, **options):
        self.page.log.append(("type", self.key, text))
        if "#time-of-day-container" in self.key:
            self.page.time_text = self.page.time_text if self.page.refuse_schedule else text
            return
        self.page.chips = len([tag for tag in text.split(",") if tag]) - self.page.lost_tags

    async def select_text(self, **options):
        self.page.log.append(("select", self.key))

    async def set_input_files(self, path, **options):
        self.page.log.append(("files", self.key, str(path)))

    async def wait_for(self, **options):
        if options.get("state") == "hidden":
            if self.exists():
                raise TimeoutError(self.key)
        elif not self.exists():
            raise TimeoutError(self.key)


class FakeStudio:
    def __init__(self, **options):
        self.log, self.text, self.missing, self.chips, self.steps, self.polls = [], {}, set(), 0, 0, 0
        self.category, self.saved, self.at_final_click, self.polls_at_final_click = None, False, None, None
        self.has_link, self.ignore_fill, self.refuse_radio, self.lost_tags = True, False, False, 0
        self.uploading_polls, self.visibility_text, self.receipt_path = 0, None, None
        self.visibility_blank_reads, self.chip_reads, self.chips_late = 0, 0, 0
        self.listeners, self.studio_sends, self.ticks, self.left_at = {}, {}, 0, []
        self.playlists, self.ticked, self.picker_open, self.refuse_tick = [], set(), False, False
        self.date_text, self.time_text, self.scheduler_text, self.refuse_schedule = "2026年10月31日", "18:00", "", False
        self.warnings, self.warning_open, self.published, self.form_stays = 0, False, False, False
        self.press_does_nothing, self.is_draft, self.checks_label = False, False, "检查完毕"
        self.keyboard = MagicMock(press=AsyncMock())
        self.url = "https://studio.youtube.com/channel/" + CHANNEL
        self.link = f"https://youtu.be/{VIDEO}"
        for name, value in options.items():
            setattr(self, name, value)

    def locator(self, selector):
        return Element(self, selector)

    def get_by_text(self, pattern, **options):
        return Element(self, "edit-draft") if pattern is browser_forms.YT_EDIT_DRAFT else MagicMock(all=AsyncMock(return_value=[]))

    async def evaluate(self, script):
        return [{"tag": "TP-YT-PAPER-DIALOG", "buttons": ["Publish"]}]

    async def goto(self, url, **options):
        self.log.append(("goto", url))
        self.url = url
        if self.saved:
            self.left_at.append(self.ticks)

    def on(self, event, listener):
        self.listeners.setdefault(event, []).append(listener)

    def remove_listener(self, event, listener):
        self.listeners[event].remove(listener)

    async def wait_for_timeout(self, ms):
        # Each pause after the final Save is one tick, and Studio's own requests go out on the ticks a test names.
        if self.saved:
            self.ticks += 1
            for event in self.studio_sends.get(self.ticks, ()):
                for listener in list(self.listeners.get(event, ())):
                    listener(MagicMock(url="https://studio.youtube.com/youtubei/v1/video_manager/metadata_update"))

    async def screenshot(self, **options):
        return None

    def chosen_visibility(self):
        names = [entry[1] for entry in self.log if entry[0] == "click" and "radio" in entry[1]]
        return next((name.split('"')[1] for name in reversed(names) if name.split('"')[1] in {"PUBLIC", "UNLISTED", "PRIVATE"}), None)

    def clicks(self, part):
        return [entry for entry in self.log if entry[0] == "click" and part in entry[1]]

    def selected_files(self):
        return [entry for entry in self.log if entry[0] == "files" and "file-picker" in entry[1]]


class Temporary(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        sleep = patch("asyncio.sleep", new=AsyncMock())
        sleep.start()
        self.addCleanup(sleep.stop)
        self.video = self.root / "video.mp4"
        self.video.write_bytes(b"fixture-video")


class FormCase(Temporary):
    def plan(self, **options):
        return {"video": str(self.video), "video_sha256": file_sha(self.video), "confirmation": "a" * 64,
                "options": {"title": "My title", "description": "Line one\nLine two", "tags": ["vlog", "travel"], "visibility": "private",
                            "made_for_kids": False, "ai_generated": True, "category_id": "27", "submission_mode": "publish", **options}}

    def run_form(self, page, plan=None):
        tx = Transaction("youtube", CHANNEL, plan or self.plan())
        page.receipt_path = tx.path
        return tx, asyncio.run(browser_forms.publish_youtube(page, tx))

    def fails_before_saving(self, page, plan, code):
        with self.assertRaises(VpubError) as caught:
            self.run_form(page, plan)
        self.assertEqual(code, caught.exception.code)
        self.assertEqual([], page.clicks("#done-button"))


class FormTests(FormCase):
    def test_one_upload_one_final_save_then_a_read_back(self):
        page = FakeStudio()
        tx, result = self.run_form(page)
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertEqual(VIDEO, result["id"])
        self.assertEqual(f"https://www.youtube.com/watch?v={VIDEO}", result["url"])
        self.assertEqual(1, len(page.selected_files()))
        self.assertEqual(1, len(page.clicks("#done-button")))
        self.assertEqual(3, page.steps)
        self.assertEqual(("goto", f"https://studio.youtube.com/video/{VIDEO}/edit"), [e for e in page.log if e[0] == "goto"][-1])
        self.assertEqual(result, read_json(tx.path))

    def test_the_page_is_not_left_until_studios_own_request_has_ended(self):
        """From a field run on Studio's other saves: Save greyed out at once, the change went out about six seconds later, and leaving before it ended cancelled it."""
        page = FakeStudio(studio_sends={12: ["request"], 40: ["requestfinished"]})
        self.assertEqual("SUBMISSION_VERIFIED", self.run_form(page)[1]["state"])
        self.assertGreater(page.left_at[0], 40)
        self.assertEqual(1, len(page.clicks("#done-button")))

    def test_studios_warning_is_answered_by_going_back_and_pressing_once_more_after_its_checks(self):
        """Public uploaders show Studio asking this when Publish goes in while its checks run. A field run's film stayed a draft, and the page wasn't captured."""
        page = FakeStudio(warnings=1)
        tx, result = self.run_form(page)
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertEqual(2, len(page.clicks("#done-button")))
        self.assertEqual(1, len(page.clicks(browser_forms.YT_CHECKS_WARNING)))
        self.assertEqual(1, len(page.selected_files()))
        self.assertEqual(2, result["final_presses"])
        self.assertIn("checks_warning", result)

    def test_a_warning_that_comes_back_is_left_as_a_draft_with_two_presses(self):
        page = FakeStudio(warnings=5, visibility_text="", is_draft=True)
        tx, result = self.run_form(page)
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
        self.assertTrue(result["still_a_draft"])
        self.assertIn("vpub resume", result["note"])
        self.assertEqual(2, len(page.clicks("#done-button")))

    def test_checks_that_never_finish_get_no_second_press(self):
        page = FakeStudio(warnings=5, checks_label="Checking", visibility_text="", is_draft=True)
        with patch.object(browser_forms, "YT_CHECKS_SECONDS", 0):
            tx, result = self.run_form(page)
        self.assertTrue(result["still_a_draft"])
        self.assertEqual(1, len(page.clicks("#done-button")))

    def test_a_stop_during_the_wait_for_checks_holds_the_second_press(self):
        page = FakeStudio(warnings=1, visibility_text="", is_draft=True)
        with patch.object(browser_forms, "stop_check", side_effect=VpubError("stopped", "Stop.")), self.assertRaises(VpubError) as caught:
            self.run_form(page)
        self.assertEqual("stopped", caught.exception.code)
        self.assertEqual(1, len(page.clicks("#done-button")))

    def test_a_press_that_neither_closes_nor_warns_records_what_is_open(self):
        """A field run had nothing to show for this moment."""
        page = FakeStudio(press_does_nothing=True, visibility_text="", is_draft=True)
        with patch.object(browser_forms, "YT_PRESS_ANSWER_SECONDS", 1):
            tx, result = self.run_form(page)
        self.assertEqual([{"tag": "TP-YT-PAPER-DIALOG", "buttons": ["Publish"]}], result["after_press"])
        self.assertTrue(result["still_a_draft"])
        self.assertEqual(1, len(page.clicks("#done-button")))

    def test_a_save_with_no_request_seen_still_gets_the_longest_delay_seen(self):
        page = FakeStudio()
        self.run_form(page)
        self.assertEqual(youtube_studio.PIN_TICKS, page.left_at[0])

    def test_tags_that_show_a_moment_after_typing_do_not_stop_it(self):
        page = FakeStudio(chips_late=2)
        self.assertEqual("SUBMISSION_VERIFIED", self.run_form(page)[1]["state"])

    def test_every_setting_reaches_the_form(self):
        page = FakeStudio()
        self.run_form(page)
        self.assertIn(("fill", "#title-textarea #textbox", "My title"), page.log)
        self.assertIn(("fill", "#description-textarea #textbox", "Line one\nLine two"), page.log)
        self.assertIn(("type", "#tags-container input", "vlog,travel,"), page.log)
        self.assertEqual(1, len(page.clicks('tp-yt-paper-item[test-id="CREATOR_VIDEO_CATEGORY_EDUCATION"]')))
        self.assertEqual("Education", page.category)
        self.assertEqual("PRIVATE", page.chosen_visibility())

    def test_a_category_studio_does_not_show_stops_before_the_final_click(self):
        page = FakeStudio(missing={"CREATOR_VIDEO_CATEGORY"})
        with self.assertRaises(VpubError) as caught:
            self.run_form(page)
        self.assertEqual("category_unverified", caught.exception.code)
        self.assertEqual([], page.clicks("#done-button"))

    def test_audience_and_ai_choices_follow_the_plan(self):
        for ai_generated, kids, altered, audience in [(True, False, "VIDEO_HAS_ALTERED_CONTENT_YES", "VIDEO_MADE_FOR_KIDS_NOT_MFK"),
                                                      (False, True, "VIDEO_HAS_ALTERED_CONTENT_NO", "VIDEO_MADE_FOR_KIDS_MFK")]:
            with self.subTest(ai_generated=ai_generated, kids=kids):
                page = FakeStudio()
                plan = self.plan(ai_generated=ai_generated, made_for_kids=kids)
                plan["confirmation"] = ("b" if kids else "c") * 64
                self.run_form(page, plan)
                self.assertEqual(1, len(page.clicks(RADIO % altered)))
                self.assertEqual(1, len(page.clicks(RADIO % audience)))

    def test_the_video_id_is_saved_before_the_final_click(self):
        page = FakeStudio()
        self.run_form(page)
        saved = page.at_final_click
        self.assertEqual(VIDEO, saved["id"])
        self.assertEqual(VIDEO, saved["draft_id"])
        self.assertTrue(saved["final_submit_attempted"])
        self.assertEqual("UNKNOWN", saved["state"])

    def test_the_upload_finishes_before_the_final_click(self):
        page = FakeStudio(uploading_polls=3)
        self.run_form(page)
        self.assertEqual(4, page.polls_at_final_click)

    def test_a_setting_that_cannot_be_confirmed_stops_before_the_final_click(self):
        for name, code in [("VIDEO_HAS_ALTERED_CONTENT", "declaration_unverified"), ("VIDEO_MADE_FOR_KIDS", "audience_unverified"),
                           ('"PRIVATE"', "visibility_unverified")]:
            with self.subTest(missing=name):
                page = FakeStudio(missing={name})
                tx = Transaction("youtube", CHANNEL, {**self.plan(), "confirmation": code[0] * 64})
                with self.assertRaises(VpubError) as caught:
                    asyncio.run(browser_forms.publish_youtube(page, tx))
                self.assertEqual(code, caught.exception.code)
                self.assertEqual([], page.clicks("#done-button"))
                self.assertFalse(tx.receipt["final_submit_attempted"])

    def test_text_that_does_not_stick_stops_before_the_final_click(self):
        page = FakeStudio(ignore_fill=True)
        with self.assertRaises(VpubError) as caught:
            self.run_form(page)
        self.assertEqual("metadata_mismatch", caught.exception.code)
        self.assertEqual([], page.clicks("#done-button"))

    def test_lost_tags_stop_before_the_final_click(self):
        page = FakeStudio(lost_tags=1)
        with self.assertRaises(VpubError) as caught:
            self.run_form(page)
        self.assertEqual("metadata_mismatch", caught.exception.code)
        self.assertEqual([], page.clicks("#done-button"))

    def test_a_channel_without_custom_thumbnails_stops_before_the_final_click(self):
        cover = self.root / "cover.jpg"
        cover.write_bytes(b"cover")
        plan = {**self.plan(), "cover": str(cover), "cover_sha256": file_sha(cover)}
        page = FakeStudio(missing={"input#file-loader"})
        with self.assertRaises(VpubError) as caught:
            self.run_form(page, plan)
        self.assertEqual("cover_unavailable", caught.exception.code)
        self.assertEqual([], page.clicks("#done-button"))

    def test_the_thumbnail_is_sent_when_the_channel_allows_it(self):
        cover = self.root / "cover.jpg"
        cover.write_bytes(b"cover")
        page = FakeStudio()
        _, result = self.run_form(page, {**self.plan(), "cover": str(cover), "cover_sha256": file_sha(cover)})
        self.assertIn(("files", "input#file-loader", str(cover)), page.log)
        self.assertFalse(result["cover"]["remote_pixels_verified"])

    def test_draft_only_uploads_and_never_clicks_final_save(self):
        page = FakeStudio(uploading_polls=1)
        _, result = self.run_form(page, self.plan(submission_mode="draft_only"))
        self.assertEqual("PREVIEW_READY", result["state"])
        self.assertEqual(VIDEO, result["draft_id"])
        self.assertNotIn("id", result)
        self.assertEqual([], page.clicks("#done-button"))
        self.assertFalse(result["final_submit_attempted"])

    def test_no_video_link_means_no_edits_and_no_final_click(self):
        page = FakeStudio(has_link=False)
        clock = MagicMock(monotonic=MagicMock(side_effect=itertools.count(0, 10)))
        with patch("video_publisher.browser_backends.time", new=clock), self.assertRaises(VpubError) as caught:
            self.run_form(page)
        self.assertEqual("processing_timeout", caught.exception.code)
        self.assertIn("YouTube video ID", caught.exception.message)
        self.assertEqual([], page.clicks("#done-button"))
        self.assertGreater(next(clock.monotonic.side_effect), browser_forms.YT_LINK_SECONDS)

    def test_a_stop_before_the_id_says_what_the_dialog_showed(self):
        """From a field run: nothing could say whether the upload was slow or its link wasn't one this read."""
        page = FakeStudio(has_link=False)
        look = asyncio.run(browser_forms.yt_upload_look(page))
        self.assertEqual({"progress_shown": 1, "percent": None, "finished": True, "links": []}, look)
        page = FakeStudio(link=f"https://studio.youtube.com/video/{VIDEO}/edit")
        self.assertEqual(["studio.youtube.com/video/<id>/edit"], asyncio.run(browser_forms.yt_upload_look(page))["links"])

    def test_a_vertical_videos_shorts_link_counts(self):
        """A field run's first vertical video stopped here: every test before it was landscape, and this only read the other two kinds of link."""
        page = FakeStudio(link=f"https://www.youtube.com/shorts/{VIDEO}")
        _, result = self.run_form(page)
        self.assertEqual(("SUBMISSION_VERIFIED", VIDEO), (result["state"], result["id"]))


class PlaylistTests(FormCase):
    def test_exactly_the_planned_playlists_are_ticked_and_read_back(self):
        page = FakeStudio(playlists=["Trips", "Trips 2", "Food"], ticked={1})
        tx, result = self.run_form(page, self.plan(playlist_titles=["Trips", "Food"]))
        self.assertEqual({0, 2}, page.ticked)
        self.assertFalse(page.picker_open)
        self.assertEqual(1, len(page.clicks("#done-button")))
        self.assertEqual(["Trips", "Food"], result["playlists"])
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])

    def test_the_picker_is_closed_with_its_own_done_never_the_final_save(self):
        page = FakeStudio(playlists=["Trips"])
        self.run_form(page, self.plan(playlist_titles=["Trips"], submission_mode="draft_only"))
        self.assertEqual(1, len(page.clicks(browser_forms.YT_PLAYLISTS + " .done-button")))
        self.assertEqual([], page.clicks("#done-button"))

    def test_a_missing_or_doubled_title_stops_before_the_final_click(self):
        for playlists, code in [(["Food"], "playlist_not_found"), (["Trips", "Trips"], "playlist_ambiguous")]:
            with self.subTest(playlists=playlists):
                plan = self.plan(playlist_titles=["Trips"])
                plan["confirmation"] = code[9] * 64
                self.fails_before_saving(FakeStudio(playlists=playlists), plan, code)

    def test_a_box_that_will_not_tick_stops_before_the_final_click(self):
        self.fails_before_saving(FakeStudio(playlists=["Trips"], refuse_tick=True), self.plan(playlist_titles=["Trips"]), "playlist_unverified")


class ScheduleTests(FormCase):
    WHEN = "2030-01-01T00:00:00+00:00"

    def test_the_time_is_typed_in_studios_own_zone_and_style(self):
        page = FakeStudio(scheduler_text="Time zone GMT+08:00 China Standard Time", visibility_text="已安排\n2030年1月1日 08:00")
        _, result = self.run_form(page, self.plan(schedule=self.WHEN))
        self.assertIn(("fill", "ytcp-date-picker tp-yt-paper-input input", "2030年1月1日"), page.log)
        self.assertIn(("type", "#time-of-day-container input", "08:00"), page.log)
        self.assertEqual({"at": self.WHEN, "local": "2030-01-01T08:00:00+08:00", "date": [2030, 1, 1], "time": [8, 0], "timezone_basis": "studio",
                          "time_entry": {"typed": "08:00", "after_leaving": "08:00"}}, result["schedule"])
        self.assertIsNone(page.chosen_visibility())
        self.assertEqual(1, len(page.clicks("#done-button")))
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])

    def test_an_english_studio_gets_its_own_date_and_twelve_hour_time(self):
        page = FakeStudio(date_text="Oct 31, 2026", time_text="6:00 PM", scheduler_text="GMT-05:00", visibility_text="Scheduled\nDec 31, 2029, 7:00 PM")
        _, result = self.run_form(page, self.plan(schedule=self.WHEN))
        self.assertEqual(("Dec 31, 2029", "7:00 PM"), (page.date_text, page.time_text))
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])

    def test_without_a_zone_on_screen_this_computers_zone_is_used(self):
        local = __import__("datetime").datetime.fromisoformat(self.WHEN).astimezone()
        page = FakeStudio()
        _, result = self.run_form(page, self.plan(schedule=self.WHEN, submission_mode="draft_only"))
        self.assertEqual(("computer", [local.hour, local.minute]), (result["schedule"]["timezone_basis"], result["schedule"]["time"]))

    def test_an_older_studio_layout_still_opens_the_schedule(self):
        page = FakeStudio(missing={"#second-container-expand-button"})
        self.run_form(page, self.plan(schedule=self.WHEN, submission_mode="draft_only"))
        self.assertEqual(1, len(page.clicks("#schedule-radio-button")))

    def test_a_time_that_will_not_hold_says_what_the_box_showed_and_how_to_get_past_it(self):
        with self.assertRaises(VpubError) as caught:
            self.run_form(FakeStudio(refuse_schedule=True), self.plan(schedule=self.WHEN))
        self.assertIn("held 18:00 before", caught.exception.message)
        self.assertIn("after_leaving", caught.exception.message)
        self.assertIn("quarter hour", caught.exception.hint)

    def test_anything_unconfirmed_stops_before_the_final_click(self):
        for options, code in [({"missing": {"#second-container-expand-button", "#schedule-radio-button"}}, "a"),
                              ({"date_text": "31/10/2026"}, "b"), ({"refuse_schedule": True}, "c"),
                              ({"scheduler_text": "GMT+08:00 or GMT-05:00"}, "d")]:
            with self.subTest(options=options):
                plan = self.plan(schedule=self.WHEN)
                plan["confirmation"] = code * 64
                self.fails_before_saving(FakeStudio(**options), plan, "schedule_unverified")


class ResumeTests(Temporary):
    def plan(self):
        return {"video": str(self.video), "video_sha256": file_sha(self.video), "confirmation": "a" * 64,
                "options": {"title": "My title", "description": "Text", "tags": [], "visibility": "public", "made_for_kids": False,
                            "ai_generated": False, "category_id": "27", "submission_mode": "publish"}}

    def test_a_failed_step_keeps_the_draft_and_resume_never_uploads_again(self):
        payload = {"action": "publish", "plan": self.plan()}
        page = FakeStudio(missing={"VIDEO_MADE_FOR_KIDS"})
        result = asyncio.run(execute(page, "youtube", CHANNEL, payload))
        self.assertEqual("PREPARATION_INCOMPLETE", result["state"])
        self.assertEqual(VIDEO, result["draft_id"])
        self.assertEqual("audience_unverified", result["error"]["code"])
        self.assertEqual(1, len(page.selected_files()))
        again = FakeStudio()
        with patch("video_publisher.youtube_studio.drafts_named", new=AsyncMock(return_value=[])):
            result = asyncio.run(execute(again, "youtube", CHANNEL, {"action": "resume", "plan": self.plan()}))
        self.assertEqual("inspect_existing_draft", result["error"]["code"])
        self.assertEqual([], again.selected_files())

    def pressed_and_left_a_draft(self):
        tx = Transaction("youtube", CHANNEL, self.plan())
        tx.save(media_selected=True, upload_filename="crosscast-abc.mp4", draft_id=VIDEO, id=VIDEO, state="SUBMITTED_UNVERIFIED", final_submit_attempted=True, final_presses=1, visibility="public")

    def draft_page(self, **options):
        page = FakeStudio(is_draft=True, **options)
        page.text.update({"#title-textarea #textbox": "My title", "#description-textarea #textbox": "Text"})
        return page

    def test_resume_presses_publish_on_a_draft_the_read_back_shows_and_uploads_nothing(self):
        self.pressed_and_left_a_draft()
        page = self.draft_page()
        result = asyncio.run(execute(page, "youtube", CHANNEL, {"action": "resume", "plan": self.plan()}))
        self.assertEqual(("SUBMISSION_VERIFIED", VIDEO), (result["state"], result["id"]))
        self.assertFalse(result["still_a_draft"])
        self.assertEqual(([], 1, 2), (page.selected_files(), len(page.clicks("#done-button")), result["final_presses"]))
        self.assertEqual([], [entry for entry in page.log if entry[0] == "fill"])

    def test_verify_only_says_so_and_presses_nothing(self):
        self.pressed_and_left_a_draft()
        page = self.draft_page()
        receipt = read_json(Transaction("youtube", CHANNEL, self.plan(), resume=True).path)
        result = asyncio.run(execute(page, "youtube", CHANNEL, {"action": "verify", "receipt": receipt}))
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
        self.assertTrue(result["still_a_draft"])
        self.assertEqual(([], []), (page.clicks("edit-draft"), page.clicks("#done-button")))

    def test_resume_presses_nothing_when_the_work_is_no_draft(self):
        self.pressed_and_left_a_draft()
        page = FakeStudio(visibility_text="Unlisted")
        page.text["#title-textarea #textbox"] = "My title"
        result = asyncio.run(execute(page, "youtube", CHANNEL, {"action": "resume", "plan": self.plan()}))
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
        self.assertFalse(result["still_a_draft"])
        self.assertEqual(["visibility"], result["readback_differs"])
        self.assertEqual([], page.clicks("#done-button"))

    def test_a_draft_with_other_words_or_another_id_is_not_pressed(self):
        for change, code in (({"#title-textarea #textbox": "Another title"}, "metadata_mismatch"), ({}, "wrong_work")):
            with self.subTest(code=code):
                self.pressed_and_left_a_draft()
                page = self.draft_page()
                page.text.update(change)
                if code == "wrong_work":
                    page.link = "https://youtu.be/aaaaaaaaaaa"
                result = asyncio.run(execute(page, "youtube", CHANNEL, {"action": "resume", "plan": self.plan()}))
                self.assertEqual(code, result["error"]["code"])
                self.assertEqual([], page.clicks("#done-button"))
                Transaction("youtube", CHANNEL, self.plan(), resume=True).path.unlink()

    def stopped_after_its_id(self):
        tx = Transaction("youtube", CHANNEL, self.plan())
        tx.save(media_selected=True, upload_filename="crosscast-abc.mp4", draft_id=VIDEO, state="PREPARATION_INCOMPLETE", phase="blocked")

    def test_a_draft_that_stopped_after_its_id_is_finished_from_its_own_row(self):
        self.stopped_after_its_id()
        again = FakeStudio()
        with patch("video_publisher.youtube_studio.drafts_named", new=AsyncMock(return_value=[again.locator("ytcp-video-row")])) as named:
            result = asyncio.run(execute(again, "youtube", CHANNEL, {"action": "resume", "plan": self.plan()}))
        named.assert_awaited_once_with(again, CHANNEL, {"crosscast-abc", "My title"})
        self.assertEqual(("SUBMISSION_VERIFIED", VIDEO), (result["state"], result["id"]))
        self.assertEqual(([], 1), (again.selected_files(), len(again.clicks("#done-button"))))

    def test_a_draft_with_another_videos_id_is_left_as_it_is(self):
        self.stopped_after_its_id()
        again = FakeStudio(link="https://youtu.be/aBcDeFgHiJk")
        with patch("video_publisher.youtube_studio.drafts_named", new=AsyncMock(return_value=[again.locator("ytcp-video-row")])):
            result = asyncio.run(execute(again, "youtube", CHANNEL, {"action": "resume", "plan": self.plan()}))
        self.assertEqual("wrong_work", result["error"]["code"])
        self.assertEqual([], [entry for entry in again.log if entry[0] == "fill"])
        self.assertEqual([], again.clicks("#done-button"))

    def stopped_before_its_id(self):
        tx = Transaction("youtube", CHANNEL, self.plan())
        tx.save(media_selected=True, upload_filename="crosscast-abc.mp4", state="PREPARATION_INCOMPLETE", phase="blocked")

    def test_an_upload_with_no_id_that_left_nothing_can_upload_again(self):
        self.stopped_before_its_id()
        tx = Transaction("youtube", CHANNEL, self.plan(), resume=True)
        tx.save(hint="Inspect the existing draft", blocked_phase="uploading", error={"code": "processing_timeout"})
        again = FakeStudio()
        with patch("video_publisher.youtube_studio.drafts_named", new=AsyncMock(return_value=[])):
            result = asyncio.run(execute(again, "youtube", CHANNEL, {"action": "resume", "plan": self.plan()}))
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertEqual(1, len(again.selected_files()))
        self.assertEqual((None, None, None), (result.get("hint"), result.get("blocked_phase"), result.get("error")))

    def test_an_upload_with_no_id_that_left_a_draft_is_finished_from_it(self):
        """From a field run: its first vertical video stopped before its ID, and was in Studio as a draft named after the file."""
        self.stopped_before_its_id()
        again = FakeStudio()
        with patch("video_publisher.youtube_studio.drafts_named", new=AsyncMock(return_value=[again.locator("ytcp-video-row")])) as named:
            result = asyncio.run(execute(again, "youtube", CHANNEL, {"action": "resume", "plan": self.plan()}))
        named.assert_awaited_once_with(again, CHANNEL, {"crosscast-abc"})
        self.assertEqual(("SUBMISSION_VERIFIED", VIDEO), (result["state"], result["id"]))
        self.assertEqual([], again.selected_files())
        self.assertEqual((1, 1), (len(again.clicks("edit-draft")), len(again.clicks("#done-button"))))

    def test_two_drafts_of_one_file_are_left_for_a_person(self):
        self.stopped_before_its_id()
        again = FakeStudio()
        row = again.locator("ytcp-video-row")
        with patch("video_publisher.youtube_studio.drafts_named", new=AsyncMock(return_value=[row, row])):
            result = asyncio.run(execute(again, "youtube", CHANNEL, {"action": "resume", "plan": self.plan()}))
        self.assertEqual(("PREPARATION_INCOMPLETE", "inspect_existing_draft"), (result["state"], result["error"]["code"]))
        self.assertEqual(([], []), (again.selected_files(), again.clicks("edit-draft")))
        self.assertEqual([], again.clicks("#done-button"))

    def test_a_first_attempt_never_looks_for_a_draft(self):
        page = FakeStudio()
        with patch("video_publisher.youtube_studio.drafts_named", new=AsyncMock(return_value=[])) as named:
            asyncio.run(execute(page, "youtube", CHANNEL, {"action": "publish", "plan": self.plan()}))
        named.assert_not_awaited()

    def test_after_the_final_click_resume_only_reads(self):
        tx = Transaction("youtube", CHANNEL, self.plan())
        tx.save(final_submit_attempted=True, media_selected=True, id=VIDEO, state="UNKNOWN", expected_text="Text", visibility="public")
        page = FakeStudio(text={"#title-textarea #textbox": "My title", "#description-textarea #textbox": "Text"}, visibility_text="Public")
        result = asyncio.run(execute(page, "youtube", CHANNEL, {"action": "resume", "plan": self.plan()}))
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertEqual([], [entry for entry in page.log if entry[0] != "goto"])

    def test_a_timed_out_final_click_is_never_repeated(self):
        page = FakeStudio()
        tx = Transaction("youtube", CHANNEL, self.plan())
        page.receipt_path = tx.path
        with patch.object(Element, "click", new=AsyncMock(side_effect=TimeoutError())):
            done = page.locator(DIALOG + "#done-button")
            with patch("video_publisher.browser_backends.risk_check", new=AsyncMock()):
                with self.assertRaises(TimeoutError):
                    asyncio.run(tx.submit(page, done))
                with self.assertRaises(VpubError) as caught:
                    asyncio.run(tx.submit(page, done))
        self.assertEqual("already_submitted", caught.exception.code)
        self.assertTrue(read_json(tx.path)["final_submit_attempted"])


class ReadBackTests(Temporary):
    receipt = {"id": VIDEO, "method": "browser", "identity": CHANNEL, "title": "My title", "expected_text": "Line one\nLine two", "visibility": "private", "state": "UNKNOWN"}

    def verify(self, page, receipt=None):
        from video_publisher.browser_backends import verify
        return asyncio.run(verify(page, "youtube", CHANNEL, receipt or self.receipt))

    def studio(self, **options):
        return FakeStudio(text={"#title-textarea #textbox": "My title", "#description-textarea #textbox": "Line one Line two"}, visibility_text="Private", **options)

    def test_matching_page_is_verified(self):
        result = self.verify(self.studio())
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertEqual([], result["readback_differs"])

    def test_a_visibility_that_fills_in_after_the_title_is_waited_for(self):
        page = self.studio()
        page.visibility_blank_reads = 2
        self.assertEqual("SUBMISSION_VERIFIED", self.verify(page)["state"])

    def test_a_visibility_that_never_fills_in_stays_unverified(self):
        page = self.studio()
        page.visibility_text = ""
        self.assertEqual(["visibility"], self.verify(page)["readback_differs"])

    def test_a_difference_stays_unverified_and_names_the_field(self):
        for key, value, field in [("#title-textarea #textbox", "Another", "title"), ("#description-textarea #textbox", "Another", "description")]:
            with self.subTest(field=field):
                page = self.studio()
                page.text[key] = value
                result = self.verify(page)
                self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
                self.assertEqual([field], result["readback_differs"])
        page = self.studio()
        page.visibility_text = "Public"
        self.assertEqual(["visibility"], self.verify(page)["readback_differs"])

    def test_a_schedule_and_playlists_are_part_of_the_read_back(self):
        receipt = {**self.receipt, "schedule": {"at": "2099-01-01T08:00:00+00:00", "date": [2030, 1, 1], "time": [8, 0]}, "playlists": ["Trips"]}
        page = self.studio(playlists=["Trips"], ticked={0})
        page.visibility_text = "已安排\n2030年1月1日 08:00"
        self.assertEqual("SUBMISSION_VERIFIED", self.verify(page, receipt)["state"])
        page.visibility_text = "已安排\n2030年1月1日 09:00"
        self.assertEqual(["visibility"], self.verify(page, receipt)["readback_differs"])
        page = self.studio()
        page.visibility_text = "已安排\n2030年1月1日 08:00"
        self.assertEqual(["playlists"], self.verify(page, receipt)["readback_differs"])

    def test_studio_dates_times_and_zones_are_read_as_numbers(self):
        from video_publisher.browser_backends import STUDIO_DATES, STUDIO_TIMES, studio_moments, studio_offsets, studio_read
        self.assertEqual((2030, 1, 1), studio_read(STUDIO_DATES, "2030年1月1日"))
        self.assertEqual((2029, 12, 31), studio_read(STUDIO_DATES, "Dec 31, 2029"))
        self.assertEqual((2029, 12, 31), studio_read(STUDIO_DATES, "31 Dec 2029"))
        self.assertIsNone(studio_read(STUDIO_DATES, "31/12/2029"))
        self.assertEqual((18, 0), studio_read(STUDIO_TIMES, "6:00 PM"))
        self.assertEqual((0, 30), studio_read(STUDIO_TIMES, "12:30 AM"))
        self.assertEqual((8, 5), studio_read(STUDIO_TIMES, "08:05"))
        self.assertEqual(({(2029, 12, 31)}, {(19, 0)}), studio_moments("Scheduled\nDec 31, 2029, 7:00 PM"))
        self.assertEqual({480}, studio_offsets("(GMT+08:00) China Standard Time"))
        self.assertEqual({-300, 330}, studio_offsets("GMT−5 GMT+5:30"))

    def test_an_edit_page_that_does_not_open_stays_unverified(self):
        result = self.verify(FakeStudio(missing={"#title-textarea"}))
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
        self.assertEqual("readback_pending", result["phase"])

    def test_another_channels_receipt_is_refused(self):
        with self.assertRaises(VpubError) as caught:
            self.verify(self.studio(), {**self.receipt, "identity": "UC" + "b" * 22})
        self.assertEqual("wrong_account", caught.exception.code)

    def test_an_adopted_video_only_needs_its_page_to_open(self):
        result = self.verify(self.studio(), {"id": VIDEO, "method": "browser", "state": "EXISTING_VERIFIED"})
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])

    def test_visibility_is_read_in_english_and_chinese(self):
        for text, expected in [("Public", "public"), ("Unlisted", "unlisted"), ("Private\nMore", "private"), ("公开", "public"), ("不公开列出", "unlisted"),
                               ("私享", "private"), ("公開", "public"), ("不公開", "unlisted"), ("私人", "private"), ("Draft", None), ("", None), (None, None)]:
            with self.subTest(text=text):
                self.assertEqual(expected, studio_visibility(text))

    def test_links_and_ids_must_be_real_video_ids(self):
        self.assertEqual(VIDEO, youtube_link_id(f"https://youtu.be/{VIDEO}"))
        self.assertEqual(VIDEO, youtube_link_id(f"https://www.youtube.com/watch?v={VIDEO}"))
        self.assertEqual(VIDEO, youtube_link_id(f"https://youtube.com/shorts/{VIDEO}"))
        self.assertEqual(VIDEO, youtube_link_id(f"https://www.youtube.com/shorts/{VIDEO}/"))
        for href in ("https://example.com/watch?v=" + VIDEO, "https://youtu.be/short", "https://www.youtube.com/channel/" + CHANNEL, "", None,
                     f"https://example.com/shorts/{VIDEO}", "https://www.youtube.com/shorts/short", f"https://www.youtube.com/shorts/{VIDEO}/more", "https://www.youtube.com/shorts/"):
            with self.subTest(href=href):
                self.assertIsNone(youtube_link_id(href))
        self.assertEqual(f"https://www.youtube.com/watch?v={VIDEO}", work_url("youtube", CHANNEL, VIDEO))
        with self.assertRaises(VpubError):
            work_url("youtube", CHANNEL, "https://example.com")


class SignInTests(Temporary):
    def identity(self, url):
        return asyncio.run(browser_runtime.identity(MagicMock(url=url), "youtube"))

    def test_the_channel_comes_from_studios_own_address(self):
        self.assertEqual(CHANNEL, self.identity(f"https://studio.youtube.com/channel/{CHANNEL}"))
        self.assertEqual(CHANNEL, self.identity(f"https://studio.youtube.com/channel/{CHANNEL}/videos/upload"))

    def test_anything_else_is_not_a_signed_in_channel(self):
        for url in ("https://accounts.google.com/v3/signin/identifier", "https://studio.youtube.com/", f"https://example.com/channel/{CHANNEL}",
                    "https://studio.youtube.com/channel/UCshort", f"https://www.youtube.com/channel/{CHANNEL}"):
            with self.subTest(url=url):
                self.assertIsNone(self.identity(url))

    def test_google_sign_in_cookies_are_never_copied_out_of_the_profile(self):
        context = MagicMock(storage_state=AsyncMock())
        asyncio.run(browser_runtime.save_session(context, "youtube", "default"))
        context.storage_state.assert_not_awaited()
        self.assertFalse((home() / "accounts/default/youtube-browser-session.json").exists())

    def test_the_profile_is_private_to_crosscast_and_lockable(self):
        self.assertEqual(home() / "accounts/default/youtube-browser-profile", browser_runtime.profile_dir("youtube"))
        with browser_runtime.profile_lock("youtube", "default"):
            with self.assertRaises(VpubError) as caught:
                with browser_runtime.profile_lock("youtube", "default"):
                    pass
        self.assertEqual("browser_busy", caught.exception.code)


class RouteTests(Temporary):
    def setUp(self):
        super().setUp()
        # No add-on, and any attempt to import it would fail loudly.
        for active in (patch.object(adapters.importlib.util, "find_spec", return_value=None), patch.dict(sys.modules, {"crosscast_api": None})):
            active.start()
            self.addCleanup(active.stop)

    def test_it_is_the_free_default(self):
        self.assertEqual("browser", adapters.default_method("youtube"))
        routes = cli.platform_routes("youtube")
        self.assertTrue(routes["standalone_cli_publish"])
        self.assertEqual("dedicated persistent background browser", routes["publish_backend"])
        self.assertTrue(routes["live_upload_verified"])

    def test_status_before_sign_in_says_how_to_sign_in(self):
        with self.assertRaises(VpubError) as caught:
            adapters.auth_status("youtube", "default")
        self.assertEqual("not_logged_in", caught.exception.code)
        self.assertIn("vpub auth login youtube --method browser", caught.exception.hint)

    def test_status_after_sign_in_asks_the_browser(self):
        atomic_json(home() / "accounts/default/youtube-identity.json", {"identity": CHANNEL, "method": "browser"})
        with patch.object(adapters, "browser_call", return_value={"identity": CHANNEL}) as browser:
            self.assertEqual(CHANNEL, adapters.auth_status("youtube", "default")["identity"])
        browser.assert_called_once_with("youtube", "status", "default")

    def test_login_opens_the_dedicated_browser(self):
        with patch("video_publisher.auth_jobs.subprocess.Popen") as popen:
            with patch("video_publisher.browser_broker.release"), patch("video_publisher.auth_jobs.time.sleep"):
                adapters.auth_login("youtube", "default")
        popen.assert_called_once()
        self.assertEqual("youtube", read_json(home() / "auth-jobs/youtube.request.json")["platform"])

    def test_publish_and_verify_go_through_the_browser(self):
        options = {"method": "browser", "account": "default", "expected_identity": CHANNEL}
        saved = []
        with patch.object(adapters, "browser_call", return_value={"state": "SUBMISSION_VERIFIED"}) as browser:
            adapters.publish("youtube", {"video": "v"}, options, saved.append)
            self.assertEqual(("youtube", "publish", "default"), browser.call_args.args)
            self.assertEqual(CHANNEL, browser.call_args.kwargs["expected_identity"])
            self.assertEqual([{"state": "SUBMISSION_VERIFIED"}], saved)
            adapters.verify("youtube", "default", {"id": VIDEO, "method": "browser", "identity": CHANNEL})
            self.assertEqual(("youtube", "verify", "default"), browser.call_args.args)

    def test_wrong_channel_stops_before_anything_starts(self):
        with patch.object(adapters, "auth_status", return_value={"identity": CHANNEL}):
            with self.assertRaises(VpubError) as caught:
                adapters.preflight("youtube", {"method": "browser", "account": "default", "expected_channel_id": "UC" + "b" * 22})
        self.assertEqual("wrong_account", caught.exception.code)

    def test_the_api_route_stays_paid(self):
        for call in (lambda: adapters.auth_status("youtube", "default", "api"),
                     lambda: adapters.auth_login("youtube", "default", "client.json", "api"),
                     lambda: adapters.publish("youtube", {"video": "v"}, {"method": "api", "account": "default"}, lambda r: None),
                     lambda: adapters.verify("youtube", "default", {"id": VIDEO, "method": "api"})):
            with self.assertRaises(VpubError) as caught:
                call()
            self.assertEqual("paid_route_required", caught.exception.code)


class HiddenDesktopTests(Temporary):
    def test_the_browser_command_goes_to_the_hidden_desktop_on_windows_only(self):
        answer = {"ok": True, "data": {"identity": CHANNEL}}
        with patch.object(adapters.os, "name", "nt"), patch.object(adapters, "hidden_desktop_call", return_value=answer) as hidden, \
                patch.object(adapters, "worker_call") as plain, patch("video_publisher.browser_broker.call") as broker:
            self.assertEqual({"identity": CHANNEL}, adapters.browser_call("youtube", "status", "default"))
        hidden.assert_called_once()
        plain.assert_not_called()
        broker.assert_not_called()
        with patch.object(adapters, "stop_check"), patch.object(adapters.os, "name", "posix"), patch.object(adapters, "hidden_desktop_call") as hidden, \
                patch.object(adapters, "worker_call", return_value=answer) as plain:
            adapters.browser_call("youtube", "status", "default")
        hidden.assert_not_called()
        plain.assert_called_once()

    def test_other_platforms_keep_their_own_route(self):
        with patch.object(adapters.os, "name", "nt"), patch.object(adapters, "hidden_desktop_call") as hidden, \
                patch.object(adapters, "worker_call", return_value={"ok": True, "data": {}}), patch("video_publisher.browser_broker.enabled", return_value=False):
            adapters.browser_call("x", "status", "default")
        hidden.assert_not_called()

    def test_a_read_on_windows_uses_the_shared_hidden_session_and_a_one_shot_only_if_none_can_start(self):
        with patch("video_publisher.browser_broker.call", return_value={"identity": CHANNEL}) as session, patch.object(hidden_desktop, "call_worker") as one_shot:
            self.assertEqual({"ok": True, "data": {"identity": CHANNEL}}, adapters.hidden_desktop_call({"platform": "youtube", "account": "default", "action": "operations"}, 10))
        session.assert_called_once()
        one_shot.assert_not_called()
        answer = {"ok": True, "data": {}}
        with patch("video_publisher.browser_broker.call", return_value=None), patch.object(hidden_desktop, "call_worker", return_value=answer) as one_shot:
            self.assertEqual(answer, adapters.hidden_desktop_call({"platform": "youtube", "account": "default", "action": "operations"}, 10))
        one_shot.assert_called_once()

    def test_a_command_that_runs_alone_closes_the_session_first_and_never_uses_it(self):
        answer = {"ok": True, "data": {}}
        for action in ("publish", "resume", "edit", "read_all"):
            with self.subTest(action=action), patch("video_publisher.browser_broker.call") as session, patch("video_publisher.browser_broker.release") as release, \
                    patch.object(hidden_desktop, "call_worker", return_value=answer) as one_shot:
                adapters.hidden_desktop_call({"platform": "youtube", "account": "default", "action": action}, 10)
                session.assert_not_called()
                release.assert_called_once_with("youtube", "default")
                one_shot.assert_called_once()

    def test_turning_shared_sessions_off_turns_this_one_off_too(self):
        with patch.dict(os.environ, {"VPUB_BROWSER_IDLE_SECONDS": "0"}), patch("video_publisher.browser_broker.call") as session, patch.object(hidden_desktop, "call_worker", return_value={"ok": True, "data": {}}) as one_shot:
            adapters.hidden_desktop_call({"platform": "youtube", "account": "default", "action": "operations"}, 10)
        session.assert_not_called()
        one_shot.assert_called_once()

    def test_a_youtube_session_starts_only_on_the_hidden_desktop_never_as_a_plain_process(self):
        from video_publisher import browser_broker as broker
        with patch.object(hidden_desktop, "Started") as started, patch.object(broker.subprocess, "Popen") as plain:
            broker.start("youtube", "default")
        started.assert_called_once()
        plain.assert_not_called()
        with patch.object(hidden_desktop, "Started") as started, patch.object(broker.subprocess, "Popen") as plain:
            broker.start("x", "default")
        started.assert_not_called()
        plain.assert_called_once()

    def test_outside_windows_youtube_never_keeps_a_session(self):
        with patch.object(adapters, "stop_check"), patch.object(adapters.os, "name", "posix"), patch("video_publisher.browser_broker.call") as session, \
                patch("video_publisher.browser_broker.start") as starting, patch.object(adapters, "worker_call", return_value={"ok": True, "data": {}}) as plain:
            adapters.browser_call("youtube", "operations", "default")
        session.assert_not_called()
        starting.assert_not_called()
        plain.assert_called_once()

    def test_a_youtube_session_stays_only_seconds_and_other_platforms_keep_their_five_minutes(self):
        from video_publisher import browser_broker as broker
        for platform, wanted in (("youtube", broker.YOUTUBE_IDLE_SECONDS), ("x", None)):
            with self.subTest(platform=platform):
                pw = MagicMock(__aenter__=AsyncMock(return_value=MagicMock()), __aexit__=AsyncMock(return_value=False))
                context = MagicMock(close=AsyncMock(), storage_state=AsyncMock(return_value={"cookies": []}))
                with patch("patchright.async_api.async_playwright", return_value=pw), patch.object(browser_runtime, "open_context", new=AsyncMock(return_value=context)), \
                        patch.object(browser_runtime, "signed_in_page", new=AsyncMock(return_value=(MagicMock(), "owner"))), patch.object(broker, "serve", new=AsyncMock()) as serve:
                    asyncio.run(broker.run(platform, "default"))
                self.assertEqual(wanted, serve.await_args.kwargs["idle"])
        self.assertLess(broker.YOUTUBE_IDLE_SECONDS, 60)

    @unittest.skipUnless(os.name == "nt", "Windows desktops")
    def test_a_process_left_running_on_the_hidden_desktop_says_when_it_has_ended(self):
        done = self.root / "done.txt"
        code = ("import ctypes,sys,time\n"
                "u=ctypes.WinDLL('user32')\n"
                "u.GetThreadDesktop.restype=ctypes.c_void_p\n"
                "d=u.GetThreadDesktop(ctypes.WinDLL('kernel32').GetCurrentThreadId())\n"
                "b=ctypes.create_unicode_buffer(256)\n"
                "u.GetUserObjectInformationW(ctypes.c_void_p(d),2,b,512,None)\n"
                "time.sleep(0.5)\n"
                "open(sys.argv[1],'w').write(b.value)\n")
        started = hidden_desktop.Started([sys.executable, "-c", code, str(done)])
        self.assertIsNone(started.poll())
        for _ in range(600):
            if started.poll() is not None:
                break
            time.sleep(0.1)
        self.assertIsNotNone(started.poll())
        self.assertEqual(hidden_desktop.DESKTOP, done.read_text())

    def test_the_answer_comes_back_and_no_private_files_stay(self):
        def worker(argv, timeout):
            request = read_json(argv[-1])
            self.assertEqual("status", request["action"])
            atomic_json(request["result_path"], {"ok": True, "data": {"identity": CHANNEL}})
        with patch.object(hidden_desktop, "run", side_effect=worker):
            self.assertEqual({"ok": True, "data": {"identity": CHANNEL}}, hidden_desktop.call_worker({"action": "status"}, 10))
        self.assertEqual([], list((home() / "browser-jobs").iterdir()))

    def test_no_answer_and_timeouts_are_reported_and_clean_up(self):
        with patch.object(hidden_desktop, "run"), self.assertRaises(VpubError) as caught:
            hidden_desktop.call_worker({"action": "status"}, 10)
        self.assertEqual("browser_error", caught.exception.code)
        with patch.object(hidden_desktop, "run", side_effect=subprocess.TimeoutExpired("x", 1)), self.assertRaises(VpubError) as caught:
            hidden_desktop.call_worker({"action": "status"}, 10)
        self.assertEqual("browser_timeout", caught.exception.code)
        self.assertEqual([], list((home() / "browser-jobs").iterdir()))

    @unittest.skipUnless(os.name == "nt", "Windows desktops")
    def test_a_real_process_runs_on_the_hidden_desktop(self):
        out = self.root / "desktop.txt"
        code = ("import ctypes,sys\n"
                "u=ctypes.WinDLL('user32')\n"
                "u.GetThreadDesktop.restype=ctypes.c_void_p\n"
                "d=u.GetThreadDesktop(ctypes.WinDLL('kernel32').GetCurrentThreadId())\n"
                "b=ctypes.create_unicode_buffer(256)\n"
                "u.GetUserObjectInformationW(ctypes.c_void_p(d),2,b,512,None)\n"
                "open(sys.argv[1],'w').write(b.value)\n")
        hidden_desktop.run([sys.executable, "-c", code, str(out)], 60)
        self.assertEqual(hidden_desktop.DESKTOP, out.read_text())

    @unittest.skipUnless(os.name == "nt", "Windows desktops")
    def test_a_real_timeout_ends_the_process(self):
        out = self.root / "late.txt"
        code = "import time,sys\ntime.sleep(20)\nopen(sys.argv[1],'w').write('late')\n"
        with self.assertRaises(subprocess.TimeoutExpired):
            hidden_desktop.run([sys.executable, "-c", code, str(out)], 2)
        self.assertFalse(out.exists())


class PlanTests(Temporary):
    def load(self, **options):
        path = self.root / "publish.json"
        atomic_json(path, {"schema_version": 1, "video": str(self.video), "title": options.pop("title", "T"), "description": options.pop("description", "D"), "tags": ["a"],
                           "platforms": {"youtube": {"visibility": "private", "ai_generated": True, "made_for_kids": False, "category_id": "27", **options}}})
        return load_plan(path)["platforms"]["youtube"]

    def refused(self, code, **options):
        with self.assertRaises(VpubError) as caught:
            self.load(**options)
        self.assertEqual(code, caught.exception.code)

    def test_a_plan_without_a_method_uses_the_free_route(self):
        self.assertEqual("browser", self.load()["method"])

    def test_every_route_can_still_be_chosen(self):
        for method in ("browser", "native", "api"):
            self.assertEqual(method, self.load(method=method)["method"])
        self.refused("invalid_method", method="cli")

    def test_what_this_route_cannot_do_is_refused_before_any_upload(self):
        self.refused("unsupported_playlist", playlist_ids=["PLabcdefgh"])
        self.refused("invalid_category", category_id="30")
        self.assertEqual("api", self.load(method="api", category_id="30")["method"])

    def test_playlists_by_title_and_a_schedule_are_free(self):
        plan = self.load(playlist_titles=["Trips"], schedule="2030-01-01T00:00:00+00:00")
        self.assertEqual(("browser", ["Trips"], "2030-01-01T00:00:00+00:00"), (plan["method"], plan["playlist_titles"], plan["schedule"]))
        # A scheduled video stays private until Studio publishes it.
        self.refused("invalid_schedule", schedule="2030-01-01T00:00:00+00:00", visibility="public")
        self.refused("invalid_schedule", schedule="2030-01-01T00:00:00")
        self.refused("unsupported_schedule", schedule="2030-01-01T00:00:00+00:00", method="native")

    def test_characters_youtube_refuses_are_caught_at_plan_time(self):
        self.refused("invalid_title", title="a <b> title")
        self.refused("invalid_description", description="1 > 0")

    def test_draft_only_needs_this_route(self):
        self.assertEqual("draft_only", self.load(submission_mode="draft_only")["submission_mode"])
        self.refused("invalid_submission_mode", submission_mode="draft_only", method="native")
        self.refused("invalid_submission_mode", submission_mode="draft_only", method="api")
        self.refused("invalid_submission_mode", submission_mode="later")

    def test_init_writes_the_free_route_and_can_ask_for_a_draft(self):
        with patch.object(adapters.importlib.util, "find_spec", return_value=None):
            for name, flags in (("a.json", []), ("b.json", ["--draft-only"])):
                out = self.root / name
                cli.execute(cli.parser().parse_args(["init", "--video", str(self.video), "--out", str(out), "--platforms", "youtube", *flags]))
                saved = json.loads(out.read_text(encoding="utf-8"))["platforms"]["youtube"]
                self.assertEqual("browser", saved["method"])
                self.assertEqual("draft_only" if flags else None, saved.get("submission_mode"))


if __name__ == "__main__":
    unittest.main()

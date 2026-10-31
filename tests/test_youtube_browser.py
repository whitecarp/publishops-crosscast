"""The free YouTube route through Studio in the dedicated browser. A fake page; no platform is contacted."""
import asyncio
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import adapters, browser_forms, browser_runtime, cli, hidden_desktop
from video_publisher.browser_backends import Transaction, execute, studio_visibility, work_url
from video_publisher.browser_forms import youtube_link_id
from video_publisher.core import VpubError, atomic_json, file_sha, home, load_plan, read_json

CHANNEL = "UC" + "a" * 22
VIDEO = "dQw4w9WgXcQ"
DIALOG = browser_forms.YT_DIALOG + " "
RADIO = 'tp-yt-paper-radio-button[name="%s"]'


class Element:
    """One thing on the fake page. Writes are logged in order, the way Studio would see them."""

    def __init__(self, page, selector):
        self.page, self.selector = page, selector
        self.key = selector[len(DIALOG):] if selector.startswith(DIALOG) else selector

    @property
    def first(self):
        return self

    def filter(self, **options):
        return self

    def exists(self):
        return "verifycenter" not in self.key and not any(part in self.key for part in self.page.missing)

    async def all(self):
        return [self] if self.exists() else []

    async def count(self):
        if "ytcp-chip" in self.key:
            return self.page.chips
        return 1 if self.exists() else 0

    async def is_visible(self):
        return self.exists()

    async def click(self, timeout=None):
        self.page.log.append(("click", self.key))
        if "#done-button" in self.key:
            self.page.at_final_click = dict(read_json(self.page.receipt_path)) if self.page.receipt_path else {}
            self.page.polls_at_final_click = self.page.polls
            self.page.saved = True
        if "#next-button" in self.key:
            self.page.steps += 1
        if "tp-yt-paper-item" in self.key:
            self.page.category = "Education"

    async def get_attribute(self, name):
        if name == "aria-checked":
            return "true" if ("click", self.key) in self.page.log and not self.page.refuse_radio else "false"
        if name == "uploading":
            self.page.polls += 1
            return "" if self.page.polls <= self.page.uploading_polls else None

    async def evaluate(self, script):
        return True

    async def evaluate_all(self, script):
        return [f"https://youtu.be/{VIDEO}"] if self.page.has_link else []

    async def fill(self, value, **options):
        self.page.log.append(("fill", self.key, value))
        if not self.page.ignore_fill:
            self.page.text[self.key] = value

    async def inner_text(self, **options):
        if "ytcp-video-metadata-visibility" in self.key:
            return self.page.visibility_text if self.page.visibility_text is not None else {"PUBLIC": "Public", "UNLISTED": "Unlisted", "PRIVATE": "Private"}.get(self.page.chosen_visibility(), "")
        if "#category" in self.key:
            return self.page.category or "Choose"
        if "tp-yt-paper-item" in self.key:
            return "Education"
        return self.page.text.get(self.key, "")

    async def press_sequentially(self, text, **options):
        self.page.log.append(("type", self.key, text))
        self.page.chips = len([tag for tag in text.split(",") if tag]) - self.page.lost_tags

    async def set_input_files(self, path, **options):
        self.page.log.append(("files", self.key, str(path)))

    async def wait_for(self, **options):
        if not self.exists():
            raise TimeoutError(self.key)


class FakeStudio:
    def __init__(self, **options):
        self.log, self.text, self.missing, self.chips, self.steps, self.polls = [], {}, set(), 0, 0, 0
        self.category, self.saved, self.at_final_click, self.polls_at_final_click = None, False, None, None
        self.has_link, self.ignore_fill, self.refuse_radio, self.lost_tags = True, False, False, 0
        self.uploading_polls, self.visibility_text, self.receipt_path = 0, None, None
        self.url = "https://studio.youtube.com/channel/" + CHANNEL
        for name, value in options.items():
            setattr(self, name, value)

    def locator(self, selector):
        return Element(self, selector)

    def get_by_text(self, pattern, **options):
        return MagicMock(all=AsyncMock(return_value=[]))

    async def goto(self, url, **options):
        self.log.append(("goto", url))
        self.url = url

    async def wait_for_timeout(self, ms):
        return None

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


class FormTests(Temporary):
    def plan(self, **options):
        return {"video": str(self.video), "video_sha256": file_sha(self.video), "confirmation": "a" * 64,
                "options": {"title": "My title", "description": "Line one\nLine two", "tags": ["vlog", "travel"], "visibility": "private",
                            "made_for_kids": False, "ai_generated": True, "category_id": "27", "submission_mode": "publish", **options}}

    def run_form(self, page, plan=None):
        tx = Transaction("youtube", CHANNEL, plan or self.plan())
        page.receipt_path = tx.path
        return tx, asyncio.run(browser_forms.publish_youtube(page, tx))

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
        clock = MagicMock(monotonic=MagicMock(side_effect=itertools.count(0, 60)))
        with patch("video_publisher.browser_backends.time", new=clock), self.assertRaises(VpubError) as caught:
            self.run_form(page)
        self.assertEqual("processing_timeout", caught.exception.code)
        self.assertEqual([], page.clicks("#done-button"))


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
        with self.assertRaises(VpubError) as caught:
            asyncio.run(execute(again, "youtube", CHANNEL, {"action": "resume", "plan": self.plan()}))
        self.assertEqual("inspect_existing_draft", caught.exception.code)
        self.assertEqual([], again.selected_files())

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
        for href in ("https://example.com/watch?v=" + VIDEO, "https://youtu.be/short", "https://www.youtube.com/channel/" + CHANNEL, "", None):
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
        with patch.object(adapters.os, "name", "posix"), patch.object(adapters, "hidden_desktop_call") as hidden, \
                patch.object(adapters, "worker_call", return_value=answer) as plain:
            adapters.browser_call("youtube", "status", "default")
        hidden.assert_not_called()
        plain.assert_called_once()

    def test_other_platforms_keep_their_own_route(self):
        with patch.object(adapters.os, "name", "nt"), patch.object(adapters, "hidden_desktop_call") as hidden, \
                patch.object(adapters, "worker_call", return_value={"ok": True, "data": {}}), patch("video_publisher.browser_broker.enabled", return_value=False):
            adapters.browser_call("x", "status", "default")
        hidden.assert_not_called()

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
        self.refused("unsupported_playlist", playlist_titles=["Trips"])
        self.refused("unsupported_playlist", playlist_ids=["PLabcdefgh"])
        self.refused("unsupported_schedule", schedule="2030-01-01T00:00:00+00:00")
        self.refused("invalid_category", category_id="30")
        self.assertEqual("api", self.load(method="api", category_id="30")["method"])

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

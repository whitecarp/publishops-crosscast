"""Publishing on X and Instagram, driven in an invisible browser against small copies of their forms, and what a Bilibili upload is held to when read back. Nothing is contacted."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).parent))
from stage_browser import SharedBrowser

from video_publisher import adapters, browser_backends, browser_forms, cli
from video_publisher.browser_backends import Transaction
from video_publisher.core import VpubError, atomic_json, file_sha, home, load_plan, read_json

OWNER = "owner"
POST = "2115117485020135559"
REEL = "Cabcde12345"
BVID = "BV1xx411c7mD"


class Form(SharedBrowser):
    """What the X and Instagram copies share. Each publish goes through the real Transaction and its durable receipt."""

    platform = None

    def prepare(self):
        self.uploads = []
        self.final = []
        self.posted = None
        self.check = ""
        self.keeps_text = True
        video = self.root / "clip.mp4"
        video.write_bytes(b"synthetic video")
        self.plan = {"confirmation": "hash", "video": str(video), "video_sha256": file_sha(video), "options": {"title": "My title", "description": "Words", "tags": [], "post_text": "My title\n\nWords", "ai_generated": True, "visibility": "account", "account": "default"}}
        sleeping = patch.object(browser_forms.asyncio, "sleep", AsyncMock())
        sleeping.start()
        self.addCleanup(sleeping.stop)

    def receipt(self):
        path = home() / "browser-receipts" / f"hash-{self.platform}.json"
        return read_json(path) if path.exists() else {}

    def note_final(self, route):
        saved = self.receipt()
        self.final.append((json.loads(route.request.post_data)["text"], saved.get("state"), saved.get("final_submit_attempted")))

    async def publish(self):
        return await getattr(browser_forms, "publish_" + self.platform)(self.page, Transaction(self.platform, OWNER, self.plan))

    async def refused(self, code):
        with self.assertRaises(VpubError) as caught:
            await self.publish()
        self.assertEqual(code, caught.exception.code)
        await self.page.evaluate("1")
        self.assertEqual([], self.final)

    async def test_it_records_the_submit_then_presses_once_and_reads_the_work_back(self):
        result = await self.publish()
        self.assertEqual(("SUBMISSION_VERIFIED", True), (result["state"], result["final_submit_attempted"]))
        self.assertEqual([("My title\n\nWords", "UNKNOWN", True)], self.final)
        self.assertEqual(1, len(self.uploads))
        self.assertEqual("SUBMISSION_VERIFIED", self.receipt()["state"])

    async def test_a_draft_stops_at_the_preview_and_never_presses(self):
        self.plan["options"]["submission_mode"] = "draft_only"
        result = await self.publish()
        self.assertEqual(("PREVIEW_READY", False), (result["state"], result["final_submit_attempted"]))
        self.assertEqual([], self.final)

    async def test_words_the_form_did_not_keep_stop_before_the_final_press(self):
        self.keeps_text = False
        await self.refused("metadata_mismatch")

    async def test_a_stop_holds_the_final_press(self):
        self.stop()
        await self.refused("stopped")
        self.assertFalse(self.receipt()["final_submit_attempted"])

    async def test_the_platform_asking_for_a_check_holds_the_final_press(self):
        self.check = "<div role='dialog'><h1>Confirm your identity</h1></div>"
        with self.assertRaises(VpubError) as caught:
            await self.publish()
        self.assertEqual("human_verification_required", caught.exception.code)
        self.assertEqual([], self.final)
        self.assertTrue((home() / "STOP.json").exists())

    async def test_a_work_that_never_shows_is_not_called_published(self):
        self.never_shows = True
        result = await self.publish()
        self.assertEqual(("SUBMITTED_UNVERIFIED", True), (result["state"], result["final_submit_attempted"]))
        self.assertEqual(1, len(self.final))

    async def test_a_second_run_of_the_same_plan_never_starts(self):
        await self.publish()
        with self.assertRaises(VpubError) as caught:
            await self.publish()
        self.assertEqual("attempt_exists", caught.exception.code)
        self.assertEqual(1, len(self.final))


class FileGoingUpTests(SharedBrowser):
    """From a review: Media Studio's page was checked for nothing between loading and choosing the file, so a warning, or a stop from another command, did not hold it back."""
    PAGE = """<input type="file" accept="video/mp4" onchange="document.body.dataset.chosen = this.files.length">"""

    def prepare(self):
        self.body = self.PAGE
        self.clip = self.root / "clip.mp4"
        self.clip.write_bytes(b"clip")

    async def handle(self, route):
        await self.page_at(route, self.body)

    async def choose(self):
        await self.page.goto("https://studio.x.com/library")
        return await browser_backends.upload_file(self.page, self.clip, accept="video/")

    async def chosen(self):
        return await self.page.locator("body").get_attribute("data-chosen")

    async def test_with_nothing_wrong_the_file_is_chosen(self):
        await self.choose()
        self.assertEqual("1", await self.chosen())

    async def test_a_warning_on_the_page_holds_it_back_and_stops_everything(self):
        self.body = self.PAGE + '<div role="alert">Verify your identity</div>'
        with self.assertRaises(VpubError) as caught:
            await self.choose()
        self.assertEqual("human_verification_required", caught.exception.code)
        self.assertIsNone(await self.chosen())
        self.assertTrue((home() / "STOP.json").exists())

    async def test_a_stop_from_another_command_holds_it_back(self):
        self.stop()
        with self.assertRaises(VpubError) as caught:
            await self.choose()
        self.assertEqual("stopped", caught.exception.code)
        self.assertIsNone(await self.chosen())

    PICKER = """<button onclick="document.querySelector('input').click()">Open</button><input type="file" accept="image/png" onchange="document.body.dataset.chosen = this.files.length">"""

    async def open_picker(self, while_opening=lambda: None):
        """Nothing on this page takes a video directly, so the file goes through a picker that the button opens."""
        self.body = self.PICKER
        await self.page.goto("https://studio.x.com/library")
        button = self.page.locator("button")

        class Opener:
            async def click(self, **options):
                while_opening()
                await button.click(**options)
        await browser_backends.upload_file(self.page, self.clip, trigger=Opener(), accept="video/")

    async def test_a_picker_gets_the_file_when_nothing_is_wrong(self):
        await self.open_picker()
        self.assertEqual("1", await self.chosen())

    async def test_a_stop_that_comes_while_a_picker_opens_holds_it_back(self):
        with self.assertRaises(VpubError) as caught:
            await self.open_picker(self.stop)
        self.assertEqual("stopped", caught.exception.code)
        self.assertIsNone(await self.chosen())

    async def test_a_warning_that_shows_once_a_form_is_ready_holds_a_cover_back(self):
        await self.page.goto("https://studio.x.com/library")
        await self.page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div role=alert>Verify your identity</div>')")
        with self.assertRaises(VpubError) as caught:
            await browser_backends.choose_file(self.page, self.page.locator("input"), self.clip)
        self.assertEqual("human_verification_required", caught.exception.code)
        self.assertIsNone(await self.chosen())


class XFormTests(Form):
    platform = "x"

    def prepare(self):
        super().prepare()
        self.studio_account = OWNER
        self.composer_outside, self.composer_video, self.extra_video = False, "this-file", ""
        self.studio_open = True
        self.never_shows = False

    async def handle(self, route):
        url = urlsplit(route.request.url)
        if route.request.method == "POST":
            if url.path == "/upload":
                self.uploads.append(1)
            elif url.path == "/post":
                self.note_final(route)
                self.posted = json.loads(route.request.post_data)["text"]
            return await self.json_at(route, {})
        if url.hostname == "studio.x.com":
            return await self.studio(route, url.path)
        if url.path == f"/{OWNER}/status/{POST}" and self.posted:
            return await self.page_at(route, f'<article><a href="/{OWNER}/status/{POST}">now</a><div>{self.posted}</div></article>')
        shown = f'<article><a href="/{OWNER}/status/{POST}">now</a><div>{self.posted}</div></article>' if self.posted and not self.never_shows else ""
        await self.page_at(route, f"<main>{shown}</main>")

    async def studio(self, route, path):
        if not self.studio_open:
            return await self.page_at(route, "<p>No access</p>")
        account = f'<a href="https://analytics.twitter.com/user/{self.studio_account}/home">account</a>'
        if path.endswith("/edit"):
            keep = "" if self.keeps_text else "this.value = '';"
            name = parse_qs(urlsplit(route.request.url).query)["name"][0]
            box = f"""<div id="box"><video src="https://studio.x.com/{self.composer_video}.mp4"></video><textarea placeholder="What's happening?" oninput="{keep}"></textarea><button type="button" onclick="fetch('/post', {{method: 'POST', body: JSON.stringify({{text: document.querySelector('textarea').value}})}})">Post</button></div>"""
            return await self.page_at(route, f"""{account}{self.check}<div><a download="{name}" href="#">Download</a><button type="button">Change thumbnail</button>{self.extra_video}<video src="https://studio.x.com/this-file.mp4"></video><button type="button" id="compose" onclick="const home = {'document.body' if self.composer_outside else 'this.parentElement'}; document.querySelector('video').remove(); this.remove(); home.insertAdjacentHTML('beforeend', document.getElementById('held').innerHTML)">Post</button></div><template id="held">{box}</template>""")
        await self.page_at(route, f"""{account}<button>Upload Media</button><input type="file" accept="video/mp4" onchange="fetch('/upload', {{method: 'POST', body: '{{}}'}}); const row = document.createElement('div'); row.textContent = this.files[0].name; row.onclick = () => location.href = '/library/1_2/edit?name=' + encodeURIComponent(row.textContent); document.body.append(row)">""")

    async def test_a_composer_outside_this_files_form_posts_nothing(self):
        """From a review, and a field read of where the composer really sits."""
        self.composer_outside = True
        await self.refused("layout_changed")
        self.assertEqual([], self.final)

    async def test_a_form_with_two_videos_posts_nothing(self):
        self.extra_video = '<video src="https://studio.x.com/another-file.mp4"></video>'
        await self.refused("layout_changed")
        self.assertEqual([], self.final)

    async def test_a_composer_showing_another_video_posts_nothing(self):
        self.composer_video = "another-file"
        await self.refused("layout_changed")
        self.assertEqual([], self.final)

    async def test_a_studio_that_names_another_account_stops_before_any_upload(self):
        self.studio_account = "someone_else"
        await self.refused("wrong_account")
        self.assertEqual([], self.uploads)

    async def test_an_account_without_media_studio_is_told_so_and_nothing_is_uploaded(self):
        self.studio_open = False
        await self.refused("studio_access_required")
        self.assertEqual([], self.uploads)


class InstagramFormTests(Form):
    platform = "instagram"

    def prepare(self):
        super().prepare()
        self.offers_label = True
        self.label_turns_on = True
        self.never_shows = False
        self.label_presses = 0

    async def handle(self, route):
        url = urlsplit(route.request.url)
        if route.request.method == "POST":
            if url.path == "/upload":
                self.uploads.append(1)
            elif url.path == "/share":
                self.note_final(route)
                self.posted = json.loads(route.request.post_data)["text"]
            elif url.path == "/label":
                self.label_presses += 1
            return await self.json_at(route, {})
        if url.path.rstrip("/") == f"/reel/{REEL}":
            return await self.page_at(route, f'<a href="/{OWNER}/">{OWNER}</a><video width="10" height="10"></video><div>{self.posted}</div>')
        grid = f'<a href="/{OWNER}/reel/{REEL}/">{self.plan["options"]["title"]}</a>' if self.posted and not self.never_shows else ""
        await self.page_at(route, f"""{self.check}<main>{grid}</main>{self.composer()}""")

    def composer(self):
        keep = "" if self.keeps_text else "this.value = '';"
        turn = "this.setAttribute('aria-checked', 'true');" if self.label_turns_on else ""
        label = f"""<div><span>Add AI label</span><div role="switch" aria-checked="false" style="width:10px;height:10px" onclick="fetch('/label', {{method: 'POST', body: '{{}}'}}); {turn}"></div></div>""" if self.offers_label else ""
        return f"""<svg aria-label="New post" width="10" height="10" onclick="showPart(0)"></svg>
          <div class="step" hidden><button>Select from computer</button><input type="file" accept="video/mp4" onchange="fetch('/upload', {{method: 'POST', body: '{{}}'}}); showPart(1)"></div>
          <div class="step" hidden><svg aria-label="Select crop" width="10" height="10"></svg><span>Original</span><button onclick="showPart(2)">Next</button></div>
          <div class="step" hidden><button onclick="showPart(3)">Next</button></div>
          <div class="step" hidden><textarea oninput="{keep}"></textarea>{label}<button onclick="fetch('/share', {{method: 'POST', body: JSON.stringify({{text: document.querySelector('textarea').value}})}}).then(() => document.getElementById('done').hidden = false)">Share</button><p id="done" hidden>Your reel has been shared.</p></div>
          <script>function showPart(number) {{ document.querySelectorAll('.step').forEach((part, index) => part.hidden = index !== number); }}</script>"""

    async def test_an_ai_video_turns_the_platforms_own_label_on_before_sharing(self):
        await self.publish()
        await self.page.evaluate("1")
        self.assertEqual(1, self.label_presses)
        self.assertEqual("native_control", self.receipt()["ai_disclosure"])

    async def test_an_ordinary_video_leaves_the_label_alone(self):
        self.plan["options"]["ai_generated"] = False
        await self.publish()
        await self.page.evaluate("1")
        self.assertEqual(0, self.label_presses)

    async def test_no_label_control_stops_before_sharing(self):
        self.offers_label = False
        await self.refused("declaration_unverified")

    async def test_a_label_that_will_not_turn_on_stops_before_sharing(self):
        self.label_turns_on = False
        await self.refused("declaration_unverified")


del Form


class SchedulePlanTests(unittest.TestCase):
    """A time the platform would not take is refused before anything starts."""

    def plan(self, platform, hours, **options):
        when = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "clip.mp4").write_bytes(b"synthetic")
            atomic_json(root / "publish.json", {"schema_version": 1, "video": "clip.mp4", "title": "T", "description": "", "tags": ["a"], "content_language": "en",
                                                "platforms": {platform: {"account": "default", "ai_generated": True, "schedule": when, **options}}})
            return load_plan(root / "publish.json")["platforms"][platform]

    def refused(self, code, *args, **options):
        with self.assertRaises(VpubError) as caught:
            self.plan(*args, **options)
        self.assertEqual(code, caught.exception.code)

    def test_instagram_has_no_schedule_and_says_so(self):
        self.refused("unsupported_schedule", "instagram", 24, method="browser", visibility="account")

    def test_bilibili_takes_a_time_from_two_hours_to_fifteen_days_ahead(self):
        for hours in (3, 24 * 14):
            with self.subTest(hours=hours):
                self.assertTrue(self.plan("bilibili", hours, method="cli", visibility="public")["schedule"])
        for hours in (-1, 1, 24 * 16):
            with self.subTest(hours=hours):
                self.refused("invalid_schedule", "bilibili", hours, method="cli", visibility="public")


class StopBeforeAnythingTests(unittest.TestCase):
    """A stop that lands between the check and the upload's first step means nothing went out, so the plan can still run later."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": str(Path(temp.name) / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        self.plan = {"confirmation": "hash", "video_sha256": "a" * 64, "platforms": {"x": {"account": "default", "title": "T", "method": "browser"}}}

    def publish(self, side_effect=None, answer=None):
        with patch.object(adapters, "preflight", return_value={"identity": "me"}), patch.object(adapters, "publish", side_effect=side_effect, return_value=answer) as sent, patch.object(cli, "snapshot_plan", side_effect=lambda plan, platform: plan):
            return cli.publish_plan(self.plan, "hash")["results"]["x"], sent

    def test_it_is_reported_as_not_started_and_can_run_again(self):
        first, _ = self.publish(VpubError("stopped", "Publication stop file exists."))
        self.assertEqual(("NOT_STARTED", "stopped"), (first["state"], first["code"]))
        again, sent = self.publish(answer={"state": "SUBMISSION_VERIFIED"})
        sent.assert_called_once()
        self.assertEqual("SUBMISSION_VERIFIED", again["state"])

    def test_a_stop_after_a_receipt_exists_is_not_treated_as_never_started(self):
        def part_way(platform, plan, options, checkpoint):
            checkpoint({"state": "SUBMITTED", "id": "1234567890"})
            raise VpubError("stopped", "Publication stop file exists.")
        first, _ = self.publish(part_way)
        self.assertEqual(("SUBMITTED_UNVERIFIED", "1234567890"), (first["state"], first["id"]))
        again, sent = self.publish(answer={"state": "SUBMISSION_VERIFIED"})
        sent.assert_not_called()
        self.assertEqual("SKIPPED_EXISTING", again["state"])


class BilibiliReadBackTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": str(Path(temp.name) / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        self.status = {"bvid": BVID, "ai_declare": True, "private": False, "scheduled_time": None}
        target = patch.object(adapters, "bili", self.bili)
        target.start()
        self.addCleanup(target.stop)
        self.upload = None

    def bili(self, args, timeout=210, program=None):
        if args[:2] == ["upload", "video"]:
            self.upload = list(args)
            return {"bvid": BVID}
        if args[:2] == ["upload", "status"]:
            return dict(self.status)
        raise AssertionError(args)

    def publish(self, **options):
        settings = {"title": "T", "description": "D", "ai_generated": True, "tags": ["a"], "visibility": "public", "account": "default", **options}
        return adapters.publish_bilibili({"video": "v.mp4"}, settings, lambda mark: None)

    def test_a_public_upload_that_reads_back_public_is_verified(self):
        self.assertEqual("SUBMISSION_VERIFIED", self.publish()["state"])

    def test_a_private_upload_that_reads_back_public_is_not_called_done(self):
        result = self.publish(visibility="private")
        self.assertIn("--private", self.upload)
        self.assertEqual(("SUBMITTED_UNVERIFIED", ["visibility"]), (result["state"], result["readback_differs"]))

    def test_a_private_upload_that_reads_back_private_is_verified(self):
        self.status["private"] = True
        self.assertEqual("SUBMISSION_VERIFIED", self.publish(visibility="private")["state"])

    def test_a_public_upload_that_reads_back_private_is_not_called_done(self):
        self.status["private"] = True
        self.assertEqual(["visibility"], self.publish()["readback_differs"])

    def when(self, hours):
        return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0)

    def test_a_scheduled_upload_with_no_time_on_record_is_not_called_done(self):
        result = self.publish(schedule=self.when(5).isoformat())
        self.assertEqual(("SUBMITTED_UNVERIFIED", ["schedule"]), (result["state"], result["readback_differs"]))

    def test_a_scheduled_upload_with_another_time_on_record_is_not_called_done(self):
        self.status["scheduled_time"] = self.when(9).isoformat()
        self.assertEqual(["schedule"], self.publish(schedule=self.when(5).isoformat())["readback_differs"])

    def test_a_scheduled_upload_with_its_time_on_record_is_verified_in_any_time_zone(self):
        moment = self.when(5)
        self.status["scheduled_time"] = moment.astimezone(timezone(timedelta(hours=8))).isoformat()
        self.assertEqual("SUBMISSION_VERIFIED", self.publish(schedule=moment.isoformat())["state"])

    def test_after_its_time_the_work_being_there_is_enough(self):
        receipt = {"id": BVID, "state": "SUBMITTED", "expected_schedule": self.when(-1).isoformat()}
        self.assertEqual("SUBMISSION_VERIFIED", adapters.verify("bilibili", "default", receipt)["state"])

    def test_a_wrong_ai_declaration_is_still_named_as_before(self):
        self.status["ai_declare"] = False
        result = self.publish()
        self.assertEqual(("SUBMITTED_UNVERIFIED", True, ["ai_declaration"]), (result["state"], result["declaration_mismatch"], result["readback_differs"]))

    def test_an_older_receipt_with_nothing_expected_keeps_its_old_answer(self):
        self.status.update(private=True, scheduled_time="2030-01-01T00:00:00+08:00")
        self.assertEqual("SUBMISSION_VERIFIED", adapters.verify("bilibili", "default", {"id": BVID, "state": "SUBMITTED"})["state"])


if __name__ == "__main__":
    unittest.main()

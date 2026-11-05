"""Publishing on X and Instagram, driven in an invisible browser against small copies of their forms, and what a Bilibili upload is held to when read back. Nothing is contacted."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
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


def post_made(seconds_ago=0):
    """An X post's ID, which carries the moment it was made."""
    return str(int((time.time() - seconds_ago) * 1000 - 1288834974657) << 22)


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


class FakeForm:
    """A form whose download link says what a test needs."""

    def __init__(self, href):
        self.href = href

    def locator(self, selector):
        return type("Link", (), {"first": type("One", (), {"get_attribute": AsyncMock(return_value=self.href)})()})()


class XFormTests(Form):
    platform = "x"

    def prepare(self):
        super().prepare()
        isolated = patch.dict(os.environ, VPUB_HOME=str(self.root / 'runtime'), BILI_CLI_HOME=str(self.root / 'bili'))
        isolated.start()
        self.addCleanup(isolated.stop)
        held = patch("video_publisher.browser_forms.X_FIRST_CLICK_SECONDS", 0)
        held.start()
        self.addCleanup(held.stop)
        self.studio_account = OWNER
        self.clip_in_studio = f"crosscast-{self.plan['video_sha256'][:16]}.mp4"
        self.composer_outside, self.composer_video, self.extra_video = False, "this-file", ""
        self.form_video = '<video src="https://studio.x.com/this-file.mp4"></video>'
        self.studio_open = True
        self.never_shows = False
        self.schedule_reads = []
        self.post = post_made()
        self.post_video = '<video poster="https://pbs.twimg.com/amplify_video_thumb/2/img/a.jpg"></video>'
        self.own_words = None
        self.reply_row = ""
        self.older_post = None
        self.final_class = ""
        self.final_goes = ""
        self.compose_goes = "this.remove();"
        self.file_link = "#"
        self.tile_icons = ""
        self.wrong_presses = []

    async def handle(self, route):
        url = urlsplit(route.request.url)
        if route.request.method == "POST":
            if url.path == "/upload":
                self.uploads.append(1)
            elif url.path == "/post":
                self.note_final(route)
                self.posted = json.loads(route.request.post_data)["text"]
            elif url.path == "/wrong":
                self.wrong_presses.append(1)
            return await self.json_at(route, {})
        if url.hostname == "studio.x.com":
            return await self.studio(route, url.path)
        row = f'<article><a href="/{OWNER}/status/{self.post}"><time>now</time></a><div data-testid="tweetText" dir="auto">{self.own_words or self.posted}</div>{self.post_video}</article>{self.reply_row}'
        if url.path == f"/{OWNER}/status/{self.post}" and self.posted:
            return await self.page_at(route, row)
        if self.older_post and url.path == f"/{OWNER}/status/{self.older_post}" and self.posted:
            return await self.page_at(route, row.replace(self.post, self.older_post))
        shown = row if self.posted and not self.never_shows else ""
        older = row.replace(self.post, self.older_post) if self.older_post and self.posted else ""
        await self.page_at(route, f"<main>{shown}{older}</main>")

    async def studio(self, route, path):
        if not self.studio_open:
            return await self.page_at(route, "<p>No access</p>")
        if path == "/schedule":
            self.schedule_reads.append(len(self.uploads))
            return await self.page_at(route, f"<p>{browser_backends.X_SCHEDULE_EMPTY}</p>")
        account = f'<a href="https://analytics.twitter.com/user/{self.studio_account}/home">account</a>'
        if path.endswith("/edit"):
            keep = "" if self.keeps_text else "this.value = '';"
            name = parse_qs(urlsplit(route.request.url).query).get("name", [self.clip_in_studio])[0]
            box = f"""<div id="box"><video src="https://studio.x.com/{self.composer_video}.mp4"></video><textarea placeholder="What's happening?" oninput="{keep}"></textarea><button type="button" class="{self.final_class}" onclick="fetch('/post', {{method: 'POST', body: JSON.stringify({{text: document.querySelector('textarea').value}})}}); {self.final_goes}">Post</button></div>"""
            return await self.page_at(route, f"""{account}{self.check}{self.tile_icons}<div><a download="{name}" href="{self.file_link}">Download</a><button type="button">Change thumbnail</button>{self.extra_video}{self.form_video}<button type="button" id="compose" onclick="const home = {'document.body' if self.composer_outside else 'this.parentElement'}; document.querySelector('video')?.remove(); {self.compose_goes} home.insertAdjacentHTML('beforeend', document.getElementById('held').innerHTML)">Post</button></div><template id="held">{box}</template>""")
        await self.page_at(route, f"""{account}<button>Upload Media</button><input type="file" accept="video/mp4" onchange="fetch('/upload', {{method: 'POST', body: '{{}}'}}); const row = document.createElement('div'); row.textContent = this.files[0].name; row.onclick = () => location.href = '/library/1_2/edit?name=' + encodeURIComponent(row.textContent); document.body.append(row)">""")

    async def test_a_clip_already_in_media_studio_is_posted_with_no_upload(self):
        """From a field run: its dry run left the clip there, and uploading the file again would make two tiles of one name."""
        self.plan["options"]["existing_media_url"] = "https://studio.x.com/library/13_77/edit"
        self.post_video = '<video poster="https://pbs.twimg.com/amplify_video_thumb/77/img/a.jpg"></video>'
        result = await self.publish()
        self.assertEqual(("SUBMISSION_VERIFIED", True), (result["state"], result["final_submit_attempted"]))
        self.assertEqual(([], 1), (self.uploads, len(self.final)))
        self.assertEqual((True, "https://studio.x.com/library/13_77/edit"), (result["reused_media"], result["media_url"]))

    async def test_a_page_that_goes_on_to_another_file_is_not_taken_for_this_one(self):
        """From a review: the file it landed on would have replaced this attempt's address."""
        self.plan["options"]["existing_media_url"] = "https://studio.x.com/library/13_77/edit"
        self.tile_icons = "<script>history.replaceState(null, '', '/library/13_88/edit')</script>"
        await self.refused("wrong_work")
        self.assertEqual([], self.uploads)

    async def test_a_download_link_for_another_video_stops_it_and_this_ones_goes_through(self):
        """A field read found the link under the address's second number."""
        self.plan["options"]["existing_media_url"] = "https://studio.x.com/library/13_77/edit"
        self.file_link = "https://video.twimg.com/amplify_video/88/vid/1920x1080/a.mp4"
        await self.refused("wrong_work")
        self.assertEqual([], self.uploads)

    async def test_a_download_link_naming_this_video_or_nothing_we_can_read_goes_through(self):
        for link in ("https://video.twimg.com/ext_tw_video/77/pu/vid/a.mp4", "https://video.twimg.com/tweet_video/a.mp4"):
            with self.subTest(link=link):
                self.assertFalse(await browser_forms.x_link_names_another_video(FakeForm(link), "a.mp4", "https://studio.x.com/library/13_77/edit"))

    async def test_a_file_title_that_does_not_hold_stops_before_any_post(self):
        """From a review: the file's own title and description were filled and never read back."""
        self.extra_video = '<input placeholder="Title" oninput="this.value = \'\'"><input placeholder="Type a brief description here">'
        await self.refused("metadata_mismatch")

    async def test_a_file_title_and_description_that_hold_go_through(self):
        self.extra_video = '<input placeholder="Title"><input placeholder="Type a brief description here">'
        self.assertEqual("SUBMISSION_VERIFIED", (await self.publish())["state"])

    async def test_a_plan_with_a_length_stops_on_a_video_that_cannot_show_it(self):
        """From a review: only a source was waited for, and a video still processing has one."""
        self.plan["options"]["duration_seconds"] = 60
        with patch.object(browser_forms, "X_VIDEO_SECONDS", 1):
            await self.refused("video_unverified")

    async def test_a_length_within_a_second_of_the_plan_passes_and_further_does_not(self):
        form = FakeForm(None)
        page = self.page
        form.evaluate = AsyncMock(return_value=60.4)
        await browser_forms.x_video_is_whole(page, form, 60)
        form.evaluate = AsyncMock(return_value=70.0)
        with self.assertRaises(VpubError) as caught:
            await browser_forms.x_video_is_whole(page, form, 60)
        self.assertEqual("video_unverified", caught.exception.code)

    async def test_a_clip_named_after_another_file_is_not_posted(self):
        self.plan["options"]["existing_media_url"] = "https://studio.x.com/library/13_77/edit"
        self.clip_in_studio = "crosscast-0000000000000000.mp4"
        with patch.object(browser_forms, "X_FORM_SECONDS", 1), self.assertRaises(Exception):
            await self.publish()
        self.assertEqual(([], []), (self.uploads, self.final))

    async def test_the_library_tiles_post_icons_are_not_the_final_press(self):
        """A field read of the open composer: one type=button Post, the tweetbtn, beside Post icons with no type on the Library's tiles."""
        self.final_class = "Button Button--primary tweetbtn"
        self.tile_icons = """<button aria-label="Post" class="EOtWHsw6" onclick="fetch('/wrong', {method: 'POST', body: '{}'})">Post</button>""" * 4
        self.assertEqual("SUBMISSION_VERIFIED", (await self.publish())["state"])
        self.assertEqual((1, []), (len(self.final), self.wrong_presses))

    async def test_a_compose_button_still_showing_beside_the_final_one_presses_nothing(self):
        """Both say Post, and the field read has only one by then. Two means the right one can't be told."""
        self.compose_goes = ""
        await self.refused("layout_changed")

    async def after_the_press(self):
        seen = []

        async def reads_the_account(page, tx, before):
            seen.append(await page.locator("button.tweetbtn:visible").count())
            return {"state": "read"}
        with patch.object(browser_forms, "finish", reads_the_account):
            await self.publish()
        self.assertEqual(1, len(self.final))
        return seen

    async def test_the_account_is_not_read_until_the_composer_has_closed(self):
        """As with Done: leaving before it has closed could cut the post off."""
        self.final_class = "tweetbtn"
        self.final_goes = "setTimeout(() => this.remove(), 300);"
        self.assertEqual([0], await self.after_the_press())

    async def test_a_composer_that_never_closes_still_gets_the_account_read(self):
        self.final_class = "tweetbtn"
        with patch.object(browser_forms, "X_DONE_SECONDS", 1):
            self.assertEqual([1], await self.after_the_press())

    async def test_its_words_on_a_post_with_no_video_are_not_called_published(self):
        """Media Studio posts videos, and a field read found a real post's in its own row."""
        self.post_video = ""
        result = await self.publish()
        self.assertEqual(("SUBMITTED_UNVERIFIED", True), (result["state"], result["final_submit_attempted"]))
        self.assertEqual(1, len(self.final))

    async def test_a_quoted_posts_video_is_not_this_posts(self):
        """From a review: any video inside the row passed."""
        self.post_video = '<div role="link"><video></video></div>'
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.publish())["state"])

    async def test_a_quoted_posts_video_in_a_plain_link_is_not_this_posts(self):
        """From a review: only a role=link wrapper was looked at."""
        self.post_video = '<a href="/someone/status/999"><video></video></a>'
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.publish())["state"])

    async def test_this_posts_own_video_in_a_link_to_itself_is_this_posts(self):
        self.post_video = f'<a href="/{OWNER}/status/{self.post}/video/1"><video poster="https://pbs.twimg.com/amplify_video_thumb/2/img/a.jpg"></video></a>'
        self.assertEqual("SUBMISSION_VERIFIED", (await self.publish())["state"])

    async def test_words_in_a_reply_are_not_this_posts(self):
        """From a review: its words anywhere on the page passed, with other words on the post itself."""
        self.own_words = "My title and other words"
        self.reply_row = '<article><div data-testid="tweetText" dir="auto">My title Words</div></article>'
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.publish())["state"])

    async def test_another_videos_number_rules_a_post_out_and_its_own_number_passes(self):
        """From a review. The copy's file page is 1_2, so its number is 2."""
        self.post_video = '<video poster="https://pbs.twimg.com/amplify_video_thumb/88/img/a.jpg"></video>'
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.publish())["state"])

    async def test_a_post_showing_its_own_number_passes(self):
        self.post_video = '<video poster="https://pbs.twimg.com/amplify_video_thumb/2/img/a.jpg"></video>'
        self.assertEqual("SUBMISSION_VERIFIED", (await self.publish())["state"])

    async def test_shortened_post_text_need_not_contain_the_media_title(self):
        self.plan['options']['post_text'] = '第七集 / One more try!'
        result = await self.publish()
        self.assertEqual('SUBMISSION_VERIFIED', result['state'])
        self.assertEqual(self.post, result['id'])
        self.assertEqual(1, len(self.final))

    async def test_custom_cover_without_media_number_uses_bound_native_attempt_context(self):
        self.plan['options']['post_text'] = '第七集 / One more try!'
        self.post_video = '<video poster="https://pbs.twimg.com/media/custom-cover.jpg"></video>'
        result = await self.publish()
        self.assertEqual('SUBMISSION_VERIFIED', result['state'])
        self.assertEqual(self.post, result['id'])
        self.assertIn('correlation, not independent verification', result['note'])
        self.assertEqual(1, len(self.final))

    async def test_a_video_elsewhere_on_the_page_is_not_this_posts(self):
        self.post_video = "</article><video></video><article>"
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.publish())["state"])

    async def test_an_older_post_with_the_same_words_is_not_taken_for_this_one(self):
        """The list from before holds only what first showed, so an older post can look new. This one never showed."""
        self.never_shows = True
        self.older_post = post_made(seconds_ago=86400)
        result = await self.publish()
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
        self.assertNotIn("id", result)

    async def test_a_later_verify_does_not_take_an_older_post_for_this_one_either(self):
        self.never_shows = True
        self.older_post = post_made(seconds_ago=86400)
        await self.publish()
        result = await browser_backends.verify(self.page, "x", OWNER, self.receipt())
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
        self.assertNotIn("id", result)

    async def test_a_post_made_a_little_before_this_computers_clock_says_the_upload_began_is_still_found(self):
        self.post = post_made(seconds_ago=120)
        self.assertEqual("SUBMISSION_VERIFIED", (await self.publish())["state"])

    def scheduled(self):
        self.plan["options"]["schedule"] = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    async def test_a_schedule_reads_its_page_once_before_anything_is_uploaded(self):
        """From a review: with no read from before, a row already there could pass for this post's. This copy has no calendar, so it stops there."""
        self.scheduled()
        await self.refused("layout_changed")
        self.assertEqual(([0], 0, 1), (self.schedule_reads, self.receipt()["schedule_rows_before"], len(self.uploads)))

    async def test_a_read_already_kept_is_not_read_again(self):
        """From a review: a new read on resuming could make a row look new when it wasn't."""
        self.scheduled()
        tx = Transaction("x", OWNER, self.plan)
        tx.save(schedule_rows_before=3)
        with self.assertRaises(VpubError):
            await browser_forms.publish_x(self.page, tx)
        self.assertEqual(([], 3), (self.schedule_reads, self.receipt()["schedule_rows_before"]))

    async def test_a_draft_does_not_read_the_schedule_page(self):
        self.scheduled()
        self.plan["options"]["submission_mode"] = "draft_only"
        self.assertEqual("PREVIEW_READY", (await self.publish())["state"])
        self.assertEqual([], self.schedule_reads)
        self.assertNotIn("schedule_rows_before", self.receipt())

    async def test_a_post_with_no_schedule_does_not_read_the_schedule_page(self):
        self.assertEqual("SUBMISSION_VERIFIED", (await self.publish())["state"])
        self.assertEqual([], self.schedule_reads)

    async def test_a_composer_outside_this_files_form_posts_nothing(self):
        """From a review, and a field read of where the composer really sits."""
        self.composer_outside = True
        await self.refused("layout_changed")
        self.assertEqual([], self.final)

    async def test_a_form_with_two_videos_posts_nothing(self):
        self.extra_video = '<video src="https://studio.x.com/another-file.mp4"></video>'
        await self.refused("layout_changed")
        self.assertEqual([], self.final)

    async def test_a_new_upload_whose_form_never_shows_a_video_posts_nothing(self):
        """From a review: with nothing recorded, the composer's video couldn't be tied to the file."""
        self.form_video = ""
        with patch.object(browser_forms, "X_VIDEO_SECONDS", 1):
            await self.refused("layout_changed")
        self.assertEqual([], self.final)

    async def test_words_changed_during_the_last_wait_post_nothing(self):
        real = browser_backends.risk_check
        page = self.page

        async def changes_them(*args):
            await real(*args)
            await page.evaluate("document.querySelector('textarea').value = 'Other words'")
        original = Transaction.submit

        async def submit(tx, page, button, still_holds=None):
            with patch.object(browser_backends, "risk_check", changes_them):
                return await original(tx, page, button, still_holds)
        with patch.object(Transaction, "submit", submit):
            await self.refused("metadata_mismatch")
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
        self.offers_new_post = True
        self.label_turns_on = True
        self.never_shows = False
        self.label_presses = 0
        self.label_row = None
        self.caption_late = False
        self.edit_late = False
        self.shared_words = "<p>Your reel has been shared.</p>"
        self.own_caption = None
        self.share_row = '<div><div><span>Share to</span></div><div><span>Facebook</span><input type="checkbox" role="switch" style="width:10px;height:10px"></div></div>'
        self.caption_head = f'<a href="/{OWNER}/">{OWNER}</a> <time>3d</time>'
        self.comments = ""

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
            # From a field run: this address goes on to the Reels viewer, whose page has the video and the owner, but no caption row.
            return await self.page_at(route, f'<video width="10" height="10"></video><a href="/{OWNER}/">{OWNER}</a><span>{self.posted}</span>')
        if url.path.rstrip("/") == f"/p/{REEL}":
            caption = self.posted if self.own_caption is None else self.own_caption
            # The row and its order are those of a field read, and the date under everything is how Instagram lays out a post.
            return await self.page_at(route, f'<video width="10" height="10"></video><a href="/{OWNER}/">{OWNER}</a><div><div>{self.caption_head}</div><span>{caption}</span></div>{self.comments}<div><time>November 2</time></div>')
        grid = f'<a href="/{OWNER}/reel/{REEL}/">{self.plan["options"]["title"]}</a>' if self.posted and not self.never_shows else ""
        await self.page_at(route, f"""{self.check}<main>{grid}</main>{self.composer()}""")

    def composer(self):
        keep = "" if self.keeps_text else "this.value = '';"
        turn = "this.setAttribute('aria-checked', 'true');" if self.label_turns_on else ""
        label = f"""<div><span>Add AI label</span><div role="switch" aria-checked="false" style="width:10px;height:10px" onclick="fetch('/label', {{method: 'POST', body: '{{}}'}}); {turn}"></div></div>""" if self.offers_label else ""
        label = self.label_row if self.label_row is not None else label
        to_edit = "window.editComes = true" if self.edit_late else "showPart(2)"
        to_caption = "setTimeout(() => showPart(3), 300)" if self.caption_late else "showPart(3)"
        new_post = '<svg aria-label="New post" width="10" height="10" onclick="showPart(0)"></svg>' if self.offers_new_post else ""
        return f"""{new_post}
          <div class="step" hidden><button>Select from computer</button><input type="file" accept="video/mp4" onchange="fetch('/upload', {{method: 'POST', body: '{{}}'}}); showPart(1)"></div>
          <div class="step" hidden><svg aria-label="Select crop" width="10" height="10"></svg><span>Original</span><button onclick="{to_edit}">Next</button></div>
          <div class="step" hidden><div role="button">Select from computer</div><button onclick="{to_caption}">Next</button></div>
          <div class="step" hidden><textarea oninput="{keep}"></textarea>{label}{self.share_row}<footer><a href="https://www.threads.net/">Threads</a></footer><button onclick="fetch('/share', {{method: 'POST', body: JSON.stringify({{text: document.querySelector('textarea').value}})}}).then(() => document.getElementById('done').hidden = false)">Share</button><div id="done" hidden>{self.shared_words}</div></div>
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

    async def test_no_new_post_button_says_which_control_and_uploads_nothing(self):
        """From a field run: after a sign-in made elsewhere, its error could only say waiting for locator(…)."""
        self.offers_new_post = False
        with patch.object(browser_forms, "INSTAGRAM_NEW_POST_SECONDS", 1):
            await self.refused("layout_changed")
        self.assertEqual([], self.uploads)

    async def test_a_label_that_will_not_turn_on_stops_before_sharing(self):
        self.label_turns_on = False
        await self.refused("declaration_unverified")

    # A saved look at the real composer found the switch is an input of its own, with several of them in the step.
    SWITCH = """<input type="checkbox" role="switch" %s onclick="fetch('/label', {method: 'POST', body: '{}'})">"""

    async def test_the_switch_a_saved_look_found_is_turned_on(self):
        self.label_row = f"<div><span>Add AI label</span>{self.SWITCH % ''}</div>"
        await self.publish()
        await self.page.evaluate("1")
        self.assertEqual(1, self.label_presses)
        self.assertEqual(1, len(self.final))

    async def test_a_switch_that_is_already_on_is_left_on(self):
        """Reading only its attribute took it for off, and pressing it turned the label off and stopped the post. A video can arrive labelled."""
        self.label_row = f"<div><span>Add AI label</span>{self.SWITCH % 'checked'}</div>"
        await self.publish()
        await self.page.evaluate("1")
        self.assertEqual(0, self.label_presses)
        self.assertEqual(1, len(self.final))

    async def test_a_switch_that_turns_on_a_moment_after_its_press_counts(self):
        self.label_row = """<div><span>Add AI label</span><div role="switch" aria-checked="false" style="width:10px;height:10px" onclick="fetch('/label', {method: 'POST', body: '{}'}); window.switchComes = this"></div></div>"""

        async def turns_on(ms):
            await self.page.evaluate("if (window.switchComes) window.switchComes.setAttribute('aria-checked', 'true')", isolated_context=False)
        self.page.wait_for_timeout.side_effect = turns_on
        await self.publish()
        self.assertEqual(1, len(self.final))

    async def test_a_label_beside_several_switches_stops_before_any_is_pressed(self):
        """The step holds several, so the first one near the words could be another setting, and the post would go out saying it was labelled."""
        self.label_row = f"<div><div><span>Add AI label</span></div>{self.SWITCH % ''}{self.SWITCH % ''}</div>"
        await self.refused("declaration_unverified")
        await self.page.evaluate("1")
        self.assertEqual(0, self.label_presses)

    async def test_a_label_that_comes_a_moment_after_advanced_settings_is_found(self):
        """A look straight after the press could miss it, and the upload would stop for nothing."""
        row = f"<div><span>Add AI label</span>{self.SWITCH % ''}</div>"
        self.label_row = f"""<div onclick="window.labelComes = true">Advanced settings</div><div id="advanced" hidden>{row}</div>"""

        async def comes(ms):
            await self.page.evaluate("if (window.labelComes) document.getElementById('advanced').hidden = false", isolated_context=False)
        self.page.wait_for_timeout.side_effect = comes
        await self.publish()
        await self.page.evaluate("1")
        self.assertEqual(1, self.label_presses)
        self.assertEqual(1, len(self.final))

    async def test_a_caption_box_that_comes_a_moment_after_next_is_waited_for(self):
        """Looking straight after the press could find no box, and stop an upload that was going fine."""
        self.caption_late = True
        self.assertEqual("SUBMISSION_VERIFIED", (await self.publish())["state"])
        self.assertEqual(1, len(self.final))

    async def test_a_second_next_waits_for_the_step_it_belongs_to(self):
        """A press straight after the first could land before the next step has come. A run with no cover never waited for it."""
        self.edit_late = True

        async def comes(ms):
            await self.page.evaluate("if (window.editComes) { window.editComes = false; showPart(2); }", isolated_context=False)
        self.page.wait_for_timeout.side_effect = comes
        self.assertEqual("SUBMISSION_VERIFIED", (await self.publish())["state"])
        self.assertEqual(1, len(self.final))

    async def test_a_window_that_says_it_was_shared_twice_still_counts(self):
        """After the press, an error from how many times it says so would read as a failed post."""
        self.shared_words = "<h2>Post shared</h2><p>Your post has been shared.</p>"
        self.assertEqual("SUBMISSION_VERIFIED", (await self.publish())["state"])
        self.assertEqual(1, len(self.final))

    async def changes_during_the_last_wait(self, script, code):
        real = browser_backends.risk_check
        page = self.page

        async def changes(*args):
            await real(*args)
            await page.evaluate(script)
        original = Transaction.submit

        async def submit(tx, page, button, still_holds=None):
            with patch.object(browser_backends, "risk_check", changes):
                return await original(tx, page, button, still_holds)
        with patch.object(Transaction, "submit", submit):
            await self.refused(code)
        self.assertIs(False, self.receipt()["final_submit_attempted"])

    async def test_words_changed_during_the_last_wait_share_nothing(self):
        await self.changes_during_the_last_wait("document.querySelector('textarea').value = 'Other words'", "metadata_mismatch")

    async def test_a_comment_saying_its_words_is_not_its_caption(self):
        """From a review: the words passed from anywhere on the page."""
        self.own_caption = "Something else"
        self.comments = '<div><div><a href="/someone/">someone</a> <time>1d</time></div><span>My title Words</span></div>'
        result = await self.publish()
        self.assertEqual(("SUBMITTED_UNVERIFIED", 1), (result["state"], len(self.final)))

    async def test_the_owners_own_comment_saying_its_words_is_not_its_caption(self):
        """From a review: the owner's name beside a comment passed for the caption's."""
        self.own_caption = "Something else"
        self.comments = f'<div><div><a href="/{OWNER}/">{OWNER}</a> <time>1d</time></div><span>My title Words</span></div>'
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.publish())["state"])

    async def test_its_words_inside_a_longer_caption_are_not_its_caption(self):
        """From a review: a span holding only these words passed, though the caption around it said more."""
        self.own_caption = "<span>My title Words</span> and more"
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.publish())["state"])

    async def test_more_words_loose_beside_the_caption_are_not_its_caption(self):
        self.own_caption = "My title Words</span> and more<span>"
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.publish())["state"])

    async def test_a_hidden_copy_of_its_words_is_not_its_caption(self):
        self.own_caption = '<span hidden>My title Words</span>'
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.publish())["state"])

    async def test_someone_elses_name_in_the_captions_row_stays_unconfirmed(self):
        self.caption_head = '<a href="/someone/">someone</a> <time>3d</time>'
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.publish())["state"])

    async def test_a_page_with_no_time_beside_its_caption_stays_unconfirmed(self):
        self.caption_head = f'<a href="/{OWNER}/">{OWNER}</a>'
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.publish())["state"])

    async def test_a_caption_with_more_words_is_not_its_caption(self):
        self.own_caption = "My title Words and more"
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.publish())["state"])

    async def test_a_comment_repeating_the_words_does_not_get_in_the_way(self):
        self.comments = '<div><div><a href="/someone/">someone</a> <time>1d</time></div><span>My title Words</span></div>'
        self.assertEqual("SUBMISSION_VERIFIED", (await self.publish())["state"])

    async def test_a_caption_with_a_tag_and_a_person_in_it_is_still_its_caption(self):
        """A link inside the words isn't the person who wrote them."""
        self.plan["options"]["post_text"] = "My title @friend #tag"
        self.own_caption = 'My title <a href="/friend/">@friend</a> <a href="/explore/tags/tag/">#tag</a>'
        self.assertEqual("SUBMISSION_VERIFIED", (await self.publish())["state"])

    async def test_the_share_to_switch_on_shares_nothing(self):
        """The plan says Instagram, and that switch would post it somewhere else too."""
        self.share_row = self.share_row.replace('role="switch"', 'role="switch" checked')
        await self.refused("shares_elsewhere")

    async def test_a_threads_link_in_the_footer_and_no_share_to_row_are_nothing_to_stop_for(self):
        """From a field run: a check that took that footer link for a posting switch stopped a real post."""
        self.share_row = ""
        self.assertEqual("SUBMISSION_VERIFIED", (await self.publish())["state"])

    async def test_a_preview_says_whether_it_would_share_elsewhere(self):
        self.plan["options"]["submission_mode"] = "draft_only"
        self.share_row = self.share_row.replace('role="switch"', 'role="switch" checked')
        self.assertIs(True, (await self.publish())["shares_elsewhere"])

    async def test_a_share_to_switch_turned_on_during_the_last_wait_shares_nothing(self):
        await self.changes_during_the_last_wait("document.querySelector('[type=checkbox]').checked = true", "shares_elsewhere")

    async def test_a_switch_added_during_the_last_wait_shares_nothing(self):
        """From a review: the old locator could land on a new checked switch in front, with the AI label's own gone off."""
        await self.changes_during_the_last_wait("""document.querySelector('[role=switch]').insertAdjacentHTML('beforebegin', '<input type="checkbox" role="switch" checked>')""", "declaration_unverified")

    async def test_a_step_that_never_comes_stops_with_nothing_shared(self):
        """From a review: pressing on without it would press the last step's Next again."""
        self.edit_late = True
        await self.refused("layout_changed")
        self.assertEqual(1, len(self.uploads))

    async def test_a_label_turned_off_during_the_last_wait_shares_nothing(self):
        await self.changes_during_the_last_wait("document.querySelector('[role=switch]').setAttribute('aria-checked', 'false')", "declaration_unverified")


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
            return cli.publish_plan(self.plan, "hash", reviewed=True)["results"]["x"], sent

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
        if args[:3] == ["danmaku", "interact", "list"]:
            return {"items": []}
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

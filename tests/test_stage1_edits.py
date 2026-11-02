"""Edits to works already out: a stop at the last moment, a check that changes nothing, and what an edit says about AI. Fakes and small page copies; nothing is contacted."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parent))
from stage_browser import SharedBrowser

from video_publisher import adapters, browser_edits, edits, youtube_studio
from video_publisher.core import Ledger, VpubError, atomic_json, digest, home, read_json

OWNER = "owner"
TEXT = {"expected_identity": OWNER, "title": "New title", "description": "New text", "content_language": "en", "metadata_language": "en", "metadata_mode": "target_only"}


class Temporary(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        env.start()
        self.addCleanup(env.stop)


class LastMomentTests(SharedBrowser):
    def prepare(self):
        self.saves = []
        self.title, self.words = "Old title", "Old words"
        self.stays = None

    async def handle(self, route):
        if route.request.method == "POST":
            self.saves.append(json.loads(route.request.post_data))
            self.title, self.words = self.saves[-1]["title"], self.saves[-1]["words"]
            return await self.json_at(route, {})
        if urlsplit(route.request.url).path == "/creator-micro/content/post/video":
            return await self.page_at(route, f"""<input placeholder="填写作品标题，为作品获得更多流量" value="{self.title}"><div contenteditable="true" id="c">{self.words}</div>
              <button id="go">发布</button><script>document.getElementById('go').onclick = async () => {{
                await fetch('/save', {{method: 'POST', body: JSON.stringify({{title: document.querySelector('input').value, words: document.getElementById('c').innerText}})}}); {"document.body.insertAdjacentHTML('beforeend', " + repr(self.stays) + ")" if self.stays else "location.href = '/creator-micro/content/manage'"}; }};</script>""")
        await self.page_at(route, "manage")

    async def edit(self):
        checked = edits.validate_patch("douyin", {**TEXT, "metadata_language": "zh-Hans", "content_language": "zh-Hans"})
        self.path = self.root / "edit.json"
        return await browser_edits.execute(self.page, "douyin", OWNER, {"patch": checked, "receipt": {"id": "123", "title": "Old title"}, "edit_receipt_path": str(self.path)})

    STAYS = '<div role="dialog">请先完成封面设置 secret-viewer-words<div contenteditable="true">secret-form-words</div></div>'

    async def stays_after_the_press(self):
        self.stays = self.STAYS
        with patch.object(browser_edits, "DOUYIN_SAVE_SECONDS", 1):
            result = await self.edit()
        self.assertEqual(("EDIT_SUBMITTED_UNVERIFIED", 1), (result["state"], result["platform_writes"]))
        self.assertEqual(1, len(self.saves))
        self.assertEqual(result["state"], read_json(self.path)["state"])
        return result, read_json(self.path.with_suffix(".error.json"))

    async def test_a_save_that_does_not_move_on_is_not_repeated_and_keeps_no_page_words_by_default(self):
        """From a field run: pressed once, the page stayed and the old words stayed, and nothing recorded why. From a review: a dialog can quote anyone's words."""
        result, record = await self.stays_after_the_press()
        self.assertEqual(("发布", True), (result["after_press"]["pressed"], result["after_press"]["still_on_edit_page"]))
        for kept in (result, record):
            for words in ("请先完成封面设置", "secret-viewer-words", "secret-form-words"):
                self.assertNotIn(words, str(kept))
        self.assertNotIn("text", record)

    async def test_with_diagnostics_on_it_keeps_what_the_page_says_and_never_the_form(self):
        atomic_json(home() / "config.json", {"page_diagnostics": True})
        result, record = await self.stays_after_the_press()
        self.assertIn("请先完成封面设置", record["text"])
        self.assertNotIn("secret-form-words", record["text"])
        self.assertNotIn("请先完成封面设置", str(result))

    async def test_a_stop_that_arrives_during_an_edit_holds_the_save(self):
        self.stop()
        with self.assertRaises(VpubError) as caught:
            await self.edit()
        self.assertEqual("stopped", caught.exception.code)
        self.assertEqual([], self.saves)
        self.assertFalse(self.path.exists())

    async def test_with_no_stop_the_save_goes_once(self):
        self.assertEqual("EDIT_VERIFIED", (await self.edit())["state"])
        self.assertEqual(1, len(self.saves))


class DouyinPageTests(SharedBrowser):
    """A Douyin work's cover and chapters, each saved on its own page and read back from a fresh load."""

    WORK = "7300000000000000001"
    CHAPTERS = [{"start_seconds": 0, "title": "开场"}, {"start_seconds": 90, "title": "正片"}]

    def prepare(self):
        self.saves = []
        self.title = "Old title"
        self.saved_chapters = []
        self.keeps_chapter_titles = True
        self.portrait = self.root / "portrait.jpg"
        self.portrait.write_bytes(b"synthetic")
        self.path = self.root / "edit.json"

    async def handle(self, route):
        url = urlsplit(route.request.url)
        if route.request.method == "POST":
            # What the receipt said at the moment the save arrived.
            self.saves.append((url.path, read_json(self.path).get("state") if self.path.exists() else None))
            if url.path == "/chapters":
                self.saved_chapters = json.loads(route.request.post_data)
            return await self.json_at(route, {})
        if url.path == "/creator-micro/content/post/video":
            return await self.cover_page(route)
        if url.path == "/creator-micro/content/manage":
            return await self.manage_page(route)
        await self.page_at(route, "elsewhere")

    async def cover_page(self, route):
        await self.page_at(route, f"""<input placeholder="填写作品标题，为作品获得更多流量" value="{self.title}">
          <div class="coverControl-a"><span>竖封面3:4</span><div class="cover-a" onclick="document.getElementById('picker').hidden = false">cover</div></div>
          <div role="dialog" id="picker" hidden><div class="semi-upload">点击上传文件或拖拽文件到这里<input class="semi-upload-hidden-input" type="file"></div><button onclick="document.getElementById('picker').hidden = true">完成</button></div>
          <button onclick="fetch('/cover', {{method: 'POST', body: '{{}}'}}).then(() => location.href = '/creator-micro/content/manage')">保存</button>""")

    async def manage_page(self, route):
        rows = "".join(f"<p>{int(row['start_seconds']) // 60:02d}:{int(row['start_seconds']) % 60:02d} {row['title']}</p>" for row in self.saved_chapters)
        keep = "name" if self.keeps_chapter_titles else "'不是这个'"
        await self.page_at(route, f"""<a href="https://creator.douyin.com/creator-micro/content/post/video?mid={self.WORK}">Old title</a>
          <div class="video-card-content-a">Old title<span onmouseenter="document.getElementById('menu').hidden = false">编辑作品</span><div id="menu" hidden><span onclick="document.getElementById('editor').hidden = false">添加章节</span></div></div>
          <div id="editor" hidden><video width="10" height="10"></video><button onclick="document.getElementById('name').hidden = false; document.getElementById('name').focus()">手动添加</button>
            <input id="name" placeholder="输入章节名" hidden><span onclick="addChapter()">确定</span><input type="checkbox" id="bar" {'checked' if self.saved_chapters else ''}>
            <div id="list">{rows}</div><p id="count">章节数：{len(self.saved_chapters)}</p><button id="save" onclick="sendChapters()">保存</button></div>
          <script>let chosen = [], times = {json.dumps([row['start_seconds'] for row in self.CHAPTERS])};
            function addChapter() {{ const box = document.getElementById('name'); const name = box.value; box.value = ''; box.hidden = true; const start = times[chosen.length]; chosen.push({{start_seconds: start, title: {keep}}});
              document.getElementById('list').innerHTML += '<p>' + String(Math.floor(start / 60)).padStart(2, '0') + ':' + String(start % 60).padStart(2, '0') + ' ' + chosen[chosen.length - 1].title + '</p>'; document.getElementById('count').textContent = '章节数：' + chosen.length; }}
            function sendChapters() {{ fetch('/chapters', {{method: 'POST', body: JSON.stringify(chosen)}}); document.getElementById('save').hidden = true; }}</script>""")

    async def cover(self, **extra):
        checked = edits.validate_patch("douyin", {"expected_identity": OWNER, "portrait": str(self.portrait)})
        return await browser_edits.execute(self.page, "douyin", OWNER, {"patch": checked, "receipt": {"id": self.WORK, "title": "Old title"}, "edit_receipt_path": str(self.path), **extra})

    async def chapters(self, **extra):
        checked = edits.validate_patch("douyin", {"expected_identity": OWNER, "chapters": self.CHAPTERS, "duration_seconds": 300})
        return await browser_edits.execute(self.page, "douyin", OWNER, {"patch": checked, "receipt": {"id": self.WORK, "title": "Old title", "identity": OWNER}, "edit_receipt_path": str(self.path), **extra})

    async def test_a_cover_is_saved_once_and_stays_unconfirmed_until_a_person_looks(self):
        result = await self.cover()
        self.assertEqual("EDIT_SUBMITTED_UNVERIFIED", result["state"])
        self.assertEqual([("/cover", "EDIT_SUBMITTING")], self.saves)

    async def test_a_cover_is_not_put_on_a_work_whose_title_differs(self):
        self.title = "Someone else's title"
        with self.assertRaises(VpubError) as caught:
            await self.cover()
        self.assertEqual("wrong_work", caught.exception.code)
        self.assertEqual([], self.saves)

    async def test_a_cover_file_that_changed_since_the_check_is_not_sent(self):
        checked = edits.validate_patch("douyin", {"expected_identity": OWNER, "portrait": str(self.portrait)})
        self.portrait.write_bytes(b"changed")
        with self.assertRaises(VpubError) as caught:
            await browser_edits.execute(self.page, "douyin", OWNER, {"patch": checked, "receipt": {"id": self.WORK, "title": "Old title"}, "edit_receipt_path": str(self.path)})
        self.assertEqual("source_changed", caught.exception.code)
        self.assertEqual([], self.saves)

    async def test_checking_a_cover_saves_nothing(self):
        await self.cover(verify_only=True)
        self.assertEqual([], self.saves)

    async def test_chapters_are_saved_once_and_read_back(self):
        result = await self.chapters()
        self.assertEqual(("EDIT_VERIFIED", True), (result["state"], result["timing_readback"]))
        self.assertEqual([("/chapters", "EDIT_SUBMITTING")], self.saves)

    async def test_chapters_the_page_did_not_keep_stop_before_the_save(self):
        self.keeps_chapter_titles = False
        with self.assertRaises(VpubError) as caught:
            await self.chapters()
        self.assertEqual("chapters_mismatch", caught.exception.code)
        self.assertEqual([], self.saves)

    async def test_checking_chapters_saves_nothing(self):
        result = await self.chapters(verify_only=True)
        self.assertEqual("EDIT_SUBMITTED_UNVERIFIED", result["state"])
        self.assertEqual([], self.saves)

    async def test_a_stop_holds_the_chapters_at_the_last_moment(self):
        self.stop()
        with self.assertRaises(VpubError) as caught:
            await self.chapters()
        self.assertEqual("stopped", caught.exception.code)
        self.assertEqual([], self.saves)


class CheckingTests(Temporary):
    def run_send(self, answer, verify_only):
        patch_value = edits.validate_patch("x", TEXT)

        def worker(platform, action, account, **options):
            if not options["verify_only"]:
                atomic_json(options["edit_receipt_path"], answer)
            return answer
        with patch.object(adapters, "browser_call", side_effect=worker) as call:
            result = edits.send("x", "default", OWNER, {"id": "1234567890"}, "key", patch_value, False, verify_only)
        return result, call

    def test_a_check_that_finds_other_words_does_not_look_like_a_save_that_was_tried(self):
        result, _ = self.run_send({"state": "EDIT_SUBMITTED_UNVERIFIED", "platform_writes": 0}, True)
        self.assertEqual("EDIT_MISMATCH", result["state"])
        saved, call = self.run_send({"state": "EDIT_VERIFIED", "platform_writes": 1}, False)
        self.assertEqual("EDIT_VERIFIED", saved["state"])
        call.assert_called_once()

    def test_a_check_after_a_save_that_really_was_tried_keeps_blocking_a_repeat(self):
        patch_value = edits.validate_patch("x", TEXT)
        path = home() / "edits" / (digest(["key", patch_value]) + ".json")
        atomic_json(path, {"state": "EDIT_SUBMITTING"})
        result, _ = self.run_send({"state": "EDIT_SUBMITTED_UNVERIFIED", "platform_writes": 0}, True)
        self.assertEqual("EDIT_SUBMITTED_UNVERIFIED", result["state"])
        with self.assertRaises(VpubError) as caught:
            self.run_send({"state": "EDIT_VERIFIED", "platform_writes": 1}, False)
        self.assertEqual("inspect_existing_edit", caught.exception.code)

    def tried(self):
        atomic_json(home() / "edits" / (digest(["key", edits.validate_patch("x", TEXT)]) + ".json"), {"state": "EDIT_SUBMITTED_UNVERIFIED"})

    def test_a_check_that_cannot_confirm_an_unclear_save_never_clears_it(self):
        """From a review: a thumbnail check said it could not compare, that read as a difference, and the same thumbnail was saved a second time."""
        for answer in ("EDIT_MISMATCH", "EDIT_SUBMITTED_UNVERIFIED"):
            with self.subTest(answer=answer):
                self.tried()
                result, _ = self.run_send({"state": answer, "platform_writes": 0}, True)
                self.assertEqual("EDIT_SUBMITTED_UNVERIFIED", result["state"])
                with self.assertRaises(VpubError) as caught:
                    self.run_send({"state": "EDIT_VERIFIED", "platform_writes": 1}, False)
                self.assertEqual("inspect_existing_edit", caught.exception.code)
                self.assertIn("--person-approved", caught.exception.hint)

    def test_once_a_person_has_looked_it_can_be_tried_once_more(self):
        self.tried()
        patch_value = edits.validate_patch("x", TEXT)
        with patch.object(adapters, "browser_call", return_value={"state": "EDIT_VERIFIED", "platform_writes": 1}) as call:
            result = edits.send("x", "default", OWNER, {"id": "1234567890"}, "key", patch_value, False, False, person_approved=True)
        self.assertEqual("EDIT_VERIFIED", result["state"])
        call.assert_called_once()
        self.assertIs(True, result["person_approved"])
        self.assertIs(True, read_json(home() / "edits" / (digest(["key", patch_value]) + ".json"))["person_approved"])

    def test_the_record_says_nothing_of_a_person_unless_the_flag_was_given(self):
        result, _ = self.run_send({"state": "EDIT_VERIFIED", "platform_writes": 1}, False)
        self.assertNotIn("person_approved", result)

    def test_a_check_before_any_save_still_does_not_block_the_save(self):
        result, _ = self.run_send({"state": "EDIT_MISMATCH", "platform_writes": 0}, True)
        self.assertEqual("EDIT_MISMATCH", result["state"])
        self.assertEqual("EDIT_VERIFIED", self.run_send({"state": "EDIT_VERIFIED", "platform_writes": 1}, False)[0]["state"])

    def test_a_check_that_finds_the_words_there_clears_an_earlier_unclear_save(self):
        patch_value = edits.validate_patch("x", TEXT)
        atomic_json(home() / "edits" / (digest(["key", patch_value]) + ".json"), {"state": "EDIT_SUBMITTING"})
        self.assertEqual("EDIT_VERIFIED", self.run_send({"state": "EDIT_VERIFIED", "platform_writes": 0}, True)[0]["state"])
        self.assertEqual("EDIT_VERIFIED", self.run_send({"state": "EDIT_VERIFIED", "platform_writes": 1}, False)[0]["state"])


class LostCheckTests(Temporary):
    """From a review: a check wrote what it found to the edit's record, the command was then cut off before that could be put right, and the next try saved a second time."""
    COVER = {"expected_identity": OWNER, "cover": "cover.jpg", "cover_sha256": "hash"}
    CAPTION = {"expected_identity": OWNER, "title": "New title", "post_text": "New title\n\nNew text"}

    def setUp(self):
        super().setUp()
        addon = patch.object(adapters, "addon_installed", return_value=False)
        addon.start()
        self.addCleanup(addon.stop)
        self.pressed = 0

    def send(self, platform, value, verify_only, cut_off):
        editor, post, page = MagicMock(), MagicMock(), MagicMock()
        editor.inner_text = AsyncMock(return_value="Old words")
        post.first.wait_for = page.goto = AsyncMock()

        def worker(platform, action, account, **options):
            if not options["verify_only"]:
                self.pressed += 1
                return {"state": "EDIT_VERIFIED", "platform_writes": 1}
            with patch.object(youtube_studio, "edit_fields", new=AsyncMock(return_value=(MagicMock(), MagicMock()))), patch.object(browser_edits, "instagram_caption_editor", new=AsyncMock(return_value=editor)), \
                    patch.object(browser_edits.x_pages, "post_row", return_value=post), patch.object(browser_edits.x_pages, "post_fields", new=AsyncMock(return_value={"text": "Old words"})):
                answer = asyncio.run(browser_edits.execute(page, platform, OWNER, options))
            if cut_off:
                raise cut_off("The answer never got back.")
            return answer
        with patch.object(adapters, "browser_call", side_effect=worker):
            return edits.send(platform, "default", OWNER, {"id": "1234567890"}, "key", value, False, verify_only)

    def record(self, value):
        return home() / "edits" / (digest(["key", value]) + ".json")

    def test_a_check_cut_off_leaves_a_save_already_tried_on_record(self):
        for platform, value, cut_off in (("youtube", self.COVER, TimeoutError), ("youtube", self.COVER, KeyboardInterrupt), ("instagram", self.CAPTION, TimeoutError), ("x", self.CAPTION, TimeoutError)):
            with self.subTest(platform=platform, cut_off=cut_off):
                atomic_json(self.record(value), {"state": "EDIT_SUBMITTED_UNVERIFIED"})
                with self.assertRaises(cut_off):
                    self.send(platform, value, True, cut_off)
                self.assertEqual("EDIT_SUBMITTED_UNVERIFIED", read_json(self.record(value))["state"])
                with self.assertRaises(VpubError) as caught:
                    self.send(platform, value, False, None)
                self.assertEqual("inspect_existing_edit", caught.exception.code)
                self.assertEqual(0, self.pressed)

    def test_a_check_that_finishes_still_keeps_a_save_already_tried_on_record(self):
        for platform, value in (("youtube", self.COVER), ("instagram", self.CAPTION), ("x", self.CAPTION)):
            with self.subTest(platform=platform):
                atomic_json(self.record(value), {"state": "EDIT_SUBMITTING"})
                self.assertEqual("EDIT_SUBMITTED_UNVERIFIED", self.send(platform, value, True, None)["state"])
                self.assertEqual("EDIT_SUBMITTED_UNVERIFIED", read_json(self.record(value))["state"])

    def test_a_check_cut_off_before_any_save_does_not_block_the_save(self):
        """On X a check that finds other words answers as an unclear save, and that must not reach the record before edits.send has read it."""
        for platform, value in (("youtube", self.COVER), ("instagram", self.CAPTION), ("x", self.CAPTION)):
            with self.subTest(platform=platform):
                self.pressed = 0
                self.record(value).unlink(missing_ok=True)
                with self.assertRaises(TimeoutError):
                    self.send(platform, value, True, TimeoutError)
                self.assertFalse(self.record(value).exists())
                self.assertEqual("EDIT_VERIFIED", self.send(platform, value, False, None)["state"])
                self.assertEqual(1, self.pressed)


class TitleTests(unittest.TestCase):
    def test_a_title_a_form_or_a_command_could_not_hold_is_refused_at_once(self):
        for platform in ("bilibili", "youtube", "douyin", "x", "instagram"):
            for title in ("line\nbreak", "return\rcharacter", "null\x00character"):
                with self.subTest(platform=platform, title=title), self.assertRaises(VpubError) as caught:
                    edits.validate_patch(platform, {**TEXT, "title": title})
                self.assertEqual("invalid_title", caught.exception.code)


class AiWordsTests(Temporary):
    def publish_record(self, platform, ai_generated):
        ledger = Ledger()
        key = ledger.claim(platform, "default", "a" * 64, {})
        ledger.save(key, "SUBMISSION_VERIFIED", {"identity": {"identity": OWNER}, "options": {"ai_generated": ai_generated, "title": "Old"}, "receipt": {"id": "1234567890", "title": "Old"}})
        ledger.close()
        return key

    def edit(self, platform, ai_generated):
        key = self.publish_record(platform, ai_generated)
        patch_path = self.root / "patch.json"
        atomic_json(patch_path, TEXT)
        with patch.object(adapters, "browser_call", return_value={"state": "EDIT_VERIFIED"}) as call:
            edits.run(key, patch_path)
        (home() / "publications.sqlite3").unlink()
        return call.call_args.kwargs["patch"]["post_text"]

    def test_an_ordinary_video_does_not_become_ai_generated_by_being_edited(self):
        for platform in ("x", "instagram"):
            with self.subTest(platform=platform):
                self.assertEqual("New title\n\nNew text", self.edit(platform, False))

    def test_an_ai_video_keeps_saying_so_after_an_edit(self):
        for platform in ("x", "instagram"):
            with self.subTest(platform=platform):
                self.assertEqual("New title\n\nNew text\n\nAI-generated", self.edit(platform, True))


if __name__ == "__main__":
    unittest.main()

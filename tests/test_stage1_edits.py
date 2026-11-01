"""Edits to works already out: a stop at the last moment, a check that changes nothing, and what an edit says about AI. Fakes and small page copies; nothing is contacted."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parent))
from stage_browser import SharedBrowser

from video_publisher import adapters, browser_edits, edits
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

    async def handle(self, route):
        if route.request.method == "POST":
            self.saves.append(json.loads(route.request.post_data))
            self.title, self.words = self.saves[-1]["title"], self.saves[-1]["words"]
            return await self.json_at(route, {})
        if urlsplit(route.request.url).path == "/creator-micro/content/post/video":
            return await self.page_at(route, f"""<input placeholder="填写作品标题，为作品获得更多流量" value="{self.title}"><div contenteditable="true" id="c">{self.words}</div>
              <button id="go">发布</button><script>document.getElementById('go').onclick = async () => {{
                await fetch('/save', {{method: 'POST', body: JSON.stringify({{title: document.querySelector('input').value, words: document.getElementById('c').innerText}})}}); location.href = '/creator-micro/content/manage'; }};</script>""")
        await self.page_at(route, "manage")

    async def edit(self):
        checked = edits.validate_patch("douyin", {**TEXT, "metadata_language": "zh-Hans", "content_language": "zh-Hans"})
        self.path = self.root / "edit.json"
        return await browser_edits.execute(self.page, "douyin", OWNER, {"patch": checked, "receipt": {"id": "123", "title": "Old title"}, "edit_receipt_path": str(self.path)})

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


class CheckingTests(Temporary):
    def run_send(self, answer, verify_only):
        patch_value = edits.validate_patch("x", TEXT)

        def worker(platform, action, account, **options):
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

    def test_a_check_that_finds_the_words_there_clears_an_earlier_unclear_save(self):
        patch_value = edits.validate_patch("x", TEXT)
        atomic_json(home() / "edits" / (digest(["key", patch_value]) + ".json"), {"state": "EDIT_SUBMITTING"})
        self.assertEqual("EDIT_VERIFIED", self.run_send({"state": "EDIT_VERIFIED", "platform_writes": 0}, True)[0]["state"])
        self.assertEqual("EDIT_VERIFIED", self.run_send({"state": "EDIT_VERIFIED", "platform_writes": 1}, False)[0]["state"])


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

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


if __name__ == "__main__":
    unittest.main()

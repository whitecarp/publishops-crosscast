"""AI-generated and ordinary videos on every route. Offline; no platform is contacted."""
import os
from pathlib import Path
import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import adapters, browser_forms, cli, native_browser
from video_publisher.core import VpubError, atomic_json, load_plan, read_json


class FakeControl:
    """Just enough of a page or locator for the Douyin form: everything succeeds and clicks are logged."""

    def __init__(self, clicks, values, label=""):
        self.clicks, self.values, self.label = clicks, values, label

    def locator(self, selector):
        return FakeControl(self.clicks, self.values, selector)

    def filter(self, **options):
        return self

    def get_by_text(self, text, **options):
        return FakeControl(self.clicks, self.values, text)

    def get_by_role(self, role, name=None, **options):
        return FakeControl(self.clicks, self.values, name or role)

    async def all(self):
        return [self]

    async def is_visible(self):
        return True

    async def is_checked(self):
        return True

    async def click(self, **options):
        self.clicks.append(self.label)

    async def wait_for(self, **options):
        pass

    async def wait_for_url(self, *args, **options):
        pass

    async def check(self):
        pass

    async def input_value(self):
        return self.values["title"]

    async def inner_text(self):
        return self.values["caption"]


class DeclarationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        self.env.start()
        self.video = self.root / "ordinary.mp4"
        self.video.write_bytes(b"synthetic test fixture, not a playable upload")
        self.manifest = self.root / "publish.json"

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def write(self, ai_generated=False, **routes):
        value = cli.template(self.video, ai_generated=ai_generated)
        self.assertEqual(["AI"] if ai_generated else [], value["tags"])
        value["tags"] = ["vlog"]  # what metadata apply would write
        value["platforms"]["youtube"]["category_id"] = "22"
        value["platforms"]["x"] = {"account": "default", "method": "native", "visibility": "account", "ai_generated": ai_generated}
        for platform, method in routes.items():
            value["platforms"][platform]["method"] = method
        atomic_json(self.manifest, value)
        return value

    def test_ordinary_video_plans_on_every_route(self):
        for routes in ({}, {"bilibili": "cli", "douyin": "browser"}):
            self.write(False, **routes)
            plan = load_plan(self.manifest)
            self.assertTrue(all(o["ai_generated"] is False for o in plan["platforms"].values()))
        self.assertNotIn("AI-generated", plan["platforms"]["x"]["post_text"])
        self.write(True)
        self.assertIn("AI-generated", load_plan(self.manifest, ["x"])["platforms"]["x"]["post_text"])

    def test_declaration_must_still_be_explicit(self):
        value = self.write(False)
        del value["platforms"]["bilibili"]["ai_generated"]
        atomic_json(self.manifest, value)
        with self.assertRaises(VpubError) as e:
            load_plan(self.manifest, ["bilibili"])
        self.assertEqual("invalid_declaration", e.exception.code)

    def test_init_sets_every_platform_declaration(self):
        for flag, expected in (("no", False), ("yes", True)):
            out = self.root / f"init-{flag}.json"
            args = cli.parser().parse_args(["init", "--video", str(self.video), "--out", str(out),
                                            "--platforms", "bilibili,douyin,instagram,x", "--ai-generated", flag])
            cli.execute(args)
            self.assertTrue(all(o["ai_generated"] is expected for o in read_json(out)["platforms"].values()))
        default = cli.parser().parse_args(["init", "--video", "v.mp4", "--out", "o.json"])
        self.assertEqual("yes", default.ai_generated)
        with self.assertRaises(SystemExit):
            with patch("sys.stderr"):
                cli.parser().parse_args(["init", "--video", "v.mp4", "--out", "o.json", "--ai-generated", "maybe"])

    def bilibili_cli(self, ai_generated, recorded):
        self.write(ai_generated, bilibili="cli")
        plan = load_plan(self.manifest, ["bilibili"])
        calls = []

        def fake_bili(args, timeout=180):
            calls.append(args)
            if args[:2] == ["upload", "video"]:
                return {"bvid": "BV1ab411c7mD"}
            return {"bvid": "BV1ab411c7mD", "ai_declare": recorded}

        with patch.object(adapters, "bili", side_effect=fake_bili):
            receipt = adapters.publish_bilibili({"video": str(self.video), "cover": None},
                                                plan["platforms"]["bilibili"], lambda r: None)
        return calls[0], receipt

    def test_bilibili_cli_opts_out_for_ordinary_videos(self):
        upload, receipt = self.bilibili_cli(False, recorded=False)
        self.assertIn("--no-ai-declare", upload)
        self.assertNotIn("--ai-declare", upload)
        self.assertEqual("SUBMISSION_VERIFIED", receipt["state"])
        self.assertFalse(receipt["expected_ai_declare"])

    def test_bilibili_cli_declares_ai_videos(self):
        upload, receipt = self.bilibili_cli(True, recorded=True)
        self.assertIn("--ai-declare", upload)
        self.assertNotIn("--no-ai-declare", upload)
        self.assertEqual("SUBMISSION_VERIFIED", receipt["state"])

    def test_bilibili_readback_flags_a_wrong_declaration(self):
        _, receipt = self.bilibili_cli(False, recorded=True)
        self.assertEqual("SUBMITTED_UNVERIFIED", receipt["state"])
        self.assertTrue(receipt["declaration_mismatch"])

    def test_bilibili_receipts_without_expectation_keep_old_behaviour(self):
        with patch.object(adapters, "bili", return_value={"ai_declare": True}):
            result = adapters.verify("bilibili", "default", {"id": "BV1ab411c7mD", "state": "EXISTING_VERIFIED"})
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertNotIn("declaration_mismatch", result)

    def test_douyin_form_declares_only_ai_videos(self):
        for ai_generated in (True, False):
            with self.subTest(ai_generated=ai_generated):
                clicks = []
                page = FakeControl(clicks, {"title": "Title", "caption": "Text\n\n#vlog"})
                tx = MagicMock(options={"title": "Title", "description": "Text", "tags": ["vlog"], "visibility": "public", "ai_generated": ai_generated},
                               receipt={}, submit=AsyncMock())
                tx.cover.return_value = None
                with patch.object(browser_forms, "finish", new=AsyncMock(return_value={"state": "SUBMISSION_VERIFIED"})):
                    asyncio.run(browser_forms.finish_douyin_form(page, tx, []))
                self.assertEqual(ai_generated, "自主声明" in clicks)
                tx.submit.assert_awaited_once()

    def test_browser_evidence_must_match_ordinary_declaration(self):
        self.write(False)
        plan = load_plan(self.manifest, ["bilibili"])
        key = cli.publish_plan(plan, plan["confirmation"])["results"]["bilibili"]["key"]
        native_browser.begin(key, "12345")
        opts = plan["platforms"]["bilibili"]
        evidence = {k: opts[k] for k in ("title", "description", "tags", "visibility")}
        evidence.update(identity="12345", outcome="verified", id="BV1ab411c7mD", observation="Synthetic read-back.",
                        page_url="https://www.bilibili.com/video/BV1ab411c7mD")
        path = self.root / "evidence.json"
        atomic_json(path, {**evidence, "ai_generated": True})
        with self.assertRaises(VpubError) as e:
            native_browser.record(key, path)
        self.assertEqual("metadata_mismatch", e.exception.code)
        atomic_json(path, {**evidence, "ai_generated": False})
        self.assertEqual("AGENT_REPORTED_VERIFIED", native_browser.record(key, path)["state"])


if __name__ == "__main__":
    unittest.main()

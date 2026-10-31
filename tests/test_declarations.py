"""AI-generated and ordinary videos on every route. Offline; no platform is contacted."""
import argparse
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import adapters, cli, douyin_worker, native_browser
from video_publisher.core import VpubError, atomic_json, load_plan, read_json


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

    def test_douyin_backend_declares_only_ai_videos(self):
        self.assertEqual("内容由AI生成", douyin_worker.ai_declaration({"ai_generated": True}))
        self.assertIsNone(douyin_worker.ai_declaration({"ai_generated": False}))

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

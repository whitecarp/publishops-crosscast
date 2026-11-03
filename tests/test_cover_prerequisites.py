"""Local missing-cover checks happen before an account call or upload claim."""
import os
import ast
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock, AsyncMock

from PIL import Image
from video_publisher import adapters, cli, rehearsal, drafts_bilibili
from video_publisher.core import Ledger, VpubError, atomic_json, load_plan


class CoverPrerequisiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "home"), "BILI_CLI_HOME": str(self.root / "bili")})
        env.start()
        self.addCleanup(env.stop)
        self.video = self.root / "video.mp4"
        self.video.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42")
        self.manifest = self.root / "plan.json"
        value = cli.template(self.video)
        value.update(title="A written title", content_language="en")
        value["platforms"] = {"bilibili": {"account": "default", "method": "cli", "visibility": "public", "ai_generated": False}}
        atomic_json(self.manifest, value)
        self.cover = self.root / "cover.jpg"
        Image.new("RGB", (1600, 1000)).save(self.cover)
        credential = self.root / "bili/credential.json"
        credential.parent.mkdir()
        credential.write_text("fake saved record, not a credential")

    def run_cli(self, *args):
        return cli.execute(cli.parser().parse_args([str(x) for x in args]))

    def prepare(self, *extra):
        return self.run_cli("prepare", self.video, "--folder", self.root / "prepared", "--platforms", "bilibili", "--language", "en", "--visibility", "public", "--ai-generated", "no", *extra)

    def test_prepare_missing_tool_refuses_without_a_partial_folder_or_account_call(self):
        with patch.object(rehearsal.shutil, "which", return_value=None), patch.object(adapters, "bili", side_effect=AssertionError("network")):
            with self.assertRaises(VpubError) as error:
                self.prepare()
        self.assertEqual("cover_tool_missing", error.exception.code)
        self.assertIn("--cover", error.exception.hint)
        self.assertFalse((self.root / "prepared").exists())

    def test_prepare_supplied_cover_and_detected_tool_each_work(self):
        with patch.object(rehearsal.shutil, "which", return_value=None):
            self.assertEqual("NEEDS_COPY", self.prepare("--cover", self.cover)["state"])
        with patch.object(rehearsal.shutil, "which", return_value="ffmpeg.exe"):
            result = self.run_cli("prepare", self.video, "--folder", self.root / "with-tool", "--platforms", "bilibili", "--language", "en", "--visibility", "public", "--ai-generated", "no")
        self.assertEqual("NEEDS_COPY", result["state"])

    def test_dry_run_reports_local_blocker_not_ready_and_no_network(self):
        with patch.object(rehearsal.shutil, "which", return_value=None), patch.object(adapters, "auth_status", side_effect=AssertionError("network")), patch.object(adapters, "publish", side_effect=AssertionError("upload")):
            result = self.run_cli("publish", self.manifest, "--dry-run")
        self.assertEqual("LOCAL_BLOCKERS", result["state"])
        self.assertFalse(cli.finished(result))
        self.assertFalse(result["ready_to_publish"])
        self.assertEqual(["cover_tool_missing"], [x["code"] for x in result["blockers"]])
        self.assertEqual(0, result["platform_requests"])
        self.assertFalse((self.root / "home/publications.sqlite3").exists())

    def test_effective_cover_routes_and_drafts_are_checked(self):
        plan = load_plan(self.manifest)
        with patch.object(rehearsal.shutil, "which", return_value=None):
            for change in ({"cover": str(self.cover)}, {"covers": {"bilibili": {"landscape": {"path": str(self.cover)}}}}, {"platforms": {"youtube": {"method": "browser"}}}):
                with self.subTest(change=change):
                    self.assertEqual([], rehearsal.local_prerequisites({**plan, **change}))
            plan["platforms"]["bilibili"]["submission_mode"] = "draft_only"
            self.assertEqual("cover_tool_missing", rehearsal.local_prerequisites(plan)[0]["code"])
        with patch.object(rehearsal.shutil, "which", return_value="ffmpeg.exe"):
            self.assertEqual([], rehearsal.local_prerequisites(plan))

    def test_real_publish_refuses_before_preflight_claim_or_backend(self):
        plan = load_plan(self.manifest)
        with patch.object(rehearsal.shutil, "which", return_value=None), patch.object(adapters, "preflight", side_effect=AssertionError("network")), patch.object(adapters, "publish", side_effect=AssertionError("upload")), patch.object(Ledger, "claim", side_effect=AssertionError("claim")):
            result = cli.publish_plan(plan, plan["confirmation"], reviewed=True)
        self.assertEqual("NOT_STARTED", result["results"]["bilibili"]["state"])
        self.assertEqual("cover_tool_missing", result["results"]["bilibili"]["code"])

    def test_unknown_attempt_stays_blocked_even_if_cover_was_missing(self):
        plan = load_plan(self.manifest)
        ledger = Ledger()
        try:
            record = {"options": plan["platforms"]["bilibili"], "error": {"code": "invalid_argument", "message": "missing cover"}}
            self.assertTrue(ledger.claim("bilibili", "default", plan["video_sha256"], record))
            key = ledger.key("bilibili", "default", plan["video_sha256"])
            ledger.save(key, "UNKNOWN", record)
        finally:
            ledger.close()
        with patch.object(rehearsal.shutil, "which", return_value=None), patch.object(adapters, "publish", side_effect=AssertionError("replay")):
            result = cli.publish_plan(plan, plan["confirmation"], reviewed=True)
        self.assertEqual("UNKNOWN", result["results"]["bilibili"]["previous_state"])

    def test_draft_cover_decode_failure_precedes_any_platform_request_or_media_upload(self):
        # Execute only the helper's real add function with fake client dependencies.
        # The GPL client and its credentials are never imported into this process.
        node = next(n for n in ast.parse(drafts_bilibili.HELPER).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "add")
        upload = SimpleNamespace(_extract_cover=Mock(side_effect=ValueError("cannot decode fixture")),
                                 _predict_zone=AsyncMock(), _preflight=AsyncMock(), _upload_file=AsyncMock())
        namespace = {"Path": Path, "upload": upload}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "draft-helper", "exec"), namespace)
        with self.assertRaisesRegex(ValueError, "cannot decode fixture"):
            asyncio.run(namespace["add"](object(), {"video": str(self.video), "title": "fixture"}))
        upload._extract_cover.assert_called_once_with(self.video)
        upload._predict_zone.assert_not_called()
        upload._preflight.assert_not_called()
        upload._upload_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()

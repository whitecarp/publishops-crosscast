"""Local rehearsal never borrows a live session or treats a file as a login."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import adapters, cli, rehearsal
from video_publisher.core import VpubError, atomic_json, load_plan

# Container header only, deliberately not a decodable video.
MP4_HEADER = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42"


class RehearsalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "vpub"), "BILI_CLI_HOME": str(self.root / "bili")})
        env.start()
        self.addCleanup(env.stop)
        self.video = self.root / "film.mp4"
        self.video.write_bytes(MP4_HEADER)
        self.manifest = self.root / "plan.json"
        value = cli.template(self.video)
        value["platforms"] = {p: {"account": "default", "method": "cli" if p == "bilibili" else "browser",
                                  "visibility": "public" if p in {"bilibili", "douyin"} else "account" if p in {"x", "instagram"} else "private",
                                  "ai_generated": True} for p in cli.PLATFORMS}
        value["platforms"]["youtube"].update(category_id="1", made_for_kids=False)
        atomic_json(self.manifest, value)

    def run_cli(self, *args):
        return cli.execute(cli.parser().parse_args(args))

    def test_missing_records_are_actionable_and_no_platform_is_called(self):
        with patch.object(adapters, "auth_status", side_effect=AssertionError("network")), \
             patch.object(adapters, "bili", side_effect=AssertionError("network")), \
             patch.object(adapters, "browser_call", side_effect=AssertionError("browser")), \
             patch.object(adapters, "publish", side_effect=AssertionError("write")):
            result = self.run_cli("publish", str(self.manifest), "--dry-run")
        self.assertEqual("LOCAL_REHEARSAL", result["mode"])
        self.assertFalse(result["ready"])
        self.assertFalse(cli.finished(result))
        self.assertEqual(5, len(result["blockers"]))
        self.assertEqual(0, result["platform_requests"])
        for platform, state in result["sign_ins"].items():
            self.assertIsNone(state["logged_in"])
            parsed = cli.parser().parse_args(state["next_command"].split()[1:])
            self.assertEqual(("auth", "login", platform), (parsed.command, parsed.action, parsed.platform))
        self.assertFalse((self.root / "bili").exists())
        self.assertFalse((self.root / "vpub/publications.sqlite3").exists())

    def test_main_returns_nonzero_for_missing_sign_ins(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["vpub", "publish", str(self.manifest), "--dry-run"]), \
             contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            cli.main()
        self.assertEqual(4, exited.exception.code)
        self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_saved_files_are_not_read_or_treated_as_live_identity(self):
        paths = [self.root / "bili/credential.json"]
        paths += [self.root / "vpub/accounts/default" / (p + "-identity.json") for p in cli.PLATFORMS if p != "bilibili"]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"not even valid JSON: secret-must-not-appear")
        original = Path.read_text
        def no_credentials(path, *args, **kwargs):
            self.assertNotIn(path, paths)
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", no_credentials):
            result = self.run_cli("publish", str(self.manifest), "--dry-run")
        self.assertIsNone(result["ready_to_publish"])
        self.assertEqual("LIVE_CHECK_REQUIRED", result["state"])
        self.assertNotIn("secret-must-not-appear", json.dumps(result))
        for state in result["sign_ins"].values():
            self.assertEqual("SAVED_SIGN_IN_UNVERIFIED", state["state"])
            self.assertIsNone(state["logged_in"])

    def test_profile_without_identity_still_requires_live_check(self):
        profile = self.root / "vpub/accounts/default/x-browser-profile"
        profile.mkdir(parents=True)
        (profile / "Local State").write_text("fake-profile")
        result = rehearsal.sign_in("x", {"account": "default", "method": "browser"})
        self.assertTrue(result["saved_record_present"])
        self.assertIsNone(result["logged_in"])
        self.assertIn("auth status x", result["next_command"])
        self.assertFalse(result["next_command_required"])

    def test_native_and_unrecognised_addon_formats_stay_unknown(self):
        for platform, method in [("x", "api"), ("youtube", "native")]:
            result = rehearsal.sign_in(platform, {"account": "default", "method": method})
            self.assertEqual("LIVE_CHECK_REQUIRED", result["state"])
            self.assertIsNone(result["saved_record_present"])

    def test_youtube_api_uses_token_presence_only(self):
        path = self.root / "vpub/accounts/default/youtube-token.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fake-token")
        result = rehearsal.sign_in("youtube", {"account": "default", "method": "api"})
        self.assertTrue(result["saved_record_present"])
        self.assertIsNone(result["logged_in"])

    def test_stop_is_reported_and_unchanged(self):
        path = self.root / "vpub/STOP.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"malformed stop still holds")
        result = self.run_cli("publish", str(self.manifest), "--dry-run")
        self.assertEqual(5, sum(b["code"] == "stopped" for b in result["blockers"]))
        self.assertEqual(b"malformed stop still holds", path.read_bytes())

    def test_local_checks_do_not_change_confirmation_or_claim_a_video_decode(self):
        before = load_plan(self.manifest)
        planned = self.run_cli("plan", str(self.manifest))
        rehearsed = self.run_cli("publish", str(self.manifest), "--dry-run")
        self.assertEqual(before["confirmation"], planned["confirmation"])
        self.assertEqual(before["confirmation"], rehearsed["confirmation"])
        self.assertNotIn("sign_ins", planned)
        self.assertFalse(rehearsed["media_check"]["playback_verified"])

    def test_renamed_text_is_refused_at_init_plan_dry_run_and_publish(self):
        self.video.write_bytes(b"This is text, not a video." * 10)
        commands = [("init", "--video", str(self.video), "--out", str(self.root / "new.json")),
                    ("plan", str(self.manifest)), ("publish", str(self.manifest), "--dry-run"),
                    ("publish", str(self.manifest), "--confirm", "unused")]
        with patch.object(cli, "publish_plan", side_effect=AssertionError("must not publish")):
            for command in commands:
                with self.subTest(command=command), self.assertRaises(VpubError) as caught:
                    self.run_cli(*command)
                self.assertEqual("invalid_video", caught.exception.code)
        self.assertFalse((self.root / "new.json").exists())

    def test_signature_check_is_bounded_and_detects_bad_first_box_lengths(self):
        for content in [b"", b"\x00\x00\x00\xffftyp" + b"x" * 20, b"\x00\x00\x00\x04ftyp" + b"x" * 20, b"\x89PNG\r\n\x1a\n" + b"x" * 20]:
            self.video.write_bytes(content)
            with self.subTest(content=content), self.assertRaises(VpubError):
                rehearsal.inspect_video(self.video)
        mkv = self.video.with_suffix(".mkv")
        mkv.write_bytes(b"\x1aE\xdf\xa3" + b"x" * 20)
        self.assertFalse(rehearsal.inspect_video(mkv)["playback_verified"])

    def test_headers_do_not_depend_on_the_filename(self):
        for suffix in (".mp4", ".mov", ".webm", ".mkv"):
            for data, expected in ((MP4_HEADER, "MP4/MOV"), (b"\x1aE\xdf\xa3" + bytes(20), "WebM/Matroska")):
                path = self.video.with_suffix(suffix)
                path.write_bytes(data)
                with self.subTest(suffix=suffix, expected=expected):
                    self.assertEqual(expected, rehearsal.inspect_video(path)["container"])

    def test_other_container_headers_and_initial_iso_boxes(self):
        examples = [(b"RIFF" + bytes(4) + b"AVI " + bytes(16), "AVI"),
                    (b"FLV\x01\x05\x00\x00\x00\x09" + bytes(4), "FLV"),
                    ((b"G" + bytes(187)) * 4, "MPEG-TS")]
        examples += [(b"\x00\x00\x00\x10" + tag + bytes(8), "MP4/MOV")
                     for tag in (b"uuid", b"pnot", b"udta", b"junk", b"pict", b"ediw", b"prfl", b"sidx", b"styp")]
        for content, expected in examples:
            self.video.write_bytes(content)
            with self.subTest(expected=expected, signature=content[:12]):
                result = rehearsal.inspect_video(self.video)
                self.assertEqual(expected, result["container"])
                self.assertFalse(result["playback_verified"])


if __name__ == "__main__":
    unittest.main()

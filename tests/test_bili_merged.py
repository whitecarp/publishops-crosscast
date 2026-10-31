"""bili-cli ships inside CrossCast. Every call CrossCast makes is checked against the included code.

The other tests mock bili-cli, so they cannot see a renamed command, flag or output field. Offline; no
platform is contacted and only temporary sign-in folders are used.
"""
import inspect
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import typer

import bili_cli
from bili_cli import runtime, skills
from bili_cli.commands.video import _folders, _own_video
from bili_cli.main import _hoist_global_flags, app
import video_publisher
from video_publisher import adapters, cli, direct_messages_bili
from video_publisher.core import VpubError, atomic_json, read_json

BV = "BV1ab411c7mD"

# Every bili command line CrossCast builds, with synthetic values.
CALLS = [
    ["auth", "status"],
    ["auth", "status", "--wait", "30"],
    ["auth", "login"],
    ["upload", "video", "video.mp4", "--title", "T", "--desc", "D", "--ai-declare", "--tag", "a", "--tag", "b",
     "--cover", "cover.jpg", "--tid", "21", "--private", "--schedule", "2030-01-01T00:00:00+08:00"],
    ["upload", "video", "video.mp4", "--title", "T", "--desc", "D", "--no-ai-declare", "--tag", "a"],
    ["upload", "status", BV],
    ["upload", "stats", BV],
    ["upload", "list", "--status", "published", "--page", "1", "--size", "20"],
    ["video", "info", BV],
    ["video", "relation", BV],
    ["video", "like", BV],
    ["video", "like", BV, "--undo"],
    ["video", "fav", BV, "--folder", "123"],
    ["video", "fav", BV, "--folder", "123", "--remove"],
    ["me", "favs"],
    ["comment", "post", BV, "你好"],
    ["comment", "list", BV, "--sort", "time", "--page", "1"],
    ["comment", "list", BV, "--page", "1", "--raw"],
    ["comment", "replies", BV, "1", "--page", "1", "--size", "20"],
    ["comment", "replies", BV, "1", "--size", "20", "--raw"],
    ["comment", "reply", BV, "1", "--file", "reply.txt"],
    ["comment", "reply", BV, "1", "--file", "reply.txt", "--root", "2"],
    ["comment", "like", BV, "1"],
    ["comment", "like", BV, "1", "--undo"],
    ["comment", "pin", BV, "1"],
    ["comment", "pin", BV, "1", "--undo"],
    ["dm", "list", "--limit", "50"],
    ["feed", "mentions", "--limit", "50"],
    ["dm", "read", "123", "--limit", "20"],
    ["dm", "send", "123", "--file", "message.txt"],
]


def parse(argv):
    """Walk the real command tree. Unknown commands, flags and bad values raise; nothing runs."""
    command = typer.main.get_command(app)
    context = command.make_context("bili", _hoist_global_flags(list(argv)))
    while hasattr(command, "commands"):
        pending = [*getattr(context, "_protected_args", getattr(context, "protected_args", [])), *context.args]
        name, command, rest = command.resolve_command(context, pending)
        context = command.make_context(name, rest, parent=context)
    return command


class IncludedCopyTests(unittest.TestCase):
    def test_version_is_the_one_these_calls_were_checked_against(self):
        # When this changes, read the upstream changelog and the diff of every command in CALLS.
        self.assertEqual("1.2.1", bili_cli.__version__)
        self.assertEqual("1.2.1", cli.included_bili_version())

    def test_crosscast_runs_its_own_copy(self):
        self.assertEqual(Path(video_publisher.__file__).resolve().parents[1], Path(bili_cli.__file__).resolve().parents[1])

    def test_license_and_agent_skill_ship_with_the_code(self):
        folder = Path(bili_cli.__file__).parent
        self.assertIn("MIT License", (folder / "LICENSE").read_text(encoding="utf-8"))
        self.assertTrue((skills.bundled_dir() / "SKILL.md").is_file())

    def test_every_call_crosscast_makes_is_a_valid_command(self):
        for argv in CALLS:
            with self.subTest(argv=argv):
                self.assertIsNotNone(parse(argv))

    def test_the_parser_really_rejects_what_does_not_exist(self):
        for argv in (["dm", "send", "123", "--no-such-flag"], ["no-such-group", "x"], ["upload", "no-such-command"]):
            with self.subTest(argv=argv), self.assertRaises(Exception):
                parse(argv)

    def test_the_helpers_crosscast_reuses_are_still_there(self):
        self.assertEqual(["video_id"], list(inspect.signature(_own_video).parameters))
        self.assertEqual(["cred", "v"], list(inspect.signature(_folders).parameters))
        self.assertEqual("coro_fn", next(iter(inspect.signature(runtime.run).parameters)))

    def test_crosscast_waits_long_enough_for_bili_cli_to_report_a_stuck_command(self):
        patience = inspect.signature(adapters.bili).parameters["timeout"].default
        self.assertGreater(patience, runtime.DEFAULT_DEADLINE + runtime.HARD_EXIT_GRACE)

    def test_crosscast_keeps_the_gpl_library_in_the_separate_bili_process(self):
        code = ("import sys\n"
                "from video_publisher import cli, adapters, direct_messages_bili, engagement_api, comment_actions_api, operations_api, operations_sync\n"
                "cli.doctor(); cli.capabilities()\n"
                "print([name for name in sys.modules if name.split('.')[0] == 'bilibili_api'])\n")
        with tempfile.TemporaryDirectory() as folder:
            done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=dict(os.environ, VPUB_HOME=folder))
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("[]", done.stdout.strip())


class SubprocessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        # A real bili-cli process, pointed at an empty folder, so a real sign-in is never read.
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "vpub"), "BILI_CLI_HOME": str(self.root / "bili")})
        env.start()
        self.addCleanup(env.stop)

    def test_a_real_command_answers_through_crosscast(self):
        with self.assertRaises(VpubError) as e:
            adapters.bili(["auth", "status"])
        self.assertEqual("not_logged_in", e.exception.code)

    def test_auth_status_reports_that_through_the_public_route(self):
        with self.assertRaises(VpubError) as e:
            adapters.auth_status("bilibili", "default", "cli")
        self.assertEqual("not_logged_in", e.exception.code)

    def test_a_timeout_never_looks_like_a_crash(self):
        with patch.object(adapters.subprocess, "run", side_effect=subprocess.TimeoutExpired("bili", 1)):
            with self.assertRaises(VpubError) as e:
                adapters.bili(["auth", "status"])
        self.assertEqual("backend_timeout", e.exception.code)


class DirectMessageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": str(Path(temp.name) / "vpub")})
        env.start()
        self.addCleanup(env.stop)
        self.receipt = Path(temp.name) / "receipt.json"
        atomic_json(self.receipt, {"state": "DM_READY"})
        status = patch.object(adapters, "auth_status", return_value={"identity": "1"})
        status.start()
        self.addCleanup(status.stop)

    def test_sent_message_keeps_its_id_from_the_new_output(self):
        with patch.object(adapters, "bili", return_value={"sent": True, "msg_keys": ["key-1"]}):
            result = direct_messages_bili.execute("send", "1", recipient="2", text="Hi", receipt_path=str(self.receipt))
        saved = read_json(self.receipt)
        self.assertEqual("key-1", saved["message_id"])
        self.assertTrue(saved["send_acknowledged"])
        self.assertTrue(result["send_acknowledged"])

    def test_bilibili_auto_reply_is_not_mistaken_for_the_sent_message(self):
        receipt = {"before": {"items": []}, "request": {"text": "Thanks"}}
        rows = [{"from": "me", "text": "Thanks", "seqno": 5, "auto_reply": True}]
        with patch.object(adapters, "bili", return_value={"messages": rows}):
            result = direct_messages_bili.execute("verify", "1", recipient="2", receipt=receipt)
        self.assertFalse(result["matching_outgoing_message"])
        rows[0].pop("auto_reply")
        with patch.object(adapters, "bili", return_value={"messages": rows}):
            result = direct_messages_bili.execute("verify", "1", recipient="2", receipt=receipt)
        self.assertTrue(result["matching_outgoing_message"])


class DoctorTests(unittest.TestCase):
    def test_a_leftover_separate_install_is_named_with_its_fix(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"VPUB_HOME": folder}), \
                patch.object(cli, "separate_bili_agent", return_value="0.7.0"):
            result = cli.doctor()
        issue = next(item for item in result["issues"] if item["code"] == "separate_bili_agent")
        self.assertIn("0.7.0", issue["message"])
        self.assertIn("pip uninstall -y bili-agent", issue["fix"])
        self.assertEqual("1.2.1", result["bili_cli_version"])


if __name__ == "__main__":
    unittest.main()

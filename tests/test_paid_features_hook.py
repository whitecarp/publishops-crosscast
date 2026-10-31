"""Without the add-on, its features say so clearly and everything else keeps working."""
import ast
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from video_publisher import adapters, cli

ROOT = Path(__file__).resolve().parents[1]


class PaidFeatureTests(unittest.TestCase):
    def setUp(self):
        for active in (patch.object(adapters.importlib.util, "find_spec", return_value=None), patch.dict(sys.modules, {"crosscast_api": None})):
            active.start()
            self.addCleanup(active.stop)

    def run_cli(self, *argv):
        return cli.run_command(cli.parser().parse_args(list(argv)))

    def test_each_paid_command_says_it_needs_the_add_on(self):
        for argv in (["report"], ["read-all", "status", "a" * 32], ["notify", "status"], ["notify", "pending"]):
            with self.subTest(argv=argv):
                with self.assertRaises(adapters.VpubError) as caught:
                    self.run_cli(*argv)
                self.assertEqual("paid_feature_required", caught.exception.code)
                self.assertIn("paid CrossCast add-on", caught.exception.message)

    def test_a_person_needing_to_act_is_still_told_without_email(self):
        self.assertEqual({"state": "NOT_INSTALLED", "email_sent": False, "inline_notice_required": True}, adapters.email_reminder("key", "x", 1))
        self.assertFalse(adapters.email_status()["available"])

    def test_a_broken_dependency_is_not_mistaken_for_a_missing_add_on(self):
        error = ModuleNotFoundError("No module named 'requests'", name="requests")
        with patch.object(adapters.importlib, "import_module", side_effect=error):
            with self.assertRaises(ModuleNotFoundError):
                adapters.paid_feature("report", "The operations report")

    def test_the_free_core_contains_none_of_the_paid_code(self):
        for name in ("notifications", "full_read", "full_read_browser"):
            self.assertFalse((ROOT / "src/video_publisher" / (name + ".py")).exists(), name)
        functions = {node.name for node in ast.walk(ast.parse((ROOT / "src/video_publisher/operations.py").read_text(encoding="utf-8"))) if isinstance(node, ast.FunctionDef)}
        self.assertNotIn("report", functions)

    def test_the_capabilities_name_what_the_add_on_adds(self):
        self.assertIn("vpub report", cli.capabilities()["paid_api_addon"]["covers"])


if __name__ == "__main__":
    unittest.main()

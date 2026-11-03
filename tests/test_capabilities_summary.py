"""The short CLI overview must preserve access to the complete route contract."""
import json
import unittest
from unittest.mock import patch

from video_publisher import adapters, cli


class CapabilitiesSummaryTests(unittest.TestCase):
    def test_default_is_small_and_does_not_build_the_full_record(self):
        with patch.object(adapters, "addon_installed", return_value=False), \
             patch.object(cli, "capabilities", side_effect=AssertionError("full record loaded")):
            result = cli.execute(cli.parser().parse_args(["capabilities"]))
        self.assertEqual(result["detail"], "summary")
        self.assertLess(len(json.dumps({"ok": True, "data": result}, ensure_ascii=False).encode("utf-8")), 3000)
        self.assertEqual(set(result["platforms"]), set(cli.PLATFORMS))
        self.assertEqual(result["details"], "vpub capabilities --full")
        self.assertIn("not every operation has a live test", " ".join(result["limits"]))

    def test_full_preserves_existing_capabilities_record(self):
        with patch.object(adapters, "addon_installed", return_value=False):
            expected = cli.capabilities()
            actual = cli.execute(cli.parser().parse_args(["capabilities", "--full"]))
        self.assertEqual(actual, expected)
        self.assertIn("youtube_studio_operations", actual)
        self.assertIsInstance(actual["operations"], dict)

    def test_installed_routes_follow_addon_presence(self):
        for installed in (False, True):
            with self.subTest(installed=installed), patch.object(adapters, "addon_installed", return_value=installed):
                result = cli.capabilities_summary()
                self.assertEqual(result["paid_api_addon"]["installed"], installed)
                self.assertEqual(result["platforms"]["youtube"]["default_method"], "api" if installed else "browser")
                self.assertEqual(result["platforms"]["bilibili"]["default_method"], "cli")
                self.assertEqual(result["platforms"]["x"]["default_method"], "browser")
                self.assertEqual(result["platforms"]["instagram"]["default_method"], "browser")
                self.assertIn("Media Studio", result["platforms"]["x"]["scope"])
                for platform, route in result["platforms"].items():
                    self.assertEqual(route["live_upload_verified"], cli.platform_routes(platform)["live_upload_verified"])

    def test_overview_is_local_and_next_commands_parse(self):
        with patch.object(adapters, "addon_installed", return_value=False), \
             patch.object(adapters, "auth_status", side_effect=AssertionError("account read")), \
             patch.object(adapters, "bili", side_effect=AssertionError("bili account read")):
            result = cli.capabilities_summary()
        self.assertEqual(cli.parser().parse_args(result["next"][0].split()[1:]).command, "doctor")
        self.assertIn("BILI_CLI_HOME", result["state_dirs"])
        self.assertIn("commenter's language only", result["language"])


if __name__ == "__main__":
    unittest.main()

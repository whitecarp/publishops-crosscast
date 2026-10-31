"""Without the paid add-on, API routes for X and Instagram stop before any platform request."""
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

from video_publisher import adapters, cli
from video_publisher.core import VpubError


class PaidHookTests(unittest.TestCase):
    def test_missing_add_on_gives_a_clear_error_on_every_entry_point(self):
        with patch.dict(sys.modules, {"crosscast_api": None}):
            for platform in ("x", "instagram"):
                for call in (lambda: adapters.auth_status(platform, "default", "api"),
                             lambda: adapters.auth_login(platform, "default", None, "api"),
                             lambda: adapters.publish(platform, {"video": "v"}, {"method": "api", "account": "default"}, lambda r: None),
                             lambda: adapters.verify(platform, "default", {"id": "1", "method": "api"})):
                    with self.assertRaises(VpubError) as e:
                        call()
                    self.assertEqual("paid_route_required", e.exception.code)

    def test_free_routes_stay_the_default(self):
        self.assertEqual("browser", adapters.default_method("x"))
        self.assertEqual("browser", adapters.default_method("instagram"))


    def test_youtube_is_free_in_the_browser_and_paid_on_the_api(self):
        with patch.object(adapters.importlib.util, "find_spec", return_value=None), patch.dict(sys.modules, {"crosscast_api": None}):
            self.assertEqual("browser", adapters.default_method("youtube"))
            for call in (lambda: adapters.yt_credentials("default"), lambda: adapters.auth_login("youtube", "default", "client.json", "api")):
                with self.assertRaises(VpubError) as e:
                    call()
                self.assertEqual("paid_route_required", e.exception.code)
        with patch.object(adapters.importlib.util, "find_spec", return_value=object()):
            self.assertEqual("api", adapters.default_method("youtube"))

    def test_capabilities_follow_what_is_installed(self):
        with patch.object(adapters.importlib.util, "find_spec", return_value=None):
            free = cli.capabilities()
        self.assertFalse(free["paid_api_addon"]["installed"])
        self.assertTrue(free["platforms"]["youtube"]["standalone_cli_publish"])
        self.assertEqual("browser", free["platforms"]["youtube"]["default_method"])
        self.assertEqual("dedicated persistent background browser", free["platforms"]["youtube"]["publish_backend"])
        self.assertTrue(free["platforms"]["x"]["standalone_cli_publish"])
        self.assertNotIn("X/Instagram official API", free["not_implemented"])
        with patch.object(adapters.importlib.util, "find_spec", return_value=object()):
            paid = cli.capabilities()
        self.assertTrue(paid["paid_api_addon"]["installed"])
        self.assertTrue(paid["platforms"]["youtube"]["standalone_cli_publish"])
        self.assertEqual("api", paid["platforms"]["youtube"]["default_method"])

    def test_the_public_core_contains_no_official_api_code(self):
        # Anything that talks to an official API is the paid add-on's. The core only has the hook above.
        root = Path(adapters.__file__).parent
        calls = re.compile(r"googleapiclient|google_auth_oauthlib|googleapis\.com|api\.x\.com|graph\.facebook\.com|graph\.instagram\.com|rupload|media_publish")
        found = [path.name for path in root.glob("*.py") if calls.search(path.read_text(encoding="utf-8"))]
        self.assertEqual([], found)
        project = (root.parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        self.assertNotIn("google-api-python-client", project)


if __name__ == "__main__":
    unittest.main()

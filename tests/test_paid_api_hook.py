"""Without the paid add-on, API routes for X and Instagram stop before any platform request."""
import sys
import unittest
from unittest.mock import patch

from video_publisher import adapters
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


    def test_youtube_is_free_on_studio_and_paid_on_the_api(self):
        with patch.object(adapters.importlib.util, "find_spec", return_value=None), patch.dict(sys.modules, {"crosscast_api": None}):
            self.assertEqual("native", adapters.default_method("youtube"))
            for call in (lambda: adapters.yt_credentials("default"), lambda: adapters.auth_login("youtube", "default", "client.json", "api")):
                with self.assertRaises(VpubError) as e:
                    call()
                self.assertEqual("paid_route_required", e.exception.code)
        with patch.object(adapters.importlib.util, "find_spec", return_value=object()):
            self.assertEqual("api", adapters.default_method("youtube"))


if __name__ == "__main__":
    unittest.main()

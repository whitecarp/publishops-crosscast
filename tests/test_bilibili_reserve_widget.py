"""The user asked for Bilibili's reservation widget: allowed, but sent only once the person has seen the dry run and said yes."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_publisher import widgets_bilibili as widgets
from video_publisher.core import VpubError

WORK = "BV1xx411c7mD"
NEXT = {"type": "reserve", "title": "下期更精彩敬请期待", "time": 12}


class ReserveWidgetTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        homes = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "vpub"), "BILI_CLI_HOME": str(self.root / "bili")})
        homes.start()
        self.addCleanup(homes.stop)

    def file(self, items):
        path = self.root / "widgets.json"
        path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        return path

    def test_reserve_is_checked_and_built_as_bili_cli_expects(self):
        self.assertEqual(["danmaku", "interact", "reserve", WORK, "--title", "下期更精彩敬请期待", "--time", "12", "--page", "1"],
                         widgets.command(WORK, widgets.clean(NEXT)))
        live = widgets.clean({"type": "reserve", "title": "周五晚上直播", "time": 3, "live": "2026-11-20T20:00"})
        self.assertEqual(["--live", "2026-11-20T20:00"], widgets.command(WORK, live)[6:8])

    def test_short_titles_bad_live_times_and_other_fields_are_refused(self):
        for widget in ({**NEXT, "title": "下期"}, {**NEXT, "live": "Friday night"}, {**NEXT, "skin": "star"}):
            with self.subTest(widget=widget), self.assertRaises(VpubError):
                widgets.clean(widget)

    def test_without_the_persons_yes_a_reserve_sends_nothing(self):
        with patch.object(widgets, "checked_work"), patch.object(widgets, "send", side_effect=AssertionError("sent")):
            with self.assertRaises(VpubError) as caught:
                widgets.add(WORK, self.file([NEXT]))
        self.assertEqual("needs_person", caught.exception.code)

    def test_the_dry_run_shows_a_reserve_without_approval(self):
        with patch.object(widgets, "checked_work"), patch.object(widgets, "read", return_value=[]), \
                patch.object(widgets.adapters, "bili", return_value={"dry_run": True}) as bili:
            result = widgets.add(WORK, self.file([NEXT]), dry_run=True)
        self.assertEqual(("DRY_RUN", 0), (result["state"], result["platform_writes"]))
        self.assertEqual("reserve", bili.call_args.args[0][2])

    def test_with_the_persons_yes_the_reserve_goes_to_send(self):
        with patch.object(widgets, "checked_work"), patch.object(widgets, "send", return_value=([], {"state": "SENT"})) as send:
            result = widgets.add(WORK, self.file([NEXT]), person_approved=True)
        send.assert_called_once()
        self.assertEqual("SENT", result["state"])


if __name__ == "__main__":
    unittest.main()

"""A field test's dry run of an already published file said nothing; the real run skips such a platform, and the dry run now says which."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_publisher import rehearsal
from video_publisher.core import Ledger

SHA = "a" * 64
PLAN = {"video_sha256": SHA, "platforms": {"bilibili": {"account": "default"}, "youtube": {"account": "default"}}}


class WillSkipTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name) / "vpub"
        self.home.mkdir()
        homes = patch.dict(os.environ, {"VPUB_HOME": str(self.home), "BILI_CLI_HOME": str(Path(folder.name) / "bili")})
        homes.start()
        self.addCleanup(homes.stop)

    def test_no_record_file_means_nothing_to_skip_and_none_is_made(self):
        self.assertEqual([], rehearsal.will_skip(PLAN))
        self.assertFalse((self.home / "publications.sqlite3").exists())

    def test_a_published_file_is_named_and_a_preview_is_not(self):
        ledger = Ledger()
        try:
            for platform, state, data in (("bilibili", "SUBMISSION_VERIFIED", {"receipt": {"state": "SUBMISSION_VERIFIED"}}),
                                          ("youtube", "PREVIEW_READY", {"receipt": {"final_submit_attempted": False}})):
                ledger.claim(platform, "default", SHA, {})
                ledger.save(Ledger.key(platform, "default", SHA), state, data)
        finally:
            ledger.close()
        found = rehearsal.will_skip(PLAN)
        self.assertEqual([("bilibili", "SUBMISSION_VERIFIED")], [(row["platform"], row["previous_state"]) for row in found])


if __name__ == "__main__":
    unittest.main()

"""A field test found playlist and collection deletes went through in one step; they now ask for a dry run, its code and the person's yes."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from video_publisher import cli, collections
from video_publisher.core import VpubError


class ContainerDeleteGuardTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        homes = patch.dict(os.environ, {"VPUB_HOME": str(Path(folder.name) / "vpub"), "BILI_CLI_HOME": str(Path(folder.name) / "bili")})
        homes.start()
        self.addCleanup(homes.stop)

    def run_cli(self, *words):
        return cli.execute(cli.parser().parse_args(list(words)))

    def test_collection_delete_needs_its_own_code_and_the_persons_yes(self):
        base = ["collection", "delete", "douyin", "C1", "--request-id", "r1"]
        with patch.object(collections, "change", return_value={"state": "CONFIRMED"}) as change:
            preview = self.run_cli(*base, "--dry-run")
            self.assertEqual(("DRY_RUN", 0, ["douyin", "C1"]), (preview["state"], preview["platform_writes"], preview["target"]))
            for extra in ([], ["--person-approved"], ["--confirm", preview["confirm"]], ["--confirm", "0" * 12, "--person-approved"]):
                with self.subTest(extra=extra), self.assertRaises(VpubError) as caught:
                    self.run_cli(*base, *extra)
                self.assertEqual("needs_person", caught.exception.code)
            change.assert_not_called()
            self.assertEqual("CONFIRMED", self.run_cli(*base, "--confirm", preview["confirm"], "--person-approved")["state"])
        change.assert_called_once()

    def test_a_code_belongs_to_one_exact_delete(self):
        with patch.object(collections, "change"):
            first = self.run_cli("collection", "delete", "douyin", "C1", "--request-id", "r1", "--dry-run")["confirm"]
            other = self.run_cli("collection", "delete", "douyin", "C2", "--request-id", "r1", "--dry-run")["confirm"]
        self.assertNotEqual(first, other)

    def test_playlist_delete_needs_its_own_code_and_the_persons_yes(self):
        playlists = MagicMock()
        playlists.change.return_value = {"state": "CONFIRMED"}
        with patch.object(cli.adapters, "youtube_playlists", return_value=playlists):
            preview = self.run_cli("playlist", "delete", "PL1", "--request-id", "r1", "--dry-run")
            with self.assertRaises(VpubError):
                self.run_cli("playlist", "delete", "PL1", "--request-id", "r1")
            playlists.change.assert_not_called()
            self.run_cli("playlist", "delete", "PL1", "--request-id", "r1", "--confirm", preview["confirm"], "--person-approved")
        playlists.change.assert_called_once()


if __name__ == "__main__":
    unittest.main()

"""Field findings: a test build reported the release's version, and Instagram's missing comment controls read like a CrossCast gap."""
import json
import unittest
from unittest.mock import MagicMock, patch

from video_publisher import cli, comment_actions
from video_publisher.core import VpubError


def distribution(record):
    found = MagicMock()
    found.read_text.return_value = None if record is None else json.dumps(record)
    return found


class InstalledFromTests(unittest.TestCase):
    def source(self, record):
        with patch("importlib.metadata.distribution", return_value=distribution(record)):
            return cli.installed_from()

    def test_an_archive_or_git_install_names_its_source_and_commit(self):
        url = "https://github.com/whitecarp/publishops-crosscast/archive/0a327c0ec9ca85903adaff80f1e7b1253d093e0f.zip"
        self.assertEqual({"source": url}, self.source({"url": url, "archive_info": {}}))
        git = "https://github.com/whitecarp/publishops-crosscast.git"
        self.assertEqual({"source": git, "commit": "abc123"}, self.source({"url": git, "vcs_info": {"vcs": "git", "commit_id": "abc123"}}))

    def test_a_local_folder_is_not_named_and_a_plain_install_says_nothing(self):
        self.assertEqual({"source": "a local folder"}, self.source({"url": "file:///C:/Users/someone/crosscast", "dir_info": {"editable": True}}))
        self.assertIsNone(self.source(None))
        with patch("importlib.metadata.distribution", side_effect=Exception("not installed")):
            self.assertIsNone(cli.installed_from())


class InstagramCommentControlsTests(unittest.TestCase):
    def test_missing_controls_are_named_as_the_websites_not_crosscasts(self):
        for kind in ("pin", "hide", "remove"):
            with self.subTest(kind=kind), self.assertRaises(VpubError) as caught:
                comment_actions.mutate("instagram", "W1", "C1", kind)
            self.assertEqual("unsupported_operation", caught.exception.code)
            self.assertIn("Instagram's website has no", caught.exception.message)


if __name__ == "__main__":
    unittest.main()

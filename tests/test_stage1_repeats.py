"""A message caught between two attempts. Fakes only; nothing is contacted."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import direct_messages as dm, safety
from video_publisher.core import VpubError, atomic_json, home, read_json

OWNER = "owner"

class SecondAttemptTests(unittest.TestCase):
    """A command killed part way can go on running in the shared background browser, and a second attempt starts behind it."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": str(Path(temp.name) / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        atomic_json(home() / "accounts/default/x-identity.json", {"identity": OWNER})
        self.sent = []
        self.running = None

    def worker(self, platform, operation, expected, **options):
        if operation == "send":
            dm.submitting(options["receipt_path"])
            self.sent.append(options["text"])
            saved = read_json(options["receipt_path"])
            saved.update(state="DM_UNVERIFIED", platform_writes=1)
            atomic_json(options["receipt_path"], saved)
            return {"state": "DM_UNVERIFIED", "platform_writes": 1}
        return {"identity": OWNER, "recipient": "viewer", "can_send": True, "items": [], "matching_outgoing_message": operation == "verify"}

    def killed_while_running(self, platform, operation, expected, **options):
        if operation == "send":
            self.running = options
            raise KeyboardInterrupt
        return self.worker(platform, operation, expected, **options)

    def test_the_second_attempt_closes_the_first_so_only_one_message_goes(self):
        with patch.object(dm, "dispatch", side_effect=self.killed_while_running), self.assertRaises(KeyboardInterrupt):
            dm.send("x", "viewer", "Hello", "en")
        first = Path(self.running["receipt_path"])
        self.assertEqual("DM_READY", read_json(first)["state"])

        def second(platform, operation, expected, **options):
            if operation == "read":
                # The session finishes the first command before this one's turn comes.
                with self.assertRaises(VpubError) as caught:
                    self.worker(platform, "send", expected, **self.running)
                self.assertEqual("attempt_replaced", caught.exception.code)
            return self.worker(platform, operation, expected, **options)
        with patch.object(dm, "dispatch", side_effect=second):
            result = dm.send("x", "viewer", "Hello", "en")
        self.assertEqual("DM_VISIBLE", result["state"])
        self.assertEqual(["Hello (AI reply)"], self.sent)
        self.assertEqual(1, safety.status()["used_today"]["x"]["messages_per_day"])

    def test_a_first_attempt_that_really_died_does_not_block_the_next(self):
        with patch.object(dm, "dispatch", side_effect=self.killed_while_running), self.assertRaises(KeyboardInterrupt):
            dm.send("x", "viewer", "Hello", "en")
        with patch.object(dm, "dispatch", side_effect=self.worker):
            self.assertEqual("DM_VISIBLE", dm.send("x", "viewer", "Hello", "en")["state"])
        self.assertEqual(1, len(self.sent))


if __name__ == "__main__":
    unittest.main()

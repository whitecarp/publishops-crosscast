"""A message caught between two attempts. Fakes only; nothing is contacted."""
import os
from pathlib import Path
import tempfile
import threading
import time
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

    def killed_attempt(self):
        with patch.object(dm, "dispatch", side_effect=self.killed_while_running), self.assertRaises(KeyboardInterrupt):
            dm.send("x", "viewer", "Hello", "en")
        return Path(self.running["receipt_path"])

    def test_a_second_attempt_arriving_during_the_press_leaves_it_alone(self):
        """From a review: the old command had read its receipt and was about to mark it, when a second attempt closed it. Then both went on to send."""
        first = self.killed_attempt()
        answers = []
        second = threading.Thread(target=lambda: answers.append(dm.send("x", "viewer", "Hello", "en")))
        read = dm.read_json

        def during_the_press(path):
            saved = read(path)
            if Path(path) == first and not second.ident:
                second.start()
                time.sleep(0.5)  # It is now at this receipt's door.
            return saved
        with patch.object(dm, "dispatch", side_effect=self.worker), patch.object(dm, "read_json", side_effect=during_the_press):
            self.worker("x", "send", OWNER, **self.running)
            second.join(30)
        self.assertTrue(answers[0]["reused"])
        self.assertEqual(0, answers[0]["platform_writes"])
        self.assertEqual(["Hello (AI reply)"], self.sent)
        self.assertEqual("DM_UNVERIFIED", read_json(first)["state"])
        self.assertEqual(1, safety.status()["used_today"]["x"]["messages_per_day"])

    def test_a_press_arriving_during_a_second_attempts_look_sends_nothing(self):
        first = self.killed_attempt()
        refused = []

        def old_command():
            try:
                self.worker("x", "send", OWNER, **self.running)
            except VpubError as caught:
                refused.append(caught.code)
        old = threading.Thread(target=old_command)
        reclaim = safety.reclaim

        def during_the_look(path, saved):
            old.start()
            time.sleep(0.5)  # It is now at this receipt's door.
            return reclaim(path, saved)
        with patch.object(dm, "dispatch", side_effect=self.worker), patch.object(safety, "reclaim", side_effect=during_the_look):
            result = dm.send("x", "viewer", "Hello", "en")
        old.join(30)
        self.assertEqual(["attempt_replaced"], refused)
        self.assertEqual("DM_REPLACED", read_json(first)["state"])
        self.assertEqual("DM_VISIBLE", result["state"])
        self.assertEqual(["Hello (AI reply)"], self.sent)

    def test_a_press_after_its_command_reported_failure_sends_nothing(self):
        """A command can stop waiting and say nothing went while its work is still running in the shared browser."""
        def gave_up(platform, operation, expected, **options):
            if operation == "send":
                self.running = options
                raise TimeoutError
            return self.worker(platform, operation, expected, **options)
        with patch.object(dm, "dispatch", side_effect=gave_up):
            self.assertEqual("DM_FAILED", dm.send("x", "viewer", "Hello", "en")["state"])
        with self.assertRaises(VpubError) as caught:
            self.worker("x", "send", OWNER, **self.running)
        self.assertEqual("attempt_replaced", caught.exception.code)
        self.assertEqual([], self.sent)

    def test_a_check_that_queued_behind_the_press_keeps_what_the_press_did(self):
        first = self.killed_attempt()

        def behind_the_press(platform, operation, expected, **options):
            if operation == "verify":
                self.worker(platform, "send", expected, **self.running)
                return {"identity": OWNER, "recipient": "viewer", "matching_outgoing_message": False}
            return self.worker(platform, operation, expected, **options)
        with patch.object(dm, "dispatch", side_effect=behind_the_press):
            self.assertEqual("DM_UNVERIFIED", dm.verify(first.stem)["state"])
        self.assertEqual("DM_UNVERIFIED", read_json(first)["state"])
        self.assertEqual(1, read_json(first)["platform_writes"])

    def test_a_first_attempt_that_really_died_does_not_block_the_next(self):
        with patch.object(dm, "dispatch", side_effect=self.killed_while_running), self.assertRaises(KeyboardInterrupt):
            dm.send("x", "viewer", "Hello", "en")
        with patch.object(dm, "dispatch", side_effect=self.worker):
            self.assertEqual("DM_VISIBLE", dm.send("x", "viewer", "Hello", "en")["state"])
        self.assertEqual(1, len(self.sent))


if __name__ == "__main__":
    unittest.main()

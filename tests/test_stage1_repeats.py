"""A message caught between two attempts. Fakes only; nothing is contacted."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from video_publisher import comment_actions, direct_messages as dm, engagement, safety
from video_publisher.core import VpubError, atomic_json, digest, home, read_json

OWNER = "owner"

class FileSharingTests(unittest.TestCase):
    """From a CI run: a read at the moment another step replaced the same receipt stopped with PermissionError. Windows refuses for a moment in both directions."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "receipt.json"

    @unittest.skipUnless(os.name == "nt", "Windows file sharing")
    def test_a_refusal_for_a_moment_is_waited_out(self):
        atomic_json(self.path, {"state": "ready"})
        refusals = [PermissionError(13, "in use"), PermissionError(13, "in use")]
        real = Path.read_text

        def read_text(path, *args, **options):
            if refusals:
                raise refusals.pop()
            return real(path, *args, **options)
        with patch.object(Path, "read_text", read_text):
            self.assertEqual({"state": "ready"}, read_json(self.path))
        real_replace = os.replace
        refusals = [PermissionError(13, "in use")]

        def replace(source, target):
            if refusals:
                raise refusals.pop()
            return real_replace(source, target)
        with patch("video_publisher.core.os.replace", replace):
            atomic_json(self.path, {"state": "moved on"})
        self.assertEqual({"state": "moved on"}, read_json(self.path))

    @unittest.skipUnless(os.name == "nt", "Windows file sharing")
    def test_a_refusal_that_lasts_still_says_so(self):
        atomic_json(self.path, {"state": "ready"})
        with patch.object(Path, "read_text", side_effect=PermissionError(13, "in use")), patch("video_publisher.core.time.sleep"), self.assertRaises(PermissionError):
            read_json(self.path)

    def test_reading_while_another_thread_replaces_the_file_never_stops(self):
        atomic_json(self.path, {"count": 0})
        errors, done = [], threading.Event()

        def write():
            try:
                for count in range(1, 100):
                    atomic_json(self.path, {"count": count})
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()

        def keep_reading():
            try:
                while not done.is_set():
                    self.assertIn("count", read_json(self.path))
                    time.sleep(0.002)  # Tight loops with no pause can hold the file open almost all the time, which nothing here does.
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=write), threading.Thread(target=keep_reading), threading.Thread(target=keep_reading)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual([], errors)
        self.assertEqual({"count": 99}, read_json(self.path))


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


class OtherPressesTests(unittest.TestCase):
    """A like counts towards the daily caps too, so one that goes out after its count went back would never be seen by them."""
    WORK = "1234567890"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": str(Path(temp.name) / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        atomic_json(home() / "operations/comments" / (digest(["x", self.WORK]) + ".json"), {"identity": OWNER, "items": [{"id": "111", "text": "Nice", "author": "V", "author_id": "viewer", "is_owner": False}]})
        self.pressed = []

    def counted(self):
        return safety.status()["used_today"]["x"]["likes_per_day"]

    def like_status(self, platform, action, work=None, **options):
        return {"identity": OWNER, "liked": bool(self.pressed), "saved": False, "collection": None}

    def like_press(self, platform, action, work=None, **options):
        if action != "set":
            return self.like_status(platform, action, work, **options)
        engagement.submitting(options["receipt_path"])
        self.pressed.append(options["receipt_path"])
        return {"state": "ACTION_SUBMITTING"}

    def like_interrupted(self, stop):
        taken = {}

        def stopped(platform, action, work=None, **options):
            if action != "set":
                return self.like_status(platform, action, work, **options)
            taken.update(options)
            raise stop
        with patch.object(engagement, "dispatch", side_effect=stopped):
            try:
                engagement.set_action("x", self.WORK, "like")
            except KeyboardInterrupt:
                pass
        return taken["receipt_path"]

    def comment_status(self, request, operation="status", receipt_path=None):
        return {"identity": OWNER, "text": "Nice", "liked": bool(self.pressed), "deleted": False}

    def comment_press(self, request, operation="status", receipt_path=None):
        if operation != "set":
            return self.comment_status(request)
        comment_actions.submitting(receipt_path)
        self.pressed.append(receipt_path)
        return {"state": "COMMENT_SUBMITTING"}

    def comment_interrupted(self, stop):
        taken = []

        def stopped(request, operation="status", receipt_path=None):
            if operation != "set":
                return self.comment_status(request)
            taken.append(receipt_path)
            raise stop
        with patch.object(comment_actions, "dispatch", side_effect=stopped):
            try:
                comment_actions.mutate("x", self.WORK, "111", "like")
            except KeyboardInterrupt:
                pass
        return taken[0]

    def test_a_like_after_its_command_gave_up_presses_nothing_because_its_count_went_back(self):
        path = self.like_interrupted(TimeoutError())
        self.assertEqual("ACTION_FAILED", read_json(path)["state"])
        self.assertEqual(0, self.counted())
        with self.assertRaises(VpubError) as caught:
            engagement.submitting(path)
        self.assertEqual("attempt_replaced", caught.exception.code)

    def test_a_new_like_attempt_closes_a_killed_one_so_only_one_counts_and_goes(self):
        path = self.like_interrupted(KeyboardInterrupt())
        self.assertEqual("ACTION_READY", read_json(path)["state"])
        with patch.object(engagement, "dispatch", side_effect=self.like_press):
            self.assertEqual("ACTION_VERIFIED", engagement.set_action("x", self.WORK, "like")["state"])
        self.assertEqual("ACTION_REPLACED", read_json(path)["state"])
        with self.assertRaises(VpubError) as caught:
            engagement.submitting(path)
        self.assertEqual("attempt_replaced", caught.exception.code)
        self.assertEqual(1, len(self.pressed))
        self.assertEqual(1, self.counted())

    def test_a_comment_like_after_its_command_gave_up_presses_nothing_because_its_count_went_back(self):
        path = self.comment_interrupted(TimeoutError())
        self.assertEqual("COMMENT_FAILED", read_json(path)["state"])
        self.assertEqual(0, self.counted())
        with self.assertRaises(VpubError) as caught:
            comment_actions.submitting(path)
        self.assertEqual("attempt_replaced", caught.exception.code)

    def test_a_new_comment_like_attempt_closes_a_killed_one_so_only_one_counts_and_goes(self):
        path = self.comment_interrupted(KeyboardInterrupt())
        self.assertEqual("COMMENT_READY", read_json(path)["state"])
        with patch.object(comment_actions, "dispatch", side_effect=self.comment_press):
            self.assertEqual("COMMENT_VERIFIED", comment_actions.mutate("x", self.WORK, "111", "like")["state"])
        self.assertEqual("COMMENT_REPLACED", read_json(path)["state"])
        with self.assertRaises(VpubError) as caught:
            comment_actions.submitting(path)
        self.assertEqual("attempt_replaced", caught.exception.code)
        self.assertEqual(1, len(self.pressed))
        self.assertEqual(1, self.counted())

    def test_a_like_check_that_queued_behind_the_press_keeps_what_the_press_did(self):
        """From a review: the check put the receipt back to waiting, so the next command gave the count back for a like that had gone out."""
        path = self.like_interrupted(KeyboardInterrupt())

        def behind_the_press(platform, action, work=None, **options):
            if not self.pressed:
                self.like_press(platform, "set", work, receipt_path=path)
            return self.like_status(platform, action, work, **options)
        with patch.object(engagement, "dispatch", side_effect=behind_the_press):
            self.assertEqual("ACTION_VERIFIED", engagement.verify(Path(path).stem)["state"])
        self.assertEqual("ACTION_VERIFIED", read_json(path)["state"])
        with patch.object(engagement, "dispatch", side_effect=self.like_status):
            self.assertEqual("NO_CHANGE", engagement.set_action("x", self.WORK, "like")["state"])
        self.assertEqual(1, self.counted())

    def test_a_comment_like_check_that_queued_behind_the_press_keeps_what_the_press_did(self):
        path = self.comment_interrupted(KeyboardInterrupt())

        def behind_the_press(request, operation="status", receipt_path=None):
            if not self.pressed:
                self.comment_press(request, "set", path)
            return self.comment_status(request)
        with patch.object(comment_actions, "dispatch", side_effect=behind_the_press):
            self.assertEqual("COMMENT_VERIFIED", comment_actions.verify(Path(path).stem)["state"])
        self.assertEqual("COMMENT_VERIFIED", read_json(path)["state"])
        with patch.object(comment_actions, "dispatch", side_effect=self.comment_status):
            self.assertEqual("NO_CHANGE", comment_actions.mutate("x", self.WORK, "111", "like")["state"])
        self.assertEqual(1, self.counted())

    def test_a_retry_that_queued_behind_a_finished_press_reads_it_and_presses_nothing(self):
        path = self.comment_interrupted(TimeoutError())
        saved = read_json(path)
        saved.update(state="COMMENT_UNVERIFIED")
        atomic_json(path, saved)

        def finished_meanwhile(request, operation="status", receipt_path=None):
            done = read_json(path)
            done.update(state="COMMENT_VERIFIED")
            atomic_json(path, done)
            return self.comment_status(request)
        with patch.object(comment_actions, "dispatch", side_effect=finished_meanwhile), self.assertRaises(VpubError) as caught:
            comment_actions.resume(Path(path).stem)
        self.assertEqual("resume_unavailable", caught.exception.code)
        self.assertEqual([], self.pressed)

    def test_only_a_press_still_waiting_gets_through(self):
        receipt = home() / "receipt.json"
        for module, ready in ((engagement, "ACTION_READY"), (comment_actions, "COMMENT_READY")):
            for state in (ready.replace("READY", "SUBMITTING"), ready.replace("READY", "UNVERIFIED"), ready.replace("READY", "FAILED"), ready.replace("READY", "REPLACED"), None):
                with self.subTest(module=module.__name__, state=state):
                    atomic_json(receipt, {"state": state})
                    with self.assertRaises(VpubError) as caught:
                        module.submitting(receipt)
                    self.assertEqual("attempt_replaced", caught.exception.code)


if __name__ == "__main__":
    unittest.main()

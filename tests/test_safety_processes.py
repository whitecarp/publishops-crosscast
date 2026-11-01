"""The limits under real conditions: restarts, overlapping scheduled runs and crashes.

Each run below is its own Python process, as a scheduled agent's would be. The fake platform is a file that gains a line
for everything that would have reached a real site, so a double send shows up as an extra line. Nothing is contacted.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from video_publisher import safety
from video_publisher.core import atomic_json, digest, home

WORK = "abcdefghijk"
POST = "1234567890"
RUN = r'''
import json, os, sys, time
from unittest.mock import patch
from video_publisher import adapters, comment_actions, direct_messages as dm, engagement, operations as ops
from video_publisher.core import VpubError, atomic_json, read_json

job = json.loads(sys.argv[1])
FAKE = os.environ["FAKE_PLATFORM"]


def reached():
    return open(FAKE, encoding="utf-8").read().splitlines() if os.path.exists(FAKE) else []


def click(label, receipt, state):
    """What a real worker does: mark the receipt, then press the button."""
    if job.get("crash") == "before":
        os._exit(9)
    saved = read_json(receipt)
    saved["state"] = state
    atomic_json(receipt, saved)
    with open(FAKE, "a", encoding="utf-8") as out:
        out.write(label + "\n")
    if job.get("crash") == "after":
        os._exit(9)
    time.sleep(job.get("hold", 0))


def reply_backend(platform, action, work, reply_request, reply_receipt_path):
    click(job["comment"], reply_receipt_path, "REPLY_SUBMITTING")
    return {"state": "REPLY_VERIFIED", "platform_writes": 1}


def message_worker(platform, operation, expected, **options):
    if operation == "send":
        click("message", options["receipt_path"], "DM_SUBMITTING")
        return {"state": "DM_SUBMITTING"}
    return {"identity": expected, "recipient": "viewer", "can_send": True, "items": [], "matching_outgoing_message": operation == "verify" and "message" in reached()}


def like_worker(platform, action, work=None, **options):
    if action == "set":
        click("like", options["receipt_path"], "ACTION_SUBMITTING")
        return {"state": "ACTION_SUBMITTING"}
    return {"identity": "channel", "liked": not job.get("hidden") and "like" in reached(), "saved": False, "collection": None}


def comment_worker(request, action=None, path=None):
    if action == "set":
        click("comment like", path, "COMMENT_SUBMITTING")
        return {"state": "COMMENT_SUBMITTING"}
    return {"identity": "channel", "liked": "comment like" in reached(), "text": "Nice"}


def warned():
    answer = {"ok": False, "error": {"code": job["code"], "message": "Check.", "hint": ""}}
    with patch.object(adapters, "worker_call", return_value=answer), patch.object(adapters, "hidden_desktop_call", return_value=answer), \
            patch("video_publisher.browser_broker.enabled", return_value=False):
        return adapters.browser_call("x", "operations", "default")


JOBS = {"reply": lambda: ops.reply("youtube", job.get("work", "abcdefghijk"), job["comment"], job.get("text", "Thanks for asking"), "en"),
        "message": lambda: dm.send("x", "viewer", "Thanks for writing", "en"),
        "like": lambda: engagement.set_action("x", "1234567890", "like"),
        "comment-like": lambda: comment_actions.mutate("x", "1234567890", "c1", "like"),
        "warned": warned}

with patch.object(ops, "backend", side_effect=reply_backend), patch.object(dm, "dispatch", side_effect=message_worker), \
        patch.object(engagement, "dispatch", side_effect=like_worker), patch.object(comment_actions, "dispatch", side_effect=comment_worker):
    while True:
        try:
            result = JOBS[job["what"]]()
            print(json.dumps({"state": result.get("state"), "reused": bool(result.get("reused"))}))
        except VpubError as error:
            if error.code == "busy" and job.get("retry"):
                time.sleep(0.1)
                continue
            print(json.dumps({"code": error.code}))
        except Exception as error:
            print(json.dumps({"code": type(error).__name__}))
        break
'''


class Runs(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.platform = self.root / "platform.txt"
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime"), "FAKE_PLATFORM": str(self.platform)})
        env.start()
        self.addCleanup(env.stop)
        atomic_json(home() / "accounts/default/x-identity.json", {"identity": "channel"})
        atomic_json(home() / "operations/comments" / (digest(["youtube", WORK]) + ".json"), {"identity": "channel", "items": [
            {"id": f"c{number}", "text": f"Question {number}", "author": f"Viewer {number}", "author_id": f"viewer-{number}",
             "is_owner": False, "owner_replied": False, "language_hint": "en"} for number in range(1, 7)]})
        atomic_json(home() / "operations/comments" / (digest(["x", POST]) + ".json"), {"identity": "channel", "items": [{"id": "c1", "text": "Nice", "is_owner": False}]})

    def start(self, **job):
        return subprocess.Popen([sys.executable, "-c", RUN, json.dumps(job)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")

    def finish(self, process):
        out, err = process.communicate(timeout=180)
        return process.returncode, json.loads(out) if out.strip() else None, err

    def run_job(self, **job):
        code, answer, err = self.finish(self.start(**job))
        self.assertEqual(0, code, err)
        return answer

    def crash(self, **job):
        code, answer, err = self.finish(self.start(**job))
        self.assertEqual(9, code, err)
        self.assertIsNone(answer)

    def reached(self):
        return self.platform.read_text(encoding="utf-8").splitlines() if self.platform.exists() else []

    def used(self, name, platform="youtube"):
        return safety.status()["used_today"].get(platform, {}).get(name, 0)


class RestartTests(Runs):
    def test_a_daily_cap_still_holds_after_a_restart(self):
        safety.set_limits(replies_per_day=1)
        self.assertEqual("REPLY_VERIFIED", self.run_job(what="reply", comment="c1")["state"])
        self.assertEqual("daily_limit_reached", self.run_job(what="reply", comment="c2", text="Another answer")["code"])
        self.assertEqual(["c1"], self.reached())

    def test_the_same_words_are_still_refused_after_a_restart(self):
        self.run_job(what="reply", comment="c1")
        safety.link(["youtube:viewer-1", "youtube:viewer-2"])
        self.assertEqual("repeat_reply", self.run_job(what="reply", comment="c2")["code"])
        self.assertEqual(["c1"], self.reached())

    def test_a_comment_answered_earlier_is_not_answered_again(self):
        self.run_job(what="reply", comment="c1")
        answer = self.run_job(what="reply", comment="c1")
        self.assertTrue(answer["reused"])
        self.assertEqual(["c1"], self.reached())
        self.assertEqual(1, self.used("replies_per_day"))

    def test_a_damaged_count_file_stops_sending_instead_of_resetting(self):
        self.run_job(what="reply", comment="c1")
        safety.usage_path().write_text("{ damaged", encoding="utf-8")
        self.assertIn("code", self.run_job(what="reply", comment="c2", text="Another answer"))
        self.assertEqual(["c1"], self.reached())


class OverlapTests(Runs):
    def test_overlapping_runs_answer_one_comment_once(self):
        runs = [self.start(what="reply", comment="c1", hold=1.5) for _ in range(5)]
        answers = [self.finish(run)[1] for run in runs]
        self.assertEqual(["c1"], self.reached())
        self.assertEqual(1, self.used("replies_per_day"))
        self.assertEqual(1, sum(answer.get("state") == "REPLY_VERIFIED" and not answer["reused"] for answer in answers), answers)
        for answer in answers:
            with self.subTest(answer=answer):
                self.assertTrue(answer.get("code") == "busy" or answer.get("state") == "REPLY_VERIFIED", answers)

    def test_overlapping_runs_cannot_pass_the_daily_cap_together(self):
        safety.set_limits(replies_per_day=2, per_person_per_day=20)
        runs = [self.start(what="reply", comment=f"c{number}", text=f"Answer {number}", retry=True) for number in range(1, 7)]
        answers = [self.finish(run)[1] for run in runs]
        self.assertEqual(2, len(self.reached()), answers)
        self.assertEqual(2, self.used("replies_per_day"))
        self.assertEqual(4, sum(answer.get("code") == "daily_limit_reached" for answer in answers), answers)

    def test_overlapping_runs_like_a_post_once(self):
        runs = [self.start(what="like", hold=1.5) for _ in range(4)]
        for run in runs:
            self.finish(run)
        self.assertEqual(["like"], self.reached())
        self.assertEqual(1, self.used("likes_per_day", "x"))


class CrashTests(Runs):
    def test_a_crash_right_after_sending_a_reply_is_never_sent_again(self):
        self.crash(what="reply", comment="c1", crash="after")
        self.assertEqual(["c1"], self.reached())
        # The dead process's lock does not get in the way, and the attempt is kept for a person to check.
        answer = self.run_job(what="reply", comment="c1")
        self.assertTrue(answer["reused"], answer)
        self.assertEqual("REPLY_SUBMITTING", answer["state"])
        self.assertEqual(["c1"], self.reached())
        self.assertEqual(1, self.used("replies_per_day"))

    def test_a_crash_before_a_reply_went_out_gives_the_count_back_once(self):
        self.crash(what="reply", comment="c1", crash="before")
        self.assertEqual([], self.reached())
        self.assertEqual(1, self.used("replies_per_day"))
        self.assertEqual("REPLY_VERIFIED", self.run_job(what="reply", comment="c1")["state"])
        self.assertEqual(["c1"], self.reached())
        self.assertEqual(1, self.used("replies_per_day"))

    def test_crashing_again_and_again_never_uses_up_the_limit(self):
        safety.set_limits(replies_per_day=1)
        for _ in range(3):
            self.crash(what="reply", comment="c1", crash="before")
        self.assertEqual("REPLY_VERIFIED", self.run_job(what="reply", comment="c1")["state"])
        self.assertEqual(["c1"], self.reached())

    def test_a_crash_right_after_sending_a_message_is_never_sent_again(self):
        self.crash(what="message", crash="after")
        answer = self.run_job(what="message")
        self.assertTrue(answer["reused"], answer)
        self.assertEqual(["message"], self.reached())
        self.assertEqual(1, self.used("messages_per_day", "x"))

    def test_a_crash_before_a_message_went_out_does_not_block_it(self):
        self.crash(what="message", crash="before")
        self.assertEqual("DM_VISIBLE", self.run_job(what="message")["state"])
        self.assertEqual(["message"], self.reached())
        self.assertEqual(1, self.used("messages_per_day", "x"))

    def test_a_crash_right_after_a_like_that_does_not_show_is_not_pressed_again(self):
        self.crash(what="like", crash="after")
        answer = self.run_job(what="like", hidden=True)
        self.assertTrue(answer["reused"], answer)
        self.assertEqual(["like"], self.reached())
        self.assertEqual(1, self.used("likes_per_day", "x"))

    def test_a_crash_before_a_like_went_out_counts_it_once(self):
        self.crash(what="like", crash="before")
        self.run_job(what="like")
        self.assertEqual(["like"], self.reached())
        self.assertEqual(1, self.used("likes_per_day", "x"))

    def test_a_crash_right_after_liking_a_comment_is_not_pressed_again(self):
        self.crash(what="comment-like", crash="after")
        answer = self.run_job(what="comment-like")
        self.assertTrue(answer["reused"], answer)
        self.assertEqual(["comment like"], self.reached())
        self.assertEqual(1, self.used("likes_per_day", "x"))

    def test_a_crash_before_liking_a_comment_counts_it_once(self):
        self.crash(what="comment-like", crash="before")
        self.run_job(what="comment-like")
        self.assertEqual(["comment like"], self.reached())
        self.assertEqual(1, self.used("likes_per_day", "x"))


class WarningTests(Runs):
    def test_a_platform_warning_in_one_run_stops_the_next_run_until_it_is_cleared(self):
        for code in sorted(safety.PUSHBACK):
            with self.subTest(code=code):
                self.assertEqual(code, self.run_job(what="warned", code=code)["code"])
                self.assertEqual("stopped", self.run_job(what="reply", comment="c1")["code"])
                self.assertEqual([], self.reached())
                safety.clear_stop()
        self.assertEqual("REPLY_VERIFIED", self.run_job(what="reply", comment="c1")["state"])
        self.assertEqual(["c1"], self.reached())

    def test_a_warning_stops_every_kind_of_send(self):
        self.run_job(what="warned", code="rate_limited")
        for what in ("reply", "message", "like", "comment-like"):
            with self.subTest(what=what):
                self.assertEqual("stopped", self.run_job(what=what, comment="c1")["code"])
        self.assertEqual([], self.reached())
        self.assertEqual({}, safety.status()["used_today"])


if __name__ == "__main__":
    unittest.main()

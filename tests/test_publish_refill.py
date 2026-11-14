"""Work-conserving publication dispatch; isolated homes and fake adapters only."""
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
import io
import os
from pathlib import Path
import signal
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from video_publisher import adapters, cli, core


class PublishRefillTests(unittest.TestCase):
    def setUp(self):
        tool = patch("video_publisher.rehearsal.shutil.which", return_value="ffmpeg.exe")
        tool.start()
        self.addCleanup(tool.stop)
        runs = patch("video_publisher.rehearsal.ffmpeg_problem", return_value=None)
        runs.start()
        self.addCleanup(runs.stop)
        temporary = tempfile.TemporaryDirectory(prefix="publish-refill-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.patch(patch.dict(os.environ, VPUB_HOME=str(self.root / "vpub"),
                              BILI_CLI_HOME=str(self.root / "bili")))
        self.patch(patch.object(socket.socket, "connect", side_effect=AssertionError("offline test")))
        self.patch(patch.object(adapters.subprocess, "run", side_effect=AssertionError("no real adapter")))
        self.patch(patch.object(adapters, "preflight", side_effect=lambda p, o: {"identity": p + "-owner"}))
        self.patch(patch.object(adapters, "publish", side_effect=self.dispatch))
        self.patch(patch.object(adapters, "email_reminder", side_effect=AssertionError("no notification")))
        self.patch(patch.object(cli.sys, "stderr", io.StringIO()))
        self.video = self.root / "synthetic.mp4"
        self.video.write_bytes(b"approved synthetic video")
        self.guard = threading.Lock()
        self.reset_activity()

    def patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def reset_activity(self):
        self.active, self.started, self.clicks = set(), [], []
        self.peak = 0

    def plan(self, platforms=("x", "instagram", "youtube", "douyin")):
        return {"confirmation": "refill", "video": str(self.video), "video_sha256": core.file_sha(self.video),
                "platforms": {p: {"account": "default", "method": "browser", "title": p,
                                  "content_language": "und", "visibility": "private"} for p in platforms}}

    def run_plan(self, plan=None):
        plan = plan or self.plan()
        return cli.publish_plan(plan, plan["confirmation"], workers=2, reviewed=True)

    @staticmethod
    def verified(platform):
        return {"state": "SUBMISSION_VERIFIED", "id": platform + "-work", "final_submit_attempted": True}

    def dispatch(self, platform, plan, options, checkpoint):
        with self.guard:
            self.assertNotIn(platform, self.active)
            self.active.add(platform)
            self.started.append(platform)
            self.peak = max(self.peak, len(self.active))
        try:
            self.assertLessEqual(self.peak, 2)
            self.assertEqual(options["visibility"], "private")
            self.assertEqual(core.file_sha(plan["video"]), plan["video_sha256"])
            return self.behavior(platform, plan, options, checkpoint)
        finally:
            with self.guard:
                self.active.remove(platform)

    def rows(self):
        ledger = core.Ledger()
        try:
            return {row["platform"]: row for row in ledger.rows()}
        finally:
            ledger.close()

    def assert_locked(self):
        with self.assertRaises(core.VpubError) as caught:
            with core.publication_lock():
                self.fail("publisher lock released while a worker is active")
        self.assertEqual(caught.exception.code, "busy")

    def waiting_upload(self, platform, checkpoint, ready, release):
        checkpoint({"state": "PREPARING", "media_selected": True, "final_submit_attempted": False})
        ready.set()
        self.assertTrue(release.wait(3))
        self.assert_locked()
        core.stop_check(platform)
        self.clicks.append(platform)
        return self.verified(platform)

    def test_fast_completion_refills_both_remaining_platforms_while_slow_is_alive(self):
        slow, last_started = threading.Event(), threading.Event()

        def publish(platform, plan, options, checkpoint):
            if platform == "x":
                slow.set()
                self.assertTrue(last_started.wait(3))
            else:
                self.assertTrue(slow.wait(3))
                with self.guard:
                    self.assertIn("x", self.active)
                if platform == "douyin":
                    last_started.set()
            return self.verified(platform)

        self.behavior = publish
        plan = self.plan()
        answer = self.run_plan(plan)
        self.assertTrue(answer["all_submissions_verified"])
        self.assertEqual(self.peak, 2)
        self.assertEqual(list(answer["results"]), list(plan["platforms"]))
        self.assertEqual(self.started[2:], ["youtube", "douyin"])
        self.assertEqual(set(self.rows()), set(plan["platforms"]))
        from video_publisher.audit_export import export_data
        spans={e['platform']:e for e in export_data()['events'] if e['type']=='span'}
        fast=[spans[p] for p in ('instagram','youtube','douyin')]
        self.assertEqual(len({s['slot'] for s in fast}),1)
        self.assertNotEqual(fast[0]['slot'],spans['x']['slot'])
        for a,b in zip(fast,fast[1:]):
            self.assertGreaterEqual(b['t_start'],a['t_end']);self.assertLess(b['t_start']-a['t_end'],1.0)

    def test_refilled_segment_drains_before_bilibili_api_native_or_host_barrier(self):
        for barrier, method in (("bilibili", "cli"), ("youtube", "api"),
                                ("youtube", "native"), ("youtube", "host")):
            with self.subTest(method=method), patch.dict(os.environ, VPUB_HOME=str(self.root / method)):
                self.reset_activity()
                slow, third = threading.Event(), threading.Event()
                after = "youtube" if barrier == "bilibili" else "bilibili"
                plan = self.plan(("x", "instagram", "douyin", barrier, after))
                plan["platforms"][barrier]["method"] = method
                if after == "bilibili":
                    plan["platforms"][after]["method"] = "cli"
                ended = set()

                def publish(platform, plan, options, checkpoint):
                    if platform == "x":
                        slow.set()
                        self.assertTrue(third.wait(3))
                    elif platform == "instagram":
                        self.assertTrue(slow.wait(3))
                    elif platform == "douyin":
                        with self.guard:
                            self.assertIn("x", self.active)
                        third.set()
                    elif platform == barrier:
                        with self.guard:
                            self.assertEqual(self.active, {barrier})
                            self.assertEqual(ended, {"x", "instagram", "douyin"})
                    else:
                        self.assertIn(barrier, ended)
                    with self.guard:
                        ended.add(platform)
                    return self.verified(platform)

                self.behavior = publish
                self.assertTrue(self.run_plan(plan)["all_submissions_verified"])
                self.assertEqual(self.started[-2:], [barrier, after])
                from video_publisher.audit_export import export_data
                spans={e['platform']:e for e in export_data()['events'] if e['type']=='span'}
                self.assertEqual(spans[barrier]['slot'],0)
                self.assertGreaterEqual(spans[barrier]['t_start'],max(spans[p]['t_end'] for p in ('x','instagram','douyin')))
                self.assertGreaterEqual(spans[after]['t_start'],spans[barrier]['t_end'])

    def test_unknown_worker_failure_refills_slot_without_retrying_original_attempt(self):
        slow, last_started = threading.Event(), threading.Event()

        def publish(platform, plan, options, checkpoint):
            if platform == "x":
                slow.set()
                self.assertTrue(last_started.wait(3))
            elif platform == "instagram":
                self.assertTrue(slow.wait(3))
                durable = core.home() / "browser-receipts" / "refill-instagram.json"
                durable.parent.mkdir(parents=True, exist_ok=True)
                durable.write_text("{", encoding="utf-8")
                raise OSError("synthetic readback failure")
            elif platform == "douyin":
                last_started.set()
            return self.verified(platform)

        self.behavior = publish
        answer = self.run_plan()
        self.assertEqual(answer["results"]["instagram"]["state"], "UNKNOWN")
        self.assertEqual(answer["results"]["instagram"]["code"], "backend_error")
        for p in ("x", "youtube", "douyin"):
            self.assertEqual(self.rows()[p]["state"], "SUBMISSION_VERIFIED")
        self.assertEqual(self.rows()["instagram"]["state"], "IN_PROGRESS")
        before, starts = self.rows(), list(self.started)
        again = self.run_plan()
        self.assertTrue(all(row["state"] == "SKIPPED_EXISTING" for row in again["results"].values()))
        self.assertEqual(self.started, starts)
        self.assertEqual(self.rows(), before)

    def test_warning_from_refilled_worker_blocks_last_launch_and_active_final_click(self):
        ready, release = threading.Event(), threading.Event()
        pushback = cli.pushed_back

        def latch(platform, code):
            result = pushback(platform, code)
            if result:
                release.set()
            return result

        def publish(platform, plan, options, checkpoint):
            if platform == "x":
                return self.waiting_upload(platform, checkpoint, ready, release)
            self.assertTrue(ready.wait(3))
            if platform == "youtube":
                raise core.VpubError("human_verification_required", "synthetic warning")
            return self.verified(platform)

        self.behavior = publish
        with patch.object(cli, "pushed_back", latch):
            answer = self.run_plan()
        # The first two workers may enter in either OS scheduling order. The
        # warning must still come from the sole refill, with no fourth launch.
        self.assertCountEqual(self.started[:2], ["x", "instagram"])
        self.assertEqual(self.started[2:], ["youtube"])
        self.assertEqual(self.clicks, [])
        self.assertEqual(answer["results"]["douyin"], {"state": "NOT_STARTED", "code": "stopped"})
        self.assertEqual(core.read_json(core.home() / "STOP.json")["platform"], "youtube")

    def test_stop_at_completion_prevents_refill(self):
        ready, release = threading.Event(), threading.Event()

        def publish(platform, plan, options, checkpoint):
            if platform == "x":
                return self.waiting_upload(platform, checkpoint, ready, release)
            self.assertEqual(platform, "instagram")
            self.assertTrue(ready.wait(3))
            core.atomic_json(core.home() / "STOP.json", {"source": "operator", "reason": "synthetic stop"})
            release.set()
            return self.verified(platform)

        self.behavior = publish
        answer = self.run_plan()
        self.assertCountEqual(self.started, ["x", "instagram"])
        self.assertEqual(self.clicks, [])
        for p in ("youtube", "douyin"):
            self.assertEqual(answer["results"][p]["code"], "stopped")

    def test_real_ctrl_c_after_refill_persists_stop_and_drains_without_more_clicks(self):
        ready = {p: threading.Event() for p in ("x", "youtube")}
        stopped, release = threading.Event(), threading.Event()
        record_stop = cli._stop_interrupted_publish
        errors = []
        handler = signal.getsignal(signal.SIGINT)

        def observed_stop():
            record_stop()
            stopped.set()

        def publish(platform, plan, options, checkpoint):
            if platform == "instagram":
                self.assertTrue(ready["x"].wait(3))
                return self.verified(platform)
            return self.waiting_upload(platform, checkpoint, ready[platform], release)

        def operator():
            try:
                for event in ready.values():
                    self.assertTrue(event.wait(3))
                self.assertIsNot(signal.getsignal(signal.SIGINT), handler)
                signal.raise_signal(signal.SIGINT)
                self.assertTrue(stopped.wait(3))
                self.assert_locked()
                signal.raise_signal(signal.SIGINT)
            except BaseException as exc:
                errors.append(exc)
            finally:
                release.set()

        self.behavior = publish
        operator_thread = threading.Thread(target=operator)
        with patch.object(cli, "_stop_interrupted_publish", observed_stop):
            operator_thread.start()
            try:
                with self.assertRaises(KeyboardInterrupt):
                    self.run_plan()
            finally:
                release.set()
                operator_thread.join(4)
        self.assertEqual(errors, [])
        self.assertFalse(operator_thread.is_alive())
        self.assertIs(signal.getsignal(signal.SIGINT), handler)
        self.assertCountEqual(self.started, ["x", "instagram", "youtube"])
        self.assertEqual(self.clicks, [])
        self.assertEqual(core.read_json(core.home() / "STOP.json")["source"], "operator")
        self.assertEqual(self.rows()["instagram"]["state"], "SUBMISSION_VERIFIED")
        with core.publication_lock():
            pass

    def test_fatal_worker_cancellation_or_submit_error_aborts_refill_and_drains(self):
        for failure_type, saved_cancel in ((SystemExit, False), (CancelledError, False),
                                           (CancelledError, True), (RuntimeError, False)):
            with self.subTest(failure=failure_type.__name__, saved_cancel=saved_cancel), patch.dict(os.environ, VPUB_HOME=str(self.root / (failure_type.__name__ + str(saved_cancel)))):
                self.reset_activity()
                ready, release = threading.Event(), threading.Event()
                record_stop, submit = cli._stop_interrupted_publish, ThreadPoolExecutor.submit

                def stop_then_release():
                    record_stop()
                    self.assert_locked()
                    release.set()

                def publish(platform, plan, options, checkpoint):
                    if platform == "x":
                        return self.waiting_upload(platform, checkpoint, ready, release)
                    self.assertTrue(ready.wait(3))
                    if platform == "youtube":
                        raise SystemExit(7)
                    return self.verified(platform)

                def failing_submit(pool, fn, plan, platform, lock, slot):
                    if platform == "youtube" and failure_type is RuntimeError:
                        raise RuntimeError("synthetic scheduling failure")
                    if platform == "youtube" and failure_type is CancelledError:
                        future = Future()
                        if saved_cancel:
                            future.set_exception(CancelledError())
                        else:
                            future.cancel()
                            future.set_running_or_notify_cancel()
                        return future
                    return submit(pool, fn, plan, platform, lock, slot)

                self.behavior = publish
                with patch.object(cli, "_stop_interrupted_publish", stop_then_release), patch.object(ThreadPoolExecutor, "submit", failing_submit), self.assertRaises(failure_type):
                    self.run_plan()
                self.assertNotIn("douyin", self.started)
                self.assertEqual(self.clicks, [])
                self.assertEqual(core.read_json(core.home() / "STOP.json")["source"], "operator")
                self.assertFalse(self.rows()["x"]["data"]["receipt"]["final_submit_attempted"])
                self.assertEqual(self.rows()["instagram"]["state"], "SUBMISSION_VERIFIED")
                with core.publication_lock():
                    pass


if __name__ == "__main__":
    unittest.main()

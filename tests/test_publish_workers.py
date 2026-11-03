"""Bounded publishing with fake adapters only; both account homes are isolated."""
from concurrent.futures import ThreadPoolExecutor, wait
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from video_publisher import adapters, browser_backends, cli, core


class PublishWorkersTests(unittest.TestCase):
    def patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="publish-workers-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.patch(patch.dict(os.environ, VPUB_HOME=str(self.root / "vpub"),
                              BILI_CLI_HOME=str(self.root / "bili")))
        self.patch(patch.object(socket.socket, "connect", side_effect=AssertionError("offline test")))
        self.patch(patch.object(adapters.subprocess, "run", side_effect=AssertionError("no real adapter")))
        self.preflight = self.patch(patch.object(adapters, "preflight", side_effect=self.identity))
        self.publish = self.patch(patch.object(adapters, "publish", side_effect=self.verified))
        self.patch(patch.object(adapters, "email_reminder", side_effect=AssertionError("no notification")))
        self.video = self.root / "synthetic.mp4"
        self.video.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + bytes(64))

    @staticmethod
    def identity(platform, options):
        return {"identity": platform + "-owner", "account": "default"}

    @staticmethod
    def verified(platform, plan, options, checkpoint):
        return {"state": "SUBMISSION_VERIFIED", "id": platform + "-work", "platform": platform}

    def plan(self, platforms=("x", "instagram"), **changes):
        return {"confirmation": "fixture", "video": str(self.video),
                "video_sha256": core.file_sha(self.video),
                "platforms": {p: {"account": "default", "method": "browser", "title": p,
                                  "content_language": "und", **changes} for p in platforms}}

    def run_plan(self, plan, workers=2):
        return cli.publish_plan(plan, plan["confirmation"], workers=workers, reviewed=True)

    def rows(self):
        ledger = core.Ledger()
        try:
            return {row["platform"]: row for row in ledger.rows()}
        finally:
            ledger.close()

    def assert_locked(self):
        with self.assertRaises(core.VpubError) as caught:
            with core.publication_lock():
                self.fail("publication lock released while a worker is active")
        self.assertEqual(caught.exception.code, "busy")

    def await_fake(self, coroutine):
        """Drive fake awaits without opening Windows asyncio's loopback socket."""
        try:
            while True:
                event = coroutine.send(None)
                self.assertIsInstance(event, threading.Event)
                self.assertTrue(event.wait(3))
        except StopIteration as done:
            return done.value
        finally:
            coroutine.close()

    def pending_upload(self, platform, plan, options, ready, release, button):
        tx = browser_backends.Transaction(platform, platform + "-owner", {**plan, "options": options})
        tx.save(media_selected=True, phase="uploading")

        async def still_holds():
            class Released:
                def __await__(self):
                    yield release

            ready.set()
            await Released()

        return self.await_fake(tx.submit(SimpleNamespace(url="https://x.com/compose/post"), button, still_holds))

    def test_default_serial_and_two_workers_have_bounded_overlap(self):
        timings = {}
        for workers in (1, 2):
            with patch.dict(os.environ, VPUB_HOME=str(self.root / str(workers))):
                active, peak, starts = set(), [0], []
                guard = threading.Lock()
                overlap = threading.Barrier(workers)

                def publish(platform, plan, options, checkpoint):
                    with guard:
                        self.assertNotIn(platform, active)
                        active.add(platform)
                        self.assertLessEqual(len(active), workers)
                        starts.append(platform)
                        peak[0] = max(peak[0], len(active))
                    overlap.wait(3)
                    time.sleep(0.20)
                    with guard:
                        active.remove(platform)
                    return self.verified(platform, plan, options, checkpoint)

                self.publish.side_effect = publish
                plan = self.plan(("x", "instagram", "douyin", "youtube"))
                started = time.perf_counter()
                # Omitting workers must still be serial after explicit review.
                answer = cli.publish_plan(plan, plan["confirmation"], reviewed=True) if workers == 1 else self.run_plan(plan)
                timings[workers] = time.perf_counter() - started
                self.assertTrue(answer["all_submissions_verified"])
                self.assertEqual(list(answer["results"]), list(plan["platforms"]))
                self.assertEqual(peak[0], workers)
                self.assertCountEqual(starts, plan["platforms"])
        print(f"BOUNDED_TIMING workers=1:{timings[1]:.3f}s workers=2:{timings[2]:.3f}s", flush=True)

    def test_cli_choices_dispatch_and_dry_run(self):
        parser = cli.parser()
        self.assertEqual(parser.parse_args(["publish", "fixture"]).workers, 1)
        args = parser.parse_args(["publish", "fixture", "--confirm", "fixture", "--workers", "2", "--person-approved"])
        with patch.object(cli, "load_plan", return_value=self.plan()), patch.object(cli, "publish_plan", return_value={}) as publish:
            cli.run_command(args)
            publish.assert_called_once_with(self.plan(), "fixture", workers=2, reviewed=True)
            args.dry_run = True
            self.assertEqual(cli.run_command(args)["platform_writes"], 0)
            self.assertEqual(publish.call_count, 1)
        for value in ("0", "3"):
            with self.subTest(value=value), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(["publish", "fixture", "--workers", value])

    def test_invalid_workers_and_confirmation_start_nothing(self):
        for value in (0, 3, True, 2.0):
            with self.subTest(value=value), self.assertRaises(core.VpubError) as caught:
                self.run_plan(self.plan(), value)
            self.assertEqual(caught.exception.code, "invalid_workers")
        with self.assertRaises(core.VpubError):
            cli.publish_plan(self.plan(), "wrong", workers=2)
        self.preflight.assert_not_called()
        self.publish.assert_not_called()

    def test_workers_own_ledger_checkpoint_and_mutable_plan(self):
        original = self.plan()
        approved = deepcopy(original)
        ready = threading.Barrier(2)
        instances = []
        ledger_type = core.Ledger
        testcase = self

        class OwnedLedger(ledger_type):
            def __init__(self):
                super().__init__()
                self.owner = threading.get_ident()
                instances.append(self)

            def save(self, *args):
                testcase.assertEqual(threading.get_ident(), self.owner)
                return super().save(*args)

            def close(self):
                testcase.assertEqual(threading.get_ident(), self.owner)
                super().close()

        def publish(platform, plan, options, checkpoint):
            self.assertEqual(plan["platforms"]["x"]["title"], "x")
            ready.wait(3)
            plan["platforms"]["x"]["title"] = platform
            receipt = {"state": "UPLOADING", "platform": platform, "nested": {"done": False}}
            checkpoint(receipt)
            ready.wait(3)
            receipt.update(self.verified(platform, plan, options, checkpoint))
            receipt["nested"]["done"] = True
            return receipt

        self.publish.side_effect = publish
        with patch.object(cli, "Ledger", OwnedLedger):
            answer = self.run_plan(original)
        self.assertEqual(original, approved)
        self.assertEqual(len({item.owner for item in instances}), 2)
        for platform, row in self.rows().items():
            self.assertEqual(row["data"]["receipt"]["platform"], platform)
            self.assertTrue(row["data"]["receipt"]["nested"]["done"])
            self.assertEqual(answer["results"][platform]["id"], platform + "-work")

    def test_bilibili_native_api_and_unspecified_routes_are_serial_barriers(self):
        for platform, method in (("bilibili", "browser"), ("youtube", "native"),
                                 ("youtube", "api"), ("youtube", None)):
            with self.subTest(platform=platform, method=method), patch.dict(os.environ, VPUB_HOME=str(self.root / (platform + str(method)))):
                events, active = [], set()
                guard = threading.Lock()
                plan = self.plan(("x", "instagram", platform, "douyin"))
                plan["platforms"][platform]["method"] = method

                def publish(p, plan, options, checkpoint):
                    with guard:
                        if p == platform:
                            self.assertFalse(active)
                        else:
                            self.assertNotIn(platform, active)
                        active.add(p)
                        events.append((p, "start"))
                    time.sleep(0.02)
                    with guard:
                        active.remove(p)
                        events.append((p, "end"))
                    return self.verified(p, plan, options, checkpoint)

                self.publish.side_effect = publish
                answer = self.run_plan(plan)
                self.assertTrue(answer["all_submissions_verified"])
                for p in ("x", "instagram"):
                    self.assertLess(events.index((p, "end")), events.index((platform, "start")))
                self.assertLess(events.index((platform, "end")), events.index(("douyin", "start")))

    def test_warning_during_await_blocks_final_press_and_all_queued_routes(self):
        pending, stopped = threading.Event(), threading.Event()
        button = SimpleNamespace(click=AsyncMock())
        transactions = []
        original_pushback = cli.pushed_back

        def pushback(platform, code):
            result = original_pushback(platform, code)
            if result:
                stopped.set()
            return result

        def publish(platform, plan, options, checkpoint):
            if platform == "instagram":
                self.assertTrue(pending.wait(3))
                raise core.VpubError("human_verification_required", "synthetic warning")
            self.assertEqual(platform, "x")
            tx = browser_backends.Transaction(platform, "x-owner", {**plan, "options": options})
            transactions.append(tx)
            tx.save(media_selected=True, phase="uploading")

            async def still_holds():
                class WaitForStop:
                    def __await__(self):
                        yield stopped

                pending.set()
                await WaitForStop()

            self.await_fake(tx.submit(SimpleNamespace(url="https://x.com/compose/post"), button, still_holds))
            self.fail("a stopped upload reached final submission")

        self.publish.side_effect = publish
        plan = self.plan(("x", "instagram", "douyin", "youtube", "bilibili"))
        plan["platforms"]["bilibili"]["method"] = "cli"
        with patch.object(cli, "pushed_back", pushback), patch.object(browser_backends, "risk_check", AsyncMock()):
            answer = self.run_plan(plan)
        self.assertEqual({call.args[0] for call in self.publish.call_args_list}, {"x", "instagram"})
        button.click.assert_not_awaited()
        self.assertFalse(transactions[0].receipt["final_submit_attempted"])
        self.assertTrue(self.rows()["x"]["data"]["receipt"]["media_selected"])
        self.assertEqual(answer["results"]["x"]["code"], "stopped")
        for p in ("douyin", "youtube", "bilibili"):
            self.assertEqual(answer["results"][p], {"state": "NOT_STARTED", "code": "stopped"})

    def test_preflight_warning_prevents_peer_snapshot_and_upload(self):
        ready, stopped = threading.Barrier(2), threading.Event()
        pushback = cli.pushed_back

        def preflight(platform, options):
            ready.wait(3)
            if platform == "instagram":
                raise core.VpubError("rate_limited", "synthetic warning")
            self.assertTrue(stopped.wait(3))
            return self.identity(platform, options)

        def latch(platform, code):
            result = pushback(platform, code)
            stopped.set()
            return result

        self.preflight.side_effect = preflight
        with patch.object(cli, "pushed_back", latch), patch.object(cli, "snapshot_plan") as snapshot:
            answer = self.run_plan(self.plan(("x", "instagram", "douyin")))
        self.publish.assert_not_called()
        snapshot.assert_not_called()
        self.assertEqual(answer["results"]["douyin"]["code"], "stopped")
        self.assertFalse(self.rows())

    def test_independent_preflight_failure_keeps_other_success(self):
        def preflight(platform, options):
            if platform == "x":
                raise OSError("synthetic preflight failure")
            return self.identity(platform, options)

        self.preflight.side_effect = preflight
        answer = self.run_plan(self.plan())
        self.assertEqual(answer["results"]["x"]["code"], "preflight_error")
        self.assertEqual(self.rows()["instagram"]["state"], "SUBMISSION_VERIFIED")
        self.assertFalse((core.home() / "STOP.json").exists())

    def test_failed_readback_preserves_receipts_and_never_reuploads(self):
        for source in ("checkpoint", "durable", "uncertain"):
            with self.subTest(source=source), patch.dict(os.environ, VPUB_HOME=str(self.root / source)):
                def publish(platform, plan, options, checkpoint):
                    receipt = self.verified(platform, plan, options, checkpoint)
                    if platform != "x":
                        return receipt
                    if source == "checkpoint":
                        checkpoint(receipt)
                    elif source == "durable":
                        core.atomic_json(core.home() / "browser-receipts" / "fixture-x.json", receipt)
                    raise OSError("synthetic readback failure")

                self.publish.side_effect = publish
                answer = self.run_plan(self.plan())
                rows = self.rows()
                self.assertEqual(answer["results"]["x"]["state"], "UNKNOWN" if source == "uncertain" else "SUBMITTED_UNVERIFIED")
                self.assertEqual(rows["instagram"]["data"]["receipt"]["id"], "instagram-work")
                calls = self.publish.call_count
                again = self.run_plan(self.plan())
                self.assertEqual(self.publish.call_count, calls)
                self.assertTrue(all(row["state"] == "SKIPPED_EXISTING" for row in again["results"].values()))
                self.assertEqual(self.rows(), rows)

    def test_existing_verified_receipts_are_unchanged(self):
        plan = self.plan()
        self.run_plan(plan)
        rows = self.rows()
        self.publish.reset_mock()
        self.preflight.reset_mock()
        answer = self.run_plan(plan)
        self.assertTrue(answer["all_submissions_verified"])
        self.publish.assert_not_called()
        self.preflight.assert_not_called()
        self.assertEqual(rows, self.rows())

    def test_unreadable_failed_receipt_keeps_peers_and_later_batch(self):
        def publish(platform, plan, options, checkpoint):
            if platform == "x":
                path = core.home() / "browser-receipts" / "fixture-x.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{", encoding="utf-8")
                raise OSError("synthetic readback failure")
            return self.verified(platform, plan, options, checkpoint)

        self.publish.side_effect = publish
        plan = self.plan(("x", "instagram", "douyin", "youtube"))
        answer = self.run_plan(plan)
        self.assertFalse(answer["all_submissions_verified"])
        self.assertEqual(answer["results"]["x"]["state"], "UNKNOWN")
        self.assertEqual(self.rows()["x"]["state"], "IN_PROGRESS")
        for platform in ("instagram", "douyin", "youtube"):
            self.assertEqual(answer["results"][platform]["state"], "SUBMISSION_VERIFIED")
            self.assertEqual(self.rows()[platform]["data"]["receipt"]["id"], platform + "-work")
        self.publish.reset_mock()
        again = self.run_plan(plan)
        self.publish.assert_not_called()
        self.assertTrue(all(r["state"] == "SKIPPED_EXISTING" for r in again["results"].values()))

    def test_copy_fallback_never_replaces_a_peers_open_upload_alias(self):
        replace = core.os.replace
        ready = threading.Barrier(2)
        guard = threading.Lock()
        open_aliases = set()
        seen = {}

        def replace_and_open(source, target):
            if Path(target).name.startswith("crosscast-"):
                ready.wait(3)  # Both aliases were absent when their copies began.
                with guard:
                    if str(target) in open_aliases:
                        raise PermissionError("synthetic Windows upload file in use")
                    result = replace(source, target)
                    open_aliases.add(str(target))
                    return result
            return replace(source, target)

        def publish(platform, plan, options, checkpoint):
            tx = browser_backends.Transaction(platform, platform + "-owner", {**plan, "options": options})
            alias = tx.media()
            self.assertEqual(core.file_sha(alias), plan["video_sha256"])
            seen[platform] = alias
            return self.verified(platform, plan, options, checkpoint)

        self.publish.side_effect = publish
        with patch.object(core.os, "link", side_effect=OSError("no hardlinks")), patch.object(core.os, "replace", replace_and_open):
            answer = self.run_plan(self.plan())
        self.assertTrue(answer["all_submissions_verified"])
        self.assertEqual(seen["x"].name, seen["instagram"].name)
        self.assertNotEqual(seen["x"], seen["instagram"])

    def test_draft_contracts_and_explicit_preview_promotion(self):
        def publish(platform, plan, options, checkpoint):
            if options.get("submission_mode") == "draft_only":
                return {"state": "DRAFT_SAVED" if platform == "x" else "PREVIEW_READY", "final_submit_attempted": False}
            return self.verified(platform, plan, options, checkpoint)

        self.publish.side_effect = publish
        plan = self.plan(submission_mode="draft_only")
        for _ in range(2):
            answer = self.run_plan(plan)
            self.assertIsNone(answer["all_submissions_verified"])
            self.assertTrue(answer["all_drafts_ready"])
            self.assertEqual(answer["drafts_read_back"], ["x"])
        self.assertEqual(self.publish.call_count, 2)
        plan["platforms"]["x"]["submission_mode"] = "publish"
        answer = self.run_plan(plan)
        self.assertTrue(answer["all_submissions_verified"])
        self.assertTrue(answer["all_drafts_ready"])
        self.assertEqual(self.publish.call_count, 3)

    def test_snapshot_phase_is_serial_and_exact_assets_are_preserved(self):
        plan = self.plan()
        cover = self.root / "cover.png"
        cover.write_bytes(b"synthetic cover")
        plan.update(cover=str(cover), cover_sha256=core.file_sha(cover))
        plan["covers"] = {p: {"landscape": {"path": str(cover), "sha256": core.file_sha(cover)}} for p in plan["platforms"]}
        original, active, peak = cli.snapshot_plan, [0], [0]
        guard = threading.Lock()

        def snapshot(plan, platform):
            with guard:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            try:
                time.sleep(0.02)
                return original(plan, platform)
            finally:
                with guard:
                    active[0] -= 1

        def publish(platform, safe, options, checkpoint):
            for path, sha in ((safe["video"], safe["video_sha256"]), (safe["cover"], safe["cover_sha256"]),
                              (safe["covers"][platform]["landscape"]["path"], core.file_sha(cover))):
                self.assertTrue(Path(path).is_relative_to(core.home()))
                self.assertEqual(core.file_sha(path), sha)
            return self.verified(platform, safe, options, checkpoint)

        self.publish.side_effect = publish
        with patch.object(cli, "snapshot_plan", snapshot):
            self.assertTrue(self.run_plan(plan)["all_submissions_verified"])
        self.assertEqual(peak[0], 1)

    def test_changed_source_is_rejected_before_any_claim_or_upload(self):
        plan = self.plan()
        self.video.write_bytes(b"changed after approval")
        answer = self.run_plan(plan)
        self.assertTrue(all(row["state"] == "NOT_STARTED" and row["code"] == "source_changed" for row in answer["results"].values()))
        self.publish.assert_not_called()
        self.assertFalse(self.rows())

    def test_repeated_keyboard_interrupt_does_not_release_lock_before_drain(self):
        ready = {p: threading.Event() for p in ("x", "instagram")}
        release = threading.Event()
        button = SimpleNamespace(click=AsyncMock())
        waits = [0]

        def publish(platform, plan, options, checkpoint):
            return self.pending_upload(platform, plan, options, ready[platform], release, button)

        def drain(futures, *args, **kwargs):
            self.assertEqual(kwargs["timeout"], 0.05)
            for event in ready.values():
                self.assertTrue(event.wait(3))
            self.assert_locked()
            waits[0] += 1
            if waits[0] > 1:
                self.assertEqual(core.read_json(core.home() / "STOP.json")["source"], "operator")
            if waits[0] <= 2:
                raise KeyboardInterrupt()
            release.set()
            return wait(futures, *args, **kwargs)

        self.publish.side_effect = publish
        with patch.object(browser_backends, "risk_check", AsyncMock()), patch("concurrent.futures.wait", drain), self.assertRaises(KeyboardInterrupt):
            self.run_plan(self.plan(("x", "instagram", "douyin")))
        self.assertGreaterEqual(waits[0], 3)
        self.assertEqual(set(self.rows()), {"x", "instagram"})
        button.click.assert_not_awaited()
        self.assertTrue(all(not row["data"]["receipt"]["final_submit_attempted"] for row in self.rows().values()))
        with core.publication_lock():
            pass

    def test_worker_base_exception_cannot_skip_peer_drain(self):
        ready, release = threading.Event(), threading.Event()
        button = SimpleNamespace(click=AsyncMock())
        record_stop = cli._stop_interrupted_publish

        def stop_then_release():
            record_stop()
            self.assert_locked()
            release.set()

        def publish(platform, plan, options, checkpoint):
            if platform == "x":
                self.assertTrue(ready.wait(3))
                raise KeyboardInterrupt()
            return self.pending_upload(platform, plan, options, ready, release, button)

        self.publish.side_effect = publish
        with patch.object(cli, "_stop_interrupted_publish", stop_then_release), patch.object(browser_backends, "risk_check", AsyncMock()), self.assertRaises(KeyboardInterrupt):
            self.run_plan(self.plan(("x", "instagram", "douyin")))
        self.assertEqual(self.rows()["x"]["state"], "IN_PROGRESS")
        self.assertEqual(core.read_json(core.home() / "STOP.json")["reason"], "Publication interrupted; inspect receipts before continuing.")
        self.assertFalse(self.rows()["instagram"]["data"]["receipt"]["final_submit_attempted"])
        button.click.assert_not_awaited()
        self.assertNotIn("douyin", self.rows())

    def test_ctrl_c_during_submit_drains_even_before_future_is_recorded(self):
        submit = ThreadPoolExecutor.submit
        ready, release = threading.Event(), threading.Event()
        button = SimpleNamespace(click=AsyncMock())
        record_stop = cli._stop_interrupted_publish
        previous = signal.getsignal(signal.SIGINT)
        self.assertIs(previous, signal.default_int_handler)

        def stop_then_release():
            record_stop()
            self.assert_locked()
            release.set()

        def interrupted_submit(pool, *args, **kwargs):
            future = submit(pool, *args, **kwargs)
            self.assertTrue(ready.wait(3))
            signal.raise_signal(signal.SIGINT)
            return future

        def publish(platform, plan, options, checkpoint):
            return self.pending_upload(platform, plan, options, ready, release, button)

        self.publish.side_effect = publish
        with patch.object(ThreadPoolExecutor, "submit", interrupted_submit), patch.object(cli, "_stop_interrupted_publish", stop_then_release), patch.object(browser_backends, "risk_check", AsyncMock()), self.assertRaises(KeyboardInterrupt):
            self.run_plan(self.plan())
        self.assertEqual(list(self.rows()), ["x"])
        self.assertEqual(core.read_json(core.home() / "STOP.json")["code"], "interrupted")
        self.assertFalse(self.rows()["x"]["data"]["receipt"]["final_submit_attempted"])
        button.click.assert_not_awaited()
        self.assertIs(signal.getsignal(signal.SIGINT), previous)

    def interrupt_active_uploads(self, existing_stop=None, already_final=False):
        ready = {p: threading.Event() for p in ("x", "instagram")}
        release, stop_written = threading.Event(), threading.Event()
        button = SimpleNamespace(click=AsyncMock())
        record_stop = cli._stop_interrupted_publish
        errors = []
        previous = signal.getsignal(signal.SIGINT)
        stop_path = core.home() / "STOP.json"
        stop_bytes = []

        def observed_stop():
            self.assertIs(threading.current_thread(), threading.main_thread())
            record_stop()
            stop_written.set()

        def publish(platform, plan, options, checkpoint):
            if already_final and platform == "x":
                receipt = self.verified(platform, plan, options, checkpoint)
                receipt["final_submit_attempted"] = True
                checkpoint(receipt)
                ready[platform].set()
                return receipt
            return self.pending_upload(platform, plan, options, ready[platform], release, button)

        def operator():
            try:
                for event in ready.values():
                    self.assertTrue(event.wait(3))
                if existing_stop is not None:
                    core.atomic_json(stop_path, existing_stop)
                    stop_bytes.append(stop_path.read_bytes())
                self.assertIsNot(signal.getsignal(signal.SIGINT), previous)
                signal.raise_signal(signal.SIGINT)
                # The coordinator must react while the uploads remain pending.
                self.assertTrue(stop_written.wait(3))
                self.assertTrue(stop_path.exists())
                self.assert_locked()
                signal.raise_signal(signal.SIGINT)
            except BaseException as exc:
                errors.append(exc)
            finally:
                release.set()

        self.publish.side_effect = publish
        operator_thread = threading.Thread(target=operator)
        progress = io.StringIO()
        with patch.object(cli, "_stop_interrupted_publish", observed_stop), patch.object(browser_backends, "risk_check", AsyncMock()), patch.object(cli.sys, "stderr", progress):
            operator_thread.start()
            try:
                with self.assertRaises(KeyboardInterrupt):
                    # A completed slot now refills immediately. This case tests
                    # retention of a completed receipt, not cancellation before
                    # an unrelated third platform could legitimately start.
                    platforms = ("x", "instagram") if already_final else ("x", "instagram", "douyin")
                    self.run_plan(self.plan(platforms))
            finally:
                release.set()
                operator_thread.join(4)
        self.assertFalse(operator_thread.is_alive())
        self.assertEqual(errors, [])
        updates = [json.loads(line) for line in progress.getvalue().splitlines()]
        self.assertEqual([event["event"] for event in updates], ["publication_stopping"])
        self.assertIn("minutes", updates[0]["message"])
        self.assertIs(signal.getsignal(signal.SIGINT), previous)
        button.click.assert_not_awaited()
        self.assertEqual(set(self.rows()), {"x", "instagram"})
        for platform, row in self.rows().items():
            receipt = row["data"]["receipt"]
            if already_final and platform == "x":
                self.assertEqual(row["state"], "SUBMISSION_VERIFIED")
                self.assertEqual(receipt["id"], "x-work")
            else:
                self.assertFalse(receipt["final_submit_attempted"])
        if existing_stop is None:
            self.assertEqual(core.read_json(stop_path)["source"], "operator")
        else:
            self.assertEqual(stop_path.read_bytes(), stop_bytes[0])
        with core.publication_lock():
            pass

    def test_real_sigint_stops_active_uploads_before_final_submit(self):
        self.interrupt_active_uploads()

    def test_real_sigint_preserves_preexisting_platform_stop_reason(self):
        self.interrupt_active_uploads(existing_stop={"source": "safety", "platform": "instagram",
                                                    "code": "human_verification_required", "seen": "keep this evidence"})

    def test_real_sigint_retains_already_final_receipt(self):
        self.interrupt_active_uploads(already_final=True)

    def test_default_one_worker_retains_legacy_interrupt_behavior(self):
        self.publish.side_effect = KeyboardInterrupt()
        plan = self.plan()
        with self.assertRaises(KeyboardInterrupt):
            cli.publish_plan(plan, plan["confirmation"], reviewed=True)
        self.assertFalse((core.home() / "STOP.json").exists())
        self.assertEqual(list(self.rows()), ["x"])
        self.assertEqual(self.rows()["x"]["state"], "IN_PROGRESS")

    def test_interrupt_answer_explains_the_persistent_stop_without_clearing_it(self):
        stop = core.home() / "STOP.json"
        for stopped in (False, True):
            with self.subTest(stopped=stopped):
                if stopped:
                    core.atomic_json(stop, {"source": "safety", "code": "human_verification_required"})
                before = stop.read_bytes() if stopped else None
                output = io.StringIO()
                with patch.object(cli.sys, "argv", ["vpub", "status"]), patch.object(cli, "execute", side_effect=KeyboardInterrupt()), patch.object(cli.sys, "stdout", output), self.assertRaises(SystemExit) as caught:
                    cli.main()
                self.assertEqual(caught.exception.code, 3)
                answer = json.loads(output.getvalue())["error"]
                self.assertEqual(answer["code"], "interrupted")
                self.assertIn("Do not upload it again", answer["hint"])
                self.assertEqual("vpub stop --clear" in answer["hint"], stopped)
                self.assertEqual(stop.read_bytes() if stopped else None, before)

    def test_final_submit_still_marks_before_click_and_does_not_repeat(self):
        plan = self.plan(("x",))
        tx = browser_backends.Transaction("x", "x-owner", {**plan, "options": plan["platforms"]["x"]})

        async def click(**kwargs):
            self.assertTrue(core.read_json(tx.path)["final_submit_attempted"])

        button = SimpleNamespace(click=AsyncMock(side_effect=click))
        with patch.object(browser_backends, "risk_check", AsyncMock()):
            self.await_fake(tx.submit(SimpleNamespace(url="https://x.com/compose/post"), button))
            with self.assertRaises(core.VpubError) as caught:
                self.await_fake(tx.submit(SimpleNamespace(url="https://x.com/compose/post"), button))
        self.assertEqual(caught.exception.code, "already_submitted")
        button.click.assert_awaited_once()

    def test_stop_during_receipt_save_prevents_click_and_keeps_submit_marker(self):
        plan = self.plan(("x",))
        tx = browser_backends.Transaction("x", "x-owner", {**plan, "options": plan["platforms"]["x"]})
        save = tx.save

        def save_then_stop(**changes):
            result = save(**changes)
            core.atomic_json(core.home() / "STOP.json", {"reason": "synthetic concurrent stop"})
            return result

        button = SimpleNamespace(click=AsyncMock())
        with patch.object(tx, "save", save_then_stop), patch.object(browser_backends, "risk_check", AsyncMock()), self.assertRaises(core.VpubError) as caught:
            self.await_fake(tx.submit(SimpleNamespace(url="https://x.com/compose/post"), button))
        self.assertEqual(caught.exception.code, "stopped")
        button.click.assert_not_awaited()
        self.assertTrue(core.read_json(tx.path)["final_submit_attempted"])


if __name__ == "__main__":
    unittest.main()

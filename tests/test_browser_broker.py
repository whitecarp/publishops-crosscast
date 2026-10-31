"""Shared browser session over a real local pipe, with a fake handler. No browser or platform."""
import asyncio
from multiprocessing.connection import Client, Listener
import os
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

from video_publisher import adapters, browser_broker as broker
from video_publisher.browser_runtime import profile_lock
from video_publisher.core import VpubError, atomic_json, home


class Server:
    """Runs broker.serve in a thread and publishes its state file like the real process."""

    def __init__(self, handler, platform="x", **options):
        self.key = os.urandom(32)
        self.address = broker.new_address()
        self.listener = Listener(self.address, authkey=self.key)
        self.path = broker.state_path(platform, "default")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(self.path, {"address": self.address, "key": self.key.hex(), "pid": os.getpid()})
        self.exit = None
        self.error = None

        def main():
            try:
                self.exit = asyncio.run(self.guard(handler, platform, options))
            except Exception as exc:
                self.error = exc
            finally:  # What the real process does by exiting.
                self.path.unlink(missing_ok=True)
                self.listener.close()

        self.thread = Thread(target=main, daemon=True)
        self.thread.start()

    async def guard(self, handler, platform, options):
        return await broker.serve(self.listener, handler, platform, **{"idle": 30, "poll_seconds": 0.05, **options})

    def wait(self):
        self.thread.join(10)
        self.path.unlink(missing_ok=True)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"VPUB_HOME": self.temp.name})
        self.env.start()
        self.seen = []

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    async def ok(self, payload):
        self.seen.append(payload)
        return {"n": len(self.seen)}

    def payload(self, action="operations"):
        return {"platform": "x", "account": "default", "action": action}

    def test_later_commands_reuse_the_same_session_without_starting_another(self):
        server = Server(self.ok)
        with patch.object(broker, "start") as start:
            self.assertEqual({"n": 1}, broker.call(self.payload(), 5))
            self.assertEqual({"n": 2}, broker.call(self.payload(), 5))
        start.assert_not_called()
        broker.release("x", "default")
        server.wait()
        self.assertEqual("shutdown", server.exit)

    def test_session_exits_when_idle(self):
        server = Server(self.ok, idle=0)
        server.wait()
        self.assertEqual("idle", server.exit)

    def test_session_exits_at_lifetime_limit(self):
        server = Server(self.ok, lifetime=0)
        server.wait()
        self.assertEqual("idle", server.exit)

    def test_stop_file_ends_the_session(self):
        server = Server(self.ok)
        atomic_json(home() / "STOP.json", {"reason": "test"})
        server.wait()
        self.assertEqual("stopped", getattr(server.error, "code", None))

    def test_any_error_is_returned_once_and_ends_the_session(self):
        async def fail(payload):
            self.seen.append(payload)
            raise VpubError("human_verification_required", "Verify.", "Run login.")
        server = Server(fail)
        with self.assertRaises(VpubError) as e:
            broker.call(self.payload(), 5)
        self.assertEqual("human_verification_required", e.exception.code)
        self.assertEqual("Run login.", e.exception.hint)
        server.wait()
        self.assertEqual("error", server.exit)
        self.assertEqual(1, len(self.seen))

    def test_sent_command_is_never_replayed_or_sent_to_a_one_shot_worker(self):
        async def hang(payload):
            self.seen.append(payload)
            await asyncio.sleep(1)
            return {}
        server = Server(hang)
        with patch.object(adapters.subprocess, "run") as one_shot:
            with patch.object(adapters, "browser_timeout", return_value=0.05):
                with self.assertRaises(VpubError) as e:
                    adapters.browser_call("x", "operations", "default")
        self.assertEqual("browser_session_interrupted", e.exception.code)
        one_shot.assert_not_called()
        self.assertEqual(1, len(self.seen))
        broker.release("x", "default")
        server.wait()

    def test_wrong_key_is_refused_and_session_survives(self):
        server = Server(self.ok)
        with self.assertRaises(Exception):
            Client(server.address, authkey=b"wrong")
        self.assertEqual({"n": 1}, broker.call(self.payload(), 5))
        broker.release("x", "default")
        server.wait()

    def test_stale_state_file_starts_a_new_session_or_falls_back(self):
        path = broker.state_path("x", "default")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, {"address": broker.new_address(), "key": "00", "pid": 1})
        with patch.object(broker, "start") as start, patch.object(broker, "START_SECONDS", 0.2):
            self.assertIsNone(broker.call(self.payload(), 5))
        start.assert_called_once()
        self.assertFalse(path.exists())

    def test_one_shot_runs_when_no_session_can_start(self):
        done = type("Done", (), {"stdout": '{"ok": true, "data": {"route": "one-shot"}}'})
        with patch.object(broker, "call", return_value=None), patch.object(adapters.subprocess, "run", return_value=done) as run:
            self.assertEqual({"route": "one-shot"}, adapters.browser_call("x", "operations", "default"))
        run.assert_called_once()

    def test_publish_closes_the_session_and_never_uses_it(self):
        done = type("Done", (), {"stdout": '{"ok": true, "data": {"route": "one-shot"}}'})
        for action in ("publish", "resume", "edit", "read_all", "chat_unlock"):
            with self.subTest(action=action):
                self.assertNotIn(action, broker.REUSABLE)
                with patch.object(broker, "call") as call, patch.object(broker, "release") as release, patch.object(adapters.subprocess, "run", return_value=done):
                    self.assertEqual({"route": "one-shot"}, adapters.browser_call("x", action, "default"))
                call.assert_not_called()
                release.assert_called_once_with("x", "default")

    def test_setting_idle_to_zero_disables_reuse(self):
        done = type("Done", (), {"stdout": '{"ok": true, "data": {}}'})
        with patch.dict(os.environ, {"VPUB_BROWSER_IDLE_SECONDS": "0"}), patch.object(broker, "call") as call, patch.object(adapters.subprocess, "run", return_value=done):
            adapters.browser_call("x", "operations", "default")
        call.assert_not_called()

    def test_writes_do_not_trust_a_cached_identity(self):
        for action in ("engagement", "comment_management", "direct_messages", "verify", "status", "adopt_work", "collections", "operations"):
            self.assertNotIn(action, broker.CACHED_IDENTITY)

    def test_busy_session_keeps_its_state_and_a_later_command_can_still_reach_it(self):
        free = Event()

        async def slow(payload):
            self.seen.append(payload)
            await asyncio.to_thread(free.wait, 10)
            return {"n": len(self.seen)}

        server = Server(slow)
        first = Thread(target=broker.call, args=(self.payload(), 15), daemon=True)
        first.start()
        while not self.seen:
            time.sleep(0.01)
        with patch.object(broker, "CONNECT_SECONDS", 0.2), patch.object(broker, "BUSY_SECONDS", 0.2, create=True), \
                patch.object(broker, "START_SECONDS", 0.3), patch.object(broker, "start") as start:
            with self.assertRaises(VpubError) as e:
                broker.call(self.payload(), 5)
        self.assertEqual("browser_busy", e.exception.code)
        start.assert_not_called()
        self.assertTrue(server.path.exists())
        free.set()
        first.join(10)
        self.assertEqual(2, broker.call(self.payload(), 15)["n"])
        broker.release("x", "default")
        server.wait()

    def test_losing_the_lock_race_leaves_the_winners_state_file_alone(self):
        path = broker.state_path("x", "default")
        atomic_json(path, {"address": broker.new_address(), "key": "00", "pid": os.getpid()})

        async def lost(platform, account):
            raise VpubError("browser_busy", "Another command owns this profile.")

        with patch.object(broker, "run", lost), patch.object(sys, "argv", ["broker", "x", "default"]):
            broker.main()
        self.assertTrue(path.exists())

    def test_startup_failure_is_reported_at_once_without_a_second_sign_in(self):
        def fail_to_start(platform, account):
            atomic_json(broker.failure_path(platform, account), {"code": "auth_expired", "message": "Sign in again.", "hint": "Run login."})
            return type("Done", (), {"poll": lambda self: 1})()

        started = time.monotonic()
        with patch.object(broker, "start", fail_to_start), patch.object(adapters.subprocess, "run") as one_shot:
            with self.assertRaises(VpubError) as e:
                adapters.browser_call("x", "operations", "default")
        self.assertEqual("auth_expired", e.exception.code)
        self.assertEqual("Run login.", e.exception.hint)
        self.assertLess(time.monotonic() - started, 5)
        one_shot.assert_not_called()

    def test_new_session_waits_for_one_that_is_still_closing(self):
        lock = Event()

        def hold():
            with profile_lock("x", "default"):
                lock.set()
                time.sleep(0.5)

        holder = Thread(target=hold, daemon=True)
        holder.start()
        lock.wait(5)
        with patch.object(broker, "start", return_value=type("Done", (), {"poll": lambda self: 1})()) as start:
            self.assertIsNone(broker.call(self.payload(), 5))
        start.assert_called_once()
        holder.join(5)

    def test_profile_held_by_another_command_reports_busy_without_starting_a_session(self):
        with profile_lock("x", "default"), patch.object(broker, "CLOSING_SECONDS", 0.2), patch.object(broker, "start") as start:
            with self.assertRaises(VpubError) as e:
                broker.call(self.payload(), 5)
        self.assertEqual("browser_busy", e.exception.code)
        start.assert_not_called()

    def test_broker_crash_falls_back_at_once(self):
        started = time.monotonic()
        with patch.object(broker, "start", return_value=type("Done", (), {"poll": lambda self: 1})()):
            self.assertIsNone(broker.call(self.payload(), 5))
        self.assertLess(time.monotonic() - started, 5)

    def test_lifetime_limit_also_applies_while_commands_keep_arriving(self):
        async def outlast(payload):
            await asyncio.sleep(0.4)
            return {}

        server = Server(outlast, lifetime=0.3, poll_seconds=30)
        self.assertEqual({}, broker.call(self.payload(), 5))
        server.wait()
        self.assertEqual("idle", server.exit)


if __name__ == "__main__":
    unittest.main()

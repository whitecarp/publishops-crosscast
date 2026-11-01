"""Shared browser session over a real local pipe, with a fake handler. No browser or platform."""
import asyncio
from multiprocessing.connection import Client, Listener
import os
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import adapters, browser_broker as broker, browser_runtime as rt
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
        answer = Event()

        async def hang(payload):
            self.seen.append(payload)
            await asyncio.to_thread(answer.wait, 10)
            return {}
        server = Server(hang)
        with patch.object(adapters.subprocess, "run") as one_shot:
            with patch.object(adapters, "browser_timeout", return_value=1):  # Long enough for a busy computer to get the command to the session.
                with self.assertRaises(VpubError) as e:
                    adapters.browser_call("x", "operations", "default")
        self.assertEqual("browser_session_interrupted", e.exception.code)
        one_shot.assert_not_called()
        self.assertEqual(1, len(self.seen))
        answer.set()
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

    def test_a_session_that_says_it_cannot_go_on_answers_first_and_then_ends(self):
        server = Server(self.ok, usable=lambda: False)
        self.assertEqual({"n": 1}, broker.call(self.payload(), 5))
        server.wait()
        self.assertEqual("error", server.exit)


class HangingBrowserTests(unittest.TestCase):
    """The whole session with a browser that stops answering. Only Chrome is replaced."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.closing = []
        self.done = {}

    def session(self, context, signed_in=None, opened=None, dispatch=None):
        """Runs the real session on a thread until it ends by itself, with only the browser's own calls faked."""
        pw = MagicMock(__aenter__=AsyncMock(return_value=MagicMock()), __aexit__=AsyncMock(return_value=False))
        self.dispatch = dispatch or AsyncMock(return_value={"finished": True})
        targets = [patch("patchright.async_api.async_playwright", return_value=pw), patch.object(rt, "open_context", opened or AsyncMock(return_value=context)),
                   patch.object(rt, "signed_in_page", signed_in or AsyncMock(return_value=(MagicMock(), "owner"))),
                   patch("video_publisher.browser_worker.dispatch", self.dispatch),
                   patch.object(rt, "CLOSE_SECONDS", 0.1), patch.object(broker, "SIGN_IN_SECONDS", 0.2)]
        for target in targets:
            target.start()
            self.addCleanup(target.stop)

        def main():
            try:
                asyncio.run(broker.run("x", "default", lambda: self.closing.append(True)))
            except Exception as exc:
                self.done["error"] = exc
        thread = Thread(target=main, daemon=True)
        thread.start()
        return thread

    def wait_for_session(self):
        deadline = time.monotonic() + 10
        while not broker.state_path("x", "default").exists():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)

    def lock_is_free(self):
        return broker.wait_for_lock("x", "default", 0)

    def test_a_save_that_never_answers_keeps_the_commands_answer_and_ends_the_session(self):
        async def forever():
            await asyncio.Event().wait()
        context = MagicMock(storage_state=forever, close=AsyncMock())
        thread = self.session(context)
        self.wait_for_session()
        self.assertEqual({"finished": True}, broker.call({"platform": "x", "account": "default", "action": "operations"}, 5))
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual([True], self.closing)
        self.dispatch.assert_awaited_once()
        context.close.assert_awaited_once()
        self.assertTrue(self.lock_is_free())
        self.assertFalse(broker.state_path("x", "default").exists())

    def test_a_client_that_gives_up_leaves_a_session_that_still_ends_by_itself_without_repeating_the_command(self):
        seen = []

        async def never(*args):
            seen.append(args)
            await asyncio.Event().wait()
        context = MagicMock(close=AsyncMock(), storage_state=AsyncMock(return_value={"cookies": []}))
        with patch("video_publisher.browser_worker.action_timeout", return_value=0.5):
            thread = self.session(context, dispatch=never)
            self.wait_for_session()
            with self.assertRaises(VpubError) as caught:
                broker.call({"platform": "x", "account": "default", "action": "operations"}, 0.1)
            self.assertEqual("browser_session_interrupted", caught.exception.code)
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(seen))
        self.assertEqual([True], self.closing)
        self.assertTrue(self.lock_is_free())

    def test_a_sign_in_check_that_never_answers_at_the_start_ends_the_session(self):
        async def forever(*args):
            await asyncio.Event().wait()
        context = MagicMock(close=AsyncMock())
        thread = self.session(context, signed_in=forever)
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual([True], self.closing)
        context.close.assert_awaited_once()
        self.assertTrue(self.lock_is_free())

    def test_a_sign_in_check_that_never_answers_later_fails_that_command_and_ends_the_session(self):
        answers = [(MagicMock(), "owner")]

        async def first_then_never(*args):
            if answers:
                return answers.pop()
            await asyncio.Event().wait()
        context = MagicMock(close=AsyncMock(), storage_state=AsyncMock(return_value={"cookies": []}))
        thread = self.session(context, signed_in=first_then_never)
        self.wait_for_session()
        with self.assertRaises(VpubError) as caught:
            broker.call({"platform": "x", "account": "default", "action": "operations"}, 5)
        self.assertEqual("browser_step_timeout", caught.exception.code)
        self.assertIn("who is signed in", caught.exception.message)
        self.assertNotIn("expired.", caught.exception.message)
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual([True], self.closing)
        self.assertTrue(self.lock_is_free())

    def test_a_command_that_runs_out_of_time_says_that_is_where_it_stopped(self):
        async def forever(*args):
            await asyncio.Event().wait()
        context = MagicMock(close=AsyncMock(), storage_state=AsyncMock(return_value={"cookies": []}))
        with patch("video_publisher.browser_worker.action_timeout", return_value=0.2):
            thread = self.session(context, dispatch=forever)
            self.wait_for_session()
            with self.assertRaises(VpubError) as caught:
                broker.call({"platform": "x", "account": "default", "action": "operations"}, 5)
        self.assertEqual("browser_step_timeout", caught.exception.code)
        self.assertIn("running the command", caught.exception.message)
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertTrue(self.lock_is_free())

    def test_chrome_that_never_starts_ends_the_session_too(self):
        async def forever(*args, **kwargs):
            await asyncio.Event().wait()
        thread = self.session(MagicMock(), opened=forever)
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual([True], self.closing)
        self.assertTrue(self.lock_is_free())


class RealProcessHangTests(unittest.TestCase):
    """A real separate process that hangs in every last step: only its exit guard can end it, and that lets go of the profile."""
    SCRIPT = """
import asyncio, sys
from unittest.mock import AsyncMock, MagicMock, patch
from video_publisher import browser_broker as broker, browser_runtime as rt

async def forever(*args, **kwargs):
    await asyncio.Event().wait()

pw = MagicMock(__aenter__=AsyncMock(return_value=MagicMock()), __aexit__=forever)
context = MagicMock(storage_state=forever, close=forever)
with patch("patchright.async_api.async_playwright", return_value=pw), patch.object(rt, "open_context", new=AsyncMock(return_value=context)), \\
        patch.object(rt, "signed_in_page", new=AsyncMock(return_value=(MagicMock(), "owner"))), \\
        patch("video_publisher.browser_worker.dispatch", new=AsyncMock(return_value={"finished": True})), patch.object(rt, "CLOSE_SECONDS", 0.2):
    asyncio.run(broker.run("x", "default", lambda: rt.exit_soon(1)))
"""

    def test_the_next_command_gets_the_answer_the_profile_and_a_clean_start(self):
        with tempfile.TemporaryDirectory() as temp:
            env = patch.dict(os.environ, {"VPUB_HOME": temp})
            env.start()
            self.addCleanup(env.stop)
            process = subprocess.Popen([sys.executable, "-c", self.SCRIPT], env=dict(os.environ))
            self.addCleanup(process.kill)
            deadline = time.monotonic() + 60
            while not broker.state_path("x", "default").exists():
                self.assertLess(time.monotonic(), deadline)
                self.assertIsNone(process.poll())
                time.sleep(0.05)
            self.assertEqual({"finished": True}, broker.call({"platform": "x", "account": "default", "action": "operations"}, 30))
            process.wait(30)
            self.assertTrue(broker.wait_for_lock("x", "default", 5))
            with patch.object(broker, "start", return_value=MagicMock(poll=lambda: 1)) as start:
                self.assertIsNone(broker.call({"platform": "x", "account": "default", "action": "operations"}, 5))
            start.assert_called_once()


if __name__ == "__main__":
    unittest.main()

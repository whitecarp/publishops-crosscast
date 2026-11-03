"""Host interruptions with real synthetic processes; never imports or opens a browser."""
import concurrent.futures
import contextlib
import ctypes
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from video_publisher import auth_jobs
from video_publisher.core import VpubError, atomic_json, home, read_json
from video_publisher.process_lock import alive


HERE = Path(__file__).resolve()
REAL_POPEN = subprocess.Popen


def below_normal():
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32")
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000):
            raise RuntimeError("Could not lower test priority")


def offline(event, args):
    if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "os.system", "os.startfile"}:
        raise AssertionError("External access is forbidden in this fixture")
    if event == "subprocess.Popen":
        command = args[1]
        allowed = (command.startswith(subprocess.list2cmdline([sys.executable, str(HERE), "--fixture-worker"]) + " ")
                   if isinstance(command, str) else len(command) >= 3 and command[1:3] == [str(HERE), "--fixture-worker"])
        if not allowed:
            raise AssertionError("Only this synthetic worker may start")


def eventually(check, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("Synthetic process did not reach the expected checkpoint")


def fake_login():
    payload = read_json(sys.argv[2])
    result = Path(payload["job_path"])
    atomic_json(result, {"state": "LOGIN_PENDING", "platform": payload["platform"],
                         "expires_epoch": time.time() + 900, "fixture_pid": os.getpid()})
    eventually(lambda: result.with_name("finish").exists(), 30)
    atomic_json(result, {"state": "LOGGED_IN", "platform": payload["platform"],
                         "logged_in": True, "identity": "synthetic-only"})


def fake_modules():
    return {"video_publisher.browser_worker": types.SimpleNamespace(main=fake_login),
            "video_publisher.browser_broker": types.SimpleNamespace(enabled=lambda: False, release=lambda *args: None)}


def fake_popen(command, **options):
    return REAL_POPEN([sys.executable, str(HERE), "--fixture-worker", command[-1]], **options)


def fixture(mode, request=None):
    below_normal()
    sys.addaudithook(offline)
    with patch.dict(sys.modules, fake_modules()):
        if mode == "--fixture-worker":
            payload = read_json(request)
            atomic_json(Path(payload["job_path"]).with_name("worker.fixture-pid"), {"pid": os.getpid()})
            if hasattr(auth_jobs, "run_job"):
                auth_jobs.run_job(request)
            else:  # Also permits the stale-status regression to run against the old launcher.
                sys.argv = [sys.argv[0], "--job", request]
                fake_login()
        else:
            atomic_json(home() / "controller.fixture-pid", {"pid": os.getpid()})
            with patch.object(auth_jobs.subprocess, "Popen", side_effect=fake_popen):
                auth_jobs.start("youtube", "default")
            atomic_json(home() / "launcher.ready", {"ready": True, "pid": os.getpid()})
            auth_jobs.wait("youtube", 180)


class AuthJobLifecycleTests(unittest.TestCase):
    def setUp(self):
        below_normal()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "vpub"),
                                      "BILI_CLI_HOME": str(self.root / "bili"),
                                      "VPUB_BROWSER_IDLE_SECONDS": "0", "PYTHONDONTWRITEBYTECODE": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.children = []
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for record in home().rglob("*.fixture-pid"):
            pid = read_json(record)["pid"]
            if alive(pid):
                os.kill(pid, signal.SIGTERM)
            eventually(lambda: not alive(pid))
        for child in self.children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)

    def launch(self, account="default"):
        def spawn(command, **options):
            child = fake_popen(command, **options)
            self.children.append(child)
            return child
        with patch.object(auth_jobs.subprocess, "Popen", side_effect=spawn):
            return auth_jobs.start("youtube", account)

    def request(self):
        return read_json(auth_jobs.job_path("youtube").with_suffix(".request.json"))

    def pending(self):
        return eventually(lambda: (answer := auth_jobs.status("youtube")) and
                          answer.get("state") == "LOGIN_PENDING")

    def finish(self, request=None):
        Path((request or self.request())["job_path"]).with_name("finish").touch()
        def completed():
            answer = auth_jobs.status("youtube")
            return answer if answer and answer.get("state") == "LOGGED_IN" else None
        return eventually(completed)

    def test_killed_worker_is_not_pending_and_explicit_resume_completes(self):
        self.launch()
        self.pending()
        old_request = self.request()
        sentinel = home() / "saved-profile-marker"
        sentinel.write_text("synthetic retained session", encoding="utf-8")
        os.kill(auth_jobs.status("youtube")["fixture_pid"], signal.SIGTERM)
        self.children[0].wait(timeout=5)
        with patch.object(auth_jobs.subprocess, "Popen") as spawn, self.assertRaises(VpubError) as caught:
            auth_jobs.wait("youtube", 5)
        self.assertEqual("login_interrupted", caught.exception.code)
        self.assertIn("once to resume", caught.exception.hint)
        self.assertIn("own terminal", caught.exception.hint)
        spawn.assert_not_called()
        self.assertEqual("LOGIN_INTERRUPTED", auth_jobs.status("youtube")["state"])
        self.launch()
        self.pending()
        self.assertNotEqual(old_request["job_path"], self.request()["job_path"])
        self.assertTrue(self.finish()["logged_in"])
        self.assertEqual("synthetic retained session", sentinel.read_text(encoding="utf-8"))

    def test_stopping_waiting_command_does_not_lose_surviving_worker(self):
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        command = REAL_POPEN([sys.executable, str(HERE), "--fixture-controller"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
        self.children.append(command)
        eventually(lambda: (home() / "launcher.ready").exists())
        self.pending()
        pid = read_json(auth_jobs.job_path("youtube"))["worker_pid"]

        def stop_orphan():
            if alive(pid):
                os.kill(pid, signal.SIGTERM)
            eventually(lambda: not alive(pid))
        self.addCleanup(stop_orphan)
        os.kill(read_json(home() / "launcher.ready")["pid"], signal.SIGTERM)
        command.wait(timeout=5)
        self.assertTrue(alive(pid))
        with patch.object(auth_jobs.subprocess, "Popen") as spawn:
            self.assertEqual("LOGIN_PENDING", auth_jobs.start("youtube", "default")["state"])
            self.assertTrue(self.finish()["logged_in"])
        spawn.assert_not_called()

    def test_start_returns_without_waiting_for_browser_startup(self):
        with patch.object(auth_jobs.subprocess, "Popen", return_value=types.SimpleNamespace(pid=os.getpid())) as spawn, \
                patch.object(auth_jobs.time, "sleep", side_effect=AssertionError("Launcher waited")):
            result = auth_jobs.start("youtube", "default")
        self.assertEqual("STARTING", result["state"])
        self.assertIn("--wait 5", result["hint"])
        self.assertEqual("video_publisher.auth_jobs", spawn.call_args.args[0][2])
        if os.name == "nt":
            self.assertEqual(subprocess.CREATE_NO_WINDOW, spawn.call_args.kwargs["creationflags"])

    def test_live_worker_is_not_restarted_even_after_expiry(self):
        self.launch()
        self.pending()
        request = self.request()
        atomic_json(request["job_path"], {"state": "VERIFICATION_REQUIRED", "expires_epoch": 1})
        with patch.object(auth_jobs.subprocess, "Popen") as spawn:
            self.assertEqual("VERIFICATION_REQUIRED", auth_jobs.start("youtube", "default")["state"])
        spawn.assert_not_called()

    def seed_start(self, pid=None, deadline=None):
        path = auth_jobs.job_path("youtube")
        saved = {"state": "STARTING", "platform": "youtube", "account": "default", "attempt": "0123456789abcdef0123456789abcdef",
                 "start_deadline": time.time() + 30 if deadline is None else deadline}
        if pid:
            saved["worker_pid"] = pid
        atomic_json(path, saved)
        request = path.parent / saved["attempt"] / "request.json"
        atomic_json(request, {"action": "login", "platform": "youtube", "account": "default",
                              "attempt": saved["attempt"], "job_path": str(request.with_name("result.json"))})
        return path, request

    def test_killed_launcher_before_pid_save_allows_worker_to_claim(self):
        path, request = self.seed_start()
        worker = types.SimpleNamespace(main=lambda: atomic_json(request.with_name("result.json"),
                                                                {"state": "LOGGED_IN", "logged_in": True}))
        with patch.dict(sys.modules, {**fake_modules(), "video_publisher.browser_worker": worker}):
            auth_jobs.run_job(request)
        self.assertEqual(os.getpid(), read_json(path)["worker_pid"])
        self.assertTrue(auth_jobs.wait("youtube")["logged_in"])

    def test_unclaimed_start_expires_and_late_worker_cannot_open_browser(self):
        _, request = self.seed_start(deadline=1)
        self.assertEqual("LOGIN_INTERRUPTED", auth_jobs.status("youtube")["state"])
        with patch.dict(sys.modules, fake_modules()), patch.object(auth_jobs, "_result", side_effect=AssertionError("Old job revived")):
            auth_jobs.run_job(request)
        self.assertFalse(request.with_name("result.json").exists())

    def test_old_worker_cannot_claim_or_overwrite_a_replacement(self):
        _, request = self.seed_start(deadline=1)
        with patch.object(auth_jobs.subprocess, "Popen"):
            replacement = auth_jobs.start("youtube", "default")
        with patch.dict(sys.modules, fake_modules()):
            auth_jobs.run_job(request)
        self.assertFalse(request.with_name("result.json").exists())
        atomic_json(request.with_name("result.json"), {"state": "LOGGED_IN", "logged_in": True})
        answer = auth_jobs.status("youtube")
        self.assertEqual(replacement["attempt"], answer["attempt"])
        self.assertEqual("STARTING", answer["state"])

    def test_final_answer_saved_during_exit_wins_over_interruption(self):
        _, request = self.seed_start(pid=os.getpid())
        def exited(pid):
            atomic_json(request.with_name("result.json"), {"state": "LOGGED_IN", "logged_in": True})
            return False
        with patch.object(auth_jobs, "alive", side_effect=exited):
            self.assertTrue(auth_jobs.wait("youtube")["logged_in"])

    def test_spawn_failure_is_durable_and_actionable(self):
        with patch.object(auth_jobs.subprocess, "Popen", side_effect=OSError("synthetic")), self.assertRaises(VpubError) as caught:
            auth_jobs.start("youtube", "default")
        self.assertEqual("login_start_failed", caught.exception.code)
        self.assertEqual("LOGIN_INTERRUPTED", auth_jobs.status("youtube")["state"])

    def test_worker_start_failure_remains_actionable_without_private_exception_text(self):
        _, request = self.seed_start()
        worker = types.SimpleNamespace(main=lambda: (_ for _ in ()).throw(RuntimeError("private synthetic page text")))
        with patch.dict(sys.modules, {**fake_modules(), "video_publisher.browser_worker": worker}):
            auth_jobs.run_job(request)
        self.assertNotIn("private synthetic", request.with_name("result.json").read_text(encoding="utf-8"))
        with self.assertRaises(VpubError) as caught:
            auth_jobs.wait("youtube")
        self.assertEqual("login_start_failed", caught.exception.code)
        self.assertIn("once to resume", caught.exception.hint)

    def test_pending_job_is_not_reused_for_another_account(self):
        self.launch()
        self.pending()
        with patch.object(auth_jobs.subprocess, "Popen") as spawn:
            for action in (lambda: auth_jobs.start("youtube", "second"),
                           lambda: auth_jobs.status("youtube", account="second"),
                           lambda: auth_jobs.wait("youtube", account="second")):
                with self.subTest(action=action), self.assertRaises(VpubError) as caught:
                    action()
                self.assertEqual("login_account_mismatch", caught.exception.code)
        spawn.assert_not_called()
        self.assertEqual("LOGIN_PENDING", auth_jobs.wait("youtube", account="default")["state"])

    def test_named_account_can_check_its_own_job(self):
        self.launch("named")
        eventually(lambda: auth_jobs.status("youtube", account="named").get("state") == "LOGIN_PENDING")
        self.assertEqual("named", auth_jobs.wait("youtube", account="named")["account"])
        with self.assertRaises(VpubError) as caught:
            auth_jobs.start("youtube", "default")
        self.assertEqual("login_account_mismatch", caught.exception.code)

    def test_cli_status_cannot_return_another_accounts_pending_job(self):
        from video_publisher import adapters, cli
        self.launch("named")
        eventually(lambda: auth_jobs.status("youtube", account="named").get("state") == "LOGIN_PENDING")
        # The free CLI accepts only default; an older or API-created named job must not satisfy it.
        args = cli.parser().parse_args(["auth", "status", "youtube", "--account", "default", "--method", "browser"])
        with patch.object(adapters, "auth_status", side_effect=AssertionError("Browser fallback")), self.assertRaises(VpubError) as caught:
            cli.run_command(args)
        self.assertEqual("login_account_mismatch", caught.exception.code)

    def test_attempt_pointer_rejects_malformed_or_escaping_paths(self):
        path, _ = self.seed_start()
        saved = read_json(path)
        for attempt in ("", None, [], "..", "../outside", str(self.root), "nested/" + "a" * 32, "A" * 32):
            with self.subTest(attempt=attempt):
                atomic_json(path, {**saved, "attempt": attempt})
                with patch.object(auth_jobs.subprocess, "Popen") as spawn, self.assertRaises(VpubError) as caught:
                    auth_jobs.start("youtube", "default")
                self.assertEqual("invalid_login_job", caught.exception.code)
                spawn.assert_not_called()

    def test_worker_request_and_result_paths_are_bound_to_attempt(self):
        _, request = self.seed_start()
        payload = read_json(request)
        changed = [{**payload, "job_path": str(self.root / "outside.json")},
                   {**payload, "attempt": "a" * 32},
                   {**payload, "account": "second"}]
        for data in changed:
            with self.subTest(data=data):
                atomic_json(request, data)
                with self.assertRaises(VpubError) as caught:
                    auth_jobs.run_job(request)
                self.assertIn(caught.exception.code, {"invalid_login_job", "login_account_mismatch"})
                self.assertFalse((self.root / "outside.json").exists())
                self.assertFalse(request.with_name("result.json").exists())
        with patch.object(auth_jobs, "read_json", side_effect=AssertionError("Read outside attempt")), self.assertRaises(VpubError):
            auth_jobs.run_job(self.root / "request.json")

    def test_legacy_pending_account_is_checked_using_its_request(self):
        path = home() / "auth-jobs" / "youtube.json"
        atomic_json(path, {"state": "LOGIN_PENDING", "platform": "youtube", "expires_epoch": time.time() + 900})
        atomic_json(path.with_suffix(".request.json"), {"platform": "youtube", "account": "named"})
        self.assertEqual("LOGIN_PENDING", auth_jobs.wait("youtube", account="named")["state"])
        with self.assertRaises(VpubError) as caught:
            auth_jobs.start("youtube", "default")
        self.assertEqual("login_account_mismatch", caught.exception.code)

    def test_legacy_main_final_save_cannot_erase_replacement_before_claim(self):
        from video_publisher import browser_worker
        for succeeds in (False, True):
            with self.subTest(succeeds=succeeds), tempfile.TemporaryDirectory() as tmp, \
                    patch.dict(os.environ, {"VPUB_HOME": str(Path(tmp) / "vpub"), "BILI_CLI_HOME": str(Path(tmp) / "bili")}):
                legacy = home() / "auth-jobs" / "youtube.json"
                old_request = legacy.with_suffix(".request.json")
                atomic_json(legacy, {"state": "LOGIN_PENDING", "platform": "youtube", "expires_epoch": 1})
                atomic_json(old_request, {"action": "login", "platform": "youtube", "account": "default", "job_path": str(legacy)})
                original_request = old_request.read_bytes()
                replacement = {}

                async def finishing_old_login(payload):
                    self.assertEqual(str(legacy), payload["job_path"])
                    with patch.object(auth_jobs.subprocess, "Popen"):
                        replacement.update(auth_jobs.start("youtube", "default"))
                    if not succeeds:
                        raise VpubError("login_timeout", "Synthetic old login expired")
                    return {"logged_in": True, "platform": "youtube", "account": "default", "identity": "old-synthetic"}

                # The real main performs the late FAILED/LOGGED_IN save to its original job_path.
                with patch.object(browser_worker, "run", finishing_old_login), \
                        patch.object(sys, "argv", ["worker", "--job", str(old_request)]), contextlib.redirect_stdout(io.StringIO()):
                    browser_worker.main()
                self.assertEqual("LOGGED_IN" if succeeds else "FAILED", read_json(legacy)["state"])
                self.assertEqual(replacement["attempt"], read_json(auth_jobs.job_path("youtube")).get("attempt"))
                self.assertEqual(original_request, old_request.read_bytes())
                request = auth_jobs.job_path("youtube").parent / replacement["attempt"] / "request.json"
                worker = types.SimpleNamespace(main=lambda: atomic_json(request.with_name("result.json"),
                                                                        {"state": "LOGGED_IN", "logged_in": True, "identity": "new-synthetic"}))
                with patch.dict(sys.modules, {**fake_modules(), "video_publisher.browser_worker": worker}):
                    auth_jobs.run_job(request)
                self.assertEqual("new-synthetic", auth_jobs.wait("youtube")["identity"])

    def test_legacy_expiry_is_read_only_and_late_completion_stays_visible_before_replacement(self):
        from video_publisher import browser_worker
        legacy = home() / "auth-jobs" / "youtube.json"
        request = legacy.with_suffix(".request.json")
        atomic_json(legacy, {"state": "LOGIN_PENDING", "platform": "youtube", "expires_epoch": 1})
        atomic_json(request, {"action": "login", "platform": "youtube", "account": "default", "job_path": str(legacy)})
        original = legacy.read_bytes()
        with self.assertRaises(VpubError) as caught:
            auth_jobs.wait("youtube")
        self.assertEqual("login_interrupted", caught.exception.code)
        self.assertEqual(original, legacy.read_bytes())
        self.assertFalse(auth_jobs.job_path("youtube").exists())

        async def completed(payload):
            return {"logged_in": True, "platform": "youtube", "account": "default", "identity": "late-synthetic"}
        with patch.object(browser_worker, "run", completed), patch.object(sys, "argv", ["worker", "--job", str(request)]), \
                contextlib.redirect_stdout(io.StringIO()):
            browser_worker.main()
        self.assertEqual("late-synthetic", auth_jobs.wait("youtube")["identity"])

    def test_concurrent_starts_launch_once(self):
        calls = []
        def spawn(command, **options):
            calls.append(command)
            return types.SimpleNamespace(pid=os.getpid())
        with patch.object(auth_jobs.subprocess, "Popen", side_effect=spawn), concurrent.futures.ThreadPoolExecutor(2) as pool:
            answers = list(pool.map(lambda _: auth_jobs.start("youtube", "default"), range(2)))
        self.assertEqual(1, len(calls))
        self.assertEqual(answers[0]["attempt"], answers[1]["attempt"])

    def test_cli_status_reports_interruption_without_browser_fallback(self):
        from video_publisher import adapters, cli
        self.seed_start(deadline=1)
        args = cli.parser().parse_args(["auth", "status", "youtube", "--method", "browser"])
        with patch.object(adapters, "auth_status", side_effect=AssertionError("Browser fallback")), self.assertRaises(VpubError) as caught:
            cli.run_command(args)
        self.assertEqual("login_interrupted", caught.exception.code)

    def test_old_final_result_and_expired_untracked_job(self):
        path = home() / "auth-jobs" / "youtube.json"
        final = {"state": "LOGGED_IN", "platform": "youtube", "logged_in": True}
        atomic_json(path, final)
        self.assertEqual(final, auth_jobs.wait("youtube"))
        atomic_json(path, {"state": "LOGIN_PENDING", "platform": "youtube", "expires_epoch": 1})
        with self.assertRaises(VpubError) as caught:
            auth_jobs.wait("youtube")
        self.assertEqual("login_interrupted", caught.exception.code)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].startswith("--fixture-"):
        fixture(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        unittest.main()

"""A one-shot command on the hidden desktop: what it started has gone by the time it returns, and one that is killed leaves its worker to finish. Stand-ins and harmless local processes; no browser."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from video_publisher import hidden_desktop as hd
from video_publisher.browser_runtime import profile_lock
from video_publisher.core import VpubError, atomic_json, home, read_json


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.user, self.kernel = MagicMock(), MagicMock()
        self.process = hd.PROCESS_INFORMATION(hProcess=101, hThread=102, dwProcessId=103)
        self.kernel.CreateJobObjectW.return_value = 104
        self.kernel.WaitForSingleObject.return_value = hd.WAIT_OBJECT_0
        self.kernel.QueryInformationJobObject.side_effect = self.query
        self.kernel.TerminateJobObject.side_effect = self.terminate
        self.active = [0]  # What the job says in turn. The last one repeats.
        for target in (patch.object(hd, "libraries", return_value=(self.user, self.kernel)),
                       patch.object(hd, "launch", return_value=(self.user, self.kernel, 105, self.process))):
            self.launch = target.start()
            self.addCleanup(target.stop)
        taskkill = patch.object(hd.subprocess, "run")  # The process here is made up, so this must never really run.
        self.taskkill = taskkill.start()
        self.addCleanup(taskkill.stop)

    def query(self, job, kind, info, size, returned):
        info._obj.ActiveProcesses = self.active.pop(0) if len(self.active) > 1 else self.active[0]
        return 1

    def terminate(self, job, code):
        self.active = [0]
        return 1

    def called(self, name):
        return [call[0] for call in self.kernel.method_calls].count(name)

    def closed(self, *handles):
        for handle in handles:
            self.kernel.CloseHandle.assert_any_call(handle)
        self.user.CloseDesktop.assert_called_once_with(105)

    def test_the_worker_joins_the_job_as_it_starts_and_is_never_left_suspended(self):
        hd.run(["worker"], 1)
        self.launch.assert_called_once_with(["worker"])
        order = [call[0] for call in self.kernel.method_calls]
        self.assertLess(order.index("AssignProcessToJobObject"), order.index("WaitForSingleObject"))
        self.assertNotIn("ResumeThread", order)

    def test_a_job_with_nothing_left_has_nothing_ended(self):
        self.assertIsNone(hd.run(["worker"], 1))
        self.assertEqual(0, self.called("TerminateJobObject"))
        self.closed(101, 102, 104)

    def test_a_browser_gets_time_to_close_itself(self):
        self.active = [3, 2, 0]
        hd.run(["worker"], 1)
        self.assertEqual(0, self.called("TerminateJobObject"))
        self.assertEqual(3, self.called("QueryInformationJobObject"))

    def test_a_failed_look_does_not_cut_that_time_short(self):
        """From a review: one failed read of the job counted as time up, so it was ended at once."""
        looks = iter((0, 0, 1))

        def query(job, kind, info, size, returned):
            info._obj.ActiveProcesses = 0
            return next(looks)
        self.kernel.QueryInformationJobObject.side_effect = query
        hd.run(["worker"], 1)
        self.assertEqual(0, self.called("TerminateJobObject"))
        self.assertEqual(3, self.called("QueryInformationJobObject"))

    def test_what_outstays_that_time_is_ended(self):
        self.active = [1]
        with patch.object(hd, "CLOSES_ITSELF_SECONDS", 0):
            self.assertIsNone(hd.run(["worker"], 1))
        self.kernel.TerminateJobObject.assert_called_once_with(104, 1)
        self.taskkill.assert_not_called()
        self.closed(101, 102, 104)

    def test_a_taskkill_that_hangs_still_leaves_the_job_ended(self):
        self.kernel.WaitForSingleObject.return_value = hd.WAIT_TIMEOUT
        self.active = [3]
        self.taskkill.side_effect = subprocess.TimeoutExpired(["taskkill"], hd.CLEANUP_SECONDS)
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            hd.run(["worker"], 1)
        self.assertEqual(["worker"], caught.exception.cmd)
        self.kernel.TerminateJobObject.assert_called_once_with(104, 1)

    def test_a_timeout_follows_parents_and_ends_the_job_at_once(self):
        self.kernel.WaitForSingleObject.return_value = hd.WAIT_TIMEOUT
        self.active = [3]
        with self.assertRaises(subprocess.TimeoutExpired):
            hd.run(["worker"], 1)
        self.assertEqual(["taskkill", "/PID", "103", "/T", "/F"], self.taskkill.call_args.args[0])
        self.kernel.TerminateJobObject.assert_called_once_with(104, 1)
        self.assertEqual(2, self.called("QueryInformationJobObject"))
        self.closed(101, 102, 104)

    def test_a_failed_wait_is_not_a_finished_worker(self):
        for status in (0xFFFFFFFF, 0x80):
            self.active = [1]
            self.kernel.WaitForSingleObject.return_value = status
            with self.subTest(status=status), self.assertRaises(VpubError) as caught:
                hd.run(["worker"], 1)
            self.assertEqual("browser_error", caught.exception.code)
        self.assertEqual(2, self.called("TerminateJobObject"))

    def test_an_interrupted_wait_ends_everything(self):
        self.kernel.WaitForSingleObject.side_effect = KeyboardInterrupt()
        self.active = [1]
        with self.assertRaises(KeyboardInterrupt):
            hd.run(["worker"], 1)
        self.kernel.TerminateJobObject.assert_called_once_with(104, 1)
        self.closed(101, 102, 104)

    def test_cleanup_it_cannot_confirm_says_so(self):
        for broken in ("TerminateJobObject", "QueryInformationJobObject", "still running"):
            self.active = [1]
            self.kernel.TerminateJobObject.side_effect = self.terminate
            self.kernel.QueryInformationJobObject.side_effect = self.query
            if broken == "still running":
                self.kernel.TerminateJobObject.side_effect = None
                self.kernel.TerminateJobObject.return_value = 1
            else:
                getattr(self.kernel, broken).side_effect = None
                getattr(self.kernel, broken).return_value = 0
            with self.subTest(broken=broken), patch.object(hd, "CLOSES_ITSELF_SECONDS", 0), patch.object(hd, "CLEANUP_SECONDS", 0), self.assertRaises(VpubError) as caught:
                hd.run(["worker"], 1)
            self.assertEqual("browser_error", caught.exception.code)
            self.assertIn("Could not confirm", caught.exception.message)
            self.kernel.CloseHandle.assert_any_call(104)

    def test_the_command_still_runs_where_no_job_can_be_made_or_joined(self):
        for broken, closes_a_job in (("CreateJobObjectW", False), ("AssignProcessToJobObject", True)):
            self.kernel.reset_mock(return_value=False)
            getattr(self.kernel, broken).return_value = 0
            with self.subTest(broken=broken), patch.object(hd.subprocess, "run") as taskkill:
                self.assertIsNone(hd.run(["worker"], 1))
                taskkill.assert_not_called()
                self.assertEqual(1, self.called("WaitForSingleObject"))
                self.assertEqual(0, self.called("TerminateJobObject"))
                self.assertEqual(closes_a_job, (104,) in [call.args for call in self.kernel.CloseHandle.call_args_list])
            self.kernel.CreateJobObjectW.return_value = 104
            self.kernel.AssignProcessToJobObject.return_value = 1
            self.user.reset_mock()

    def test_without_a_job_a_timeout_goes_back_to_taskkill(self):
        self.kernel.AssignProcessToJobObject.return_value = 0
        self.kernel.WaitForSingleObject.return_value = hd.WAIT_TIMEOUT
        with patch.object(hd.subprocess, "run") as taskkill, self.assertRaises(subprocess.TimeoutExpired):
            hd.run(["worker"], 1)
        self.assertEqual(["taskkill", "/PID", "103", "/T", "/F"], taskkill.call_args.args[0])
        self.assertEqual(hd.CLEANUP_SECONDS, taskkill.call_args.kwargs["timeout"])
        self.assertEqual(0, self.called("TerminateJobObject"))

    def test_a_job_sets_no_kill_on_close(self):
        hd.run(["worker"], 1)
        self.assertEqual(0, self.called("SetInformationJobObject"))

    def test_a_process_left_running_gets_no_job_and_says_what_a_failed_look_means(self):
        started = hd.Started(["broker"])
        self.launch.assert_called_once_with(["broker"])
        self.kernel.WaitForSingleObject.side_effect = [hd.WAIT_TIMEOUT, hd.WAIT_OBJECT_0, 0xFFFFFFFF]
        self.assertIsNone(started.poll())
        self.assertEqual(1, started.poll())
        with self.assertRaises(VpubError) as caught:
            started.poll()
        self.assertEqual("browser_error", caught.exception.code)
        self.assertEqual(0, self.called("CreateJobObjectW"))


class AnswerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)

    def test_a_finished_commands_answer_stands_when_its_cleanup_cannot_be_confirmed(self):
        answer = {"ok": True, "data": {"state": "SUBMISSION_VERIFIED"}}

        def worker(argv, timeout):
            atomic_json(read_json(argv[-1])["result_path"], answer)
            raise VpubError("browser_error", "Could not confirm that the background browser processes have stopped.")
        with patch.object(hd, "run", side_effect=worker):
            self.assertEqual(answer, hd.call_worker({"action": "publish"}, 1))
        self.assertEqual([], list((home() / "browser-jobs").iterdir()))

    def test_a_finished_commands_answer_stands_after_a_timeout_too(self):
        answer = {"ok": True, "data": {"state": "SUBMISSION_VERIFIED"}}

        def worker(argv, timeout):
            atomic_json(read_json(argv[-1])["result_path"], answer)
            raise subprocess.TimeoutExpired(argv, timeout)
        with patch.object(hd, "run", side_effect=worker):
            self.assertEqual(answer, hd.call_worker({"action": "publish"}, 1))

    def test_a_timeout_with_no_answer_says_so(self):
        with patch.object(hd, "run", side_effect=subprocess.TimeoutExpired(["worker"], 1)), self.assertRaises(VpubError) as caught:
            hd.call_worker({"action": "publish"}, 1)
        self.assertEqual("browser_timeout", caught.exception.code)

    def test_with_no_answer_the_error_stands(self):
        with patch.object(hd, "run", side_effect=VpubError("browser_unavailable", "No desktop.")), self.assertRaises(VpubError) as caught:
            hd.call_worker({"action": "publish"}, 1)
        self.assertEqual("browser_unavailable", caught.exception.code)
        self.assertEqual([], list((home() / "browser-jobs").iterdir()))


class TimesOutWhenTold:
    """kernel32, except that its wait for the worker reports a timeout when the test says so, however slow a machine is to start the tree."""

    def __init__(self, kernel32, told):
        self.kernel32, self.told = kernel32, told

    def __getattr__(self, name):
        return getattr(self.kernel32, name)

    def WaitForSingleObject(self, handle, milliseconds):
        self.told.wait(120)
        return hd.WAIT_TIMEOUT


@unittest.skipUnless(os.name == "nt", "Windows processes")
class RealProcessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "home"), "PYTHONPATH": str(Path(hd.__file__).resolve().parents[1])})
        env.start()
        self.addCleanup(env.stop)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.argv = [sys.executable, "-B", str(Path(__file__).with_name("hidden_desktop_fixture.py")), "worker", str(self.root)]
        self.handles = []
        self.addCleanup(self.reap)

    def reap(self):
        (self.root / "finish").touch()
        for handle in self.handles:
            if self.kernel.WaitForSingleObject(handle, 0) == hd.WAIT_TIMEOUT:
                self.kernel.TerminateProcess(handle, 90)
            self.kernel.WaitForSingleObject(handle, 5000)
            self.kernel.CloseHandle(handle)

    def wait_for_tree(self):
        deadline = time.monotonic() + 120
        while not (self.root / "worker.json").exists():
            self.assertLess(time.monotonic(), deadline, "The fixture's tree never started.")
            time.sleep(0.02)
        for name in ("worker", "child", "grandchild"):
            handle = self.kernel.OpenProcess(0x00100001, False, json.loads((self.root / (name + ".json")).read_text())["pid"])  # SYNCHRONIZE | PROCESS_TERMINATE.
            self.assertTrue(handle)
            self.handles.append(handle)

    def run_in_a_thread(self):
        results = []

        def run():
            try:
                results.append(hd.run(self.argv, 120))
            except BaseException as error:
                results.append(error)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, results

    def assert_profile_is_busy(self):
        with self.assertRaises(VpubError) as caught:
            with profile_lock("douyin", "lifecycle-test"):
                pass
        self.assertEqual("browser_busy", caught.exception.code)

    def assert_all_gone(self):
        self.assertEqual([0, 0, 0], [self.kernel.WaitForSingleObject(handle, 5000) for handle in self.handles], "258 means still running")
        with profile_lock("douyin", "lifecycle-test"):
            pass
        import msvcrt
        with (self.root / "browser.lock").open("r+b") as lock:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)

    def test_a_normal_exit_leaves_nothing_running(self):
        with patch.object(hd, "CLOSES_ITSELF_SECONDS", 0.2):
            thread, results = self.run_in_a_thread()
            self.wait_for_tree()
            self.assert_profile_is_busy()
            (self.root / "finish").touch()
            thread.join(60)
        self.assertEqual([None], results)
        self.assert_all_gone()

    def test_a_job_joined_too_late_still_has_its_whole_tree_ended_on_a_timeout(self):
        """From a review: a venv's python.exe only starts the real one, so a late join can hold nothing else. These are real processes and a real taskkill, on this test's own tree."""
        told, real, root = threading.Event(), hd.libraries, self.root
        self.addCleanup(told.set)

        class JoinsLate(TimesOutWhenTold):
            def AssignProcessToJobObject(self, job, handle):
                deadline = time.monotonic() + 120
                while not (root / "worker.json").exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                return self.kernel32.AssignProcessToJobObject(job, handle)

        def libraries():
            user32, kernel32 = real()
            return user32, JoinsLate(kernel32, told)
        with patch.object(hd, "libraries", libraries):
            thread, results = self.run_in_a_thread()
            self.wait_for_tree()
            self.assert_profile_is_busy()
            told.set()
            thread.join(60)
        self.assertEqual(1, len(results))
        self.assertIsInstance(results[0], subprocess.TimeoutExpired)
        self.assert_all_gone()

    def test_the_job_alone_ends_the_whole_tree_on_a_timeout(self):
        told, real = threading.Event(), hd.libraries
        self.addCleanup(told.set)

        def libraries():
            user32, kernel32 = real()
            return user32, TimesOutWhenTold(kernel32, told)
        with patch.object(hd, "libraries", libraries), patch.object(hd.subprocess, "run") as taskkill:
            thread, results = self.run_in_a_thread()
            self.wait_for_tree()
            self.assert_profile_is_busy()
            told.set()
            thread.join(60)
        taskkill.assert_called_once()
        self.assertEqual(1, len(results))
        self.assertIsInstance(results[0], subprocess.TimeoutExpired)
        self.assertFalse((self.root / "finished.json").exists())
        self.assert_all_gone()

    def test_a_killed_command_leaves_its_worker_to_finish(self):
        code = "import sys; from video_publisher import hidden_desktop as hd; hd.run(sys.argv[1:], 120)"
        command = subprocess.Popen([sys.executable, "-B", "-c", code, *self.argv], creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(command.wait)
        self.addCleanup(command.kill)
        self.wait_for_tree()
        command.kill()
        command.wait(30)
        self.assertEqual(hd.WAIT_TIMEOUT, self.kernel.WaitForSingleObject(self.handles[0], 1000), "The worker went with its command.")
        self.assert_profile_is_busy()
        (self.root / "finish").touch()
        self.assertEqual(0, self.kernel.WaitForSingleObject(self.handles[0], 30000))
        self.assertTrue((self.root / "finished.json").exists())


if __name__ == "__main__":
    unittest.main()

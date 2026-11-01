"""Progress lines on stderr while a browser command works. Real processes and a fake browser package; nothing is contacted."""
import ast
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import adapters, browser_broker as broker, browser_runtime as runtime, comment_actions, direct_messages, engagement, progress
from video_publisher.browser_backends import Transaction
from video_publisher.core import atomic_json, home, read_json

PRIVATE = "SECRET-VIEWER-WORDS"
OWNER = "SecretOwner"
CHANNEL = "UC" + "s" * 22
VIDEO = "dQw4w9WgXcQ"
POST = "1234567890"
# The whole of a browser for the commands below, shaped like the real package's own module.
FAKE_BROWSER = r'''
import asyncio, ctypes, json, os, sys

STATE = os.environ["FAKE_BROWSER_STATE"]
PRIVATE, OWNER, CHANNEL, VIDEO = "SECRET-VIEWER-WORDS", "SecretOwner", "UC" + "s" * 22, "dQw4w9WgXcQ"


def state():
    with open(STATE, encoding="utf-8") as file:
        return json.load(file)


def remember(**changes):
    saved = {**state(), **changes}
    with open(STATE, "w", encoding="utf-8") as file:
        json.dump(saved, file)


def desktop_name():
    if os.name != "nt":
        return None
    user32, kernel32 = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
    user32.GetThreadDesktop.restype = ctypes.c_void_p
    name = ctypes.create_unicode_buffer(256)
    user32.GetUserObjectInformationW(ctypes.c_void_p(user32.GetThreadDesktop(kernel32.GetCurrentThreadId())), 2, name, 512, None)
    return name.value


class Element:
    def __init__(self, selector):
        self.selector = selector

    @property
    def first(self):
        return self

    def exists(self):
        return "verifycenter" not in self.selector

    def locator(self, selector):
        return Element(self.selector + " " + selector)

    def filter(self, **options):
        return self

    async def all(self):
        return [self] if self.exists() else []

    async def count(self):
        return 1 if self.exists() else 0

    async def is_visible(self):
        return self.exists()

    async def get_attribute(self, name):
        return "/" + OWNER

    async def wait_for(self, **options):
        return None

    async def evaluate(self, script, argument=None):
        return bool(argument and "unlike" in argument and state().get("liked"))

    async def evaluate_all(self, script):
        if "ytcp-video-row" in self.selector:
            return [{"href": f"/video/{VIDEO}/edit", "title": PRIVATE, "visibility": "Public", "date": "", "views": "1", "comments": "1", "likes": "1"}]
        return [{"url": f"https://x.com/{OWNER}/status/1111111111", "text": PRIVATE}]

    async def click(self, trial=False, timeout=None):
        if not trial:
            remember(liked=True, clicks=state().get("clicks", 0) + 1)


class Page:
    url = "about:blank"

    def set_default_timeout(self, value):
        pass

    def locator(self, selector):
        return Element(selector)

    def get_by_text(self, *args, **options):
        return Element("verifycenter")

    async def goto(self, url, **options):
        await asyncio.sleep(float(os.environ.get("FAKE_BROWSER_SLOW", 0)))
        if os.environ.get("FAKE_BROWSER_FAILS") and url.rstrip("/") != "https://x.com/home":
            raise ValueError(f"Could not open {url}: {PRIVATE} {OWNER}")
        self.url = url.rstrip("/") + f"/channel/{CHANNEL}" if url.rstrip("/") == "https://studio.youtube.com" else url

    async def wait_for_timeout(self, value):
        return None


class Context:
    pages = [Page()]

    async def add_cookies(self, cookies):
        return None

    async def storage_state(self):
        return {"cookies": []}

    async def close(self):
        return None


class Chromium:
    async def launch_persistent_context(self, path, **options):
        remember(desktops=state().get("desktops", []) + [desktop_name()], headless=options.get("headless"))
        return Context()


class Playwright:
    chromium = Chromium()


class Manager:
    async def __aenter__(self):
        return Playwright()

    async def __aexit__(self, *args):
        return False


def async_playwright():
    return Manager()


class TimeoutError(Exception):
    pass
'''


def lines_of(stderr):
    return [json.loads(line) for line in stderr.splitlines() if line.strip()]


def stages_of(stderr):
    return [line["stage"] for line in lines_of(stderr)]


class Temporary(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        progress._pressed.clear()
        self.addCleanup(progress._pressed.clear)
        self.addCleanup(progress.attach, {})

    def relayed(self, platform="x", during=None):
        """What a browser step's words become on stderr, and that step's payload."""
        payload, errors = {}, io.StringIO()
        with contextlib.redirect_stderr(errors), progress.relay(platform, payload):
            if during:
                during(payload["stages_path"])
        return errors.getvalue(), payload


class StageTests(Temporary):
    def test_only_the_fixed_words_can_be_written_at_all(self):
        path = self.root / "stages"
        path.touch()
        for word in (PRIVATE, OWNER, "https://x.com/" + OWNER, "", None):
            progress.stage(word, str(path))
        self.assertEqual("", path.read_text())

    def test_a_command_that_stopped_waiting_leaves_no_new_file(self):
        path = self.root / "stages"
        progress.stage("pressing", str(path))
        self.assertFalse(path.exists())

    def test_a_stage_that_cannot_be_written_never_fails_the_command(self):
        progress.stage("pressing", str(self.root))
        progress.stage("pressing", str(self.root / "missing" / "stages"))
        progress.stage("pressing")

    def test_each_command_in_a_shared_session_writes_to_its_own_file(self):
        first, second = self.root / "first", self.root / "second"
        first.touch()
        second.touch()
        progress.attach({"stages_path": str(first)})
        progress.stage("opening the page")
        progress.attach({"stages_path": str(second)})
        progress.stage("pressing")
        self.assertEqual(("opening the page\n", "pressing\n"), (first.read_text(), second.read_text()))

    def test_every_stage_named_in_the_code_is_one_of_the_fixed_words(self):
        source = Path(progress.__file__).parent
        named = set()
        for path in source.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) == "stage" and node.args:
                    self.assertIsInstance(node.args[0], (ast.Constant, ast.Name), path.name)
                    if isinstance(node.args[0], ast.Constant):
                        named.add(node.args[0].value)
        self.assertLessEqual(named, progress.STAGES)
        self.assertLessEqual({"starting the browser", "checking the account", "opening the page", "reading the page", "uploading", "pressing", "closing the browser", "waiting for the browser"}, named)


class RelayTests(Temporary):
    def test_each_word_becomes_one_short_line_with_nothing_else_in_it(self):
        stderr, _ = self.relayed(during=lambda path: progress.stage("opening the page", path))
        line, = lines_of(stderr)
        self.assertEqual({"event", "platform", "stage", "elapsed_seconds"}, set(line))
        self.assertEqual(("browser_progress", "x", "opening the page"), (line["event"], line["platform"], line["stage"]))
        self.assertIsInstance(line["elapsed_seconds"], (int, float))

    def test_anything_else_in_the_file_never_reaches_stderr(self):
        def during(path):
            with open(path, "a", encoding="utf-8") as file:
                file.write(f"{PRIVATE}\nhttps://x.com/{OWNER}\n你好\npressing\n")
        stderr, _ = self.relayed(during=during)
        self.assertEqual(["pressing"], stages_of(stderr))
        self.assertNotIn(PRIVATE, stderr)
        self.assertNotIn(OWNER, stderr)

    def test_its_file_is_gone_afterwards_even_when_the_step_fails(self):
        payload = {}
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(ValueError), progress.relay("x", payload):
            self.assertTrue(Path(payload["stages_path"]).exists())
            raise ValueError
        self.assertFalse(Path(payload["stages_path"]).exists())
        self.assertEqual([], [path for path in (home() / "browser-jobs").iterdir()])

    def test_a_stage_that_lasts_is_said_again_so_it_does_not_look_hung(self):
        def during(path):
            progress.stage("uploading", path)
            time.sleep(0.5)
        with patch.object(progress, "QUIET_SECONDS", 0.1), patch.object(progress, "LOOK_SECONDS", 0.02):
            stderr, _ = self.relayed(during=during)
        seen = lines_of(stderr)
        self.assertGreater(len(seen), 2)
        self.assertEqual({"uploading"}, {line["stage"] for line in seen})
        self.assertGreater(seen[-1]["elapsed_seconds"], seen[0]["elapsed_seconds"])

    def test_the_same_word_twice_is_one_line(self):
        def during(path):
            progress.stage("starting the browser", path)
            progress.stage("starting the browser", path)
        self.assertEqual(["starting the browser"], stages_of(self.relayed(during=during)[0]))

    def test_what_follows_a_press_is_the_read_back_including_in_a_later_step(self):
        def press(path):
            for word in ("reading the page", "pressing", "opening the page", "reading the page"):
                progress.stage(word, path)
        self.assertEqual(["reading the page", "pressing", "reading back"], stages_of(self.relayed(during=press)[0]))

        def after(path):
            for word in ("starting the browser", "checking the account", "opening the page", "closing the browser"):
                progress.stage(word, path)
        self.assertEqual(["starting the browser", "reading back", "closing the browser"], stages_of(self.relayed(during=after)[0]))

    def test_a_press_on_one_platform_says_nothing_about_another(self):
        self.relayed("x", lambda path: progress.stage("pressing", path))
        self.assertEqual(["reading the page"], stages_of(self.relayed("douyin", lambda path: progress.stage("reading the page", path))[0]))

    def test_a_stderr_nobody_reads_never_holds_up_the_command(self):
        stuck = Event()
        self.addCleanup(stuck.set)
        started = time.monotonic()
        with patch.object(progress, "show", lambda *args: stuck.wait(30)), patch.object(progress, "LOOK_SECONDS", 0.01):
            self.relayed(during=lambda path: (progress.stage("pressing", path), time.sleep(0.1)))
        self.assertLess(time.monotonic() - started, 5)

    def test_a_stderr_that_is_gone_is_ignored(self):
        with patch.object(sys, "stderr", None):
            progress.show("x", "pressing")
        closed = io.StringIO()
        closed.close()
        with patch.object(sys, "stderr", closed):
            progress.show("x", "pressing")

    def test_two_steps_at_once_keep_their_own_files(self):
        first, second = {}, {}
        with contextlib.redirect_stderr(io.StringIO()), progress.relay("x", first), progress.relay("douyin", second):
            self.assertNotEqual(first["stages_path"], second["stages_path"])

    def test_a_folder_that_cannot_be_written_still_runs_the_command(self):
        payload = {}
        with patch("pathlib.Path.touch", side_effect=PermissionError), progress.relay("x", payload):
            self.assertNotIn("stages_path", payload)

    def test_no_failure_in_setting_up_the_lines_can_fail_the_command(self):
        for target, error in (("video_publisher.progress.home", RuntimeError("no home")), ("threading.Thread.start", RuntimeError("no thread"))):
            with self.subTest(target=target):
                payload = {}
                with patch(target, side_effect=error), progress.relay("x", payload):
                    self.assertNotIn("stages_path", payload)


class RouteTests(Temporary):
    def test_every_route_receives_the_file_and_the_command_gets_its_answer(self):
        seen = []

        def answer(payload, timeout):
            seen.append(payload["stages_path"])
            progress.stage("reading the page", payload["stages_path"])
            return {"ok": True, "data": {"done": True}}

        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), patch.object(adapters, "worker_call", answer), patch.object(adapters, "hidden_desktop_call", answer), patch.object(broker, "enabled", return_value=False):
            self.assertEqual({"done": True}, adapters.browser_call("x", "operations", "default"))
            with patch.object(adapters.os, "name", "nt"):
                self.assertEqual({"done": True}, adapters.browser_call("youtube", "operations", "default"))
        self.assertEqual(2, len(set(seen)))
        self.assertEqual(["reading the page"] * 2, stages_of(errors.getvalue()))

    def test_a_shared_session_is_told_where_to_write(self):
        sent = {}

        def session(payload, timeout):
            sent.update(payload)
            return {"done": True}

        with patch.object(broker, "call", session), contextlib.redirect_stderr(io.StringIO()):
            adapters.browser_call("x", "operations", "default")
        self.assertIn("stages_path", sent)

    def test_a_failed_step_keeps_its_own_error(self):
        answer = {"ok": False, "error": {"code": "layout_changed", "message": "Changed.", "hint": ""}}
        with patch.object(adapters, "worker_call", return_value=answer), patch.object(broker, "enabled", return_value=False), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(adapters.VpubError) as caught:
            adapters.browser_call("x", "operations", "default")
        self.assertEqual("layout_changed", caught.exception.code)


class WatchTests(Temporary):
    def page(self):
        class Page:
            goto = AsyncMock(return_value="answer")
            reload = AsyncMock(return_value="again")
        return Page()

    def test_a_page_load_says_opening_then_reading_and_never_where(self):
        path = self.root / "stages"
        path.touch()
        progress.attach({"stages_path": str(path)})
        page = self.page()
        progress.watch(page)
        self.assertEqual("answer", asyncio.run(page.goto(f"https://x.com/{OWNER}?q={PRIVATE}", timeout=5)))
        self.assertEqual("again", asyncio.run(page.reload()))
        self.assertEqual("opening the page\nreading the page\n" * 2, path.read_text())

    def test_watching_twice_still_says_it_once(self):
        path = self.root / "stages"
        path.touch()
        progress.attach({"stages_path": str(path)})
        page = self.page()
        progress.watch(page)
        progress.watch(page)
        asyncio.run(page.goto("https://x.com/"))
        self.assertEqual("opening the page\nreading the page\n", path.read_text())

    def test_a_page_that_fails_to_load_keeps_its_error(self):
        page = self.page()
        page.goto = AsyncMock(side_effect=ValueError("no"))
        progress.watch(page)
        with self.assertRaises(ValueError):
            asyncio.run(page.goto("https://x.com/"))

    def test_a_page_in_the_real_browser_library_can_be_watched(self):
        from patchright.async_api import async_playwright
        path = self.root / "stages"
        path.touch()
        progress.attach({"stages_path": str(path)})

        async def visit():
            async with async_playwright() as pw:
                try:
                    browser = await pw.chromium.launch(channel="chrome", headless=True)
                except Exception:
                    self.skipTest("Chrome is unavailable.")
                try:
                    page = await browser.new_page()
                    await page.route("**/*", lambda route: route.abort())
                    progress.watch(page)
                    await page.goto("data:text/html,<p>hello</p>", wait_until="domcontentloaded")
                    await page.reload(wait_until="domcontentloaded")
                    return await page.locator("p").inner_text()
                finally:
                    await browser.close()
        self.assertEqual("hello", asyncio.run(visit()))
        self.assertEqual("opening the page\nreading the page\n" * 2, path.read_text())


class PressTests(Temporary):
    """A line that says a press is coming comes after the receipt that says so, and before the press."""

    def stages_file(self):
        path = self.root / "stages"
        path.touch()
        progress.attach({"stages_path": str(path)})
        return path

    def test_each_receipt_step_says_pressing_only_once_the_receipt_is_saved(self):
        for module, state, ready in ((comment_actions, "COMMENT_SUBMITTING", "COMMENT_READY"), (engagement, "ACTION_SUBMITTING", "ACTION_READY"), (direct_messages, "DM_SUBMITTING", "DM_READY")):
            with self.subTest(module=module.__name__):
                stages = self.stages_file()
                stages.write_text("")
                receipt = self.root / "receipt.json"
                atomic_json(receipt, {"state": ready})
                seen = []
                real = module.stage
                with patch.object(module, "stage", lambda word: (seen.append(read_json(receipt)["state"]), real(word))):
                    module.submitting(receipt)
                self.assertEqual([state], seen)
                self.assertEqual("pressing\n", stages.read_text())

    def test_a_publish_says_pressing_after_its_saved_attempt_and_before_the_one_click(self):
        stages = self.stages_file()
        video = self.root / "video.mp4"
        video.write_bytes(b"v")
        plan = {"video": str(video), "video_sha256": "a" * 64, "confirmation": "b" * 64, "options": {"title": "T", "submission_mode": "publish"}}
        tx = Transaction("x", OWNER, plan)
        order = []

        async def click(**options):
            order.append((stages.read_text(), read_json(tx.path)["final_submit_attempted"]))
        button = MagicMock(click=click)
        page = MagicMock(url="https://x.com/")
        with patch("video_publisher.browser_backends.risk_check", new=AsyncMock()):
            asyncio.run(tx.submit(page, button))
        self.assertEqual([("pressing\n", True)], order)

    def test_a_read_says_nothing_about_pressing(self):
        stages = self.stages_file()
        page = MagicMock(url="https://x.com/", goto=AsyncMock(), set_default_timeout=MagicMock())
        page.stages_watched = False
        context = MagicMock(pages=[page])
        with patch.object(runtime, "risk_check", new=AsyncMock()), patch.object(runtime, "identity", new=AsyncMock(return_value=OWNER)):
            self.assertEqual(OWNER, asyncio.run(runtime.signed_in_page(context, "x"))[1])
        self.assertEqual("checking the account\nopening the page\nreading the page\n", stages.read_text())


class RealProcessTests(Temporary):
    """The real command line and its real browser processes, with only the browser library replaced."""

    def setUp(self):
        super().setUp()
        package = self.root / "fake" / "patchright"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "async_api.py").write_text(FAKE_BROWSER, encoding="utf-8")
        self.state = self.root / "browser.json"
        self.state.write_text("{}")
        src = Path(adapters.__file__).parents[1]
        self.env = {**os.environ, "PYTHONPATH": f"{package.parent}{os.pathsep}{src}", "FAKE_BROWSER_STATE": str(self.state), "PYTHONIOENCODING": "utf-8",
                    "VPUB_HOME": str(home())}
        self.addCleanup(self.close_sessions)

    def close_sessions(self):
        for platform in ("x", "youtube"):
            broker.release(platform, "default")

    def vpub(self, *arguments, idle=None, slow=None):
        env = dict(self.env)
        if idle is not None:
            env["VPUB_BROWSER_IDLE_SECONDS"] = str(idle)
        if slow:
            env["FAKE_BROWSER_SLOW"] = str(slow)
        return subprocess.run([sys.executable, "-m", "video_publisher", *arguments], capture_output=True, encoding="utf-8", env=env, timeout=240)

    def assert_one_answer(self, done):
        self.assertEqual(1, len(done.stdout.strip().splitlines()), done.stdout)
        return json.loads(done.stdout)

    def assert_nothing_private(self, stderr):
        for word in (PRIVATE, OWNER, CHANNEL, VIDEO, POST, "https://", str(home())):
            self.assertNotIn(word, stderr)
        for line in lines_of(stderr):
            self.assertEqual("browser_progress", line["event"])
            self.assertIn(line["stage"], progress.STAGES)

    def test_a_one_shot_read_keeps_one_answer_on_stdout_and_only_stages_on_stderr(self):
        done = self.vpub("works", "x", idle=0)
        answer = self.assert_one_answer(done)
        self.assertEqual(PRIVATE, answer["data"]["items"][0]["text"])
        self.assertEqual(OWNER, answer["data"]["identity"])
        self.assert_nothing_private(done.stderr)
        self.assertEqual(["starting the browser", "checking the account", "opening the page", "reading the page", "opening the page", "reading the page", "closing the browser"], stages_of(done.stderr))

    def test_a_like_says_each_stage_in_order_and_presses_once(self):
        done = self.vpub("engage", "like", "x", POST, "--test-id", "t1", idle=0)
        answer = self.assert_one_answer(done)
        self.assertEqual("ACTION_VERIFIED", answer["data"]["state"])
        self.assertEqual(1, read_json(self.state)["clicks"])
        self.assert_nothing_private(done.stderr)
        stages = stages_of(done.stderr)
        self.assertEqual(1, stages.count("pressing"))
        press = stages.index("pressing")
        self.assertIn("reading the page", stages[:press])
        self.assertNotIn("reading back", stages[:press])
        self.assertIn("reading back", stages[press:])
        self.assertNotIn("opening the page", stages[press:])

    def test_a_shared_session_answers_the_same_way_and_a_later_command_uses_it(self):
        first = self.vpub("works", "x")
        answer = self.assert_one_answer(first)
        self.assertEqual(PRIVATE, answer["data"]["items"][0]["text"])
        self.assert_nothing_private(first.stderr)
        self.assertEqual("starting the browser", stages_of(first.stderr)[1])
        self.assertIn("opening the page", stages_of(first.stderr))
        second = self.vpub("works", "x")
        self.assert_one_answer(second)
        self.assert_nothing_private(second.stderr)
        self.assertNotIn("starting the browser", stages_of(second.stderr))
        self.assertIn("opening the page", stages_of(second.stderr))
        self.assertEqual(1, len(read_json(self.state)["desktops"]))

    def test_a_command_that_fails_still_gives_one_answer_and_keeps_its_details_off_stderr(self):
        for idle in (0, None):
            with self.subTest(idle=idle):
                env = dict(self.env, FAKE_BROWSER_FAILS="1")
                if idle is not None:
                    env["VPUB_BROWSER_IDLE_SECONDS"] = str(idle)
                done = subprocess.run([sys.executable, "-m", "video_publisher", "works", "x"], capture_output=True, encoding="utf-8", env=env, timeout=240)
                self.assertEqual(False, self.assert_one_answer(done)["ok"])
                self.assert_nothing_private(done.stderr)
                self.assertIn("opening the page", stages_of(done.stderr))
                self.assertNotEqual(0, done.returncode)

    def test_the_lines_arrive_while_the_page_loads_not_all_at_the_end(self):
        routes = [("x", 0), ("x", None)] + ([("youtube", 0), ("youtube", None)] if os.name == "nt" else [])
        for platform, idle in routes:
            with self.subTest(platform=platform, idle=idle):
                done = self.vpub("works", platform, idle=idle, slow=1.5)
                self.assert_one_answer(done)
                seen = lines_of(done.stderr)
                stages = [line["stage"] for line in seen]
                last_open = len(stages) - 1 - stages[::-1].index("opening the page")
                self.assertEqual("reading the page", stages[last_open + 1])
                self.assertGreaterEqual(seen[last_open + 1]["elapsed_seconds"] - seen[last_open]["elapsed_seconds"], 1)
                self.close_sessions()

    @unittest.skipUnless(os.name == "nt", "Windows desktops")
    def test_youtube_says_its_stages_from_the_hidden_desktop_which_stays_hidden(self):
        done = self.vpub("works", "youtube", idle=0)
        answer = self.assert_one_answer(done)
        self.assertEqual(PRIVATE, answer["data"]["items"][0]["title"])
        self.assert_nothing_private(done.stderr)
        self.assertEqual(["starting the browser", "checking the account", "opening the page", "reading the page", "opening the page", "reading the page", "closing the browser"], stages_of(done.stderr))
        self.assertEqual(["CrossCastBackground"], read_json(self.state)["desktops"])
        self.assertEqual([], [path for path in (home() / "browser-jobs").iterdir()])

    @unittest.skipUnless(os.name == "nt", "Windows desktops")
    def test_the_steps_of_a_youtube_command_share_one_chrome_on_the_hidden_desktop(self):
        first = self.vpub("works", "youtube")
        self.assertEqual(PRIVATE, self.assert_one_answer(first)["data"]["items"][0]["title"])
        self.assert_nothing_private(first.stderr)
        self.assertIn("starting the browser", stages_of(first.stderr))
        self.assertNotIn("closing the browser", stages_of(first.stderr))
        second = self.vpub("works", "youtube")
        self.assert_one_answer(second)
        self.assertNotIn("starting the browser", stages_of(second.stderr))
        self.assertEqual(["CrossCastBackground"], read_json(self.state)["desktops"])
        self.assertEqual(False, read_json(self.state)["headless"])

    @unittest.skipUnless(os.name == "nt", "Windows desktops")
    def test_a_youtube_session_nobody_asks_again_closes_itself_soon(self):
        self.assert_one_answer(self.vpub("works", "youtube", idle=1))
        path = broker.state_path("youtube", "default")
        deadline = time.monotonic() + 30
        while path.exists():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.2)
        self.assertTrue(broker.wait_for_lock("youtube", "default", 15))

    def test_a_command_with_no_browser_says_nothing(self):
        done = self.vpub("doctor")
        self.assert_one_answer(done)
        self.assertEqual("", done.stderr)


if __name__ == "__main__":
    unittest.main()

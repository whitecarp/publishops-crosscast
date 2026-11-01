"""One Chrome for a whole test run, and the event loop that keeps it usable from one test to the next.

Starting the automation driver and Chrome takes about two seconds, which every page test used to pay for. A class that derives
from ChromeCase runs all its tests on one loop that lives as long as the process, and the async_playwright here hands each test
a window of the one Chrome. A window is a context of its own, so cookies, storage, routes and permissions stay as separate as
they were in a Chrome per test. Closing the browser a test was given closes only that test's windows.
"""
import asyncio
import atexit
import inspect
import unittest

LONGEST_WAIT = 8000  # Milliseconds.

_loop = None
_chrome = None  # The driver and the one Chrome, once a test has asked for them.
_kept = set()  # The driver's own tasks, which no test's clean-up may cancel.
_open = []  # Windows handed out and not closed yet.


def loop():
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop


class ChromeCase(unittest.TestCase):
    """What unittest.IsolatedAsyncioTestCase gives a test class, except that every test runs on the one shared loop."""

    async def asyncSetUp(self):
        pass

    async def asyncTearDown(self):
        pass

    def addAsyncCleanup(self, function, *args, **kwargs):
        self.addCleanup(lambda: self.run_async(function(*args, **kwargs)))

    def run_async(self, awaitable):
        return loop().run_until_complete(awaitable)

    def run(self, result=None):
        before = asyncio.all_tasks(loop())
        asyncio.set_event_loop(loop())
        try:
            return super().run(result)
        finally:  # What a loop and a Chrome of its own would have done when the test ended.
            self.run_async(close_windows())
            left = [task for task in asyncio.all_tasks(loop()) if task not in before and task not in _kept]
            for task in left:
                task.cancel()
            if left:
                self.run_async(asyncio.gather(*left, return_exceptions=True))
            asyncio.set_event_loop(None)

    def _callSetUp(self):
        self.setUp()
        self.run_async(self.asyncSetUp())

    def _callTestMethod(self, method):
        outcome = method()
        if inspect.isawaitable(outcome):
            self.run_async(outcome)

    def _callTearDown(self):
        self.run_async(self.asyncTearDown())
        self.tearDown()


def shorten_waits():
    """The code under test waits as long as a slow real site might take, up to a minute. The copies of the sites in these tests answer at
    once or never, so a wait that is going to run out is cut to LONGEST_WAIT and still runs out."""
    from patchright._impl import _connection
    if getattr(_connection, "_augment_params", None) is None or getattr(_connection._augment_params, "shortened", False):
        return
    asked = _connection._augment_params

    def shorter(params, timeout_calculator):
        answer = asked(params, timeout_calculator)
        if answer.get("timeout", 0) > LONGEST_WAIT:
            answer["timeout"] = LONGEST_WAIT
        return answer
    shorter.shortened = True
    _connection._augment_params = shorter


async def chrome():
    global _chrome
    if asyncio.get_running_loop() is not loop():
        raise RuntimeError("The shared Chrome only works in a ChromeCase test, which runs on the loop that holds it.")
    if _chrome is None or not _chrome[1].is_connected():
        from patchright.async_api import async_playwright as driver
        before = asyncio.all_tasks()
        started = await driver().start()
        try:
            _chrome = (started, await started.chromium.launch(channel="chrome", headless=True))
        except BaseException:
            await started.stop()
            raise
        _kept.update(task for task in asyncio.all_tasks() if task not in before)
        shorten_waits()
    return _chrome[1]


def close_chrome():
    global _chrome
    if _loop is None or _loop.is_closed():
        return
    if _chrome is not None:
        started, browser = _chrome
        _chrome = None

        async def stop():
            await browser.close()
            await started.stop()
        try:
            _loop.run_until_complete(asyncio.wait_for(stop(), 20))
        except Exception:
            pass  # The driver ends Chrome by itself when this process does.
    _loop.close()


atexit.register(close_chrome)


async def close_windows(windows=None):
    for window in reversed(list(_open if windows is None else windows)):
        if window in _open:
            _open.remove(window)
        try:
            await window.close()
        except Exception:
            pass  # The test closed it already.


class Browser:
    """What a test sees as its own browser. Closing it closes the windows it opened and leaves the one Chrome running."""

    def __init__(self, shared):
        self.shared = shared
        self.windows = []

    async def new_context(self, **options):
        window = await self.shared.new_context(**options)
        self.windows.append(window)
        _open.append(window)
        return window

    async def new_page(self, **options):
        window = await self.new_context(**options)
        return await window.new_page()

    async def close(self):
        await close_windows(self.windows)
        self.windows.clear()


class Chromium:
    async def launch(self, **options):
        if options != {"channel": "chrome", "headless": True}:
            raise ValueError("The shared Chrome is only the plain headless one; a test that needs other options starts its own.")
        return Browser(await chrome())


class Driver:
    """The same few calls as the automation driver's own object, for tests that only ask it for a browser."""

    def __init__(self):
        self.chromium = Chromium()

    async def start(self):
        return self

    async def stop(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *error):
        pass


def async_playwright():
    return Driver()

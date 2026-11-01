"""One invisible browser for a whole test class, because starting and closing one is most of what a browser test costs.

Each test still gets a fresh page and its own temporary CrossCast folder. Test methods are written as async functions, as in the other browser tests.
"""
import asyncio
import functools
import inspect
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_publisher.core import atomic_json, home

MOST_SECONDS = float(os.environ.get("STAGE_TEST_SECONDS", 180))


def blocking(test):
    @functools.wraps(test)
    def run(self):
        # Pages under test have waits of up to ten minutes, and a test must not sit through one.
        return self.loop.run_until_complete(asyncio.wait_for(test(self), MOST_SECONDS))
    return run


class SharedBrowser(unittest.TestCase):
    def __init_subclass__(cls, **options):
        super().__init_subclass__(**options)
        for name, value in list(vars(cls).items()):
            if name.startswith("test_") and inspect.iscoroutinefunction(value):
                setattr(cls, name, blocking(value))

    @classmethod
    def setUpClass(cls):
        from patchright.async_api import async_playwright
        cls.loop = asyncio.new_event_loop()
        cls.pw = cls.loop.run_until_complete(async_playwright().start())
        cls.browser = cls.loop.run_until_complete(cls.pw.chromium.launch(channel="chrome", headless=True))

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(cls.browser.close())
        cls.loop.run_until_complete(cls.pw.stop())
        cls.loop.close()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        self.context = self.loop.run_until_complete(self.browser.new_context())
        self.addCleanup(self.loop.run_until_complete, self.close_context())
        self.page = self.loop.run_until_complete(self.context.new_page())
        self.page.set_default_timeout(5000)
        waiting = patch.object(self.page, "wait_for_timeout", side_effect=self.no_wait)
        waiting.start()
        self.addCleanup(waiting.stop)
        self.loop.run_until_complete(self.page.route("**/*", self.handle))
        self.prepare()

    def prepare(self):
        """Each test class's own starting state."""

    async def handle(self, route):
        await self.page_at(route, "")

    async def close_context(self):
        await self.context.close()

    async def no_wait(self, ms):
        return None

    async def page_at(self, route, body):
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body="<!doctype html><title>Page</title><body>" + body)

    async def json_at(self, route, value):
        import json
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(value))

    def stop(self):
        atomic_json(home() / "STOP.json", {"reason": "A person paused it."})

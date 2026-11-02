"""Fixed waits that end as soon as what they wait for shows, and last as long as they did when it never does. Fake pages; nothing is contacted."""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import browser_backends, collections_browser, youtube_studio
from video_publisher.browser_runtime import look_until
from video_publisher.core import VpubError, home

CHANNEL = "UC" + "a" * 22
OWNER = "tester"
WORK = "Abc_123"


class Clock:
    """Counts what a page was made to wait, and can show something once enough of it has passed."""

    def __init__(self):
        self.waited = 0

    async def wait_for_timeout(self, milliseconds):
        self.waited += milliseconds


class LookUntilTests(unittest.TestCase):
    def looks(self, answers, milliseconds=2000):
        clock, answers = Clock(), iter(answers)

        async def look():
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return asyncio.run(look_until(clock, look, milliseconds)), clock.waited

    def test_something_already_there_ends_the_wait_at_once(self):
        self.assertEqual(("found", 0), self.looks(["found"]))

    def test_something_that_shows_part_way_ends_it_there(self):
        self.assertEqual(("found", 1000), self.looks([None, None, "found"]))

    def test_nothing_showing_lasts_the_whole_wait_and_looks_once_more_at_its_end(self):
        self.assertEqual((None, 2000), self.looks([None] * 5))
        self.assertEqual(("late", 2000), self.looks([None] * 4 + ["late"]))

    def test_a_page_still_changing_counts_as_nothing_yet(self):
        self.assertEqual(("found", 500), self.looks([ValueError("navigating"), "found"]))

    def test_only_the_last_look_can_fail_the_command(self):
        with self.assertRaises(ValueError):
            self.looks([None] * 4 + [ValueError("gone")])


class Temporary(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)


class HandlePage(Clock):
    def __init__(self, shows_after):
        super().__init__()
        self.shows_after, self.url = shows_after, ""
        self.goto = AsyncMock()

    async def content(self):
        if self.shows_after is not None and self.waited >= self.shows_after:
            return '"vanityChannelUrl":"http://www.youtube.com/@MyChannel"'
        return "<html></html>"


class OwnHandleTests(Temporary):
    def read(self, page):
        risk = AsyncMock()
        with patch.object(youtube_studio, "risk_check", risk):
            handle = asyncio.run(youtube_studio.own_handle(page, CHANNEL))
        risk.assert_awaited_once()
        return handle

    def test_a_handle_already_on_the_page_needs_no_wait_and_still_gets_its_risk_check(self):
        page = HandlePage(0)
        self.assertEqual("mychannel", self.read(page))
        self.assertEqual(0, page.waited)

    def test_a_handle_that_shows_late_is_still_found(self):
        page = HandlePage(1500)
        self.assertEqual("mychannel", self.read(page))
        self.assertEqual(1500, page.waited)

    def test_a_page_with_no_handle_gets_the_same_two_seconds_and_nothing_is_kept(self):
        page = HandlePage(None)
        self.assertIsNone(self.read(page))
        self.assertEqual(2000, page.waited)
        self.assertFalse((home() / "accounts/default/youtube-handle.json").exists())


class Shown:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    async def all(self):
        return [self] if self.page.ready() else []

    async def is_visible(self):
        return True

    async def inner_text(self):
        return "My caption" if self.page.ready() else ""

    async def evaluate_all(self, script):
        return []


class InstagramPage(Clock):
    url = f"https://www.instagram.com/p/{WORK}/"

    def __init__(self, shows_after):
        super().__init__()
        self.shows_after = shows_after
        self.goto = AsyncMock()

    def ready(self):
        return self.shows_after is not None and self.waited >= self.shows_after

    def locator(self, selector):
        return Shown(self, selector)

    async def evaluate(self, script, words):
        return self.ready() and words["words"] == "My caption"


class InstagramVerifyTests(Temporary):
    def verify(self, page):
        receipt = {"id": WORK, "identity": OWNER, "expected_text": "My caption"}
        risk = AsyncMock()
        with patch.object(browser_backends, "works", new=AsyncMock(return_value={WORK: {"id": WORK}})), patch.object(browser_backends, "risk_check", risk):
            result = asyncio.run(browser_backends.verify(page, "instagram", OWNER, receipt))
        risk.assert_awaited_once()
        return result["state"]

    def test_a_page_already_showing_everything_is_read_at_once(self):
        page = InstagramPage(0)
        self.assertEqual("SUBMISSION_VERIFIED", self.verify(page))
        self.assertEqual(0, page.waited)

    def test_a_page_that_fills_in_late_is_read_once_it_has(self):
        page = InstagramPage(2500)
        self.assertEqual("SUBMISSION_VERIFIED", self.verify(page))
        self.assertEqual(2500, page.waited)

    def test_a_page_that_never_shows_it_gets_the_same_four_seconds_and_is_not_confirmed(self):
        page = InstagramPage(None)
        self.assertEqual("SUBMITTED_UNVERIFIED", self.verify(page))
        self.assertEqual(4000, page.waited)


class XFolderPage(Clock):
    url = "https://x.com/i/history"

    def __init__(self):
        super().__init__()
        self.listeners = []

    def on(self, event, listener):
        self.listeners.append(listener)

    def remove_listener(self, event, listener):
        self.listeners.remove(listener)

    async def goto(self, url, **options):
        for listener in self.listeners:
            await listener(self.answer)

    @property
    def answer(self):
        body = {"data": {"viewer": {"user_results": {"result": {"bookmark_collections_slice": {"items": [{"id": "1", "name": "Trips"}], "slice_info": {}}}}}}}
        return MagicMock(status=200, url="https://x.com/i/api/graphql/abc/Folders", json=AsyncMock(return_value=body))


class XFolderListTests(Temporary):
    def test_the_list_waits_for_xs_own_answer_and_not_for_a_pause_as_well(self):
        page = XFolderPage()
        with patch.object(collections_browser.rt, "risk_check", new=AsyncMock()) as risk:
            listed = asyncio.run(collections_browser.read_list(page, "x", OWNER, True))
        self.assertEqual(["1"], [row["id"] for row in listed["items"]])
        self.assertEqual(0, page.waited)
        risk.assert_awaited_once()

    def test_no_answer_still_fails_the_way_it_did(self):
        page = XFolderPage()
        page.listeners = None
        page.on = page.remove_listener = lambda *args: None
        page.goto = AsyncMock()
        with patch.object(collections_browser.rt, "risk_check", new=AsyncMock()), patch.object(collections_browser, "LIST_WAIT", 0.01), self.assertRaises(VpubError) as caught:
            asyncio.run(collections_browser.read_list(page, "x", OWNER, True))
        self.assertEqual("collection_list_unavailable", caught.exception.code)

    def test_opening_the_page_to_press_something_keeps_its_pause(self):
        page = XFolderPage()
        page.goto = AsyncMock()
        with patch.object(collections_browser.rt, "risk_check", new=AsyncMock()):
            asyncio.run(collections_browser.root(page, "x", OWNER, True))
        self.assertEqual(900, page.waited)


if __name__ == "__main__":
    unittest.main()

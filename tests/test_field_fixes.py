"""Problems the first real-account test run found: a session that would not close, sign-ins that lost their window, and unclear failures."""
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import adapters, browser_runtime as rt, cli
from video_publisher.core import VpubError, atomic_json, read_json


class Home(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        target = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        target.start()
        self.addCleanup(target.stop)


class ClosingTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_browser_that_never_answers_does_not_hold_the_command_forever(self):
        context = MagicMock()

        async def forever():
            await asyncio.sleep(3600)
        context.close = forever
        with patch.object(rt, "CLOSE_SECONDS", 0.05), patch.object(rt, "save_session", new=AsyncMock()):
            await asyncio.wait_for(rt.close_context(context, "x", "default"), 2)

    async def test_a_save_that_fails_still_closes_and_keeps_the_commands_result(self):
        context = MagicMock(close=AsyncMock())
        with patch.object(rt, "save_session", new=AsyncMock(side_effect=OSError("disk"))):
            await rt.close_context(context, "x", "default")
        context.close.assert_awaited_once()

    def test_a_session_that_finished_ends_itself_if_closing_hangs(self):
        with patch.object(rt.threading, "Timer") as timer:
            rt.exit_soon()
        self.assertEqual((rt.EXIT_SECONDS, os._exit), timer.call_args.args[:2])
        timer.return_value.start.assert_called_once()
        self.assertTrue(timer.return_value.daemon)

    def test_the_next_command_waits_longer_than_a_session_can_take_to_end(self):
        from video_publisher import browser_broker
        self.assertGreater(browser_broker.CLOSING_SECONDS, rt.EXIT_SECONDS)
        self.assertGreater(rt.EXIT_SECONDS, 2 * rt.CLOSE_SECONDS)


class FailureMessageTests(unittest.TestCase):
    def test_it_names_the_step_and_the_control(self):
        error = TimeoutError("Locator.wait_for: Timeout 45000ms exceeded.\nCall log:\n  - waiting for locator('article').first to be visible\n  - other line")
        self.assertEqual("Browser command failed (TimeoutError): Locator.wait_for: Timeout 45000ms exceeded. waiting for locator('article').first to be visible", rt.failure_message(error))

    def test_addresses_lose_their_query_strings(self):
        message = rt.failure_message(RuntimeError("Page.goto: net::ERR_FAILED at https://x.com/a/b?token=secret&x=1 now"))
        self.assertIn("https://x.com/a/b", message)
        self.assertNotIn("secret", message)

    def test_an_error_with_no_words_still_reads(self):
        self.assertEqual("Browser command failed (ValueError).", rt.failure_message(ValueError()))

    def test_a_long_error_is_cut_short(self):
        self.assertLess(len(rt.failure_message(RuntimeError("x" * 5000))), 300)


class FakePage:
    def __init__(self, identity=None, closed=False, labels=False):
        self.who, self.closed, self.labels = identity, closed, labels
        self.goto = AsyncMock()
        self.reload = AsyncMock()
        self.bring_to_front = AsyncMock()

    def is_closed(self):
        return self.closed

    def get_by_text(self, pattern):
        return self


class FakeContext:
    def __init__(self, pages):
        self.pages = pages
        self.close = AsyncMock()


class LoginTests(Home, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Home.setUp(self)

    async def run_login(self, pages, steps=None):
        """Each step changes the tabs before the next look, as a person signing in would."""
        context = FakeContext(pages)
        job = self.root / "job.json"
        looks = {"count": 0}

        async def identity(page, platform):
            return page.who

        async def visible(locator):
            return locator if locator.labels else None

        async def tick(seconds):
            looks["count"] += 1
            if steps and looks["count"] <= len(steps):
                steps[looks["count"] - 1](context)
            if looks["count"] > 8:
                raise AssertionError("the sign-in never ended")
        with patch.object(rt, "open_context", new=AsyncMock(return_value=context)), patch.object(rt, "identity", identity), patch.object(rt, "visible", visible), \
                patch.object(rt.asyncio, "sleep", tick), patch.object(rt, "save_session", new=AsyncMock()):
            result = await rt.login(None, {"platform": "youtube", "account": "default", "job_path": str(job)})
        return result, context, job

    async def test_a_sign_in_that_replaces_its_tab_is_not_cancelled(self):
        first, second = FakePage(), FakePage("UC1")

        def replace(context):
            first.closed = True
            context.pages[:] = [first, second]
        result, context, job = await self.run_login([first], [replace])
        self.assertEqual("UC1", result["identity"])
        self.assertEqual("UC1", read_json(rt.identity_path("youtube", "default"))["identity"])
        context.close.assert_awaited()

    async def test_closing_every_tab_cancels_it(self):
        page = FakePage()

        def close(context):
            page.closed = True
        with self.assertRaises(VpubError) as caught:
            await self.run_login([page], [close])
        self.assertEqual("login_cancelled", caught.exception.code)

    async def test_the_window_is_brought_forward_and_says_it_opened(self):
        page = FakePage("UC1")
        _, _, job = await self.run_login([page])
        page.bring_to_front.assert_awaited_once()

    async def test_a_recovery_page_is_reported_for_a_person_and_never_answered(self):
        page = FakePage(labels=True)
        seen = {}

        def look_then_finish(context):
            seen.update(read_json(self.root / "job.json"))
            page.who = "UC1"
        await self.run_login([page], [look_then_finish])
        self.assertEqual("VERIFICATION_REQUIRED", seen["state"])
        self.assertIn("yourself", seen["hint"])


class RecoveryLabelTests(unittest.TestCase):
    def test_a_locked_accounts_recovery_page_counts_as_a_platform_check(self):
        for text in ("Choose a way to recover", "Get help from friends"):
            self.assertTrue(rt.HUMAN_LABELS.search(text), text)

    def test_ordinary_words_about_accounts_do_not(self):
        self.assertFalse(rt.HUMAN_LABELS.search("my friend's account was locked last year"))


class SignedInWithoutRecordTests(Home):
    def profile(self):
        path = rt.profile_dir("youtube", "default")
        path.mkdir(parents=True)
        (path / "Local State").write_text("{}")

    def test_no_profile_at_all_still_says_not_signed_in_without_opening_a_browser(self):
        with patch.object(adapters, "browser_call") as call, self.assertRaises(VpubError) as caught:
            adapters.auth_status("youtube", "default", "browser")
        self.assertEqual("not_logged_in", caught.exception.code)
        call.assert_not_called()

    def test_a_working_profile_with_no_record_is_checked_and_recorded(self):
        self.profile()
        with patch.object(adapters, "browser_call", return_value={"logged_in": True, "identity": "UC1"}) as call:
            self.assertEqual("UC1", adapters.auth_status("youtube", "default", "browser")["identity"])
        call.assert_called_once()
        self.assertEqual("UC1", read_json(rt.identity_path("youtube", "default"))["identity"])

    def test_a_profile_the_site_no_longer_accepts_says_not_signed_in(self):
        self.profile()
        with patch.object(adapters, "browser_call", side_effect=VpubError("auth_expired", "No.")), self.assertRaises(VpubError) as caught:
            adapters.auth_status("youtube", "default", "browser")
        self.assertEqual("not_logged_in", caught.exception.code)
        self.assertFalse(rt.identity_path("youtube", "default").exists())

    def test_a_real_problem_is_not_hidden_as_not_signed_in(self):
        self.profile()
        with patch.object(adapters, "browser_call", side_effect=VpubError("human_verification_required", "Check.")), self.assertRaises(VpubError) as caught:
            adapters.auth_status("youtube", "default", "browser")
        self.assertEqual("human_verification_required", caught.exception.code)


class ExitCodeTests(unittest.TestCase):
    def test_a_change_that_failed_or_could_not_be_confirmed_is_not_reported_as_success(self):
        for state in ("FAILED_BEFORE_SUBMIT", "UNVERIFIED", "EDIT_FAILED", "EDIT_MISMATCH"):
            self.assertIn(state, cli.UNFINISHED_STATES)

    def test_a_confirmed_change_is(self):
        for state in ("CONFIRMED", "EDIT_VERIFIED", "COMMENT_VERIFIED", "NO_CHANGE"):
            self.assertNotIn(state, cli.UNFINISHED_STATES)


if __name__ == "__main__":
    unittest.main()

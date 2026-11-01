"""Problems the first real-account test run found: a session that would not close, sign-ins that lost their window, and unclear failures."""
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import adapters, browser_runtime as rt, cli
from video_publisher.core import VpubError, atomic_json, home, read_json


def home_stop():
    return home() / "STOP.json"


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

    async def test_a_save_that_never_answers_reports_itself_instead_of_waiting(self):
        async def forever(*args):
            await asyncio.Event().wait()
        with patch.object(rt, "CLOSE_SECONDS", 0.05), patch.object(rt, "save_session", forever):
            self.assertIs(False, await asyncio.wait_for(rt.keep_session(MagicMock(), "x", "default"), 2))

    async def test_a_save_that_works_says_so(self):
        with patch.object(rt, "save_session", new=AsyncMock()):
            self.assertIs(True, await rt.keep_session(MagicMock(), "x", "default"))

    def test_the_next_command_waits_longer_than_a_session_can_take_to_end(self):
        from video_publisher import browser_broker
        self.assertGreater(browser_broker.CLOSING_SECONDS, rt.EXIT_SECONDS)
        self.assertGreater(rt.EXIT_SECONDS, 2 * rt.CLOSE_SECONDS)


class FailureMessageTests(unittest.TestCase):
    def test_it_names_the_step_and_the_control(self):
        error = TimeoutError("Locator.wait_for: Timeout 45000ms exceeded.\nCall log:\n  - waiting for locator('article').first to be visible\n  - other line")
        self.assertEqual("Browser command failed (TimeoutError): Locator.wait_for: Timeout 45000ms exceeded. waiting for locator('article').first to be visible", rt.failure_message(error))

    def test_the_words_it_was_looking_for_are_not_kept(self):
        for looking in ('get_by_text("PRIVATE-WORDS")', "get_by_label('PRIVATE-WORDS')", 'locator("article").filter(has_text="PRIVATE-WORDS")', 'get_by_role("button", name="PRIVATE-WORDS")',
                        "filter(has_text=re.compile('PRIVATE-WORDS'))", 'locator(\'div:has-text("PRIVATE-WORDS")\')', "locator('internal:text=\"PRIVATE-WORDS\"')"):
            with self.subTest(looking=looking):
                note = rt.failure_message(TimeoutError(f"Locator.click: Timeout 5000ms exceeded.\nCall log:\n  - waiting for {looking}"))
                self.assertNotIn("PRIVATE-WORDS", note)
                self.assertIn("waiting for", note)

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

        async def challenge_shown(page):
            return page.labels

        async def tick(seconds):
            looks["count"] += 1
            if steps and looks["count"] <= len(steps):
                steps[looks["count"] - 1](context)
            if looks["count"] > 12:
                raise AssertionError("the sign-in never ended")
        with patch.object(rt, "open_context", new=AsyncMock(return_value=context)), patch.object(rt, "identity", identity), patch.object(rt, "challenge_shown", challenge_shown), \
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
        await self.run_login([page], [look_then_finish, self.clear(page)])
        self.assertEqual("VERIFICATION_REQUIRED", seen["state"])
        self.assertIn("yourself", seen["hint"])

    def clear(self, page):
        def clear(context):
            page.labels = False
        return clear

    def not_done(self, seen):
        def look(context):
            seen.update(read_json(self.root / "job.json"))
        return look

    async def test_a_signed_in_menu_behind_a_check_is_not_a_finished_sign_in(self):
        page, seen = FakePage("UC1", labels=True), {}
        result, _, _ = await self.run_login([page], [self.not_done(seen), self.clear(page)])
        self.assertEqual("VERIFICATION_REQUIRED", seen["state"])
        self.assertEqual("UC1", result["identity"])
        page.reload.assert_awaited_once()

    async def test_a_check_that_appears_on_the_reload_is_not_a_finished_sign_in(self):
        page, seen = FakePage("UC1"), {}

        async def reload(**options):
            if page.reload.await_count == 1:
                page.labels = True
        page.reload.side_effect = reload
        result, _, _ = await self.run_login([page], [lambda context: None, lambda context: None, lambda context: None, self.not_done(seen), self.clear(page)])
        self.assertEqual("VERIFICATION_REQUIRED", seen["state"])
        self.assertEqual(2, page.reload.await_count)
        self.assertEqual("UC1", result["identity"])

    async def test_a_check_on_another_tab_holds_the_sign_in_too(self):
        signed_in, checking, seen = FakePage("UC1"), FakePage(labels=True), {}
        result, _, _ = await self.run_login([signed_in, checking], [self.not_done(seen), self.clear(checking)])
        self.assertEqual("VERIFICATION_REQUIRED", seen["state"])
        self.assertEqual("UC1", result["identity"])

    async def test_a_tab_that_closes_during_the_check_does_not_hold_the_sign_in(self):
        closed = MagicMock(url="https://x.com/", locator=MagicMock(side_effect=RuntimeError("closed")), is_closed=MagicMock(return_value=True))
        self.assertIs(False, await rt.any_challenge([closed]))

    async def test_a_tab_still_open_that_cannot_be_read_proves_nothing(self):
        unread = MagicMock(url="https://x.com/", locator=MagicMock(side_effect=RuntimeError("busy")), is_closed=MagicMock(return_value=False))
        self.assertIsNone(await rt.any_challenge([unread]))

    async def test_a_sign_in_never_finishes_while_a_live_tab_cannot_be_read(self):
        page = FakePage("UC1")
        page.unreadable = True

        async def challenge_shown(tab):
            if tab.unreadable:
                raise RuntimeError("busy")
            return False
        seen = {}

        def still_waiting(context):
            seen["job"] = read_json(self.root / "job.json")
            seen["saved"] = rt.identity_path("youtube", "default").exists()
            page.unreadable = False
        context = FakeContext([page])
        looks = {"count": 0}

        async def tick(seconds):
            looks["count"] += 1
            if looks["count"] == 3:
                still_waiting(context)
            if looks["count"] > 12:
                raise AssertionError("the sign-in never ended")

        async def identity(tab, platform):
            return tab.who
        with patch.object(rt, "open_context", new=AsyncMock(return_value=context)), patch.object(rt, "identity", identity), patch.object(rt, "challenge_shown", challenge_shown), \
                patch.object(rt.asyncio, "sleep", tick), patch.object(rt, "save_session", new=AsyncMock()):
            result = await rt.login(None, {"platform": "youtube", "account": "default", "job_path": str(self.root / "job.json")})
        self.assertEqual("LOGIN_PENDING", seen["job"]["state"])
        self.assertIs(False, seen["saved"])
        self.assertEqual("UC1", result["identity"])

    async def test_signing_in_does_not_clear_a_stop_and_says_so(self):
        atomic_json(home_stop(), {"platform": "youtube", "source": "safety", "code": "human_verification_required"})
        result, _, _ = await self.run_login([FakePage("UC1")])
        self.assertIn("vpub stop --clear", result["next"])
        self.assertTrue(home_stop().exists())

    async def test_a_sign_in_with_no_stop_says_nothing_more(self):
        result, _, _ = await self.run_login([FakePage("UC1")])
        self.assertNotIn("next", result)

    async def test_a_save_that_never_answers_does_not_hold_the_sign_in(self):
        async def forever(*args):
            await asyncio.Event().wait()
        context = FakeContext([FakePage("UC1")])
        with patch.object(rt, "CLOSE_SECONDS", 0.05), patch.object(rt, "open_context", new=AsyncMock(return_value=context)), patch.object(rt, "identity", new=AsyncMock(return_value="UC1")), \
                patch.object(rt, "challenge_shown", new=AsyncMock(return_value=False)), patch.object(rt, "save_session", forever):
            result = await asyncio.wait_for(rt.login(None, {"platform": "x", "account": "default", "job_path": str(self.root / "job.json")}), 20)
        self.assertEqual("UC1", result["identity"])


class RecoveryLabelTests(unittest.TestCase):
    def test_a_locked_accounts_recovery_page_counts_as_a_platform_check(self):
        for text in ("Choose a way to recover", "Get help from friends"):
            self.assertTrue(rt.HUMAN_LABELS.search(text), text)

    def test_ordinary_words_about_accounts_do_not(self):
        self.assertFalse(rt.HUMAN_LABELS.search("my friend's account was locked last year"))


class DoctorTests(Home):
    def test_the_free_youtube_sign_in_is_listed_apart_from_the_paid_token(self):
        self.assertIs(False, cli.doctor()["credentials_configured"]["youtube_browser"])
        rt.remember_identity("youtube", "default", "UC1")
        saved = cli.doctor()["credentials_configured"]
        self.assertEqual((True, False), (saved["youtube_browser"], saved["youtube_api"]))


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


class ReceiptTests(Home):
    def test_a_verified_engagement_no_longer_carries_the_error_it_recovered_from(self):
        from video_publisher import engagement
        from video_publisher.core import home
        key = "a" * 32
        path = home() / "engagement/receipts" / (key + ".json")
        atomic_json(path, {"key": key, "state": "ACTION_UNVERIFIED", "platform": "youtube", "work_id": "dQw4w9WgXcQ", "identity": "UC1", "kind": "save", "desired": False,
                           "collection": "PLaaaaaaaaaaaaaaaaaaaa", "error_code": "wrong_work", "platform_writes": None})
        with patch.object(engagement, "dispatch", return_value={"identity": "UC1", "saved": False}), patch.object(engagement.operations, "record"):
            result = engagement.verify(key)
        self.assertEqual("ACTION_VERIFIED", result["state"])
        self.assertNotIn("error_code", result)
        self.assertEqual("wrong_work", result["resolved_errors"][0]["code"])


class SharedLockTests(Home):
    def test_saving_a_read_does_not_wait_for_a_change_on_another_platform(self):
        from video_publisher import operations as ops
        from video_publisher.core import publication_lock
        with publication_lock(), patch.object(ops, "record", side_effect=lambda action, platform, work, result: result):
            result = ops.save_observation("instagram", "abc", "stats", {"identity": "me", "metrics": {}})
        self.assertEqual("instagram", result["platform"])

    def test_two_saves_take_turns_and_a_stuck_one_is_reported_as_busy(self):
        from video_publisher.core import observation_lock
        with observation_lock():
            with self.assertRaises(VpubError) as caught:
                with observation_lock(seconds=0.2):
                    pass
        self.assertEqual("busy", caught.exception.code)
        with observation_lock():
            pass

    def test_an_error_inside_it_is_not_mistaken_for_a_busy_lock(self):
        from video_publisher.core import observation_lock
        with self.assertRaises(VpubError) as caught:
            with observation_lock():
                raise VpubError("wrong_account", "No.")
        self.assertEqual("wrong_account", caught.exception.code)


class CaptionReadBackTests(Home, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Home.setUp(self)

    async def run_edit(self, saved_after):
        from video_publisher import browser_edits, edits
        texts = iter(["Old words", *saved_after])

        class Editor:
            async def inner_text(self):
                return self.text

            async def fill(self, value):
                self.text = value

            async def wait_for(self, **options):
                return None

        async def open_editor(page, work):
            editor = Editor()
            editor.text = next(texts)
            return editor
        page = MagicMock(wait_for_timeout=AsyncMock())
        wanted = {"post_text": "New words"}
        with patch.object(browser_edits, "instagram_caption_editor", side_effect=open_editor), patch.object(browser_edits, "unique_visible", new=AsyncMock(return_value=MagicMock(click=AsyncMock()))), patch.object(browser_edits, "risk_check", new=AsyncMock()):
            return await browser_edits.execute(page, "instagram", "me", {"patch": wanted, "receipt": {"id": "abc"}, "edit_receipt_path": str(self.root / "edit.json")}), page

    async def test_old_words_for_a_moment_do_not_count_as_failure_and_nothing_saves_twice(self):
        result, page = await self.run_edit(["Old words", "Old words", "New words"])
        self.assertEqual(("EDIT_VERIFIED", 1), (result["state"], result["platform_writes"]))
        self.assertEqual(2, page.wait_for_timeout.await_count)

    async def test_old_words_that_stay_are_reported_as_unconfirmed(self):
        result, _ = await self.run_edit(["Old words"] * 4)
        self.assertEqual(("EDIT_SUBMITTED_UNVERIFIED", 1), (result["state"], result["platform_writes"]))


class SelfTestTests(Home):
    def test_the_free_youtube_route_says_it_cannot_and_sends_nothing(self):
        from video_publisher import operations as ops
        with patch.object(adapters, "addon_installed", return_value=False), patch.object(ops, "backend") as backend, self.assertRaises(VpubError) as caught:
            ops.self_test("youtube", "dQw4w9WgXcQ", "t1")
        self.assertEqual("unsupported_operation", caught.exception.code)
        backend.assert_not_called()


class ExitCodeTests(unittest.TestCase):
    def test_a_change_that_failed_or_could_not_be_confirmed_is_not_reported_as_success(self):
        for state in ("FAILED_BEFORE_SUBMIT", "UNVERIFIED", "EDIT_FAILED", "EDIT_MISMATCH"):
            self.assertIn(state, cli.UNFINISHED_STATES)

    def test_a_confirmed_change_is(self):
        for state in ("CONFIRMED", "EDIT_VERIFIED", "COMMENT_VERIFIED", "NO_CHANGE"):
            self.assertNotIn(state, cli.UNFINISHED_STATES)


if __name__ == "__main__":
    unittest.main()

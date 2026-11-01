"""A platform's warning stops the tool whichever route brought it, and only the platform's own check counts as one. Fakes only; nothing is contacted."""
import asyncio
from multiprocessing.connection import Listener
import os
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from video_publisher import adapters, browser_broker as broker, browser_runtime as rt, browser_worker, cli, message_sources, operations_batch, safety
from video_publisher.core import VpubError, atomic_json, home, read_json, stop_check


class Server:
    """A shared session's own loop on a thread, on a real local pipe, with its state file like the real process."""

    def __init__(self, handler):
        key = os.urandom(32)
        address = broker.new_address()
        self.listener = Listener(address, authkey=key)
        self.path = broker.state_path("x", "default")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(self.path, {"address": address, "key": key.hex(), "pid": os.getpid()})
        self.thread = Thread(target=lambda: asyncio.run(broker.serve(self.listener, handler, "x", idle=30, poll_seconds=0.05)), daemon=True)
        self.thread.start()

    def wait(self):
        self.thread.join(10)
        self.listener.close()
        self.path.unlink(missing_ok=True)


class Home(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        target = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime"), "VPUB_BROWSER_IDLE_SECONDS": "300"})
        target.start()
        self.addCleanup(target.stop)
        home().mkdir(parents=True, exist_ok=True)

    def stop_file(self):
        return home() / "STOP.json"


class EveryRouteTests(Home):
    def session_answers(self, code):
        """A real local pipe to a session that answers with the warning, as the session process does."""
        async def warn(payload):
            raise VpubError(code, "Check.", "Run login.")
        server = Server(warn)
        return server

    def session_fails_to_start(self, code):
        def fail_to_start(platform, account):
            atomic_json(broker.failure_path(platform, account), {"code": code, "message": "Check.", "hint": ""})
            return MagicMock(poll=lambda: 1)
        return patch.object(broker, "start", fail_to_start)

    def assert_held_with_no_more_requests(self, platform, code, action):
        saved = read_json(self.stop_file())
        self.assertEqual((platform, code, "safety"), (saved["platform"], saved["code"], saved["source"]))
        with patch.object(broker, "call") as session, patch.object(broker, "start") as starting, patch.object(broker, "release") as release, patch.object(adapters, "worker_call") as worker, patch.object(adapters, "hidden_desktop_call") as hidden, \
                self.assertRaises(VpubError) as caught:
            adapters.browser_call(platform, action, "default")
        self.assertEqual("stopped", caught.exception.code)
        for route in (session, starting, release, worker, hidden):
            route.assert_not_called()
        safety.clear_stop()

    def test_a_warning_in_a_shared_sessions_answer_holds_every_later_command(self):
        for code in ("human_verification_required", "rate_limited"):
            with self.subTest(code=code):
                server = self.session_answers(code)
                with self.assertRaises(VpubError):
                    adapters.browser_call("x", "operations", "default")
                server.wait()
                self.assert_held_with_no_more_requests("x", code, "operations")

    def test_a_warning_while_a_session_starts_holds_every_later_command(self):
        for code in ("human_verification_required", "rate_limited"):
            with self.subTest(code=code), self.session_fails_to_start(code):
                with self.assertRaises(VpubError):
                    adapters.browser_call("douyin", "operations", "default")
                self.assert_held_with_no_more_requests("douyin", code, "operations")

    def test_a_warning_from_a_one_shot_worker_holds_every_later_command(self):
        for code in ("human_verification_required", "rate_limited"):
            with self.subTest(code=code):
                answer = {"ok": False, "error": {"code": code, "message": "No.", "hint": ""}}
                with patch.object(broker, "release"), patch.object(adapters, "worker_call", return_value=answer), self.assertRaises(VpubError):
                    adapters.browser_call("instagram", "edit", "default")
                self.assert_held_with_no_more_requests("instagram", code, "edit")

    def test_a_warning_from_youtubes_hidden_window_holds_every_later_command(self):
        for code in ("human_verification_required", "rate_limited"):
            with self.subTest(code=code):
                answer = {"ok": False, "error": {"code": code, "message": "No.", "hint": ""}}
                with patch.object(adapters, "hidden_desktop_call", return_value=answer), patch.object(adapters, "worker_call", return_value=answer), self.assertRaises(VpubError):
                    adapters.browser_call("youtube", "operations", "default")
                self.assert_held_with_no_more_requests("youtube", code, "operations")

    def test_a_person_clearing_it_lets_commands_through_again(self):
        with patch.object(broker, "call", side_effect=VpubError("human_verification_required", "Check.")), self.assertRaises(VpubError):
            adapters.browser_call("x", "operations", "default")
        safety.clear_stop()
        with patch.object(broker, "call", return_value={"ok": True}):
            self.assertEqual({"ok": True}, adapters.browser_call("x", "operations", "default"))

    def test_an_ordinary_error_stops_nothing(self):
        with patch.object(broker, "call", side_effect=VpubError("layout_changed", "Moved.")), self.assertRaises(VpubError):
            adapters.browser_call("x", "operations", "default")
        self.assertFalse(self.stop_file().exists())


class SeenWhereItHappensTests(Home, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Home.setUp(self)

    def challenged(self, address="https://x.com/home"):
        return MagicMock(url=address, locator=lambda selector: AsyncMock(all=AsyncMock(return_value=[])),
                         get_by_text=lambda labels: AsyncMock(all=AsyncMock(return_value=[AsyncMock(is_visible=AsyncMock(return_value=True), evaluate=AsyncMock(return_value=False))])))

    async def test_the_check_is_recorded_before_the_error_goes_anywhere(self):
        with self.assertRaises(VpubError):
            await rt.risk_check(self.challenged())
        self.assertEqual(("x", "human_verification_required"), (read_json(self.stop_file())["platform"], read_json(self.stop_file())["code"]))

    async def test_each_site_is_named(self):
        for address, platform in (("https://www.instagram.com/x", "instagram"), ("https://creator.douyin.com/a", "douyin"), ("https://studio.youtube.com/a", "youtube"), ("https://accounts.google.com/a", "youtube"), ("https://mobile.x.com/a", "x")):
            self.assertEqual(platform, rt.platform_at(address))
        self.assertEqual("unknown", rt.platform_at("https://example.com/x.com"))

    async def test_a_read_that_keeps_going_past_a_failed_step_still_leaves_the_stop(self):
        page = self.challenged()

        async def mentions(page, identity, limit):
            await rt.risk_check(page)
        with patch.object(message_sources, "x_mentions", mentions):
            result = await message_sources.execute(page, "x", "me", {"sources": ["mentions"], "limit": 5})
        self.assertEqual(("FAILED", "human_verification_required"), (result["sources"]["mentions"]["state"], result["sources"]["mentions"]["code"]))
        self.assertTrue(self.stop_file().exists())

    async def test_a_batch_that_keeps_its_earlier_results_still_leaves_the_stop(self):
        page = self.challenged()

        async def read(page, platform, identity, job):
            await rt.risk_check(page)
        with patch("video_publisher.operations_browser.execute", read):
            result = await operations_batch.execute(page, "x", "me", {"platform": "x", "expected_identity": "me", "jobs": [{"operation": "stats", "work_id": "1234567890", "limit": 1}]})
        self.assertEqual("human_verification_required", result["results"][0]["error"]["code"])
        self.assertTrue(self.stop_file().exists())


class SignInDuringAStopTests(Home):
    def stop(self, **values):
        atomic_json(self.stop_file(), values)

    def test_the_platforms_own_sign_in_window_still_opens(self):
        self.stop(platform="x", code="human_verification_required", source="safety")
        stop_check("x", signing_in=True)
        with patch("video_publisher.auth_jobs.start", return_value={"state": "LOGIN_PENDING"}) as start:
            self.assertEqual("LOGIN_PENDING", adapters.auth_login("x", "default", method="browser")["state"])
        start.assert_called_once()

    def test_nothing_else_does(self):
        self.stop(platform="x", code="human_verification_required", source="safety")
        with self.assertRaises(VpubError):
            stop_check("x")
        with self.assertRaises(VpubError):
            stop_check("douyin", signing_in=True)
        with self.assertRaises(VpubError):
            adapters.auth_status("x", "default", "browser")

    def test_a_stop_a_person_placed_keeps_every_window_closed(self):
        self.stop(reason="paused")
        with self.assertRaises(VpubError):
            stop_check("x", signing_in=True)

    def test_a_broken_stop_file_keeps_every_window_closed(self):
        self.stop_file().write_text("broken")
        with self.assertRaises(VpubError):
            stop_check("x", signing_in=True)

    def test_the_worker_lets_only_a_sign_in_through(self):
        self.stop(platform="x", code="human_verification_required", source="safety")
        with self.assertRaises(VpubError) as caught:
            asyncio.run(browser_worker.run({"platform": "x", "account": "default", "action": "status"}))
        self.assertEqual("stopped", caught.exception.code)
        playwright = MagicMock(__aenter__=AsyncMock(return_value=MagicMock()), __aexit__=AsyncMock(return_value=False))
        with patch("patchright.async_api.async_playwright", return_value=playwright), patch.object(rt, "login", new=AsyncMock(return_value={"logged_in": True})) as login:
            self.assertEqual({"logged_in": True}, asyncio.run(browser_worker.run({"platform": "x", "account": "default", "action": "login", "job_path": str(self.root / "job.json")})))
        login.assert_awaited_once()

    def test_after_signing_in_during_a_stop_the_status_says_what_is_left_to_do(self):
        self.stop(platform="x", code="human_verification_required", source="safety")
        job = {"state": "LOGGED_IN", "logged_in": True, "identity": "me", "next": "Once its check is done, run vpub stop --clear."}
        atomic_json(home() / "auth-jobs/x.json", job)
        answer = cli.run_command(cli.parser().parse_args(["auth", "status", "x"]))
        self.assertEqual(job["next"], answer["next"])
        safety.clear_stop()
        with patch.object(adapters, "auth_status", return_value={"logged_in": True, "identity": "me"}) as live:
            self.assertNotIn("next", cli.run_command(cli.parser().parse_args(["auth", "status", "x"])))
        live.assert_called_once()

    def test_a_person_clearing_it_is_still_the_only_way_out(self):
        self.stop(platform="x", code="human_verification_required", source="safety")
        stop_check("x", signing_in=True)
        self.assertTrue(self.stop_file().exists())
        cli.run_command(cli.parser().parse_args(["stop", "--clear"]))
        stop_check("x")


class PublishingTests(Home):
    def plan(self):
        return {"confirmation": "hash", "video_sha256": "a" * 64, "platforms": {"x": {"account": "default", "title": "T"}, "douyin": {"account": "default", "title": "T"}}}

    def test_a_check_at_sign_in_stops_the_rest_and_says_so(self):
        plan = self.plan()
        with patch.object(adapters, "preflight", side_effect=VpubError("human_verification_required", "Check.")), patch.object(adapters, "publish") as upload:
            result = cli.publish_plan(plan, "hash")
        upload.assert_not_called()
        self.assertEqual("x", read_json(self.stop_file())["platform"])
        self.assertEqual("human_verification_required", result["results"]["x"]["code"])
        self.assertEqual("stopped", result["results"]["douyin"]["code"])

    def test_a_check_during_one_upload_keeps_that_upload_in_the_answer(self):
        plan = self.plan()

        def upload(platform, plan, options, checkpoint):
            # What a publish does when its page shows a check: the receipt comes back, and the stop is already in place.
            from video_publisher import safety
            safety.pushed_back(platform, "human_verification_required")
            return {"state": "WAITING_FOR_USER", "user_action_required": True}
        with patch.object(adapters, "preflight", return_value={"identity": "me"}), patch.object(adapters, "publish", side_effect=upload) as sent, \
                patch.object(cli, "snapshot_plan", side_effect=lambda plan, platform: plan), patch.object(adapters, "email_reminder"):
            result = cli.publish_plan(plan, "hash")
        self.assertEqual(1, sent.call_count)
        self.assertEqual("WAITING_FOR_USER", result["results"]["x"]["state"])
        self.assertEqual("stopped", result["results"]["douyin"]["code"])


class WhoIsAskingTests(Home, unittest.IsolatedAsyncioTestCase):
    """In a real browser, because where the words sit is the whole point."""

    def setUp(self):
        Home.setUp(self)

    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(5000)
        self.body = ""

        async def answer(route):
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body="<!doctype html><body>" + self.body)
        await self.page.route("**/*", answer)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def shown(self, body, address="https://x.com/home"):
        self.body = body
        await self.page.goto(address)
        return await rt.challenge_shown(self.page)

    async def test_a_viewer_writing_the_words_is_not_the_platform_asking(self):
        for body in ('<article><span>Confirm it\'s you</span></article>',
                     '<div data-testid="cellInnerDiv"><div>Verify your identity</div></div>',
                     '<ytcp-comment-thread><ytcp-comment><div id="content-text">Unusual activity</div></ytcp-comment></ytcp-comment-thread>',
                     '<ul><li role="list-item"><div>请完成验证</div></li></ul>',
                     '<div class="comment-row-abc"><span>Choose a way to recover</span></div>',
                     '<div data-pagelet="IGDInboxThreadListScrollableAreaPagelet"><div role="button"><span>Unusual activity</span></div></div>',
                     '<div><a href="/viewer/">viewer</a><span dir="auto">Confirm it\'s you</span><time datetime="2026-11-01">1h</time><span role="button">Reply</span></div>',
                     '<div role="dialog"><div><a href="/viewer/">viewer</a><span>Verify your identity</span><time datetime="2026-11-01">1h</time></div></div>'):
            with self.subTest(body=body):
                self.assertFalse(await self.shown(body))
        await rt.risk_check(self.page)
        self.assertFalse(self.stop_file().exists())

    async def test_the_platform_saying_them_is(self):
        for body in ("<h1>Confirm it's you</h1>", '<div role="dialog"><div>Choose a way to recover</div></div>', "<div>安全验证</div>", "<main><h2>確認是你本人</h2></main>",
                     '<article>An ordinary post</article><div role="dialog"><span>Verify your identity</span></div>',
                     "<div role=\"dialog\"><ul><li>Confirm it's you</li></ul></div>",
                     '<main><article><a href="/a">a</a><time datetime="2026-11-01">1h</time>A post</article></main><div role="dialog"><h2>Unusual activity</h2><a href="/help">Help</a></div>',
                     '<div><div>Unusual activity</div><main>' + '<article><a href="/a">a</a><time datetime="2026-11-01">1h</time>' + 'word ' * 500 + '</article></main></div>'):
            with self.subTest(body=body[:60]):
                self.assertTrue(await self.shown(body))

    async def test_words_nobody_can_see_are_not(self):
        self.assertFalse(await self.shown("<h1 hidden>Confirm it's you</h1>"))

    async def test_the_captcha_frame_is(self):
        self.assertTrue(await self.shown('<iframe src="https://verify.example/verifycenter/captcha/x" width="100" height="100"></iframe>'))

    async def test_a_check_address_counts_whatever_the_page_says(self):
        for address in ("https://www.instagram.com/challenge/abc/", "https://www.instagram.com/accounts/suspended/", "https://x.com/account/access", "https://accounts.google.com/v3/signin/challenge/pwd", "https://accounts.google.com/signin/rejected"):
            with self.subTest(address=address):
                self.assertTrue(await self.shown("<p>Nothing to see</p>", address))

    async def test_an_account_that_happens_to_be_called_challenge_is_not_a_check(self):
        self.assertFalse(await self.shown("<p>A profile</p>", "https://x.com/challenge"))


if __name__ == "__main__":
    unittest.main()

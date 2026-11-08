"""A Douyin sign-in finishes by itself once the person is signed in, without their clicking 首页. From a field run on 2026-11-05: after the QR scan
Douyin left the window on the upload page, signed in but without the 抖音号 that only Creator Center's home page shows, and on a slow connection that
page then sat on 加载中… for minutes. Small page copies in an invisible browser, and stand-in tabs; nothing is contacted."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parent))
from stage_browser import SharedBrowser

from video_publisher import auth_jobs, browser_runtime as rt, cli
from video_publisher.core import VpubError, atomic_json, read_json

OWNER = "dy12345"
HOME = rt.URLS["douyin"]
UPLOAD = "https://creator.douyin.com/creator-micro/content/upload"
HOME_PATH, UPLOAD_PATH = urlsplit(HOME).path, urlsplit(UPLOAD).path
MENU = "<nav><div>首页</div><div>内容管理</div><div>数据中心</div></nav>"
DASHBOARD = MENU + f"<div>小白白</div><div>抖音号：{OWNER}</div>"
# The home page on a slow connection: 加载中… on every load of it, until the account shows a while later (see CreatorCenterTests.scan).
LATE = MENU + "<div id='card'>加载中…</div>"
QR = '<div><div>扫码登录</div><div>验证码登录</div><div>密码登录</div><div class="login-qrcode"><img alt="二维码" width="100" height="100"></div></div>'
SENT_TO_SIGN_IN = "<script>location.replace('/')</script>"
SIGNED_IN = "Signed in; opening Creator Center's home page to read your 抖音号"
SIGN_IN = rt.LOGIN_NOTES["login"]["hint"]
REAL_SLEEP = asyncio.sleep


class Clock:
    """The sign-in's own clock. Each of its waits passes at once and moves this on by as long, so a quarter of an hour takes a moment."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def time(self):
        return time.time()


class SignIn:
    """Runs a sign-in on these tabs with that clock. Each wait moves it on by `speed` times its length (`seconds`), records what the job file says,
    then runs `each`, as a person acts between the sign-in's looks."""

    async def sign_in(self, pages, each=None, platform="douyin", speed=1):
        context = types.SimpleNamespace(pages=pages, close=AsyncMock())
        self.waits, self.notes, self.saves = 0, [], AsyncMock()

        async def wait(seconds):
            self.clock.now += seconds * speed
            self.waits, self.seconds = self.waits + 1, seconds
            await REAL_SLEEP(0.01)  # The page's own scripts run meanwhile.
            if self.job.exists():
                self.notes.append(read_json(self.job))
            if each:
                await each(context)
        with patch.object(rt, "open_context", new=AsyncMock(return_value=context)), patch.object(rt, "save_session", new=self.saves), \
                patch.object(rt, "time", self.clock), patch.object(rt, "asyncio", types.SimpleNamespace(sleep=wait, wait_for=asyncio.wait_for)):
            return await rt.login(None, {"platform": platform, "account": "default", "job_path": str(self.job)})

    def hints(self):
        return [note.get("hint", "") for note in self.notes]

    def signed_in_hints(self):
        return [hint for hint in self.hints() if hint.startswith("Signed in")]


class CreatorCenterTests(SignIn, SharedBrowser):
    """Creator Center as the field run met it. Signed out, its pages send the visitor to the sign-in page and its QR code. Once that is scanned,
    the window lands on the upload page, which has the left menu and no 抖音号."""

    def prepare(self):
        self.clock, self.job, self.site, self.loads, self.scanned, self.waits = Clock(), self.root / "job.json", {}, [], None, 0

    async def handle(self, route):
        path = urlsplit(route.request.url).path
        self.loads.append((path, self.clock.now, self.waits))
        await self.page_at(route, self.site.get(path, QR if path == "/" else SENT_TO_SIGN_IN))

    def paths(self):
        return [path for path, *_ in self.loads]

    def scan(self, home_page, at=5, late=0):
        """Once the QR code has loaded and the sign-in has waited `at` times, the person scans it, and Douyin takes the window to the upload page,
        signed in. A `late` home page shows the account only that many waits after each load of it."""
        async def person(context):
            if self.scanned is None and self.waits >= at and "/" in self.paths():
                self.site = {HOME_PATH: home_page, UPLOAD_PATH: MENU + "<div>发布视频</div>"}
                self.scanned = len(self.loads)
                await self.page.goto(UPLOAD)
            homes = [looked for path, _, looked in self.loads[self.scanned:] if path == HOME_PATH] if self.scanned is not None else []
            if late and homes and self.waits - homes[-1] >= late:
                await self.page.evaluate(f"(card => card && (card.textContent = '抖音号：{OWNER}'))(document.getElementById('card'))")
        return person

    async def test_a_sign_in_left_on_the_upload_page_opens_the_home_page_itself(self):
        result = await self.sign_in([self.page], self.scan(DASHBOARD))
        self.assertEqual((True, OWNER), (result["logged_in"], result["identity"]))
        self.assertEqual(OWNER, read_json(rt.identity_path("douyin", "default"))["identity"])
        # Nothing was loaded over the QR code. After the scan the home page was opened once, no sooner than 30 seconds after the first, and reloaded once.
        self.assertEqual([HOME_PATH, "/"], self.paths()[:self.scanned])
        self.assertEqual([UPLOAD_PATH, HOME_PATH, HOME_PATH], self.paths()[self.scanned:])
        self.assertGreaterEqual(self.loads[self.scanned + 1][1] - self.loads[0][1], rt.DOUYIN_HOME_SECONDS)
        self.assertEqual({SIGNED_IN}, {hint.split(".")[0] for hint in self.signed_in_hints()})

    async def test_a_home_page_that_loads_late_is_waited_for_and_not_opened_again(self):
        """It is opened once and reloaded once, though each load takes well over 30 seconds to show the account."""
        result = await self.sign_in([self.page], self.scan(LATE, late=10), speed=2)
        self.assertEqual(OWNER, result["identity"])
        self.assertEqual([UPLOAD_PATH, HOME_PATH, HOME_PATH], self.paths()[self.scanned:])
        opened, reloaded = [moment for path, moment, _ in self.loads[self.scanned:] if path == HOME_PATH]
        self.assertGreater(self.clock.now - reloaded, rt.DOUYIN_HOME_SECONDS)
        self.assertGreater(reloaded - opened, rt.DOUYIN_HOME_SECONDS)
        self.assertEqual({SIGNED_IN}, {hint.split(".")[0] for hint in self.signed_in_hints()})

    async def test_a_home_page_that_never_shows_the_number_is_never_taken_for_the_account(self):
        """Only the 抖音号 counts, never a nickname. The sign-in runs out of time, records nothing and says why."""
        self.site = {HOME_PATH: MENU + "<div>小白白</div><div>粉丝 3</div>"}
        with self.assertRaises(VpubError) as caught:
            await self.sign_in([self.page], speed=30)
        self.assertEqual("login_timeout", caught.exception.code)
        self.assertIn("抖音号", caught.exception.message)
        self.assertEqual([HOME_PATH], self.paths())
        self.assertFalse(rt.identity_path("douyin", "default").exists())
        self.saves.assert_not_awaited()
        self.assertTrue(self.hints()[-1].startswith(SIGNED_IN))

    async def test_a_sign_in_that_does_not_hold_on_the_reload_asks_for_the_sign_in_again(self):
        """The 抖音号 showed, but the reload brought the QR code back: the job says to sign in again, not that the person is signed in."""
        scan = self.scan(DASHBOARD)

        async def signed_out(context):
            await scan(context)
            if self.scanned is not None and self.seconds == 3:  # The wait before the reload: Douyin signs the window out meanwhile.
                self.site = {}
            if self.waits == 40:
                context.pages.clear()
        with self.assertRaises(VpubError) as caught:
            await self.sign_in([self.page], signed_out, speed=2)
        self.assertEqual("login_cancelled", caught.exception.code)
        self.assertEqual([UPLOAD_PATH, HOME_PATH, HOME_PATH, "/"], self.paths()[self.scanned:])
        self.assertTrue(self.signed_in_hints())
        self.assertEqual(SIGN_IN, self.hints()[-1])
        self.assertFalse(rt.identity_path("douyin", "default").exists())

    async def test_a_qr_code_is_never_loaded_over(self):
        async def leave(context):
            if self.waits == 20:
                context.pages.clear()
        with self.assertRaises(VpubError) as caught:
            await self.sign_in([self.page], leave, speed=4)
        self.assertEqual("login_cancelled", caught.exception.code)
        self.assertEqual([HOME_PATH, "/"], self.paths())
        self.assertEqual([], self.signed_in_hints())

    async def test_a_creator_center_page_showing_a_sign_in_form_or_a_check_is_left_alone(self):
        """Even at a Creator Center address and beside its menu."""
        for shown, state in ((QR, "LOGIN_PENDING"), ("<div>扫码登录</div>", "LOGIN_PENDING"), ('<div class="qrcode-box"><canvas width="80" height="80"></canvas></div>', "LOGIN_PENDING"),
                             ('<input type="password">', "LOGIN_PENDING"), ("<span>验证码登录</span>", "LOGIN_PENDING"), ("<button>登录</button>", "LOGIN_PENDING"),
                             ("<div>安全验证</div>", "VERIFICATION_REQUIRED")):
            with self.subTest(shown=shown):
                self.loads.clear()
                self.site = {UPLOAD_PATH: MENU + shown}

                async def there(context):
                    if self.waits == 3:
                        await self.page.goto(UPLOAD)
                    elif self.waits == 12:
                        context.pages.clear()
                with self.assertRaises(VpubError):
                    await self.sign_in([self.page], there, speed=8)
                # Over two minutes there, with nothing loaded after it.
                self.assertEqual(UPLOAD_PATH, self.paths()[-1])
                self.assertEqual(1, self.paths().count(HOME_PATH))
                self.assertEqual(state, self.notes[-1]["state"])
                self.assertEqual([], self.signed_in_hints())

    async def signed_in(self, body, address=UPLOAD):
        self.site = {urlsplit(address).path: body}
        await self.page.goto(address)
        return await rt.douyin_signed_in(self.page)

    async def test_only_creator_centers_own_signed_in_pages_count(self):
        for address in ("https://creator.douyin.com/", "https://www.douyin.com/creator-micro/content/upload", "http://creator.douyin.com/creator-micro/content/upload",
                        "https://creator.douyin.com.example/creator-micro/content/upload"):
            with self.subTest(address=address):
                # Decided by the address alone, before anything on the page is read.
                self.assertFalse(await rt.douyin_signed_in(types.SimpleNamespace(url=address)))
        for body, address, right in ((MENU, UPLOAD, True), (DASHBOARD, HOME, True), (MENU + "<div>加载中…</div>", HOME, True), ("<div>发布视频</div>", UPLOAD, False),
                                     # Words that only look like a sign-in, and a QR code that isn't shown, are no sign-in form.
                                     (MENU + '<div>登录设备管理</div><a hidden>退出登录</a><div class="qrcode" style="display:none">x</div>', UPLOAD, True)):
            with self.subTest(body=body, address=address):
                self.assertEqual(right, await self.signed_in(body, address))
        for shown in ("<div>扫码登录</div>", "<div>手机号登录</div>", "<span>登录/注册</span>", "<button>登录</button>", '<input type="password">',
                      '<img alt="二维码" width="10" height="10">', '<div class="qr-code-wrap">x</div>', "<div>安全验证</div>"):
            with self.subTest(shown=shown):
                self.assertFalse(await self.signed_in(MENU + shown))


class Tab:
    """A stand-in tab: where it is, the account it shows, whether Creator Center shows it signed in there, and which load of it is showing.
    Its first load ends on the upload page, where the person signed in. A check of None is a tab that can't be read just then."""

    def __init__(self, clock, url=UPLOAD, who=None, at_home=None, follows=True, refused=(), slow=0):
        self.clock, self.url, self.who, self.at_home, self.follows, self.refused, self.slow = clock, url, who, at_home, follows, refused, slow
        self.closed, self.check, self.form, self.signed_in, self.load, self.blank, self.visits = False, False, False, True, 0, 0, []
        self.goto = AsyncMock(side_effect=self.went)
        self.reload = AsyncMock(side_effect=self.reloaded)
        self.bring_to_front = AsyncMock()

    async def went(self, url, **options):
        self.visits.append(self.clock.now)
        if len(self.visits) in self.refused:
            raise TimeoutError("Page.goto: Timeout 60000ms exceeded.")
        self.load += 1
        if len(self.visits) > 1 and self.follows:
            self.url, self.who = url, self.at_home if url == HOME else None

    async def reloaded(self, **options):
        self.load += 1
        self.blank = self.slow  # How many reads show nothing yet.

    async def evaluate(self, expression):
        return self.load

    def account(self):
        if self.blank:
            self.blank -= 1
            return None
        return self.who

    def is_closed(self):
        return self.closed


class StandInTests(SignIn, unittest.IsolatedAsyncioTestCase):
    """The timing and the job file, with stand-in tabs and the sign-in's own clock."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.clock, self.job = Clock(), self.root / "job.json"
        self.signed_in = AsyncMock(side_effect=lambda page: page.signed_in)

        async def identity(page, platform):
            return page.account()

        async def check(page):
            if page.check is None:
                raise RuntimeError("The tab can't be read just now.")
            return page.check

        async def form(page):
            return page.form
        for target in (patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")}), patch.object(rt, "identity", identity), patch.object(rt, "challenge_shown", check),
                       patch.object(rt, "douyin_signed_in", self.signed_in), patch.object(rt, "sign_in_shown", form)):
            target.start()
            self.addCleanup(target.stop)

    def until(self, moment):
        async def leave(context):
            if self.clock.now >= moment:
                context.pages.clear()
        return leave

    async def test_creator_centers_home_page_is_opened_at_most_once_every_30_seconds(self):
        """Here Douyin keeps sending the tab back to the upload page, so every look could open the home page again."""
        tab = Tab(self.clock, follows=False)
        with self.assertRaises(VpubError) as caught:
            await self.sign_in([tab], self.until(1125))
        self.assertEqual("login_cancelled", caught.exception.code)
        self.assertEqual([1000, 1030, 1060, 1090, 1120], tab.visits)

    async def test_a_home_page_that_does_not_open_in_time_is_tried_again_30_seconds_later(self):
        tab = Tab(self.clock, at_home=OWNER, refused={2})
        result = await self.sign_in([tab])
        self.assertEqual(OWNER, result["identity"])
        self.assertEqual([1000, 1030, 1060], tab.visits)

    async def test_vpub_auth_status_says_the_person_signed_in_and_what_it_waits_for(self):
        path = auth_jobs.job_path("douyin")
        attempt = "0123456789abcdef0123456789abcdef"
        atomic_json(path, {"state": "STARTING", "platform": "douyin", "account": "default", "attempt": attempt, "worker_pid": os.getpid(), "start_deadline": time.time() + 30})
        self.job = path.parent / attempt / "result.json"
        tab, said = Tab(self.clock, at_home=OWNER), {}

        async def ask(context):
            if self.waits == 1:
                said.update(cli.status_selected(["douyin"])["douyin"])
        result = await self.sign_in([tab], ask)
        self.assertEqual("LOGIN_PENDING", said["state"])
        self.assertTrue(said["hint"].startswith(SIGNED_IN))
        self.assertNotIn("Complete official login", said["hint"])
        self.assertIn("抖音号", said["waiting_for"])
        self.assertEqual(OWNER, result["identity"])
        tab.reload.assert_awaited_once()

    async def test_a_check_after_the_person_signed_in_is_reported_and_then_the_wait_again(self):
        tab = Tab(self.clock, follows=False)

        async def check_comes_and_goes(context):
            tab.check = 3 <= self.waits < 6
            if self.waits == 8:
                context.pages.clear()
        with self.assertRaises(VpubError):
            await self.sign_in([tab], check_comes_and_goes)
        said = [(note["state"], note["hint"].split(".")[0]) for note in self.notes]
        self.assertEqual([("LOGIN_PENDING", SIGNED_IN), ("VERIFICATION_REQUIRED", "The platform is asking for a check or account recovery"), ("LOGIN_PENDING", SIGNED_IN)],
                         [step for at, step in enumerate(said) if not at or step != said[at - 1]])

    async def test_a_tab_still_showing_a_qr_code_does_not_undo_a_sign_in_on_another(self):
        signed, stray = Tab(self.clock, at_home=OWNER, slow=3), Tab(self.clock, follows=False)
        stray.signed_in, stray.form = False, True
        result = await self.sign_in([signed, stray])
        self.assertEqual(OWNER, result["identity"])
        hints = self.hints()
        since = next(at for at, hint in enumerate(hints) if hint.startswith(SIGNED_IN))
        self.assertEqual({SIGNED_IN}, {hint.split(".")[0] for hint in hints[since:]})
        self.assertEqual([], stray.visits)

    async def test_other_platforms_never_open_a_page_for_this(self):
        for platform in ("x", "youtube", "instagram"):
            with self.subTest(platform=platform):
                tab = Tab(self.clock, follows=False)
                with self.assertRaises(VpubError):
                    await self.sign_in([tab], self.until(self.clock.now + 120), platform=platform, speed=4)
                self.assertEqual(1, len(tab.visits))
                self.assertEqual({(rt.LOGIN_NOTES["login"]["hint"], None)}, {(note["hint"], note.get("waiting_for")) for note in self.notes})
        self.signed_in.assert_not_awaited()

    async def test_a_reload_that_shows_the_account_late_is_watched_rather_than_reloaded_again(self):
        """What it proves is unchanged: the account shows again on the load the reload brought. Other platforms' job file says what it always did."""
        for platform, note in (("douyin", SIGNED_IN), ("x", SIGN_IN)):
            with self.subTest(platform=platform):
                tab = Tab(self.clock, url=HOME, who=OWNER, slow=3)
                result = await self.sign_in([tab], platform=platform)
                self.assertEqual(OWNER, result["identity"])
                tab.reload.assert_awaited_once()
                self.assertTrue(self.hints()[-1].startswith(note))

    async def test_only_the_load_the_reload_brought_counts(self):
        """A tab that loads something else by itself afterwards, such as an earlier page brought back, is reloaded again before its account counts."""
        tab, moved = Tab(self.clock, url=HOME, who=OWNER, slow=1), []

        async def load_again(context):
            if tab.reload.await_count == 1 and not moved:
                moved.append(True)
                tab.load += 1
        result = await self.sign_in([tab], load_again)
        self.assertEqual(OWNER, result["identity"])
        self.assertEqual(2, tab.reload.await_count)

    async def test_a_check_on_the_reloaded_page_means_a_new_reload_once_it_is_gone(self):
        """From a review: the person passed a check on the load the reload brought, the account then showed on that same load, and the sign-in was
        recorded there, though the next load could ask again. Now nothing is recorded until the account shows on a new reload, and the same goes
        after a tab that couldn't be read, which could have been showing one. On every platform."""
        for platform in ("douyin", "x", "youtube", "instagram"):
            # What shows (a check, or None for a tab that can't be read), from which wait after the reload to which, and how many reads the reloaded
            # page shows nothing for. The first wait after the reload comes just before the look right after it; each later one, before a pass's first look.
            for shown, start, end, slow in ((True, 1, 3, 0), (True, 1, 1, 0), (True, 2, 3, 6), (None, 1, 1, 0), (None, 2, 3, 6)):
                with self.subTest(platform=platform, shown=shown, start=start, end=end):
                    rt.identity_path(platform, "default").unlink(missing_ok=True)
                    tab, after, recorded = Tab(self.clock, url=HOME, who=OWNER, slow=slow), [], []

                    async def reload(**options):
                        recorded.append(bool(self.saves.await_count) or rt.identity_path(platform, "default").exists())
                        await tab.reloaded(**options)
                    tab.reload.side_effect = reload

                    async def person(context):
                        # The tab stays on the load the reload brought while the check comes and goes.
                        if tab.reload.await_count == 1:
                            after.append(self.waits)
                            tab.check = shown if start <= len(after) <= end else False
                    result = await self.sign_in([tab], person, platform=platform)
                    self.assertEqual(OWNER, result["identity"])
                    self.assertEqual(2, tab.reload.await_count)
                    self.assertEqual([False, False], recorded)
                    self.assertEqual(bool(shown and end > 1), "VERIFICATION_REQUIRED" in {note["state"] for note in self.notes})

    async def test_a_reload_that_never_shows_the_account_again_records_nothing(self):
        tab = Tab(self.clock, url=HOME, who=OWNER, slow=10 ** 6)
        with self.assertRaises(VpubError) as caught:
            await self.sign_in([tab], platform="x", speed=30)
        self.assertEqual(("login_timeout", "Login was not completed within 15 minutes; saved profile is retained."), (caught.exception.code, caught.exception.message))
        tab.reload.assert_awaited_once()
        self.assertFalse(rt.identity_path("x", "default").exists())
        self.saves.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

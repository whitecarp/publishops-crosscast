"""Scheduling on X through Media Studio's own calendar, in a real browser against a small copy of what a signed-in account showed."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from video_publisher import browser_backends, browser_forms, cli, x_pages
from video_publisher.core import VpubError, atomic_json, load_plan

OWNER = "owner"


def page_html(keep_clock=True):
    months = "".join(f'<option value="{i}">{name}</option>' for i, name in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()))
    years = "".join(f'<option value="{year}">{year}</option>' for year in range(2026, 2032))
    return f"""<!doctype html><body><textarea name="tweetData.text" placeholder="What's happening?">Words</textarea>
      <button type="button" id="open" class="Button Button--link">Schedule</button>
      <div id="panel" hidden>
        <fieldset class="CalendarNavigation-monthFormSelect"><select id="m">{months}</select></fieldset>
        <fieldset class="CalendarNavigation-yearFormSelect"><select id="y">{years}</select></fieldset>
        <div class="Calendar Calendar--fixedHeight" id="days"></div>
        <input type="text" class="clock" id="clock" value="12:24 AM">
      </div>
      <input type="text" name="other" value="not a time">
      <button type="button" id="final" class="Button Button--primary tweetbtn">Post</button>
      <script>
      const m = document.getElementById('m'), y = document.getElementById('y'), days = document.getElementById('days'), final = document.getElementById('final');
      const now = new Date(); m.value = String(now.getMonth()); y.value = String(now.getFullYear());
      function draw() {{
        days.innerHTML = '';
        const count = new Date(Number(y.value), Number(m.value) + 1, 0).getDate();
        for (let day = 1; day <= count + 5; day++) {{
          const cell = document.createElement('div'); const other = day > count;
          cell.className = 'Calendar-day is-selectable' + (other ? ' is-adjacentMonth' : '');
          cell.innerHTML = '<span class="Calendar-dayText">' + (other ? day - count : day) + '</span>';
          cell.onclick = () => {{ for (const c of days.children) c.classList.remove('is-rangeStart'); cell.classList.add('is-rangeStart'); final.disabled = false; }};
          days.append(cell);
        }}
      }}
      m.onchange = draw; y.onchange = draw; draw();
      document.getElementById('open').onclick = () => {{ document.getElementById('panel').hidden = false; final.textContent = 'Save'; final.disabled = true; }};
      {'' if keep_clock else "document.getElementById('clock').onchange = e => { e.target.value = '12:24 AM'; };"}
      final.onclick = () => fetch('/save', {{method: 'POST', body: JSON.stringify({{month: m.value, year: y.value, day: document.querySelector('.is-rangeStart .Calendar-dayText').textContent, clock: document.getElementById('clock').value}})}});
      </script>"""


class Calendar(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page(locale="en-US")
        self.page.set_default_timeout(5000)
        self.saved = []
        await self.page.route("**/*", self.handle)
        self.html = page_html()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def handle(self, route):
        if route.request.method == "POST":
            self.saved.append(json.loads(route.request.post_data))
            return await route.fulfill(status=200, content_type="application/json", body="{}")
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=self.html)

    async def open(self, html=None):
        self.html = html or self.html
        await self.page.goto("https://studio.x.com/library/1_2/edit")

    def next_month_day(self, day=3, hour=15, minute=7):
        # On this computer's clock that day, which may be on the other side of a clock change from today.
        local = datetime.now()
        year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
        return datetime(year, month, day, hour, minute).astimezone()

    async def test_the_date_and_time_are_chosen_and_read_back_and_nothing_is_saved(self):
        await self.open()
        when = self.next_month_day()
        result = await browser_forms.x_schedule(self.page, when)
        self.assertEqual(([when.year, when.month, 3], [15, 7]), (result["date"], result["time"]))
        self.assertEqual("3:07 PM", await self.page.locator("#clock").input_value())
        self.assertEqual(str(when.month - 1), await self.page.locator("#m").input_value())
        self.assertEqual([], self.saved)

    async def test_morning_and_midnight_are_written_the_way_the_box_shows_them(self):
        await self.open()
        await browser_forms.x_schedule(self.page, self.next_month_day(hour=0, minute=5))
        self.assertEqual("12:05 AM", await self.page.locator("#clock").input_value())

    async def test_the_same_number_in_the_next_month_is_never_chosen(self):
        await self.open()
        await browser_forms.x_schedule(self.page, self.next_month_day(day=3))
        self.assertEqual(0, await self.page.locator(".is-adjacentMonth.is-rangeStart").count())

    async def test_a_clock_that_does_not_keep_the_time_stops_it(self):
        await self.open(page_html(keep_clock=False))
        with self.assertRaises(VpubError) as caught:
            await browser_forms.x_schedule(self.page, self.next_month_day())
        self.assertEqual("schedule_unverified", caught.exception.code)

    async def test_a_year_studio_does_not_offer_stops_it(self):
        await self.open()
        with self.assertRaises(VpubError) as caught:
            await browser_forms.x_schedule(self.page, datetime(2040, 1, 3, 9, 0, tzinfo=timezone.utc))
        self.assertEqual("schedule_unverified", caught.exception.code)

    async def test_saving_clicks_once_after_a_good_read_back_and_never_posts(self):
        await self.open()
        tx = StubTx({"schedule": self.next_month_day().isoformat()})
        with patch.object(browser_forms, "verify_scheduled_x", new=AsyncMock(return_value={"state": "SUBMISSION_VERIFIED"})):
            result = await browser_forms.x_send_scheduled(self.page, tx)
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertEqual(1, len(self.saved))
        self.assertEqual("3:07 PM", self.saved[0]["clock"])
        self.assertTrue(tx.receipt["schedule"]["at"])

    async def test_a_schedule_that_fails_saves_nothing(self):
        await self.open(page_html(keep_clock=False))
        with self.assertRaises(VpubError):
            await browser_forms.x_send_scheduled(self.page, StubTx({"schedule": self.next_month_day().isoformat()}))
        self.assertEqual([], self.saved)


class StubTx:
    identity = OWNER

    def __init__(self, options):
        self.options, self.receipt = options, {}

    def save(self, **changes):
        self.receipt.update(changes)
        return dict(self.receipt)

    async def submit(self, page, button):
        await button.click(timeout=5000)


class ReadBackTests(unittest.IsolatedAsyncioTestCase):
    def receipt(self, **extra):
        return {"identity": OWNER, "title": "My title", "before_ids": ["1"], "schedule": {"at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}, **extra}

    def page(self, body):
        page = AsyncMock()
        page.locator = lambda selector: AsyncMock(inner_text=AsyncMock(return_value=body), all=AsyncMock(return_value=[]))
        page.get_by_text = lambda *a, **k: AsyncMock(all=AsyncMock(return_value=[]))
        return page

    async def test_before_its_time_words_on_the_schedule_page_are_reported_and_prove_nothing(self):
        for body, mentions in (("Scheduled posts My title Words", 1), ("My title at 3pm. My title at 5pm.", 2), ("Nothing scheduled", 0)):
            with self.subTest(body=body):
                result = await browser_backends.verify_scheduled_x(self.page(body), OWNER, self.receipt())
                self.assertEqual(("SUBMITTED_UNVERIFIED", "readback_pending", "scheduled", mentions), (result["state"], result["phase"], result["visibility"], result["schedule_page_mentions"]))
                self.assertIn("Do not post again", result["note"])

    async def after_its_time(self, posts, **extra):
        moment = datetime.now(timezone.utc) - timedelta(minutes=5)
        receipt = self.receipt(schedule={"at": moment.isoformat()}, **extra)
        rows = [{"published_at": moment.isoformat(), "has_video": True, **post} for post in posts]
        with patch.object(browser_backends, "works", new=AsyncMock()), patch.object(browser_backends, "own_posts", new=AsyncMock(return_value=rows)), \
                patch.object(browser_backends, "verify", new=AsyncMock(return_value={"state": "SUBMISSION_VERIFIED", "id": "found"})) as read:
            return await browser_backends.verify_scheduled_x(self.page(""), OWNER, receipt), read

    async def test_after_its_time_one_new_post_with_exactly_its_words_at_about_that_time_settles_it(self):
        result, read = await self.after_its_time([{"id": "1", "text": "My title"}, {"id": "2", "text": "My title"}])
        self.assertEqual("found", result["id"])
        self.assertEqual("2", read.await_args.args[3]["id"])

    async def test_the_words_it_was_given_decide_when_it_has_them(self):
        result, read = await self.after_its_time([{"id": "2", "text": "My post text"}], expected_text="My post text")
        self.assertEqual("2", read.await_args.args[3]["id"])

    async def assert_not_settled(self, posts):
        result, read = await self.after_its_time(posts)
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
        self.assertIn("do not post again", result["note"])
        read.assert_not_awaited()

    async def test_a_post_that_only_contains_its_words_does_not_settle_it(self):
        await self.assert_not_settled([{"id": "2", "text": "My title and some more"}])

    async def test_a_post_from_a_different_time_does_not_settle_it(self):
        early = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        await self.assert_not_settled([{"id": "2", "text": "My title", "published_at": early}])

    async def test_a_post_with_no_video_does_not_settle_it(self):
        await self.assert_not_settled([{"id": "2", "text": "My title", "has_video": False}])

    async def test_a_post_from_before_the_upload_does_not_settle_it(self):
        await self.assert_not_settled([{"id": "1", "text": "My title"}])

    async def test_a_post_with_no_readable_time_does_not_settle_it(self):
        for unreadable in (None, "yesterday", "2026-11-03T15:07:00"):
            with self.subTest(published_at=unreadable):
                await self.assert_not_settled([{"id": "2", "text": "My title", "published_at": unreadable}])

    async def test_two_posts_that_could_both_be_it_do_not_settle_it(self):
        await self.assert_not_settled([{"id": "2", "text": "My title"}, {"id": "3", "text": "My title"}])

    async def test_after_its_time_with_no_new_post_it_says_to_look_and_not_to_post_again(self):
        await self.assert_not_settled([])

    async def test_verify_uses_this_path_only_for_a_scheduled_post_with_no_address(self):
        with patch.object(browser_backends, "verify_scheduled_x", new=AsyncMock(return_value={"state": "X"})) as scheduled:
            self.assertEqual("X", (await browser_backends.verify(self.page(""), "x", OWNER, self.receipt()))["state"])
        scheduled.assert_awaited_once()


class OwnPostsTests(unittest.IsolatedAsyncioTestCase):
    """The words and the moment come from each post's own row, not from everything the row shows."""
    ROW = """<article><a href="/{author}/status/{post}"><time datetime="2026-11-03T15:07:00.000Z">Nov 3</time></a><div data-testid="User-Name">Someone</div>
      <div data-testid="tweetText">{words}</div><div role="group" aria-label="3 likes"><a href="/compose/post?in_reply_to={post}">Reply</a></div>{extra}</article>"""
    VIDEO = '<div data-testid="videoPlayer"><video></video></div>'

    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_only_this_accounts_posts_with_their_own_words_and_moment(self):
        rows = [self.ROW.format(author=OWNER, post="1", words="Exactly these words", extra=self.VIDEO), self.ROW.format(author="other", post="2", words="Not mine", extra=self.VIDEO),
                self.ROW.format(author=OWNER.upper(), post="3", words="Ad words", extra="<span>Ad</span>"), self.ROW.format(author=OWNER, post="4", words="No video here", extra="")]
        await self.page.set_content("<main>" + "".join(rows) + "</main>")
        posts = await x_pages.own_posts(self.page, OWNER)
        self.assertEqual([("1", "Exactly these words", "2026-11-03T15:07:00.000Z", True), ("4", "No video here", "2026-11-03T15:07:00.000Z", False)],
                         [(post["id"], post["text"], post["published_at"], post["has_video"]) for post in posts])


class PlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        target = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        target.start()
        self.addCleanup(target.stop)
        (self.root / "clip.mp4").write_bytes(b"synthetic")
        (self.root / "cover.jpg").write_bytes(b"synthetic")
        self.value = cli.template(self.root / "clip.mp4", "en")
        self.value.update(cover="cover.jpg", tags=["tag"])
        self.value["platforms"] = {"x": {"account": "default", "method": "browser", "visibility": "account", "ai_generated": True}}
        self.path = self.root / "publish.json"

    def plan(self):
        atomic_json(self.path, self.value)
        return load_plan(self.path)

    def test_the_browser_route_takes_a_future_time(self):
        when = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        self.value["platforms"]["x"]["schedule"] = when
        self.assertEqual(when, self.plan()["platforms"]["x"]["schedule"])

    def test_the_api_route_does_not(self):
        self.value["platforms"]["x"].update(schedule=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat(), method="api")
        with self.assertRaises(VpubError) as caught:
            self.plan()
        self.assertEqual("unsupported_schedule", caught.exception.code)

    def test_a_time_in_the_past_is_refused(self):
        self.value["platforms"]["x"]["schedule"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with self.assertRaises(VpubError) as caught:
            self.plan()
        self.assertEqual("invalid_schedule", caught.exception.code)


if __name__ == "__main__":
    unittest.main()

"""Scheduling on X through Media Studio's own calendar, in a real browser against a small copy of what a signed-in account showed."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from video_publisher import browser_backends, browser_forms, cli
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

    async def test_before_its_time_it_is_read_from_the_schedule_page(self):
        result = await browser_backends.verify_scheduled_x(self.page("Scheduled posts My title Words"), OWNER, self.receipt())
        self.assertEqual(("SUBMISSION_VERIFIED", "scheduled", "scheduled"), (result["state"], result["phase"], result["visibility"]))

    async def test_not_on_the_schedule_page_stays_unconfirmed(self):
        result = await browser_backends.verify_scheduled_x(self.page("Nothing scheduled"), OWNER, self.receipt())
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])

    async def test_after_its_time_it_is_found_among_the_accounts_posts(self):
        receipt = self.receipt(schedule={"at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()})
        posts = {"1": {"id": "1", "text": "My title", "url": "u1"}, "2": {"id": "2", "text": "My title words", "url": "u2"}}
        with patch.object(browser_backends, "works", new=AsyncMock(return_value=posts)), patch.object(browser_backends, "verify", new=AsyncMock(return_value={"state": "SUBMISSION_VERIFIED", "id": "2"})) as read:
            result = await browser_backends.verify_scheduled_x(self.page(""), OWNER, receipt)
        self.assertEqual("2", result["id"])
        self.assertEqual("2", read.await_args.args[3]["id"])

    async def test_after_its_time_with_no_new_post_it_says_to_look_and_not_to_post_again(self):
        receipt = self.receipt(schedule={"at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()})
        with patch.object(browser_backends, "works", new=AsyncMock(return_value={})):
            result = await browser_backends.verify_scheduled_x(self.page(""), OWNER, receipt)
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
        self.assertIn("do not post again", result["note"])

    async def test_verify_uses_this_path_only_for_a_scheduled_post_with_no_address(self):
        with patch.object(browser_backends, "verify_scheduled_x", new=AsyncMock(return_value={"state": "X"})) as scheduled:
            self.assertEqual("X", (await browser_backends.verify(self.page(""), "x", OWNER, self.receipt()))["state"])
        scheduled.assert_awaited_once()


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

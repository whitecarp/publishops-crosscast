"""Douyin's schedule, collection and draft on its upload form, in a real browser against a small copy of that form.

The copy follows what a signed-in account's form showed in a field check, and public open-source uploaders for the date box
and the collection list. Nothing here contacts Douyin, and none of these steps has been tried on a real account yet.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from video_publisher import browser_backends, browser_forms, cli
from video_publisher.core import VpubError, atomic_json, home, load_plan
from shared_chrome import ChromeCase, async_playwright

OWNER = "owner123"
TITLE = "My title"


def form(keep_time=True, shows_box=True, collections=("Recipes", "Recipes more")):
    options = "".join(f'<div class="semi-select-option collection-option"><span class="option-title-a1">{name}</span></div>' for name in collections)
    show = "document.getElementById('when').hidden = false;" if shows_box else ""
    reset = "" if keep_time else """document.querySelector('[placeholder="日期和时间"]').addEventListener('keydown', e => { if (e.key === 'Enter') e.target.value = '2026-01-01 00:00'; });"""
    return f"""<!doctype html><meta charset="utf-8"><style>label span {{ pointer-events: none; }}</style><body>
      <input class="semi-input" placeholder="填写作品标题，为作品获得更多流量" value="{TITLE}">
      <div contenteditable="true" class="zone-container editor-kit-container">Words</div>
      <label><input type="checkbox" aria-label="公开" checked>公开</label>
      <label class="radio-d4zkru one-line-pe7juM" data-checked="true"><input type="checkbox" checked><span>立即发布</span></label>
      <label id="later" class="radio-d4zkru one-line-pe7juM" data-checked="false"><input type="checkbox"><span>定时发布</span></label>
      <div id="when" hidden><input class="semi-input" placeholder="日期和时间" value="2026-01-01 00:00"></div>
      <div class="select-collection-b2"><div class="semi-select"><div class="semi-select-selection" id="picked"><span class="semi-select-selection-text">合集</span></div></div></div>
      <div id="list" hidden>{options}</div>
      <button class="button-dhlUZE primary-cECiOJ" id="post">发布</button>
      <button class="button-dhlUZE cancel-btn-zy_rHA" id="keep">暂存离开</button>
      <script>
      const send = word => fetch('/write', {{method: 'POST', body: JSON.stringify({{word, later: document.getElementById('later').dataset.checked, when: document.querySelector('[placeholder="日期和时间"]').value, picked: document.getElementById('picked').innerText}})}});
      document.getElementById('later').onclick = () => {{ document.getElementById('later').dataset.checked = 'true'; {show} }};
      document.getElementById('post').onclick = () => send('publish').then(() => {{ location.href = '/creator-micro/content/manage'; }});
      document.getElementById('keep').onclick = () => send('keep');
      {reset}
      document.getElementById('picked').onclick = () => {{ document.getElementById('list').hidden = false; }};
      for (const row of document.querySelectorAll('.collection-option')) row.onclick = () => {{ document.getElementById('picked').innerHTML = '<span class="semi-select-selection-text">' + row.innerText + '</span>'; document.getElementById('list').hidden = true; }};
      </script>"""


class StubTx:
    identity = OWNER

    def __init__(self, **options):
        self.options = {"title": TITLE, "description": "Words", "tags": [], "visibility": "public", "ai_generated": False, **options}
        self.receipt = {}
        self.platform = "douyin"

    def save(self, **changes):
        self.receipt.update(changes)
        return dict(self.receipt)

    def cover(self, role="landscape"):
        return None

    async def submit(self, page, button):
        await button.click(timeout=5000)


class Base(ChromeCase):
    quick_pauses = True  # This copy of the site has no timers of its own, so a pause can end once the page is quiet.
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(5000)
        self.writes = []
        self.html = form()
        await self.page.route("**/*", self.handle)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def handle(self, route):
        if route.request.method == "POST":
            self.writes.append(json.loads(route.request.post_data))
            return await route.fulfill(status=200, content_type="application/json", body="{}")
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=self.html)

    async def open(self, html=None):
        self.html = html or self.html
        await self.page.goto("https://creator.douyin.com/creator-micro/content/post/video")


class ScheduleTests(Base):
    def when(self):
        return datetime(2030, 3, 4, 15, 7).astimezone()

    async def test_the_label_takes_the_click_and_the_time_is_read_back(self):
        await self.open()
        result = await browser_forms.douyin_schedule(self.page, self.when())
        self.assertEqual("2030-03-04 15:07", await self.page.locator('input[placeholder="日期和时间"]').input_value())
        self.assertEqual("true", await self.page.locator("#later").get_attribute("data-checked"))
        self.assertEqual(("2030-03-04 15:07", [2030, 3, 4], [15, 7], "computer"), (result["stamp"], result["date"], result["time"], result["timezone_basis"]))
        self.assertEqual([], self.writes)

    async def test_a_box_that_does_not_keep_the_time_stops_it(self):
        await self.open(form(keep_time=False))
        with self.assertRaises(VpubError) as caught:
            await browser_forms.douyin_schedule(self.page, self.when())
        self.assertEqual("schedule_unverified", caught.exception.code)

    async def test_no_date_box_stops_it(self):
        await self.open(form(shows_box=False))
        with patch.object(self.page, "set_default_timeout"):
            with self.assertRaises(VpubError) as caught:
                await browser_forms.douyin_schedule(self.page, self.when())
        self.assertEqual("schedule_unverified", caught.exception.code)


class CollectionTests(Base):
    async def test_the_exact_title_is_chosen_and_read_back(self):
        await self.open()
        result = await browser_forms.douyin_collection(self.page, "Recipes")
        self.assertEqual({"title": "Recipes", "state": "UI_ACCEPTED"}, result)
        self.assertEqual("Recipes", (await self.page.locator("#picked").inner_text()).strip())

    async def test_a_title_only_part_of_another_is_not_chosen(self):
        await self.open(form(collections=("Recipes more",)))
        with self.assertRaises(VpubError) as caught:
            await browser_forms.douyin_collection(self.page, "Recipes")
        self.assertEqual("collection_not_found", caught.exception.code)

    async def test_two_collections_with_one_title_stop_it(self):
        await self.open(form(collections=("Recipes", "Recipes")))
        with self.assertRaises(VpubError) as caught:
            await browser_forms.douyin_collection(self.page, "Recipes")
        self.assertEqual("layout_changed", caught.exception.code)

    async def test_a_form_without_the_list_stops_it(self):
        await self.open("<!doctype html><body><p>Nothing here</p>")
        with self.assertRaises(VpubError) as caught:
            await browser_forms.douyin_collection(self.page, "Recipes")
        self.assertEqual("layout_changed", caught.exception.code)


class WholeFormTests(Base):
    async def run_form(self, tx):
        with patch.object(browser_forms, "finish", new=AsyncMock(return_value={"state": "SUBMITTED_UNVERIFIED"})):
            return await browser_forms.finish_douyin_form(self.page, tx, {})

    async def test_a_schedule_and_collection_are_on_the_form_when_it_publishes_once(self):
        await self.open()
        when = datetime(2030, 3, 4, 15, 7).astimezone()
        tx = StubTx(schedule=when.isoformat(), collection_title="Recipes")
        await self.run_form(tx)
        self.assertEqual(1, len(self.writes))
        self.assertEqual(("publish", "true", "2030-03-04 15:07"), (self.writes[0]["word"], self.writes[0]["later"], self.writes[0]["when"]))
        self.assertEqual("Recipes", self.writes[0]["picked"].strip())
        self.assertEqual("2030-03-04 15:07", tx.receipt["schedule"]["stamp"])

    async def test_a_form_that_reloaded_gets_its_schedule_again(self):
        await self.open()
        when = datetime(2030, 3, 4, 15, 7).astimezone()
        tx = StubTx(schedule=when.isoformat())
        tx.receipt["schedule"] = {"stamp": "2030-03-04 15:07"}
        await self.run_form(tx)
        self.assertEqual("true", self.writes[0]["later"])

    async def test_a_schedule_that_does_not_take_never_publishes(self):
        await self.open(form(keep_time=False))
        with self.assertRaises(VpubError):
            await self.run_form(StubTx(schedule=datetime(2030, 3, 4, 15, 7).astimezone().isoformat()))
        self.assertEqual([], self.writes)

    async def test_a_collection_that_is_not_there_never_publishes(self):
        await self.open(form(collections=("Other",)))
        with self.assertRaises(VpubError):
            await self.run_form(StubTx(collection_title="Recipes"))
        self.assertEqual([], self.writes)

    async def test_without_either_it_publishes_as_before(self):
        await self.open()
        await self.run_form(StubTx())
        self.assertEqual(("publish", "false"), (self.writes[0]["word"], self.writes[0]["later"]))

    async def test_a_draft_presses_keep_once_and_never_publish(self):
        await self.open()
        tx = StubTx(submission_mode="draft_only")
        result = await self.run_form(tx)
        self.assertEqual(["keep"], [write["word"] for write in self.writes])
        self.assertEqual(("PREVIEW_READY", "draft_saved"), (result["state"], result["phase"]))
        self.assertIn("not read back", result["note"])

    async def test_a_stop_holds_the_draft_too(self):
        await self.open()
        atomic_json(home() / "STOP.json", {"reason": "test"})
        with self.assertRaises(VpubError) as caught:
            await self.run_form(StubTx(submission_mode="draft_only"))
        self.assertEqual("stopped", caught.exception.code)
        self.assertEqual([], self.writes)


class ReadBackTests(unittest.IsolatedAsyncioTestCase):
    def page(self):
        page = AsyncMock()
        page.url = "https://creator.douyin.com/"
        return page

    def receipt(self, at):
        return {"identity": OWNER, "id": "7", "title": TITLE, "expected_text": TITLE, "schedule": {"at": at.isoformat(), "stamp": "2030-03-04 15:07"}}

    async def read(self, receipt, text):
        with patch.object(browser_backends, "works", new=AsyncMock(return_value={"7": {"id": "7", "text": text}})):
            return await browser_backends.verify(self.page(), "douyin", OWNER, receipt)

    async def test_before_its_time_the_work_must_show_that_time(self):
        later = datetime.now(timezone.utc) + timedelta(days=1)
        self.assertEqual("SUBMISSION_VERIFIED", (await self.read(self.receipt(later), f"{TITLE} 定时发布 2030-03-04 15:07"))["state"])
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.read(self.receipt(later), TITLE))["state"])

    async def test_after_its_time_the_work_being_there_is_enough(self):
        earlier = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.assertEqual("SUBMISSION_VERIFIED", (await self.read(self.receipt(earlier), TITLE))["state"])


class PlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"VPUB_HOME": str(self.root / "runtime")})
        env.start()
        self.addCleanup(env.stop)
        (self.root / "clip.mp4").write_bytes(b"synthetic")
        (self.root / "cover.jpg").write_bytes(b"synthetic")
        self.value = cli.template(self.root / "clip.mp4", "zh-Hans")
        self.value.update(cover="cover.jpg", tags=["tag"], covers={"douyin": {"portrait": "cover.jpg"}})
        self.path = self.root / "publish.json"

    def plan(self, **options):
        self.value["platforms"] = {"douyin": {"account": "default", "method": "browser", "visibility": "public", "ai_generated": True, "title": "标题", **options}}
        atomic_json(self.path, self.value)
        return load_plan(self.path)["platforms"]["douyin"]

    def future(self):
        return (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

    def refused(self, code, **options):
        with self.assertRaises(VpubError) as caught:
            self.plan(**options)
        self.assertEqual(code, caught.exception.code)

    def test_the_browser_route_takes_a_schedule_and_a_collection(self):
        when = self.future()
        chosen = self.plan(schedule=when, collection_title="合集一")
        self.assertEqual((when, "合集一"), (chosen["schedule"], chosen["collection_title"]))

    def test_the_host_browser_route_takes_no_schedule(self):
        self.refused("unsupported_schedule", schedule=self.future(), method="native")

    def test_a_time_in_the_past_is_refused(self):
        self.refused("invalid_schedule", schedule=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())

    def test_a_collection_needs_a_real_title(self):
        for value in ("", " 前后有空格 ", "x" * 21, 7):
            with self.subTest(value=value):
                self.refused("invalid_collection", collection_title=value)

    def test_a_draft_keeps_only_what_a_draft_can_hold(self):
        self.assertEqual("draft_only", self.plan(submission_mode="draft_only")["submission_mode"])
        self.refused("invalid_submission_mode", submission_mode="draft_only", schedule=self.future())
        self.refused("invalid_submission_mode", submission_mode="draft_only", collection_title="合集一")
        self.refused("invalid_submission_mode", submission_mode="draft_only", method="native")
        self.refused("invalid_submission_mode", submission_mode="later")


if __name__ == "__main__":
    unittest.main()

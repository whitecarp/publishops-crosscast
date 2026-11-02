"""Douyin's schedule, collection and draft on its upload form, in a real browser against a small copy of that form.

The copy follows what a signed-in account's form showed in a field check, and public open-source uploaders for the date box
and the collection list. Nothing here contacts Douyin, and none of these steps has been tried on a real account yet.
"""
from datetime import datetime, timedelta, timezone
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from video_publisher import browser_backends, browser_edits, browser_forms, cli
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
    resuming = False

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
        self.upload_page = "<!doctype html><body><input type=file>"
        await self.page.route("**/*", self.handle)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def handle(self, route):
        if route.request.method == "POST":
            self.writes.append(json.loads(route.request.post_data))
            return await route.fulfill(status=200, content_type="application/json", body="{}")
        body = self.upload_page if route.request.url.endswith("/content/upload") else self.html
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

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
        self.assertEqual(("PREVIEW_READY", "draft_saved", False), (result["state"], result["phase"], result["draft_offer_seen"]))
        self.assertIn("not read back", result["note"])

    OFFERS = '<!doctype html><meta charset="utf-8"><body><input type=file><button>继续编辑</button><button>放弃</button>'

    async def draft(self, before):
        await self.open()
        self.upload_page = self.OFFERS
        tx = StubTx(submission_mode="draft_only")
        tx.receipt["draft_offered_before"] = before
        return await self.run_form(tx)

    async def test_a_newly_offered_draft_is_reported_as_seen_and_nothing_is_opened(self):
        result = await self.draft(False)
        self.assertIs(True, result["draft_offer_seen"])
        self.assertIn("继续编辑", result["note"])
        self.assertIn("not read back", result["note"])
        self.assertEqual(["keep"], [write["word"] for write in self.writes])

    async def test_a_draft_already_on_offer_before_the_upload_is_not_called_new(self):
        self.assertIs(False, (await self.draft(True))["draft_offer_seen"])

    async def test_a_receipt_from_before_this_check_is_not_called_new_either(self):
        self.assertIs(False, (await self.draft(None))["draft_offer_seen"])

    async def test_an_old_offer_that_shows_late_is_still_seen_before_the_upload(self):
        await self.page.set_content('<!doctype html><meta charset="utf-8"><body><script>window.pending = 1; setTimeout(() => { const b = document.createElement("button"); b.textContent = "继续编辑"; document.body.append(b); window.pending = 0; }, 1500);</script>')
        self.assertIs(True, await browser_forms.douyin_offers_draft(self.page, 3))

    async def test_a_stop_holds_the_draft_too(self):
        await self.open()
        atomic_json(home() / "STOP.json", {"reason": "test"})
        with self.assertRaises(VpubError) as caught:
            await self.run_form(StubTx(submission_mode="draft_only"))
        self.assertEqual("stopped", caught.exception.code)
        self.assertEqual([], self.writes)


class CaptionTypingTests(Base):
    """From a field check: a caption set in one go came out with a star at each new paragraph, on the card and on a real video's public page. Nothing here can show Douyin's own side of that, only how the caption reaches its editor."""
    PAGE = """<!doctype html><meta charset="utf-8"><body><div contenteditable="true" id="box">Old words</div>
      <script>window.seen = []; document.getElementById('box').addEventListener('beforeinput', e => window.seen.push([e.inputType, e.data]));</script>"""

    async def type_it(self, caption):
        await self.page.set_content(self.PAGE)
        box = self.page.locator("#box")
        await browser_backends.douyin_type_caption(self.page, box, caption)
        return box, await self.page.evaluate("window.seen", isolated_context=False)

    async def test_no_line_break_ever_goes_in_with_the_words(self):
        box, seen = await self.type_it("One line.\n\n第二行。\n\n#Tag")
        self.assertFalse([data for _, data in seen if data and "\n" in data])
        self.assertEqual(["One line.", "第二行。", "#Tag"], [data for kind, data in seen if kind == "insertText"])
        self.assertEqual(browser_backends.douyin_card_words("One line.\n\n第二行。\n\n#Tag"), browser_backends.douyin_card_words(await box.inner_text()))

    def test_only_invisible_marks_and_how_much_spacing_there_is_are_left_out(self):
        words = browser_backends.douyin_card_words
        self.assertEqual(words("One.\n\nTwo."), words("One.\u200b\n\u200b\nTwo.\u200b"))
        self.assertNotEqual(words("We are now here."), words("We are nowhere."))
        self.assertNotEqual(words("One. Two."), words("One. *Two."))
        self.assertNotEqual(words("Version 1.2"), words("Version 12"))

    async def test_what_was_there_goes_first(self):
        box, _ = await self.type_it("New.")
        self.assertEqual("New.", (await box.inner_text()).strip())

    async def test_the_edit_page_types_it_the_same_way(self):
        self.assertIn("douyin_type_caption(page,editor,patch['caption'])", Path(browser_edits.__file__).read_text(encoding="utf-8"))


class ScheduledCardTests(Base):
    """From a field check: a scheduled work's card shows its title, its time in Chinese and a private mark, and opens nothing yet."""
    STAMP = "2030年03月04日 15:07"

    def card(self, title=TITLE, when=STAMP, private='<span class="private-mark-abc">私密</span>'):
        return f'<div class="video-card-content-1"><div class="info-title-text-1">{title}</div><div class="info-time-1">{when}</div>{private}</div>'

    async def show(self, *cards):
        await self.page.set_content('<!doctype html><meta charset="utf-8"><body>' + "".join(cards))

    def receipt(self, before=0, visibility="private", card_stamp=STAMP, same_time=0):
        return {"title": TITLE, "visibility": visibility, "same_title_cards_before": before, "same_time_cards_before": same_time, "schedule": {"card_stamp": card_stamp}}

    async def test_one_new_card_with_this_title_time_and_privacy_is_this_one(self):
        await self.show(self.card(), self.card("Another work"))
        self.assertIs(True, await browser_backends.douyin_scheduled_card(self.page, self.receipt()))

    async def test_anything_else_is_not_called_this_one(self):
        for cards, receipt in (((), self.receipt()), ((self.card(TITLE + " more"),), self.receipt()), ((self.card(when="2030年03月04日 15:08"),), self.receipt()),
                               ((self.card(private=""),), self.receipt()), ((self.card(),), self.receipt(before=1)), ((self.card(), self.card()), self.receipt()),
                               ((self.card(),), {k: v for k, v in self.receipt().items() if k != "same_title_cards_before"}), ((self.card(),), {**self.receipt(), "schedule": {}})):
            with self.subTest(cards=cards, receipt=receipt):
                await self.show(*cards)
                self.assertIs(False, await browser_backends.douyin_scheduled_card(self.page, receipt))

    CAPTION = "CC test. Private test.\n\n五秒测试。"

    def card_with_caption(self, caption, title=TITLE, when=STAMP):
        return f'<div class="video-card-content-1"><div class="info-title-text-1">{title} {caption}\u200b</div><div class="info-time-1">{when}</div><span class="private-mark-abc">私密</span></div>'

    def receipt_with_caption(self, **changes):
        return {**self.receipt(**changes), "expected_caption": self.CAPTION}

    async def test_a_real_cards_title_node_holds_the_title_and_then_the_caption(self):
        """From a field check: the one new card, with the right time and private mark, was missed because its title node goes on to hold the caption."""
        await self.show(self.card_with_caption("CC test. Private test. 五秒测试。"))
        self.assertIs(True, await browser_backends.douyin_scheduled_card(self.page, self.receipt_with_caption()))

    async def test_a_real_card_marks_each_new_paragraph_with_a_star(self):
        """Word for word from a field check: the title, then the caption with a star where each paragraph began."""
        title = "CC test B9I 临时排期"
        caption = "CC test. Private five-second schedule test; removed immediately after inspection.\n\n五秒私密排期测试，检查后立即删除。\n\n#CrossCastTest"
        node = "CC test B9I 临时排期 CC test. Private five-second schedule test; removed immediately after inspection. *五秒私密排期测试，检查后立即删除。 *#CrossCastTest"
        await self.show(self.card(title=node))
        self.assertIs(True, await browser_backends.douyin_scheduled_card(self.page, {**self.receipt(), "title": title, "expected_caption": caption}))

    async def test_a_star_stands_only_for_a_new_paragraph(self):
        for caption, node, same in (("One. Two.", f"{TITLE} One. *Two.", False), ("One.\n\nTwo.", f"{TITLE} One. *Two. *", False), ("One.\n\nTwo.", f"{TITLE} One. Two.", True),
                                    ("2*3 is six", f"{TITLE} 2*3 is six", True), ("2*3 is six", f"{TITLE} 23 is six", False), ("One.\nTwo.", f"{TITLE} One. *Two.", True),
                                    ("We are now here.", f"{TITLE} We are nowhere.", False), ("Words", f"{TITLE}Words", False)):
            with self.subTest(caption=caption, node=node):
                await self.show(self.card(title=node))
                self.assertIs(same, await browser_backends.douyin_scheduled_card(self.page, {**self.receipt(), "expected_caption": caption}))

    async def test_invisible_marks_are_no_part_of_what_was_written(self):
        for mark in ("\ufeff", "\u200b", "\u200d"):
            with self.subTest(mark=mark):
                await self.show(self.card_with_caption(f"{mark}CC test. Private test. 五秒测试。{mark}"))
                self.assertIs(True, await browser_backends.douyin_scheduled_card(self.page, self.receipt_with_caption()))

    async def test_punctuation_and_symbols_are_part_of_what_was_written(self):
        """From a review: with only letters and digits compared, each of these read as the same words."""
        for wanted, shown in (("Version 1.2", "Version 12"), ("-20%", "+20%"), ("C++", "C#"), ("Done ✅", "Done ❌")):
            with self.subTest(wanted=wanted):
                await self.show(self.card_with_caption(shown))
                self.assertIs(False, await browser_backends.douyin_scheduled_card(self.page, {**self.receipt(), "expected_caption": wanted}))
                await self.show(self.card_with_caption(wanted))
                self.assertIs(True, await browser_backends.douyin_scheduled_card(self.page, {**self.receipt(), "expected_caption": wanted}))

    async def test_starting_with_the_title_is_not_enough(self):
        for cards in ((self.card_with_caption("Other words."),), (self.card_with_caption("CC test. Private test. 五秒测试。 and more"),), (self.card(TITLE + " more"),), (self.card(TITLE + " 2"),),
                      (self.card_with_caption("CC test. Private test. 五秒测试。", title=TITLE + " more"),)):
            with self.subTest(cards=cards):
                await self.show(*cards)
                self.assertIs(False, await browser_backends.douyin_scheduled_card(self.page, self.receipt_with_caption()))

    async def test_a_date_in_the_words_never_stands_in_for_the_cards_own_time(self):
        """From a review: the caption mentioned the time asked for, the card's own time was a day later, and it passed."""
        later = "2030年03月05日 15:07"
        words = f"On {self.STAMP}"
        await self.show(self.card_with_caption(words, when=later))
        self.assertIs(False, await browser_backends.douyin_scheduled_card(self.page, {**self.receipt(), "expected_caption": words}))
        await self.show(self.card_with_caption(words))
        self.assertIs(True, await browser_backends.douyin_scheduled_card(self.page, {**self.receipt(), "expected_caption": words}))
        await self.show(self.card(title=f"{TITLE} {self.STAMP}", when=later))
        self.assertIs(False, await browser_backends.douyin_scheduled_card(self.page, {**self.receipt(), "title": f"{TITLE} {self.STAMP}"}))

    def card_with_time_nodes(self, nodes):
        return f'<div class="video-card-content-1"><div class="info-title-text-1">{TITLE}</div>{nodes}<span class="private-mark-abc">私密</span></div>'

    async def test_a_card_without_one_clear_time_is_not_known_to_be_at_this_time(self):
        one = f'<div class="info-time-1">{self.STAMP}</div>'
        for nodes in (f'<span>{self.STAMP}</span>', one * 2, f'<div class="info-time-1">{self.STAMP} {self.STAMP}</div>', '<div class="info-time-1">soon</div>'):
            with self.subTest(nodes=nodes):
                await self.show(self.card_with_time_nodes(nodes))
                self.assertIs(False, await browser_backends.douyin_scheduled_card(self.page, self.receipt()))

    async def test_an_older_card_whose_time_cannot_be_read_could_be_at_this_time(self):
        await self.show(self.card(), self.card_with_time_nodes(""))
        self.assertIs(False, await browser_backends.douyin_scheduled_card(self.page, self.receipt(before=1)))
        self.html = '<!doctype html><meta charset="utf-8"><body>' + self.card_with_time_nodes("")
        tx = StubTx(schedule=datetime(2030, 3, 4, 15, 7).astimezone().isoformat())
        tx.media = lambda: "clip.mp4"
        await self.page.goto("https://creator.douyin.com/creator-micro/content/manage")
        with patch.object(browser_forms, "works", new=AsyncMock(return_value={})), patch.object(browser_forms, "douyin_caption", return_value="Words"), \
                patch.object(browser_forms, "upload_file", new=AsyncMock(side_effect=RuntimeError("selected"))), self.assertRaises(RuntimeError):
            await browser_forms.publish_douyin(self.page, tx)
        self.assertEqual((1, 1), (tx.receipt["same_title_cards_before"], tx.receipt["same_time_cards_before"]))

    async def test_a_caption_never_says_what_state_a_card_is_in(self):
        past, later = "2020年01月02日 03:04", "2099年01月02日 03:04"
        for words, shown, waiting in ((f"Back on {later}", past, False), ("定时发布 审核中", past, False), (f"Since {past}", later, True)):
            with self.subTest(words=words):
                await self.show(self.card_with_caption(words, when=shown))
                status = await browser_backends.douyin_card_status(self.page.locator(browser_backends.DOUYIN_CARD))
                self.assertEqual(shown + " 私密", status)
                self.assertIs(waiting, browser_backends.douyin_card_is_waiting(status))

    async def test_an_older_card_with_this_title_and_caption_is_counted_before_the_upload(self):
        self.html = '<!doctype html><meta charset="utf-8"><body>' + self.card_with_caption("Words")
        tx = StubTx(schedule=datetime(2030, 3, 4, 15, 7).astimezone().isoformat())
        tx.media = lambda: "clip.mp4"
        await self.page.goto("https://creator.douyin.com/creator-micro/content/manage")
        with patch.object(browser_forms, "works", new=AsyncMock(return_value={})), patch.object(browser_forms, "douyin_caption", return_value="Words"), \
                patch.object(browser_forms, "upload_file", new=AsyncMock(side_effect=RuntimeError("selected"))), self.assertRaises(RuntimeError):
            await browser_forms.publish_douyin(self.page, tx)
        self.assertEqual((1, 1, "Words"), (tx.receipt["same_title_cards_before"], tx.receipt["same_time_cards_before"], tx.receipt["expected_caption"]))

    async def test_an_older_card_with_this_title_and_time_never_stands_in_for_the_new_one(self):
        """From a review: the old card matched, the new one showed the wrong time, and the count of cards was right, so it passed."""
        await self.show(self.card(), self.card(when="2030年03月05日 15:07"))
        self.assertIs(False, await browser_backends.douyin_scheduled_card(self.page, self.receipt(before=1, same_time=1)))
        await self.show(self.card(), self.card())
        self.assertIs(False, await browser_backends.douyin_scheduled_card(self.page, self.receipt(before=1, same_time=1)))

    async def test_an_older_card_with_this_title_at_another_time_is_told_apart(self):
        await self.show(self.card(when="2030年03月05日 15:07"), self.card())
        self.assertIs(True, await browser_backends.douyin_scheduled_card(self.page, self.receipt(before=1)))
        await self.show(self.card(when="2030年03月05日 15:07"), self.card(when="2030年03月06日 15:07"))
        self.assertIs(False, await browser_backends.douyin_scheduled_card(self.page, self.receipt(before=1)))

    async def test_a_receipt_that_never_looked_for_an_older_card_stays_unverified(self):
        await self.show(self.card())
        receipt = self.receipt()
        del receipt["same_time_cards_before"]
        self.assertIs(False, await browser_backends.douyin_scheduled_card(self.page, receipt))

    async def test_the_upload_counts_cards_with_this_title_and_time_before_it_starts(self):
        self.html = '<!doctype html><meta charset="utf-8"><body>' + self.card() + self.card(when="2030年03月05日 15:07") + self.card("Another work")
        tx = StubTx(schedule=datetime(2030, 3, 4, 15, 7).astimezone().isoformat())
        tx.media = lambda: "clip.mp4"
        await self.page.goto("https://creator.douyin.com/creator-micro/content/manage")
        with patch.object(browser_forms, "works", new=AsyncMock(return_value={})), patch.object(browser_forms, "douyin_caption", return_value="Words"), \
                patch.object(browser_forms, "upload_file", new=AsyncMock(side_effect=RuntimeError("selected"))), self.assertRaises(RuntimeError):
            await browser_forms.publish_douyin(self.page, tx)
        self.assertEqual((2, 1), (tx.receipt["same_title_cards_before"], tx.receipt["same_time_cards_before"]))

    async def test_a_public_one_needs_no_private_mark(self):
        await self.show(self.card(private=""))
        self.assertIs(True, await browser_backends.douyin_scheduled_card(self.page, self.receipt(visibility="public")))

    def test_a_card_with_a_time_still_to_come_is_waiting_and_a_past_one_is_not(self):
        self.assertTrue(browser_backends.douyin_card_is_waiting("My work 2099年01月02日 03:04"))
        self.assertFalse(browser_backends.douyin_card_is_waiting("My work 2020年01月02日 03:04"))
        self.assertTrue(browser_backends.douyin_card_is_waiting("My work 审核中"))
        self.assertFalse(browser_backends.douyin_card_is_waiting("My work"))

    async def test_the_form_gives_the_time_as_the_card_writes_it(self):
        await self.open()
        result = await browser_forms.douyin_schedule(self.page, datetime(2030, 3, 4, 15, 7).astimezone())
        self.assertEqual(self.STAMP, result["card_stamp"])

    async def test_listing_works_skips_a_scheduled_card_without_waiting_on_it(self):
        self.html = '<!doctype html><meta charset="utf-8"><body>' + self.card(when="2099年01月02日 03:04")
        started = asyncio.get_running_loop().time()
        self.assertEqual({}, await browser_backends.works(self.page, "douyin", OWNER))
        self.assertLess(asyncio.get_running_loop().time() - started, 14)


COVER_FORM = """<div class="coverControl-a"><span>竖封面3:4</span><div class="cover-b" style="width:10px;height:10px" onclick="document.getElementById('d').hidden = false"><img hidden></div></div>
  <div role="dialog" id="d" hidden><p>上传封面</p><div class="semi-upload">点击上传文件或拖拽文件到这里<input type="file" class="semi-upload-hidden-input"></div>
  <button onclick="if (%s) document.getElementById('d').hidden = true">完成</button></div>"""


class CoverOnANewPostTests(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        settled = patch.object(browser_edits, "DOUYIN_COVER_SETTLE_SECONDS", 0)
        settled.start()
        self.addCleanup(settled.stop)
        self.image = Path(tempfile.gettempdir()) / "douyin-cover-test.jpg"
        self.image.write_bytes(b"synthetic")
        self.addCleanup(self.image.unlink, True)
        self.tx = StubTx()
        self.tx.cover = lambda role="landscape": str(self.image) if role == "portrait" else None

    async def post(self, closes="true"):
        await self.open(form() + COVER_FORM % closes)
        with patch.object(browser_forms, "finish", new=AsyncMock(return_value={"state": "SUBMITTED_UNVERIFIED"})):
            return await browser_forms.finish_douyin_form(self.page, self.tx, {})

    async def test_a_cover_goes_on_and_the_post_goes_out_once(self):
        await self.post()
        self.assertEqual("UI_ACCEPTED", self.tx.receipt["portrait_cover"]["state"])
        self.assertEqual(["publish"], [write["word"] for write in self.writes])

    async def test_its_window_gets_as_long_as_an_edits_and_then_nothing_goes_out(self):
        """From a field run's first real post: a 30-second wait after 完成 ended it, with the video uploaded and nothing pressed."""
        with patch.object(browser_edits, "DOUYIN_COVER_SECONDS", 1), self.assertRaises(VpubError) as caught:
            await self.post("false")
        self.assertEqual("cover_not_ready", caught.exception.code)
        self.assertIn("nothing was published", caught.exception.message)
        self.assertIn("Requests since the press: {'started': ", caught.exception.message)
        self.assertEqual([], self.writes)

    async def test_a_stop_says_which_request_is_open_and_which_failed_with_no_address(self):
        """From field runs: nothing could say whether the window was waiting on a request, or a request had failed."""
        async def answer(route):
            if "/stuck" in route.request.url:
                return  # Never answered.
            if "/broken" in route.request.url:
                return await route.abort()
            await route.fulfill(status=200, body="{}")
        await self.open(form() + COVER_FORM % "false")
        await self.page.route("**/stuck*", answer)
        await self.page.route("**/broken*", answer)
        traffic = browser_edits.douyin_cover_traffic(self.page)
        await self.page.evaluate("fetch('https://cdn.example.org/stuck?id=secret'); fetch('https://other.example.org/broken?id=secret').catch(() => {})")
        await self.page.wait_for_timeout(300)
        seen = traffic(stop=True)
        self.assertEqual({"cdn.example.org": 1}, seen["open"])
        self.assertEqual({"other.example.org": 1}, seen["failed_hosts"])
        self.assertEqual(1, seen["failed"])
        self.assertNotIn("secret", str(seen))

    async def test_a_request_from_before_the_press_uses_up_no_newer_ones_count(self):
        """A review caught this: one that ended after the press hid a new one still open, and the totals ran past what had started."""
        listeners = {}
        page = SimpleNamespace(on=lambda event, listener: listeners.__setitem__(event, listener), remove_listener=lambda event, listener: listeners.pop(event))
        older, newer = SimpleNamespace(url="https://cover.example.org/older"), SimpleNamespace(url="https://cover.example.org/newer")
        traffic = browser_edits.douyin_cover_traffic(page)
        listeners["request"](newer)
        listeners["requestfinished"](older)
        listeners["requestfailed"](older)
        seen = traffic()
        self.assertEqual({"cover.example.org": 1}, seen["open"])
        self.assertEqual((1, 0, 0), (seen["started"], seen["finished"], seen["failed"]))
        listeners["requestfinished"](newer)
        seen = traffic(stop=True)
        self.assertEqual({}, seen["open"])
        self.assertEqual((1, 1, 0), (seen["started"], seen["finished"], seen["failed"]))
        self.assertEqual({}, listeners)

    async def test_something_going_wrong_after_the_press_leaves_nothing_listening(self):
        """A review caught the counter staying on the page when something failed before the wait for the window."""
        looks, real_count, real_wait = [], browser_edits.douyin_cover_traffic, self.page.wait_for_timeout

        def noted(page):
            looks.append(real_count(page))
            return looks[-1]

        async def goes_wrong(ms):
            if ms == 700:
                raise RuntimeError("the page went away")
            await real_wait(ms)
        with patch.object(browser_edits, "douyin_cover_traffic", noted), patch.object(self.page, "wait_for_timeout", goes_wrong), self.assertRaises(RuntimeError):
            await self.post()
        before = looks[0]()["started"]
        await self.page.route("**/later*", lambda route: route.fulfill(status=200, body="{}"))
        await self.page.evaluate("fetch('https://cdn.example.org/later').catch(() => {})")
        await self.page.wait_for_timeout(200)
        self.assertEqual(before, looks[0]()["started"])

    def test_that_window_has_the_edit_routes_three_minutes(self):
        self.assertEqual(180, browser_edits.DOUYIN_COVER_SECONDS)

    async def test_a_press_waits_as_long_as_the_one_that_went_through(self):
        """From field runs: 完成 hung at about 11 seconds after the file and went through at about 73."""
        waits, real = [], self.page.wait_for_timeout

        async def noted(ms):
            waits.append(ms)
            await real(min(ms, 50))
        with patch.object(browser_edits, "DOUYIN_COVER_SETTLE_SECONDS", 75), patch.object(self.page, "wait_for_timeout", noted):
            await self.post()
        self.assertIn(75000, waits)

    async def test_a_stop_holds_the_press(self):
        await self.open(form() + COVER_FORM % "true")
        atomic_json(home() / "STOP.json", {"reason": "A person paused it."})
        with self.assertRaises(VpubError) as caught:
            await browser_forms.finish_douyin_form(self.page, self.tx, {})
        self.assertEqual("stopped", caught.exception.code)
        self.assertEqual([], self.writes)


class ResumeFromKeptDraftTests(Base):
    """A field run's first real post stopped at its cover, with the video uploaded and nothing pressed."""
    async def asyncSetUp(self):
        await super().asyncSetUp()
        brief = patch.object(browser_forms, "DOUYIN_OFFER_SECONDS", 1)
        brief.start()
        self.addCleanup(brief.stop)

    OFFER = """<!doctype html><meta charset="utf-8"><body><input type=file><button onclick="location.href='/creator-micro/content/post/video'">继续编辑</button><button>放弃</button>"""

    def stopped(self, **receipt):
        tx = StubTx()
        tx.resuming = True
        tx.media = lambda: "clip.mp4"
        tx.receipt = {"media_selected": True, "before_ids": [], "draft_offered_before": False, **receipt}
        return tx

    async def resume(self, tx, upload_page=None, html=None):
        self.upload_page = upload_page or self.OFFER
        self.html = html or form() + "<span>重新上传</span>"
        with patch.object(browser_forms, "finish", new=AsyncMock(return_value={"state": "SUBMITTED_UNVERIFIED"})), patch.object(browser_forms, "works", new=AsyncMock(return_value={})), \
                patch.object(browser_forms, "upload_file", new=AsyncMock(side_effect=RuntimeError("selected"))) as self.selected:
            return await browser_forms.publish_douyin(self.page, tx)

    async def test_it_goes_back_into_its_form_and_publishes_once_with_no_second_upload(self):
        tx = self.stopped()
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.resume(tx))["state"])
        self.selected.assert_not_called()
        self.assertEqual(["publish"], [write["word"] for write in self.writes])
        self.assertEqual("https://creator.douyin.com/creator-micro/content/post/video", tx.receipt["draft_url"])

    async def test_a_draft_with_another_title_is_left_as_it_is(self):
        with self.assertRaises(VpubError) as caught:
            await self.resume(self.stopped(), html=form().replace(f'value="{TITLE}"', 'value="Another"') + "<span>重新上传</span>")
        self.assertEqual("wrong_work", caught.exception.code)
        self.assertEqual([], self.writes)

    async def test_a_draft_that_was_there_before_this_attempt_is_not_opened(self):
        with self.assertRaises(VpubError) as caught:
            await self.resume(self.stopped(draft_offered_before=None))
        self.assertEqual("existing_draft_requires_attention", caught.exception.code)
        self.assertEqual("https://creator.douyin.com/creator-micro/content/upload", self.page.url)

    async def test_words_the_draft_lost_are_typed_again_before_the_press(self):
        html = form().replace(">Words</div>", ">Something else</div>") + "<span>重新上传</span>"
        await self.resume(self.stopped(), html=html)
        self.assertEqual(["publish"], [write["word"] for write in self.writes])  # The form's own check holds the press if they still differ.

    async def test_with_nothing_on_offer_the_file_goes_up_again(self):
        with self.assertRaises(RuntimeError):
            await self.resume(self.stopped(), upload_page="<!doctype html><body><input type=file>")
        self.selected.assert_awaited_once()

    async def test_a_work_with_its_title_and_words_already_listed_is_not_uploaded_again(self):
        tx = self.stopped()
        cards = f'<div class="video-card-content-1"><div class="info-title-text-1">{TITLE} Words</div></div>'
        self.html = '<!doctype html><meta charset="utf-8"><body>' + cards
        with patch.object(browser_forms, "douyin_reopen_kept_draft", new=AsyncMock(return_value=False)), patch.object(browser_forms, "works", new=AsyncMock(return_value={})), \
                patch.object(browser_forms, "upload_file", new=AsyncMock(side_effect=RuntimeError("selected"))) as selected, self.assertRaises(VpubError) as caught:
            await self.page.goto("https://creator.douyin.com/creator-micro/content/manage")
            await browser_forms.publish_douyin(self.page, tx)
        self.assertEqual("inspect_existing_work", caught.exception.code)
        selected.assert_not_called()

    async def test_a_new_plan_with_the_same_words_is_not_held_by_that(self):
        tx = StubTx()
        tx.media = lambda: "clip.mp4"
        tx.receipt = {"media_selected": False}
        self.html = f'<!doctype html><meta charset="utf-8"><body><div class="video-card-content-1"><div class="info-title-text-1">{TITLE} Words</div></div>'
        with patch.object(browser_forms, "works", new=AsyncMock(return_value={})), patch.object(browser_forms, "upload_file", new=AsyncMock(side_effect=RuntimeError("selected"))) as selected, self.assertRaises(RuntimeError):
            await self.page.goto("https://creator.douyin.com/creator-micro/content/manage")
            await browser_forms.publish_douyin(tx=tx, page=self.page)
        selected.assert_awaited_once()

    async def test_a_first_attempt_never_goes_near_a_kept_draft(self):
        tx = StubTx()
        tx.media = lambda: "clip.mp4"
        tx.receipt = {"media_selected": False}
        with self.assertRaises(VpubError) as caught:
            await self.resume(tx)
        self.assertEqual("existing_draft_requires_attention", caught.exception.code)
        self.selected.assert_not_called()


class KeptDraftTests(Base):
    """An upload must not replace a draft the person kept."""

    async def publish(self, upload_page):
        self.upload_page = upload_page
        tx = StubTx()
        tx.media = lambda: "clip.mp4"
        with patch.object(browser_forms, "works", new=AsyncMock(return_value={})), patch.object(browser_forms, "douyin_caption", return_value="Words"), \
                patch.object(browser_forms, "upload_file", new=AsyncMock(side_effect=RuntimeError("selected"))) as selected, self.assertRaises(Exception) as caught:
            await browser_forms.publish_douyin(self.page, tx)
        return caught.exception, selected, tx

    async def test_a_kept_draft_on_offer_stops_it_before_any_video_is_selected(self):
        error, selected, tx = await self.publish('<!doctype html><meta charset="utf-8"><body><input type=file><button>继续编辑</button><button>放弃</button>')
        self.assertEqual("existing_draft_requires_attention", error.code)
        selected.assert_not_called()
        self.assertEqual([], self.writes)

    async def test_a_kept_draft_that_shows_late_stops_it_too(self):
        error, selected, _ = await self.publish('<!doctype html><meta charset="utf-8"><body><input type=file><script>window.pending = 1; setTimeout(() => { const b = document.createElement("button"); b.textContent = "继续编辑"; document.body.append(b); window.pending = 0; }, 1500);</script>')
        self.assertEqual("existing_draft_requires_attention", error.code)
        selected.assert_not_called()

    async def test_a_clean_page_goes_on_to_select_the_video(self):
        error, selected, tx = await self.publish("<!doctype html><body><input type=file>")
        self.assertEqual("selected", str(error))
        selected.assert_awaited_once()
        self.assertIs(False, tx.receipt["draft_offered_before"])


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

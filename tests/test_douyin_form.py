"""Douyin's schedule, collection and draft on its upload form, in a real browser against a small copy of that form.

The copy follows what a signed-in account's form showed in a field check, and public open-source uploaders for the date box
and the collection list. Nothing here contacts Douyin, and none of these steps has been tried on a real account yet.
"""
from datetime import datetime, timedelta, timezone
import asyncio
import contextlib
import html
import io
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from video_publisher import browser_backends, browser_edits, browser_forms, browser_runtime, cli, edits, progress, video_shape
from video_publisher.core import DOUYIN_NAME_LAST, VpubError, atomic_json, douyin_caption, douyin_caption_units, home, load_plan
from shared_chrome import ChromeCase, async_playwright

OWNER = "owner123"
TITLE = "My title"
# A field run's words, as its plan wrote them, which stopped before 发布 as differing from the plan: lines ending in @names, a name in the
# middle of a line with more words after it, © lines, full-width punctuation and a line of #tags.
AHA_TITLE = "五个AI娘一起跳阿哈的抓马小曲！大肥鱼端着白饭杀疯了"
AHA_DESCRIPTION = ("代码写崩了谁背锅？五个AI娘（大肥鱼、GPT娘、Gemini娘、Claude娘、Grok娘）直接随着《阿哈的抓马小曲》开启全员抓马摇摆！大肥鱼端着白饭压轴登场，"
                   "Grok娘半路乱入砸场子，全员精神状态极其美丽！\n\n音乐：《崩坏：星穹铁道》阿哈的抓马小曲 © 米哈游（原曲：BV1LHxQ6gELU）\n"
                   "鲸鱼娘（大肥鱼）原作角色设定：@上善无形\nGPT娘、Gemini娘角色设计参考：@ZipZipPipe\n角色设计参考 @ZipZipPipe 的 Grok 拟人形象（非 xAI 官方角色）\n"
                   "Claude娘为同人拟人形象\n\n© 米哈游版权所有\n《崩坏：星穹铁道》素材的权利归米哈游所有，其他内容的相关权利、利益均归各自所有者享有")
AHA_TAGS = ["阿哈的抓马小曲", "大肥鱼", "DeepSeek", "崩坏星穹铁道", "阿哈摇"]


def douyin_holds(caption, at_signs=True):
    """A box's words as Douyin can show them once typed: a line of its own for each line, a space at the end of a line typed with one,
    and #tags made links of their own, side by side. Without at_signs, every @ is gone, as a field run's Escape left them."""
    lines = []
    for line in caption.split("\n"):
        if line.startswith("#"):
            lines.append("<div>" + "".join(f'<span class="topic">{tag}</span>' for tag in line.split()) + "</div>")
        elif line:
            end = "&nbsp;" if DOUYIN_NAME_LAST.search(line) else ""
            lines.append(f"<div>{html.escape(line if at_signs else line.replace('@', ''))}{end}</div>")
        else:
            lines.append("<div><br></div>")
    return "".join(lines)


# From a review: a name list that opens late, just after the Escape that closed the one before, as a slow lookup's can, and takes the next
# Enter without putting anyone in. The line then runs on into the next, with the space typed after its last name where its break had been.
LATE_LIST_TAKES_ENTER = """<script>(() => { const box = document.querySelector('[contenteditable=true]'); let open = false;
  box.addEventListener('keydown', e => {
    if (e.key === 'Escape') open = /[@＠]/.test(getSelection().getRangeAt(0).endContainer.textContent);
    else if (e.key === 'Enter' && open) { e.preventDefault(); open = false; }
  }); })();</script>"""


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
      const send = word => fetch('/write', {{method: 'POST', body: JSON.stringify({{word, later: document.getElementById('later').dataset.checked, when: document.querySelector('[placeholder="日期和时间"]').value, picked: document.getElementById('picked').innerText, words: document.querySelector('[contenteditable=true]').innerText}})}});
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
    size = None  # The video's own width and height, when a test gives them.

    def __init__(self, **options):
        self.options = {"title": TITLE, "description": "Words", "tags": [], "visibility": "public", "ai_generated": False, **options}
        self.receipt = {}
        self.platform = "douyin"

    def save(self, **changes):
        self.receipt.update(changes)
        return dict(self.receipt)

    def cover(self, role="landscape"):
        return None

    def video_size(self):
        return self.size

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

    async def test_tags_douyin_set_side_by_side_still_read_as_the_plans(self):
        await self.open(form().replace('>Words</div>', '>Words<div><br></div><div><span class="topic">#Tag</span><span class="topic">#Other</span></div></div>'))
        await self.run_form(StubTx(tags=["Tag", "Other"]))
        self.assertEqual(["publish"], [write["word"] for write in self.writes])

    async def test_a_line_joined_before_the_tags_never_publishes(self):
        await self.open(form().replace('>Words</div>', '>Words<span class="topic">#Tag</span></div>'))
        with self.assertRaises(VpubError) as caught:
            await self.run_form(StubTx(tags=["Tag"]))
        self.assertEqual("metadata_mismatch", caught.exception.code)
        self.assertEqual([], self.writes)

    async def test_a_form_that_lost_an_at_sign_never_publishes_and_says_so(self):
        """From a field run: Douyin took the @ away from each name in the caption, and the words were stopped before 发布."""
        await self.open(form().replace('>Words</div>', '>Credit: 上善无形</div>'))
        with self.assertRaises(VpubError) as caught:
            await self.run_form(StubTx(description="Credit: @上善无形"))
        self.assertEqual("metadata_mismatch", caught.exception.code)
        self.assertIn('lacks "@" at character 9', caught.exception.message)
        self.assertEqual([], self.writes)

    def aha_form(self, at_signs=True, caption=None):
        holds = douyin_holds(caption or douyin_caption(AHA_DESCRIPTION, AHA_TAGS), at_signs)
        return form().replace(f'value="{TITLE}"', f'value="{AHA_TITLE}"').replace(">Words</div>", f">{holds}</div>")

    def aha_tx(self):
        return StubTx(title=AHA_TITLE, description=AHA_DESCRIPTION, tags=AHA_TAGS)

    async def test_that_runs_words_with_a_line_run_on_into_the_next_never_publish_and_say_where(self):
        """From a review: each of that run's lines ending in a name is now typed with a space at its end, and a line break lost after one
        left that space in its place, which read as the plan's words. Each of its three such lines, run on into the next that way, stops
        before 发布, and the stop names the plan's own line."""
        lines = douyin_caption(AHA_DESCRIPTION, AHA_TAGS).split("\n")
        for number in (4, 5, 6):
            with self.subTest(line=number):
                self.writes[:] = []
                ran_on = "\n".join(lines[:number - 1] + [lines[number - 1] + " " + lines[number]] + lines[number + 1:])
                self.assertEqual(browser_backends.douyin_card_words("\n".join(lines)), browser_backends.douyin_card_words(ran_on))
                await self.open(self.aha_form(caption=ran_on))
                with self.assertRaises(VpubError) as caught:
                    await self.run_form(self.aha_tx())
                self.assertEqual("metadata_mismatch", caught.exception.code)
                self.assertIn(f"the form has the plan's words, but its lines don't end where the plan's do, first at the plan's line {number}.", caught.exception.message)
                self.assertEqual([], self.writes)

    async def test_that_runs_words_typed_into_a_box_whose_late_list_takes_an_enter_never_publish(self):
        """The review's account of it, typed: a name list that opens just after its Escape takes the Enter after each line ending in a name."""
        await self.open(self.aha_form() + LATE_LIST_TAKES_ENTER)
        editor = self.page.locator("[contenteditable=true]")
        caption = douyin_caption(AHA_DESCRIPTION, AHA_TAGS)
        await browser_backends.douyin_type_caption(self.page, editor, caption)
        said = await editor.inner_text()
        self.assertEqual(browser_backends.douyin_card_words(caption), browser_backends.douyin_card_words(said))
        self.assertIn("@上善无形 GPT娘", said)
        with self.assertRaises(VpubError) as caught:
            await self.run_form(self.aha_tx())
        self.assertIn("its lines don't end where the plan's do, first at the plan's line 4.", caught.exception.message)
        self.assertEqual([], self.writes)

    async def edit(self, html):
        """Douyin's edit route for an existing work's words, on this copy of the form, up to its one press."""
        await self.open(html)
        boxes = self.page.locator('input[placeholder*="填写作品标题"]'), self.page.locator("[contenteditable=true]")
        submit = AsyncMock()
        with patch.object(browser_edits, "douyin_fields", new=AsyncMock(return_value=boxes)), patch.object(browser_edits, "douyin_moved_on", new=AsyncMock(return_value=None)):
            result = await browser_edits.douyin_text(self.page, "7300000000000000001", {"title": AHA_TITLE, "caption": douyin_caption(AHA_DESCRIPTION, AHA_TAGS)},
                                                     lambda **changes: changes, submit, Path(tempfile.gettempdir()) / "never-written.json")
        return result, submit

    async def test_an_edit_reads_its_lines_back_too_before_its_one_press(self):
        result, submit = await self.edit(form())
        self.assertEqual("EDIT_VERIFIED", result["state"])
        submit.assert_awaited_once()
        with self.assertRaises(VpubError) as caught:
            await self.edit(form() + LATE_LIST_TAKES_ENTER)
        self.assertEqual("metadata_mismatch", caught.exception.code)
        self.assertIn("so nothing was saved: the form has the plan's words, but its lines don't end where the plan's do, first at the plan's line 4.", caught.exception.message)

    async def test_a_field_runs_words_as_douyin_holds_them_go_out_once(self):
        """From a field run whose words stopped as differing from the plan: its lines, with a space at the end of each one whose last #topic or
        @name is an @name, and its #tags side by side, read as the plan's, and the post goes out once."""
        await self.open(self.aha_form())
        await self.run_form(StubTx(title=AHA_TITLE, description=AHA_DESCRIPTION, tags=AHA_TAGS))
        self.assertEqual(["publish"], [write["word"] for write in self.writes])

    async def test_that_runs_form_without_its_three_at_signs_still_never_publishes(self):
        """Word for word, the stop that field run ended with, once Douyin had taken away each @ of the plan's."""
        await self.open(self.aha_form(at_signs=False))
        with self.assertRaises(VpubError) as caught:
            await self.run_form(StubTx(title=AHA_TITLE, description=AHA_DESCRIPTION, tags=AHA_TAGS))
        self.assertEqual("metadata_mismatch", caught.exception.code)
        self.assertIn("they agree for 163 of the plan's 343 characters, up to \" 鲸鱼娘（大肥鱼）原作角色设定：\", and the form holds 340", caught.exception.message)
        self.assertEqual(3, caught.exception.message.count('lacks "@"'))
        self.assertEqual([], self.writes)

    async def test_words_that_came_out_different_say_where_and_never_publish(self):
        await self.open()
        with self.assertRaises(VpubError) as caught:
            await self.run_form(StubTx(description="Words, then more"))
        self.assertEqual("metadata_mismatch", caught.exception.code)
        self.assertIn('they agree for 5 of the plan\'s 16 characters, up to "Words", and the form holds 5', caught.exception.message)
        self.assertEqual([], self.writes)
        with self.assertRaises(VpubError) as caught:
            await self.run_form(StubTx(title="Another title"))
        self.assertIn("title differs", caught.exception.message)
        self.assertEqual([], self.writes)

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

    def test_space_between_topics_or_names_is_the_pages_own(self):
        """From a field run: a caption with #tags and @names stopped as differing from the plan, and Douyin can make each one a link of its own,
        set side by side with no space between them or each on a line of its own."""
        words = browser_backends.douyin_card_words
        caption = "设定：@上善无形\n参考 @ZipZipPipe 的形象\n\n#阿哈的抓马小曲 #大肥鱼 #阿哈摇"
        for shown in ("设定：@上善无形\n参考 @ZipZipPipe 的形象\n\n#阿哈的抓马小曲#大肥鱼#阿哈摇", "设定：@上善无形 参考 @ZipZipPipe 的形象 #阿哈的抓马小曲\n#大肥鱼\n#阿哈摇"):
            with self.subTest(shown=shown):
                self.assertEqual(words(caption), words(shown))
        # Any other space counts as it always has. Without the space after a name or topic, where it ends can't be told, and a line that lost
        # its line break with nothing in its place is another text.
        for wanted, shown in (("@上善无形\nGPT娘", "@上善无形GPT娘"), ("#阿哈摇 好看", "#阿哈摇好看"), ("@上善无形 GPT娘", "@SomeoneElse GPT娘"), ("#a #b", "#a #c"),
                              ("C++", "C#"), ("享有\n\n#阿哈的抓马小曲 #大肥鱼", "享有#阿哈的抓马小曲 #大肥鱼"), ("参考：\n@上善无形 的设定", "参考：@上善无形 的设定")):
            with self.subTest(wanted=wanted):
                self.assertNotEqual(words(wanted), words(shown))
        self.assertEqual(words("享有\n\n#阿哈的抓马小曲"), words("享有 #阿哈的抓马小曲"))

    async def test_what_was_there_goes_first(self):
        box, _ = await self.type_it("New.")
        self.assertEqual("New.", (await box.inner_text()).strip())

    async def test_suggestions_a_last_tag_opens_are_closed_before_anything_else_is_pressed(self):
        """From a field run: the topic suggestions Douyin opened for a caption's last #tag stayed over the cover buttons below and took their click."""
        await self.page.set_content(self.PAGE + """<div id="topics" hidden style="position:fixed;inset:0">#Tag 话题</div>
          <script>const box = document.getElementById('box'), topics = document.getElementById('topics');
          box.addEventListener('input', () => { topics.hidden = !/[#@][^\\s#@]*$/.test(box.innerText.replace(/\\n+$/, '')); });
          box.addEventListener('keydown', e => { if (e.key === 'Escape') topics.hidden = true; });</script>""")
        box = self.page.locator("#box")
        for caption in ("Words.\n\n#Tag", "Words for @someone"):
            with self.subTest(caption=caption):
                await browser_backends.douyin_type_caption(self.page, box, caption)
                self.assertTrue(await self.page.locator("#topics").is_hidden())
                self.assertEqual(browser_backends.douyin_card_words(caption), browser_backends.douyin_card_words(await box.inner_text()))

    # A box like Douyin's: a list of suggestions for the #topic or @name the cursor is at the end of, and Enter there picks one instead of starting a line.
    # Its script keeps to a scope of its own: from a review, a second page set in the same tab otherwise failed to declare its names again and had no list.
    SUGGESTING = """<!doctype html><meta charset="utf-8"><body><div contenteditable="true" id="box">Old words</div><div id="list" hidden>SomeoneElse</div>
      <script>(() => { const box = document.getElementById('box'), list = document.getElementById('list'); window.pending = 0;
      // As field runs showed: a typed @ waits for someone to be picked past any spaces after it, until the words before the cursor end in one,
      // and a # topic ends at a space.
      const token = () => { const range = getSelection().rangeCount && getSelection().getRangeAt(0); const node = range && range.endContainer;
        if (!node || node.nodeType !== 3) return null;
        const before = node.data.slice(0, range.endOffset), name = before.lastIndexOf('@'), topic = /#[^\\s#@]*$/.exec(before);
        if (topic && topic.index > name) return {node, at: topic.index, kind: '#'};
        return name >= 0 && !/\\s$/.test(before) ? {node, at: name, kind: '@'} : null; };
      const look = () => { list.hidden = !token(); };
      // A late list stands for a lookup that comes back after a while and opens its list then, if the cursor is still at the end of the name or topic.
      box.addEventListener('input', () => { if (!token()) { list.hidden = true; return; } if (!LATE) return look(); window.pending++; setTimeout(() => { window.pending--; look(); }, LATE); });
      box.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
          // As Douyin did in a field run: Escape closes a list of names and takes the @ away with it. A list of topics keeps its #.
          const open = !list.hidden && token(); list.hidden = true;
          if (open && open.kind === '@') open.node.deleteData(open.at, 1);
          if (LEAVES) box.blur();
        }
        if (e.key === 'Enter' && !list.hidden) { e.preventDefault(); list.hidden = true; document.execCommand('insertText', false, 'SomeoneElse '); }
      }); })();</script>"""
    # What a field run's caption had: lines ending in @names, a name in the middle of a line, and #tags at the end.
    FIELD_CAPTION = "开头一段。\n\n原作角色设定：@上善无形\n角色设计参考：@ZipZipPipe\n参考 @ZipZipPipe 的形象\n\n#阿哈的抓马小曲 #大肥鱼 #阿哈摇"

    async def suggesting_box(self, late=0, leaves=False):
        await self.page.set_content(self.SUGGESTING.replace("LATE", str(late)).replace("LEAVES", "true" if leaves else "false"))
        return self.page.locator("#box")

    async def type_into_suggesting_box(self, late=0, leaves=False, caption=FIELD_CAPTION):
        box = await self.suggesting_box(late, leaves)
        await browser_backends.douyin_type_caption(self.page, box, caption)
        # Whatever list was still coming has come before anything is looked at.
        await self.page.wait_for_function("window.pending === 0")
        await self.page.wait_for_timeout(50)
        return box

    async def test_enter_never_reaches_suggestions_a_line_with_a_name_opened(self):
        """From field runs: a caption with lines ending in @names came out on Douyin's form different from the plan, and then, with Escape
        closing the lists, short of exactly its three @. A line whose last name is an @name now ends in a space, as a later run's lines did
        when every @ stayed. No one is ever picked, and every line and every @ is there."""
        for late in (0, 200):
            with self.subTest(late=late):
                box = await self.type_into_suggesting_box(late)
                said = await box.inner_text()
                self.assertNotIn("SomeoneElse", said)
                self.assertEqual([line for line in self.FIELD_CAPTION.split("\n") if line], [line.strip() for line in said.split("\n") if line.strip()])
                self.assertEqual(browser_backends.douyin_card_words(self.FIELD_CAPTION), browser_backends.douyin_card_words(said))
                self.assertTrue(await self.page.locator("#list").is_hidden())

    async def test_a_box_that_escape_leaves_gets_the_rest_of_the_words_at_their_end(self):
        box = await self.type_into_suggesting_box(leaves=True)
        self.assertEqual(browser_backends.douyin_card_words(self.FIELD_CAPTION), browser_backends.douyin_card_words(await box.inner_text()))

    async def test_after_its_last_line_the_cursor_is_never_put_back_where_a_list_opens(self):
        """As when the box is left before the covers: with nothing more to type, an Escape that left the box doesn't have the cursor put back at
        the end of the last #topic, where a list can open again over whatever is pressed next, as an edit's save is."""
        await self.page.set_content(self.PAGE + REOPENS.replace('id="topics"', 'id="topics" hidden', 1))
        box = self.page.locator("#box")
        await browser_backends.douyin_type_caption(self.page, box, "Words.\n\n#Tag")
        self.assertTrue(await self.page.locator("#topics").is_hidden())
        self.assertEqual(browser_backends.douyin_card_words("Words.\n\n#Tag"), browser_backends.douyin_card_words(await box.inner_text()))

    async def test_a_field_runs_words_lost_their_at_signs_as_that_run_typed_them(self):
        """From a field run: its form came back 3 characters short after the Escape that closed Douyin's lists. Typed as that run typed it,
        a line at a time with each list closed, this box comes back the same way, word for word as that run's stop said, with no one picked."""
        caption = douyin_caption(AHA_DESCRIPTION, AHA_TAGS)
        box = await self.suggesting_box()
        await box.click()
        await box.press("ControlOrMeta+A")
        await box.press("Backspace")
        for number, line in enumerate(caption.split("\n")):
            if number:
                await self.page.keyboard.press("Enter")
            if line:
                await self.page.keyboard.insert_text(line)
                if browser_backends.DOUYIN_SUGGESTS.search(line):
                    await browser_backends.douyin_close_suggestions(self.page, box)
        said = await box.inner_text()
        self.assertNotIn("SomeoneElse", said)
        stop = browser_backends.where_words_part(browser_backends.douyin_card_words(caption), browser_backends.douyin_card_words(said))
        self.assertIn("they agree for 163 of the plan's 343 characters, up to \" 鲸鱼娘（大肥鱼）原作角色设定：\", and the form holds 340", stop)
        self.assertEqual(3, stop.count('lacks "@"'))

    async def test_that_runs_words_now_go_in_as_written(self):
        caption = douyin_caption(AHA_DESCRIPTION, AHA_TAGS)
        said = await (await self.type_into_suggesting_box(caption=caption)).inner_text()
        self.assertNotIn("SomeoneElse", said)
        self.assertEqual(browser_backends.douyin_card_words(caption), browser_backends.douyin_card_words(said))

    async def test_names_followed_by_other_words_keep_their_at_signs(self):
        """Shapes from three more field captions: names with a space and then punctuation after them, names with punctuation straight after
        them, three in one line, and lines that already ended in a space after a name, which went out with every @ kept."""
        for caption in ("鲸鱼娘原作角色设定：@上善无形 ，角色设计参考：@ZipZipPipe （Claude娘为同人拟人形象，非官方角色）。\n\n#大肥鱼 #原创音乐",
                        "原作OC by @上善无形，女仆装设定 by @ZipZipPipe（遵循CC BY-NC-SA 4.0非商用协议）；Gemini娘参考 @ZipZipPipe；Claude娘为同人形象。\n\n#大肥鱼",
                        "・鲸鱼娘原设：@上善无形 、@ZipZipPipe \n・形象设定：@ZipZipPipe \n・本视频包含 AI 生成画面与音频内容"):
            with self.subTest(caption=caption):
                said = await (await self.type_into_suggesting_box(caption=caption)).inner_text()
                self.assertNotIn("SomeoneElse", said)
                self.assertEqual(browser_backends.douyin_card_words(caption), browser_backends.douyin_card_words(said))

    async def test_only_a_line_whose_last_topic_or_name_is_a_name_gets_a_space_at_its_end(self):
        _, seen = await self.type_it("Plain line.\n参考：@上善无形\n@ZipZipPipe 的形象\n已有空格：@ZipZipPipe \n@上善无形 #tag\n#Tag")
        self.assertEqual(["Plain line.", "参考：@上善无形 ", "@ZipZipPipe 的形象 ", "已有空格：@ZipZipPipe ", "@上善无形 #tag", "#Tag"],
                         [data for kind, data in seen if kind == "insertText"])

    async def test_douyins_1000_units_are_counted_on_what_goes_into_the_box(self):
        """From a review: the checks counted the plan's words, without the space typed at the end of a line whose last #topic or @name is an
        @name, so a caption at the limit would have gone in and been cut by Douyin. Each Enter is one unit, and an emoji two."""
        caption = "Plain line.\n参考：@上善无形\n\n@ZipZipPipe 的形象\n已有空格：@ZipZipPipe \n@上善无形 #tag\n#Tag 😀"
        _, seen = await self.type_it(caption)
        typed = sum(len(data.encode("utf-16-le")) // 2 if kind == "insertText" else kind == "insertParagraph" for kind, data in seen)
        self.assertEqual(len(caption.encode("utf-16-le")) // 2 + 2, typed)  # The plan's words, and a space after each of two lines' last name.
        self.assertEqual(typed, douyin_caption_units(caption))

    async def test_lines_without_a_topic_or_name_go_in_with_no_pause(self):
        with patch.object(browser_backends, "douyin_close_suggestions", new=AsyncMock()) as closing:
            await self.type_it("One line.\n\nTwo, with #tag in it.\nThree @name\nFour.")
        self.assertEqual(2, closing.await_count)

    def test_a_forms_lines_count_but_not_its_blank_lines_its_spacing_or_where_douyin_sets_its_tags(self):
        """From a review: the space now typed after a line's last name sat where a line break lost after it had been, and the words read as
        the plan's. A form's lines count as well as its words, and a stop names only the plan's own line."""
        differs = browser_backends.douyin_form_differs
        caption = "开头。\n\n设定：@上善无形\n参考 @ZipZipPipe 的形象\n\n#阿哈的抓马小曲 #大肥鱼 #阿哈摇"
        for shown in ("开头。\n\n设定：@上善无形 \n参考 @ZipZipPipe 的形象 \n\n#阿哈的抓马小曲#大肥鱼#阿哈摇",
                      "\u200b开头。\n设定：@上善无形\u00a0\n\n\n参考 @ZipZipPipe 的形象\n#阿哈的抓马小曲\n#大肥鱼\n#阿哈摇\n"):
            with self.subTest(shown=shown):
                self.assertIsNone(differs(caption, shown))
        for shown, number in (("开头。\n\n设定：@上善无形 参考 @ZipZipPipe 的形象\n\n#阿哈的抓马小曲 #大肥鱼 #阿哈摇", 3),
                              ("开头。\n\n设定：@上善无形\n参考 @ZipZipPipe\n的形象\n\n#阿哈的抓马小曲 #大肥鱼 #阿哈摇", 4),
                              ("开头。 设定：@上善无形\n参考 @ZipZipPipe 的形象 #阿哈的抓马小曲 #大肥鱼 #阿哈摇", 1),
                              ("开头。\n\n设定：@上善无形\n参考 @ZipZipPipe 的形象 #阿哈的抓马小曲 #大肥鱼 #阿哈摇", 4)):
            with self.subTest(shown=shown):
                self.assertEqual(browser_backends.douyin_card_words(caption), browser_backends.douyin_card_words(shown))
                said = differs(caption, shown)
                self.assertEqual(f"the form has the plan's words, but its lines don't end where the plan's do, first at the plan's line {number}", said)
        self.assertIn('lacks "@" at character 9', differs("Credit: @上善无形", "Credit: 上善无形"))

    def test_a_form_that_lost_its_at_signs_says_which(self):
        """From a field run: Douyin took away a caption's three @, and the stop said only where the words first parted."""
        plan = browser_backends.douyin_card_words("原作角色设定：@上善无形\n参考：@ZipZipPipe\n参考 @ZipZipPipe 的形象")
        said = browser_backends.where_words_part(plan, plan.replace("@", ""))
        self.assertEqual(3, said.count('lacks "@"'))

    def test_where_the_words_part_is_said_with_the_plans_words_only(self):
        said = browser_backends.where_words_part("原作角色设定：@上善无形 角色设计参考", "原作角色设定：@SomeoneElse 角色设计参考")
        self.assertIn('they agree for 8 of the plan\'s 19 characters, up to "原作角色设定：@", and the form holds 26', said)
        self.assertNotIn("SomeoneElse", said)
        self.assertIn("agree for 5 of the plan's 5", browser_backends.where_words_part("Words", "Words and more"))

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
  <button onclick="if (%s) { document.getElementById('d').hidden = true; const img = document.querySelector('.cover-b img'); img.hidden = false; img.style.cssText = 'display:block;width:100%%;height:100%%'; }">完成</button></div>"""


class CoverOnANewPostTests(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        settled = patch.object(browser_edits, "DOUYIN_COVER_SETTLE_SECONDS", 0)
        settled.start()
        self.addCleanup(settled.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.image = Path(folder.name) / "cover.jpg"
        self.image.write_bytes(b"synthetic")
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

    async def saved_preview_post(self, preview, before=""):
        # Do not leave the original world's handler able to reveal the old
        # picture as well as the scenario's replacement handler.
        html = form() + COVER_FORM % "false"
        await self.open(html)
        if before:
            await self.page.evaluate(before)
        await self.page.locator('#d button').evaluate("(button, script) => button.setAttribute('onclick', script)",
            "document.getElementById('d').hidden = true; " + preview)
        with patch.object(browser_forms, "finish", new=AsyncMock(return_value={"state": "SUBMITTED_UNVERIFIED"})):
            return await browser_forms.finish_douyin_form(self.page, self.tx, {})

    async def test_saved_cover_preview_can_arrive_after_dialog_closes(self):
        await self.saved_preview_post("document.querySelector('.cover-b').replaceChildren(); "
                                      "setTimeout(() => { const img = document.createElement('img'); "
                                      "img.style.cssText = 'display:block;width:100%;height:100%'; "
                                      "document.querySelector('.cover-b').append(img); }, 1600)")
        self.assertEqual("UI_ACCEPTED", self.tx.receipt["portrait_cover"]["state"])
        self.assertEqual(["publish"], [write["word"] for write in self.writes])

    async def test_saved_cover_can_be_a_background_image(self):
        await self.saved_preview_post("const slot = document.querySelector('.cover-b'); slot.replaceChildren(); "
                                      "slot.style.backgroundImage = 'url(data:image/gif;base64,R0lGODlhAQABAIAAAAUEBA==)'")
        self.assertEqual("UI_ACCEPTED", self.tx.receipt["portrait_cover"]["state"])
        self.assertEqual(["publish"], [write["word"] for write in self.writes])

    async def test_missing_saved_preview_stops_before_publish(self):
        with patch.object(browser_forms, "DOUYIN_PREVIEW_SECONDS", 1), self.assertRaises(VpubError) as caught:
            await self.saved_preview_post("document.querySelector('.cover-b').replaceChildren()")
        self.assertEqual("cover_unverified", caught.exception.code)
        self.assertEqual([], self.writes)

    async def test_an_unchanged_existing_preview_does_not_prove_a_save(self):
        before = "const img = document.querySelector('.cover-b img'); img.hidden = false; img.style.cssText = 'display:block;width:100%;height:100%';"
        with patch.object(browser_forms, "DOUYIN_PREVIEW_SECONDS", 1), self.assertRaises(VpubError) as caught:
            await self.saved_preview_post("", before)
        self.assertEqual('cover_unverified', caught.exception.code)
        self.assertEqual([], self.writes)
        self.assertNotEqual('UI_ACCEPTED', self.tx.receipt.get('portrait_cover', {}).get('state'))

    async def test_only_the_other_slots_preview_changing_does_not_prove_this_save(self):
        before = "const other = document.createElement('div'); other.id = 'other-cover'; other.style.cssText = 'width:40px;height:30px'; document.body.append(other);"
        with patch.object(browser_forms, "DOUYIN_PREVIEW_SECONDS", 1), self.assertRaises(VpubError) as caught:
            await self.saved_preview_post("document.querySelector('#other-cover').style.backgroundImage='url(offline-other.png)'", before)
        self.assertEqual('cover_unverified', caught.exception.code)
        self.assertEqual([], self.writes)

    async def test_a_changed_existing_background_is_accepted(self):
        before = "const slot = document.querySelector('.cover-b'); slot.replaceChildren(); slot.style.backgroundImage='url(offline-before.png)';"
        await self.saved_preview_post("document.querySelector('.cover-b').style.backgroundImage='url(offline-after.png)'", before)
        cover = self.tx.receipt['portrait_cover']
        self.assertEqual('UI_ACCEPTED', cover['state'])
        self.assertTrue(cover['preview_present_before'])
        self.assertTrue(cover['preview_changed'])
        self.assertFalse(cover['remote_pixels_verified'])
        self.assertEqual(['publish'], [write['word'] for write in self.writes])

    async def test_its_window_gets_as_long_as_an_edits_and_then_nothing_goes_out(self):
        """From a field run's first real post: a 30-second wait after 完成 ended it, with the video uploaded and nothing pressed."""
        with patch.object(browser_edits, "DOUYIN_COVER_SECONDS", 1), self.assertRaises(VpubError) as caught:
            await self.post("false")
        self.assertEqual("cover_not_ready", caught.exception.code)
        self.assertIn("nothing was published", caught.exception.message)
        self.assertIn("Requests since the press: {'started': ", caught.exception.message)
        self.assertRegex(caught.exception.message, DRAWING_SAID)
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

    def test_a_covered_slot_is_looked_at_a_second_apart_for_ninety(self):
        """From a review: the tests cut this wait short, so nothing else holds it to what the field runs called for. Round 2's pointer gets
        half a second for what it brings up, and rests just off the page for a look with it away."""
        self.assertEqual((90, 1000), (browser_forms.DOUYIN_COVERED_SECONDS, browser_forms.DOUYIN_COVERED_LOOK_MS))
        # From a review of a490e80: the pointer rests just off the page for a look with it away, which hovers nothing, and the moves and the
        # spots tried were held by nothing.
        self.assertEqual((500, (-1, -1), 4, 4), (browser_forms.DOUYIN_SETTLE_MS, browser_forms.DOUYIN_POINTER_AWAY, browser_forms.DOUYIN_AIM_MOVES,
                                                 browser_forms.DOUYIN_SPOT_TRIES))

    async def test_whether_the_page_was_drawing_is_told_in_fixed_words_true_or_false_and_a_count(self):
        """For the lead that the hidden window draws no frames: a page that answers gives its facts, and a page that can't, or gives anything
        else, gives unknown for each, with no words from it and no crash."""
        await self.open(form())
        drawing = await browser_edits.douyin_drawing(self.page)
        self.assertEqual(("visible", True), (drawing["visibility"], drawing["frame_within_a_second"]))
        self.assertIs(int, type(drawing["running_animations"]))
        unknown = dict.fromkeys(("visibility", "frame_within_a_second", "running_animations"), "unknown")
        for answer in (AsyncMock(side_effect=RuntimeError("gone")), AsyncMock(return_value="张三"),
                       AsyncMock(return_value={"visibility": ["张三"], "frame_within_a_second": 1, "running_animations": True})):
            with self.subTest(answer=answer):
                self.assertEqual(unknown, await browser_edits.douyin_drawing(SimpleNamespace(evaluate=answer)))
        # From a review of a490e80: nothing held the read to its cut-off. A page that never answers gives unknown for each fact once it is up.
        started = time.monotonic()
        with patch.object(browser_edits, "DOUYIN_DRAWING_SECONDS", 0.2):
            self.assertEqual(unknown, await browser_edits.douyin_drawing(SimpleNamespace(evaluate=lambda *script: asyncio.sleep(10))))
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual("The page didn't say whether it was visible, didn't say whether it drew a frame and "
                         "didn't say how many animations were running.", browser_edits.douyin_drawing_said(unknown))
        self.assertRegex(browser_edits.douyin_drawing_said(drawing), DRAWING_SAID)

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


# Both of Douyin's cover slots, each sending word of its own press, and one cover window for the two.
BOTH_COVERS = """<div class="coverControl-a" style="position:relative"><span>横封面4:3</span><div class="cover-b" style="width:40px;height:30px" onclick="pick('wide')"><img hidden></div>%s</div>
  <div class="coverControl-a" style="position:relative"><span>竖封面3:4</span><div class="cover-b" style="width:30px;height:40px" onclick="pick('tall')"><img hidden></div>%s</div>
  <div role="dialog" id="d" hidden><p>上传封面</p><div class="semi-upload">点击上传文件或拖拽文件到这里<input type="file" class="semi-upload-hidden-input"></div>
  <button onclick="document.getElementById('d').hidden = true; const img = window.activeCover.querySelector('img'); img.hidden = false; img.style.cssText = 'display:block;width:100%%;height:100%%';">完成</button></div>
  <script>function pressed(slot) { fetch('/write', {method: 'POST', body: JSON.stringify({word: 'cover', slot})}); }
  document.addEventListener('click', e => { const box = e.target.closest('.coverControl-a'); if(box) window.activeCover = box.querySelector('.cover-b'); }, true);
  function pick(slot) { pressed(slot); document.getElementById('d').hidden = false; }</script>"""
# Something laid over a slot, which takes its press, as something did over a field run's 横封面4:3 slot.
OVER_IT = """<div style="position:absolute;inset:0" onclick="pressed('over')"></div>"""
# Inside the 横封面4:3 control: its slot turned off, its slot still hidden, and a second slot, as a change to the page's layout could bring.
# The driver counts only a page's own controls as turned off, so the slot becomes one.
TURNED_OFF = """<script>(() => { const slot = document.querySelector('.cover-b'), off = document.createElement('button');
  off.className = slot.className; off.style.cssText = slot.style.cssText; off.disabled = true; slot.replaceWith(off); })();</script>"""
HIDDEN = """<script>document.querySelector('.cover-b').style.visibility = 'hidden'</script>"""
TWO_SLOTS = """<div class="cover-c" style="width:40px;height:30px"></div>"""
# The words before the cursor, when it is in the box.
BEFORE_CURSOR = """const box = document.querySelector('[contenteditable=true]'), topics = document.getElementById('topics');
  const before = () => { const caret = getSelection().rangeCount && getSelection().getRangeAt(0), words = document.createRange();
    if (!caret || !box.contains(caret.endContainer)) return null;
    words.selectNodeContents(box); words.setEnd(caret.endContainer, caret.endOffset); return words.toString(); };"""
# A list the description opened for the #topic at the end of its words, over everything below it until Escape reaches the box with the cursor
# there, as Douyin's list is for the #topic or @name the cursor is at the end of.
OPEN_LIST = """<div id="topics" style="position:fixed;inset:0;background:white" onclick="pressed('list')">#Tag 话题</div>
  <script>(() => { %s
  box.addEventListener('keydown', e => { if (e.key === 'Escape' && before() === box.textContent) topics.hidden = true; }); })();</script>""" % BEFORE_CURSOR
# A list that opens again whenever the cursor comes to rest at the end of a #topic, and an Escape that closes it and leaves the box too,
# which the code allows a page might do. It starts open, as the description left it.
REOPENS = """<div id="topics" style="position:fixed;inset:0;background:white" onclick="pressed('list')">#Tag 话题</div>
  <script>(() => { %s
  document.addEventListener('selectionchange', () => { if (/#[^\\s#@]+$/.test(before() || '')) topics.hidden = false; });
  box.addEventListener('keydown', e => { if (e.key === 'Escape') { topics.hidden = true; box.blur(); } }); })();</script>""" % BEFORE_CURSOR
# Sends word of each Escape that reaches the description.
ESCAPES = """<script>document.querySelector('[contenteditable=true]').addEventListener('keydown', e => {
  if (e.key === 'Escape') fetch('/write', {method: 'POST', body: JSON.stringify({word: 'escape'})}); });</script>"""
# Inside a slot's own cover box and over all of it, each sending word of its own press: a mask whose press opens the cover window, as a press
# on the box would, and a button, which is a control and is never pressed, with a span inside one too.
MASK_OPENS = """<div class="mask-x1y2" style="position:absolute;inset:0" onclick="pick('mask')"></div>"""
A_BUTTON = """<button class="edit-b1" style="position:absolute;top:0;left:0;width:100%;height:100%" onclick="pressed('button')"></button>"""
IN_A_BUTTON = ("""<button class="edit-b1" style="position:absolute;top:0;left:0;width:100%;height:100%;padding:0;border:0" onclick="pressed('button')">"""
               """<span class="icon-c3" style="display:block;width:100%;height:100%"></span></button>""")
# Controls of other kinds than a button over all of the 竖封面3:4 slot inside its own cover box, each sending word of its own press: a button's
# role, one after a word that is no role, a place a keyboard can reach, a link with no role, a label, a box to type in, an embedded frame, an
# object and a plugin, and a video and a sound player with controls.
FULL = "position:absolute;top:0;left:0;width:100%;height:100%;padding:0;border:0;margin:0;display:block"
OTHER_CONTROLS = (("div.icon-r1", f"""<div class="icon-r1" role="button" style="{FULL}" onclick="pressed('control')"></div>"""),
                  ("div.icon-p1", f"""<div class="icon-p1" role="presentation-x button" style="{FULL}" onclick="pressed('control')"></div>"""),
                  ("div.icon-t1", f"""<div class="icon-t1" tabindex="0" style="{FULL}" onclick="pressed('control')"></div>"""),
                  ("a.link-a1", f"""<a class="link-a1" href="#x" style="{FULL}" onclick="pressed('control'); return false"></a>"""),
                  ("label.lab-l1", f"""<label class="lab-l1" style="{FULL}" onclick="pressed('control')"></label>"""),
                  ("input.in-i1", f"""<input class="in-i1" style="{FULL}" onclick="pressed('control')">"""),
                  ("iframe.frame-f1", f"""<iframe class="frame-f1" style="{FULL}"></iframe>"""),
                  ("video.player-v1", f"""<video class="player-v1" controls style="{FULL}" onclick="pressed('control')"></video>"""),
                  ("object.thing-o1", f"""<object class="thing-o1" style="{FULL}" onclick="pressed('control')"></object>"""),
                  ("embed.thing-e1", f"""<embed class="thing-e1" type="text/plain" src="data:text/plain,x" style="{FULL}">"""),
                  ("audio.player-a1", f"""<audio class="player-a1" controls style="{FULL}" onclick="pressed('control')"></audio>"""))
# A mask of the 竖封面3:4 cover box that shows only while the pointer is over the box, as a cover card's hover layer might, whose press opens
# the cover window. A trial press moves the pointer there, so it brings the mask up itself.
HOVER_MASK = """<style>.hover-m1 { display: none } .coverControl-a:hover > .hover-m1 { display: block }</style>
  <div class="hover-m1" style="position:absolute;inset:0" onclick="pick('mask')"></div>"""
# MASK_OPENS with a small icon of its own at the slot's centre whose press does something else, as a remove icon on a card could: it takes
# the slot's picture away. It is an <i>, or a <button>, the tag given, and shows whether the pointer is there or not.
ICON_ON_MASK = MASK_OPENS + """<script>(() => { const box = document.currentScript.parentElement, slot = box.querySelector('.cover-b');
  const s = slot.getBoundingClientRect(), b = box.getBoundingClientRect(), icon = document.createElement('%s'); icon.className = 'icon-del';
  icon.style.cssText = `position:absolute;left:${s.left - b.left + s.width / 2 - 5}px;top:${s.top - b.top + s.height / 2 - 5}px;width:10px;height:10px;padding:0;border:0`;
  icon.onclick = e => { e.stopPropagation(); pressed('delete'); slot.querySelector('img').remove(); }; box.querySelector('.mask-x1y2').append(icon); })();</script>"""
# A button of the 竖封面3:4 slot's own over the right part of it, as a remove icon on a cover card could be, sending word of its own press.
IN_THE_SLOT = """<script>(() => { const slot = document.querySelectorAll('.cover-b')[1], remove = document.createElement('button');
  remove.className = 'del-x1'; remove.style.cssText = 'position:absolute;right:0;top:0;width:40%;height:100%;padding:0;border:0';
  remove.onclick = e => { e.stopPropagation(); pressed('remove'); }; slot.style.position = 'relative'; slot.append(remove); })();</script>"""
# The 竖封面3:4 slot inside a card of its cover box that holds it, as a cover card can, with what the script given puts in the card or on it.
IN_A_CARD = """<script>(() => { const slot = document.querySelectorAll('.cover-b')[1], card = document.createElement('div');
  card.className = 'card-t1'; card.style.cssText = 'position:relative;display:inline-block'; slot.replaceWith(card); card.append(slot); %s })();</script>"""
# A card a keyboard can reach, with a mask over the slot whose press opens the cover window; and a card with a button's role whose own press
# opens it, with a badge over all of the slot. Neither card lies over the slot, and a press on the slot reaches it anyway.
MASK_IN_A_CARD = IN_A_CARD % """card.tabIndex = 0; const mask = document.createElement('div'); mask.className = 'mask-m1';
  mask.style.cssText = 'position:absolute;inset:0'; mask.onclick = () => pick('mask'); card.append(mask);"""
BADGE_IN_A_BUTTON = IN_A_CARD % """card.setAttribute('role', 'button'); card.onclick = () => pick('card'); slot.onclick = null;
  const badge = document.createElement('span'); badge.className = 'badge-z1'; badge.style.cssText = 'position:absolute;inset:0'; card.append(badge);"""
# The 竖封面3:4 slot taking no pointer events, with nothing over it, and a press on its cover box itself sending word.
NO_POINTER = """<script>(() => { const slot = document.querySelectorAll('.cover-b')[1], box = slot.parentElement; slot.style.pointerEvents = 'none';
  box.addEventListener('click', e => { if (e.target === box) pressed('box'); }); })();</script>"""
# The 竖封面3:4 slot's own press sending word and opening no window.
NO_WINDOW = """<script>document.querySelectorAll('.cover-b')[1].onclick = () => pressed('tall');</script>"""
# Once Escape reaches the description: the 竖封面3:4 slot taken off the page for three seconds, longer than a look waits for it here, as a
# redraw could; or a second cover- element in its cover box, which leaves the slot's own locator two things to choose from.
TAKEN_AWAY = """<script>document.querySelector('[contenteditable=true]').addEventListener('keydown', e => { if (e.key !== 'Escape') return;
  const slot = document.querySelectorAll('.cover-b')[1], box = slot.parentElement; slot.remove(); setTimeout(() => box.append(slot), 3000); });</script>"""
SECOND_SLOT = """<script>document.querySelector('[contenteditable=true]').addEventListener('keydown', e => { if (e.key === 'Escape')
  document.querySelectorAll('[class^="coverControl-"]')[1].append(Object.assign(document.createElement('div'), {className: 'cover-c'})); });</script>"""
# What the page can change just before a press through MASK_OPENS: a button in the mask's place, and a layer of the page's own over the slot,
# each sending word of its own press. patchright runs these in a world of its own, so the presses are set as the page's own attributes.
BUTTON_FOR_THE_MASK = """() => { const mask = document.querySelector('.mask-x1y2'), button = document.createElement('button'); button.className = 'edit-b1';
  button.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%'; button.setAttribute('onclick', "pressed('button')"); mask.replaceWith(button); }"""
LATE_LAYER = """() => { const r = document.querySelectorAll('.cover-b')[1].getBoundingClientRect(), layer = document.createElement('div'); layer.className = 'late-l1';
  layer.style.cssText = `position:fixed;z-index:9;left:${r.left - 4}px;top:${r.top - 4}px;width:${r.width + 8}px;height:${r.height + 8}px`;
  layer.setAttribute('onclick', "pressed('late')"); document.body.append(layer); }"""
# A layer of the page's own over the 竖封面3:4 slot, outside both cover boxes and hung off the page's body, sending word of its own press: over
# all of the slot, or over the part of it left of the share of its width given. With goes, it goes that many milliseconds after Escape
# reaches the description, as a layer Douyin shows while it readies the cover area might.
LAID_OVER = """<script>(() => { const slot = document.querySelectorAll('.cover-b')[%(slot)s], r = slot.getBoundingClientRect(), layer = document.createElement('div');
  layer.className = 'toast-q9 名字'; layer.onclick = () => pressed('layer'); %(words)s
  layer.style.cssText = `position:absolute;z-index:5;left:${r.left + scrollX - 4}px;top:${r.top + scrollY - 4}px;width:${r.width * %(across)s + 4}px;height:${r.height + 8}px`;
  document.body.append(layer);
  document.querySelector('[contenteditable=true]').addEventListener('keydown', e => { if (e.key === 'Escape' && %(goes)s) {
    window.pending = 1; setTimeout(() => { layer.remove(); window.pending = 0; }, %(after)s); } }); })();</script>"""


def laid_over(across=1, words="", goes=None, slot=1):
    """LAID_OVER over the 竖封面3:4 slot, or with slot 0, over the 横封面4:3 one."""
    return LAID_OVER % {"across": across, "words": words, "goes": "false" if goes is None else "true", "after": goes or 0, "slot": slot}


# Words a layer can hold that name people, as Douyin's description lists do: in its text, its title, its label and its data. From a review:
# stderr's lines are JSON in ASCII, where words in Chinese can't be found as written, so one name is in ASCII.
NAMES = """layer.title = '张三的私信'; layer.setAttribute('aria-label', '@李四 王五'); layer.dataset.owner = '赵六'; layer.dataset.user = 'zhang_san_88';
  layer.textContent = '@赵六 给你发了一条私信';"""
SECRETS = ("张三", "李四", "王五", "赵六", "私信", "名字", "zhang_san_88")
# HOVER_MASK shown by a script on the 竖封面3:4 cover box's mouseenter, a moment later, and hidden on its mouseleave.
HOVER_MASK_BY_SCRIPT = """<div class="hover-m2" style="position:absolute;inset:0;display:none" onclick="pick('mask')"></div>
  <script>(() => { const mask = document.currentScript.previousElementSibling, box = mask.parentElement;
  box.addEventListener('mouseenter', () => { window.pending = 1; setTimeout(() => { mask.style.display = 'block'; window.pending = 0; }, 40); });
  box.addEventListener('mouseleave', () => { mask.style.display = 'none'; }); })();</script>"""
# An icon of HOVER_MASK's own at the 竖封面3:4 slot's centre, the bare spot nearest it, that shows only while the pointer is over the mask, with
# a press of its own that takes the slot's picture away, as a remove icon on a card could: an <i>, or a <button>, the tag given.
ICON_ON_HOVER = """<style>.hover-m1 .icon-h1 { display: none } .hover-m1:hover .icon-h1 { display: block }</style>
  <script>(() => { const box = document.currentScript.parentElement, slot = box.querySelector('.cover-b'), mask = box.querySelector('.hover-m1');
  const s = slot.getBoundingClientRect(), b = box.getBoundingClientRect(), icon = document.createElement('%s'); icon.className = 'icon-h1';
  icon.style.cssText = `position:absolute;left:${s.left - b.left + s.width / 2 - 5}px;top:${s.top - b.top + s.height / 2 - 5}px;width:10px;height:10px;padding:0;border:0`;
  icon.onclick = e => { e.stopPropagation(); pressed('delete'); slot.querySelector('img').remove(); }; mask.append(icon); })();</script>"""
# An icon of HOVER_MASK's own that follows the pointer while it is over the mask, as a custom cursor or a tip might, with a press of its own:
# wherever the pointer goes on the mask, the icon is what lies under it.
ICON_THAT_FOLLOWS = """<script>(() => { const box = document.currentScript.parentElement, mask = box.querySelector('.hover-m1'), icon = document.createElement('i');
  icon.className = 'icon-f1'; icon.style.cssText = 'position:absolute;width:6px;height:6px;display:none'; icon.onclick = e => { e.stopPropagation(); pressed('delete'); };
  mask.append(icon); mask.addEventListener('mousemove', e => { const r = mask.getBoundingClientRect();
    icon.style.left = (e.clientX - r.left - 3) + 'px'; icon.style.top = (e.clientY - r.top - 3) + 'px'; icon.style.display = 'block'; });
  mask.addEventListener('mouseleave', () => { icon.style.display = 'none'; }); })();</script>"""
# IN_THE_SLOT's button showing only while the pointer is over the slot: by the stylesheet, which needs the hidden state in the stylesheet too,
# or by a script on the slot's mouseenter, a moment later, and its mouseleave.
HOVER_IN_THE_SLOT = IN_THE_SLOT + """<style>.del-x1 { display: none } .cover-b:hover > .del-x1 { display: block }</style>"""
HOVER_IN_THE_SLOT_BY_SCRIPT = IN_THE_SLOT.replace("slot.append(remove);", """remove.style.display = 'none'; slot.append(remove);
  slot.addEventListener('mouseenter', () => { window.pending = 1; setTimeout(() => { remove.style.display = 'block'; window.pending = 0; }, 40); });
  slot.addEventListener('mouseleave', () => { remove.style.display = 'none'; });""")
# IN_THE_SLOT's button hidden until a test shows it (LATER_SHOWN), as one that shows a little after the pointer has settled might.
LATER_IN_THE_SLOT = IN_THE_SLOT.replace("slot.append(remove);", "remove.style.display = 'none'; slot.append(remove);")
LATER_SHOWN = "() => { document.querySelector('.del-x1').style.display = 'block'; }"
# From a review of a490e80: a replace that finds nothing leaves a button that is always there, and the tests of one that shows later still passed.
assert IN_THE_SLOT not in (HOVER_IN_THE_SLOT_BY_SCRIPT, LATER_IN_THE_SLOT)
# A mask of the 竖封面3:4 cover box over all of the slot that a listener the page put on its window first, for pointerup, brings up during
# the slot's first trial press, which the browser lets pass. Its press opens the cover window.
BETWEEN_PRESSES = """<div class="mask-b1" style="position:absolute;inset:0;display:none" onclick="pick('mask')"></div>
  <script>(() => { const mask = document.currentScript.previousElementSibling;
  window.addEventListener('pointerup', () => { mask.style.display = 'block'; }, {capture: true, once: true}); })();</script>"""
# A tip of the page's own outside both cover boxes, hung off the page's body, that shows while the pointer is over a slot's cover box, the
# 竖封面3:4 one unless another is given, or over the tip itself, as a neighbour's hover tip might, sending word of its own press: over a band
# across the slot's centre, or, with full, over all of the slot.
HOVER_TIP = """<script>(() => { const slot = document.querySelectorAll('.cover-b')[%(slot)s], box = slot.parentElement, r = slot.getBoundingClientRect(), tip = document.createElement('div');
  const [from, high] = %(full)s ? [-4, r.height + 8] : [r.height * 0.35, r.height * 0.3];
  tip.className = 'tip-h1'; tip.onclick = () => pressed('tip');
  tip.style.cssText = `position:absolute;z-index:5;display:none;left:${r.left + scrollX - 4}px;top:${r.top + scrollY + from}px;width:${r.width + 8}px;height:${high}px`;
  document.body.append(tip); let over = 0;
  const show = () => { over += 1; tip.style.display = 'block'; };
  const hide = () => { over -= 1; window.pending = 1; setTimeout(() => { if (over <= 0) tip.style.display = 'none'; window.pending = 0; }, 30); };
  for (const e of [box, tip]) { e.addEventListener('mouseenter', show); e.addEventListener('mouseleave', hide); } })();</script>"""


def hover_tip(full=False, slot=1):
    return HOVER_TIP % {"slot": slot, "full": "true" if full else "false"}


# How long the pointer had been held still at each press's point when the press came, and whether the press was on a slot, or anywhere in
# a cover box, as each pointerdown sends it, seen by a listener the page put on its window first.
POINTER_HELD = """<script>(() => { let at = null, since = 0;
  window.addEventListener('pointermove', e => { if (!at || at[0] !== e.clientX || at[1] !== e.clientY) { at = [e.clientX, e.clientY]; since = performance.now(); } }, true);
  window.addEventListener('pointerdown', e => { const held = at && at[0] === e.clientX && at[1] === e.clientY ? performance.now() - since : 0;
    fetch('/write', {method: 'POST', body: JSON.stringify({word: 'pointer', held_ms: held, slot: !!e.target.closest('.cover-b'),
      box: !!e.target.closest('[class^="coverControl-"]')})}); }, true); })();</script>"""
# 完成 that leaves the cover window open, as Douyin's did in the background browser.
DONE_STAYS = """<script>document.querySelector('#d button').removeAttribute('onclick');</script>"""
# Once Escape reaches the description, a second cover- element in the 横封面4:3 cover box, which leaves its slot's locator two to choose from.
SECOND_WIDE_SLOT = """<script>document.querySelector('[contenteditable=true]').addEventListener('keydown', e => { if (e.key === 'Escape')
  document.querySelectorAll('[class^="coverControl-"]')[0].append(Object.assign(document.createElement('div'), {className: 'cover-c'})); });</script>"""
# The one sentence a stop gives on whether the page was drawing. From a review of a490e80, it no longer says whether the page had focus, which
# patchright emulates on every page it drives.
DRAWING_SAID = (r"The page (was visible|was hidden|was still being prerendered|was neither visible nor hidden|didn't say whether it was visible), "
                r"(drew a frame within a second|drew no frame within a second|didn't say whether it drew a frame) and "
                r"(had \d+ animations? running|didn't say how many animations were running)\.")
DRAWING_KINDS = {"visibility": {"visible", "hidden", "prerender", "other", "unknown"}, "frame_within_a_second": {True, False, "unknown"}}
# From a review of a490e80, things a review's probes found that the tests could not see before. A page as long as Douyin's form, which can
# scroll, so that Playwright's own press, which tries again after a refusal by scrolling first, has somewhere to go.
SPACER = """<div style="height:3000px"></div>"""
# Cover slots that lift a little under the pointer, as cards often do.
LIFTS = """<style>.cover-b:hover { transform: translateY(-3px) }</style>"""
# A layer of the page's own, fixed in the window and outside both cover boxes, that a listener the page put on its window first, for
# pointerup, lays over the 竖封面3:4 slot during its first trial press, sending word of its own press.
FIXED_BETWEEN = """<script>window.addEventListener('pointerup', () => {
  const r = document.querySelectorAll('.cover-b')[1].getBoundingClientRect(), layer = document.createElement('div'); layer.className = 'toast-f1';
  layer.style.cssText = `position:fixed;z-index:9;left:${r.left - 4}px;top:${r.top - 4}px;width:${r.width + 8}px;height:${r.height + 8}px`;
  layer.onclick = () => pressed('toast'); document.body.append(layer); }, {capture: true, once: true});</script>"""
# The same listener hiding the 竖封面3:4 slot instead.
HIDES_BETWEEN = """<script>window.addEventListener('pointerup', () => { document.querySelectorAll('.cover-b')[1].style.visibility = 'hidden'; },
  {capture: true, once: true});</script>"""
# What comes over the 竖封面3:4 slot by itself, not with the pointer, as a layer Douyin shows while it readies the cover area might, and goes
# the given milliseconds later, sending word of its own press and opening nothing: a mask of the slot's own cover box over all of the slot,
# or a toast of the page's own over all of it, outside both cover boxes. A test brings it, in the page's own world, so that its timer counts.
ARRIVES = """([inBox, goes]) => { const slot = document.querySelectorAll('.cover-b')[1], box = slot.parentElement, r = slot.getBoundingClientRect();
  const layer = document.createElement('div'); layer.className = inBox ? 'ready-r1' : 'toast-t1'; layer.onclick = () => pressed('arrived');
  layer.style.cssText = inBox ? 'position:absolute;inset:0' : `position:absolute;z-index:5;left:${r.left + scrollX - 4}px;top:${r.top + scrollY - 4}px;width:${r.width + 8}px;height:${r.height + 8}px`;
  (inBox ? box : document.body).append(layer); window.pending = 1; setTimeout(() => { layer.remove(); window.pending = 0; }, goes); }"""
# An icon of MASK_OPENS's own over all of it, as a remove icon that shows a little after the pointer has settled might, with a press of its own.
ICON_LATER = """() => { const mask = document.querySelector('.mask-x1y2'), icon = document.createElement('i'); icon.className = 'icon-l1';
  icon.style.cssText = 'position:absolute;inset:0'; icon.setAttribute('onclick', "event.stopPropagation(); pressed('delete')"); mask.append(icon); }"""
# From the final check of 073cf63, what comes on top part of the way through a press: MASK_OPENS, whose own mousedown lays a pressed look of
# its own over all of it, as a card's pressed state might; and a layer of the page's own, fixed in the window and outside both cover boxes,
# that the 竖封面3:4 slot's own mousedown lays over it, sending word of its own press. Each comes after the press's pointerdown and mousedown.
PRESSED_LOOK = MASK_OPENS + """<script>(() => { const mask = document.currentScript.previousElementSibling;
  mask.addEventListener('mousedown', () => { const look = document.createElement('span'); look.className = 'pressed-p1';
    look.style.cssText = 'position:absolute;inset:0;display:block'; mask.append(look); }); })();</script>"""
LAID_AT_MOUSEDOWN = """<script>(() => { const slot = document.querySelectorAll('.cover-b')[1];
  slot.addEventListener('mousedown', () => { const r = slot.getBoundingClientRect(), layer = document.createElement('div'); layer.className = 'toast-m1';
    layer.style.cssText = `position:fixed;z-index:9;left:${r.left - 4}px;top:${r.top - 4}px;width:${r.width + 8}px;height:${r.height + 8}px`;
    """ + NAMES + """ layer.onclick = () => pressed('toast'); document.body.append(layer); }, {once: true}); })();</script>"""
# From a review of the change after 073cf63: MASK_OPENS made a control by its own mousedown, as a page could, so that what was being pressed
# no longer passes the look; and MASK_OPENS with a pressed look that draws it in a pixel on every side while the button is down, as cards
# often have, so that it no longer covers all of the slot during its press. The mask is as wide as the whole cover box, so a shrink by a share
# of its size would take it off the slot altogether, and its inline inset would win over the rule without !important.
TURNS_CONTROL = MASK_OPENS + """<script>(() => { const mask = document.currentScript.previousElementSibling;
  mask.addEventListener('mousedown', () => { mask.tabIndex = 0; }); })();</script>"""
SHRINKS_WHEN_PRESSED = MASK_OPENS + """<style>.mask-x1y2:active { inset: 1px !important }</style>"""
# From the field run on a16202b: what lay over the 竖封面3:4 slot's centre came from the description, a div with no class inside the box's own
# wrapper (publish-mention-wrapper), over about a tenth of the slot, and it stayed. Here the description box goes into such a wrapper, and a
# list of the wrapper's own lays a row with names on it over a band through the slot's middle, a tenth of its height, sending word of its own
# press. And the slot with a small mark of its own at its centre, as an edit mark on a cover card might be, so that a press at its centre
# would reach the mark, over a part of its own given: a picture over all of it, shown as a background or as an img, as a card's preview
# might be, whose press is the slot's own; or, from a review, a button there, two pictures each over half of it, or a plain part over all
# of it, each with a press of its own.
MENTION_BAND = """<script>(() => { const slot = document.querySelectorAll('.cover-b')[1], r = slot.getBoundingClientRect();
  const words = document.querySelector('[contenteditable=true]'), wrapper = document.createElement('div'); wrapper.className = 'publish-mention-wrapper-T1';
  words.replaceWith(wrapper); wrapper.append(words);
  const list = document.createElement('div'), row = document.createElement('div');
  list.style.cssText = `position:absolute;z-index:5;left:${r.left + scrollX - 6}px;top:${r.top + scrollY + r.height * 0.45}px;width:${r.width + 12}px`;
  row.style.cssText = `height:${r.height * 0.11}px;overflow:hidden;cursor:pointer`; row.onclick = () => pressed('mention');
  """ + NAMES.replace("layer", "row") + """ list.append(row); wrapper.append(list); })();</script>"""
SLOT_PART = """<script>(() => { const slot = document.querySelectorAll('.cover-b')[1]; slot.style.position = 'relative';
  const part = (tag, css, press) => { const e = document.createElement(tag); e.className = 'part-' + tag; e.style.cssText = 'position:absolute;padding:0;border:0;' + css;
    if (press) e.onclick = event => { event.stopPropagation(); pressed(press); }; slot.append(e); return e; };
  %s
  const mark = document.createElement('i'); mark.className = 'mark-m1'; mark.style.cssText = 'position:absolute;left:10px;top:15px;width:10px;height:10px';
  slot.append(mark); })();</script>"""
GIF = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
SLOT_PICTURE = SLOT_PART % f"part('div', 'inset:0;background-image:url({GIF})');"
SLOT_IMG = SLOT_PART % f"part('img', 'left:0;top:0;width:100%;height:100%').src = '{GIF}';"
SLOT_BUTTON = SLOT_PART % f"part('button', 'left:0;top:0;width:100%;height:100%;background-image:url({GIF})', 'button');"
# From a final check: SLOT_PICTURE's picture made a control by its own mousedown, as a page could, so that what was being pressed no longer
# passes the look.
PICTURE_TURNS_CONTROL = SLOT_PART % (f"part('div', 'inset:0;background-image:url({GIF})').addEventListener('mousedown', e => {{ e.currentTarget.tabIndex = 0; }});")
SLOT_HALVES = SLOT_PART % (f"part('div', 'left:0;top:0;width:50%;height:100%;background-image:url({GIF})', 'left');"
                           f" part('div', 'left:50%;top:0;width:50%;height:100%;background-image:url({GIF})', 'right');")
SLOT_PLAIN = SLOT_PART % "part('div', 'inset:0', 'plain');"
# A part of the 竖封面3:4 slot's own over all of it but under its mark, showing a picture, that a test brings just after a spot's trial press,
# as a replace mask that shows a little after the pointer has settled might, with a press of its own. A press at the centre would still
# reach the mark, so only the guard's hold on the very part the look found keeps the press from it.
REPLACE_LATER = """() => { const slot = document.querySelectorAll('.cover-b')[1], mask = document.createElement('div'); mask.className = 'replace-r1';
  mask.style.cssText = 'position:absolute;inset:0;background-image:url(""" + GIF + """)'; mask.setAttribute('onclick', "event.stopPropagation(); pressed('replace')");
  slot.insertBefore(mask, slot.querySelector('.mark-m1')); }"""
# From the field runs on a16202b and fc60a30: once a long description was in, something of the description's lay over all of the 竖封面3:4
# slot. Here the box goes into a wrapper of its own, and once its words run past 30 characters a part of that wrapper's own lies over all of
# the slot, as long words spilling down over the cover area might, sending word of its own press.
LONG_WORDS_COVER = """<script>(() => { const words = document.querySelector('[contenteditable=true]'), wrapper = document.createElement('div');
  wrapper.className = 'publish-mention-wrapper-T2'; words.replaceWith(wrapper); wrapper.append(words);
  const spill = () => { const shown = document.querySelector('.spill-s1');
    if (words.innerText.trim().length <= 30) { if (shown) shown.remove(); return; }
    if (shown) return;
    const r = document.querySelectorAll('.cover-b')[1].getBoundingClientRect(), part = document.createElement('div'); part.className = 'spill-s1';
    part.style.cssText = `position:absolute;z-index:5;left:${r.left + scrollX - 4}px;top:${r.top + scrollY - 4}px;width:${r.width + 8}px;height:${r.height + 8}px`;
    part.onclick = () => pressed('spill'); wrapper.append(part); fetch('/write', {method: 'POST', body: JSON.stringify({word: 'spilled'})}); };
  new MutationObserver(spill).observe(words, {childList: true, subtree: true, characterData: true}); spill(); })();</script>"""
LONG_WORDS = "A description that runs on well past thirty characters, as 肥鱼抢麦's did."
# A second cover- element in the 竖封面3:4 cover box, brought by a test, which leaves the slot's own locator two things to choose from, so that
# every look after it fails.
SECOND_TALL_SLOT = """() => document.querySelectorAll('[class^="coverControl-"]')[1].append(Object.assign(document.createElement('div'), {className: 'cover-c'}))"""
# A slot with borders of two widths, at fractional places and sizes, part of it below the window, and every move of the pointer the page sees.
BORDERED = """<!doctype html><meta charset="utf-8"><body style="margin:0"><div style="height:%spx"></div>
  <div class="coverControl-a" style="position:relative;margin-left:10.29px;width:max-content">
  <div class="cover-b" style="width:41.3px;height:29.7px;border:3px solid #333;border-left-width:5px"></div></div><div style="height:2000px"></div>
  <script>window.moves = []; window.addEventListener('pointermove', e => window.moves.push([e.clientX, e.clientY]), true);</script>"""
# The cursor at the end of the box's words, as typing leaves it.
CURSOR_AFTER_TYPING = "e => { e.focus(); getSelection().selectAllChildren(e); getSelection().collapseToEnd(); }"


class BothCoversTests(Base):
    """From a field run: a 1080x1920 video's 横封面4:3 slot had something over it, and its press ran out after ten seconds and stopped the post,
    with nothing published. A video taller than it is wide now gets only its 竖封面3:4 cover. From a review, a 4:3 cover is left out only while
    a 竖封面3:4 one goes on: on such a video, or on one whose size can't be read when its slot is covered or turned off. Anything else that
    refuses a press stops before anything goes out. From field runs on 647e002 and 7fa6c10 and round 1's final check, a slot something lies
    over is pressed only where a look made with the pointer already on that point found it could be: on the slot itself, or through a bare
    part of its own cover box as big as itself. What came with the pointer is pressed through at once, what stays is waited for first, and
    nothing else over the slot is ever pressed. From a review of a490e80, each press is held to that look by a guard at the moment it is
    made, with the pointer where the look was made."""
    async def asyncSetUp(self):
        await super().asyncSetUp()
        # Long enough for the driver to have looked at what lies over a slot, which a busy test machine took more than half a second to do.
        # The wait for a covered slot to come open is a second here, against ninety on Douyin, and what the pointer brings up gets 150
        # milliseconds, against 500, which a quiet page here ends sooner still.
        for target, name, value in ((browser_edits, "DOUYIN_COVER_SETTLE_SECONDS", 0), (browser_forms, "DOUYIN_SLOT_MS", 2000), (browser_forms, "DOUYIN_COVERED_SECONDS", 1),
                                    (browser_forms, "DOUYIN_SETTLE_MS", 150)):
            kept = patch.object(target, name, value)
            kept.start()
            self.addCleanup(kept.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.image = Path(folder.name) / "cover.jpg"
        self.image.write_bytes(b"synthetic")
        self.tx = StubTx()
        self.tx.cover = lambda role="landscape": str(self.image)

    async def post(self, wide="", tall="", extra="", focused=False, words="Words"):
        await self.open(form().replace(">Words</div>", f">{words}</div>") + BOTH_COVERS % (wide, tall) + extra)
        self.tx.options["description"] = words
        if focused:
            await self.page.locator("[contenteditable=true]").evaluate(CURSOR_AFTER_TYPING)
        with patch.object(browser_forms, "finish", new=AsyncMock(return_value={"state": "SUBMITTED_UNVERIFIED"})):
            return await browser_forms.finish_douyin_form(self.page, self.tx, {})

    def pressed(self):
        return [write["slot"] for write in self.writes if write["word"] == "cover"]

    def went_out(self):
        return [write["word"] for write in self.writes if write["word"] not in ("cover", "escape", "pointer")]

    def escapes(self):
        return sum(write["word"] == "escape" for write in self.writes)

    def held_up_to_the_press(self):
        """How long the pointer had been held still at each press the page saw in a cover box, its trial presses and its press (POINTER_HELD),
        leaving out the cover window's and the form's own presses after it."""
        held = [write["held_ms"] for write in self.writes if write["word"] == "pointer" and write["box"]]
        self.assertTrue(held)
        return held

    def portrait_only(self):
        self.tx.cover = lambda role="landscape": str(self.image) if role == "portrait" else None

    async def stopped(self, **post):
        """A post whose 竖封面3:4 slot stops it: nothing goes out, and its cover's own key is left as it was."""
        with self.assertRaises(VpubError) as caught:
            await self.post(**post)
        self.assertEqual("cover_blocked", caught.exception.code)
        self.assertEqual([], self.went_out())
        self.assertNotIn("portrait_cover", self.tx.receipt)
        return caught.exception.message

    async def first_looks(self, **post):
        """Whether the description's list was over everything as each slot was first tried."""
        shown, real = [], browser_forms.douyin_slot_refuses

        async def looked(page, editor, slot):
            shown.append(await page.locator("#topics").is_visible())
            return await real(page, editor, slot)
        with patch.object(browser_forms, "douyin_slot_refuses", looked):
            await self.post(**post)
        return shown

    async def test_a_landscape_video_gets_both_covers_as_before(self):
        for size in ((1920, 1080), (1080, 1080), None):
            with self.subTest(size=size):
                self.tx.size, self.tx.receipt, self.writes[:] = size, {}, []
                await self.post()
                self.assertEqual(["wide", "tall"], self.pressed())
                self.assertEqual(("UI_ACCEPTED", "UI_ACCEPTED"), (self.tx.receipt["landscape_cover"]["state"], self.tx.receipt["portrait_cover"]["state"]))
                self.assertEqual(["publish"], self.went_out())

    async def test_a_portrait_video_gets_only_its_portrait_cover_and_its_4_3_slot_is_never_pressed(self):
        """As in that field run, something lies over the 4:3 slot, and the post goes out without anything waiting on it."""
        self.tx.size = (1080, 1920)
        await self.post(wide=OVER_IT)
        self.assertEqual(["tall"], self.pressed())
        skipped = self.tx.receipt["landscape_cover"]
        self.assertEqual(("SKIPPED", [1080, 1920]), (skipped["state"], skipped["video_size"]))
        self.assertNotIn("refused", skipped)
        self.assertIn("The video is 1080x1920, taller than it is wide, so Douyin's 横封面4:3 slot wasn't pressed", skipped["note"])
        self.assertEqual("UI_ACCEPTED", self.tx.receipt["portrait_cover"]["state"])
        self.assertEqual(["publish"], self.went_out())

    async def test_a_4_3_slot_covered_or_turned_off_on_a_video_whose_size_cant_be_read_is_left_out_and_the_record_says_why(self):
        """From a review: the record said the description's list had been closed, when Escape had gone to the description only for a slot with
        something over it. It says only what was tried, and each Escape that reached the description is counted here. A slot something lies
        over is waited for first, and its record says what lay over it and for how long. It is never pressed through, since it can be left out."""
        unread = "The video's size couldn't be read from its file, and Douyin's 横封面4:3 slot "
        for wide, refusal, note, escapes in (
                (OVER_IT, "in the way: div, intercepts pointer events", re.escape(unread + "still couldn't take a press after Escape was pressed in the description "
                 "at the end of its words and a wait of ") + r"\d+ seconds?: the last thing seen over its centre was div in the slot's own cover box, absolute, "
                 r"over all of the slot and \d+% of the window\. It stayed with the pointer away from the slot\. "
                 + re.escape("So the planned 4:3 cover wasn't set and nothing was pressed there."), 1),
                (TURNED_OFF, "element is not enabled", re.escape(unread + "was turned off, so the planned 4:3 cover wasn't set and nothing was pressed there."), 0)):
            with self.subTest(refusal=refusal):
                self.tx.receipt, self.writes[:] = {}, []
                await self.post(wide=wide, extra=ESCAPES)
                self.assertEqual(["tall"], self.pressed())
                skipped = self.tx.receipt["landscape_cover"]
                self.assertEqual(("SKIPPED", None), (skipped["state"], skipped["video_size"]))
                self.assertIn(refusal, skipped["refused"])
                self.assertRegex(skipped["note"], "^" + note + "$")
                self.assertEqual(escapes, self.escapes())
                self.assertEqual("UI_ACCEPTED", self.tx.receipt["portrait_cover"]["state"])
                self.assertEqual(["publish"], self.went_out())
                if wide == OVER_IT:
                    over_it = skipped["over_it"]
                    self.assertEqual(("div", "cover box", False, None), (over_it["tag"], over_it["place"], over_it["control"], skipped["pressed"]))
                    self.assertEqual((False, "div"), (skipped["came_with_pointer"], skipped["over_it_away"]["tag"]))
                    self.assertGreaterEqual(skipped["waited_seconds"], browser_forms.DOUYIN_COVERED_SECONDS)

    async def blocked(self, wide, says):
        self.tx.receipt, self.writes[:] = {}, []
        with self.assertRaises(VpubError) as caught:
            await self.post(wide=wide)
        self.assertEqual("cover_blocked", caught.exception.code)
        self.assertIn("Douyin's 横封面4:3 slot couldn't take a press, so nothing was published. " + says, caught.exception.message)
        self.assertEqual(([], []), (self.pressed(), self.went_out()))
        self.assertNotIn("landscape_cover", self.tx.receipt)
        return caught.exception.message

    async def test_on_a_video_at_least_as_wide_as_it_is_tall_a_4_3_slot_that_cant_take_a_press_stops_before_anything_goes_out(self):
        """From a review: any refusal let such a video go out without its 4:3 cover, which vpub edit can't set afterwards: a second slot after
        a change to the page's layout, or a slot still hidden ten seconds on, as well as one covered or turned off. What covers it here is a
        button, which is never pressed: a part of the cover box that is no control, as OVER_IT is, is now pressed through first."""
        for size, wide in (((1920, 1080), A_BUTTON), ((1920, 1080), HIDDEN), ((1920, 1080), TWO_SLOTS), ((1080, 1080), TURNED_OFF)):
            with self.subTest(size=size, wide=wide):
                self.tx.size = size
                await self.blocked(wide, f"The video is {size[0]}x{size[1]}, at least as wide as it is tall, so its 4:3 cover is never left out. Browser command failed")

    async def test_on_a_video_whose_size_cant_be_read_any_other_refusal_stops_before_anything_goes_out(self):
        for wide, refusal in ((HIDDEN, "element is not visible"), (TWO_SLOTS, "strict mode violation")):
            with self.subTest(refusal=refusal):
                said = await self.blocked(wide, "A 4:3 cover is left out only when something lies over its slot or the slot is turned off, and the browser saw neither.")
                self.assertIn(refusal, said)

    async def test_a_4_3_cover_that_is_the_only_one_is_never_left_out(self):
        """From a review: vpub resume --cover gives an attempt its one cover in the 4:3 role, and a portrait video's post then went out with none."""
        both = {"landscape": {"path": "wide.jpg", "sha256": "0"}, "portrait": {"path": "tall.jpg", "sha256": "0"}}
        plan = cli.with_cover({"confirmation": "c", "video_sha256": "0" * 64, "options": {"title": TITLE}, "covers": {"douyin": both}}, "douyin", str(self.image))
        self.tx.cover = browser_backends.Transaction("douyin", OWNER, plan).cover
        self.assertEqual((str(self.image.resolve()), None), (self.tx.cover("landscape"), self.tx.cover("portrait")))
        self.tx.size = (1080, 1920)
        await self.post()
        self.assertEqual(["wide"], self.pressed())
        self.assertEqual("UI_ACCEPTED", self.tx.receipt["landscape_cover"]["state"])
        self.assertEqual(["publish"], self.went_out())
        for size in ((1080, 1920), None):
            with self.subTest(size=size):
                self.tx.size = size
                await self.blocked(A_BUTTON, "It's the only cover this attempt has, so it isn't left out. Browser command failed")

    async def test_a_skipped_slot_is_looked_at_again_and_a_cover_already_on_is_left(self):
        self.tx.receipt = {"landscape_cover": {"state": "SKIPPED"}, "portrait_cover": {"state": "UI_ACCEPTED", "remote_pixels_verified": False}}
        await self.post()
        self.assertEqual(["wide"], self.pressed())
        self.assertEqual("UI_ACCEPTED", self.tx.receipt["landscape_cover"]["state"])

    async def test_a_list_the_description_left_open_is_closed_before_any_slot_is_pressed(self):
        """From field runs: the #tag suggestions stayed over the cover slots and took their press. From a review, this held without the close
        before the covers, as each slot's own second try closed the list too, so the list is looked for as each slot is first tried."""
        self.assertEqual([False, False], await self.first_looks(extra=OPEN_LIST, focused=True))
        self.assertEqual(["wide", "tall"], self.pressed())
        self.assertEqual(["publish"], self.went_out())

    async def test_a_list_left_open_without_the_cursor_in_the_box_is_closed_from_the_end_of_its_words(self):
        """This copy's list closes only for an Escape with the cursor at the end of the words, as Douyin's is for the #topic it is at the end of."""
        await self.post(extra=OPEN_LIST)
        self.assertEqual(["wide", "tall"], self.pressed())
        self.assertEqual(["publish"], self.went_out())

    async def test_closing_a_list_leaves_the_words_as_they_were_and_the_cursor_out_of_the_box(self):
        for focused in (True, False):
            with self.subTest(focused=focused):
                await self.open(form() + OPEN_LIST)
                editor = self.page.locator("[contenteditable=true]")
                if focused:
                    await editor.evaluate(CURSOR_AFTER_TYPING)
                await browser_backends.douyin_leave_caption(self.page, editor, from_the_end=not focused)
                self.assertTrue(await self.page.locator("#topics").is_hidden())
                self.assertEqual("Words", (await editor.inner_text()).strip())
                self.assertFalse(await editor.evaluate("e => e.contains(document.activeElement)"))

    async def test_leaving_the_box_never_puts_the_cursor_back_where_a_list_opens(self):
        """From a review: an Escape that left the box had the cursor put back at the end of its words, the end of its last #topic, where Douyin
        opens its list, just before the box was left. A list that opened again there would have lain over the cover slots."""
        for focused in (True, False):
            with self.subTest(focused=focused):
                self.tx.receipt, self.writes[:] = {}, []
                await self.post(extra=REOPENS, focused=focused, words="Words #Tag")
                self.assertEqual(["wide", "tall"], self.pressed())
                self.assertEqual(["publish"], self.went_out())

    async def test_a_portrait_slot_that_cannot_take_a_press_stops_before_anything_goes_out(self):
        """OVER_IT is a part of the slot's own cover box and no control, so once the wait is over the slot is pressed through it, once. Its own
        press opens no cover window, so the post stops there, nothing goes out and nothing is pressed again."""
        self.portrait_only()
        with patch.object(browser_forms, "DOUYIN_WINDOW_MS", 1000):
            said = await self.stopped(tall=OVER_IT)
        self.assertIn("Douyin's 竖封面3:4 slot was pressed once through its own cover box, and Douyin's cover window didn't open, so nothing was "
                      "published and nothing was pressed again. After a wait of ", said)
        self.assertIn(", the last thing seen over its centre was div in the slot's own cover box, absolute, over all of the slot and ", said)
        self.assertEqual(["over"], self.pressed())
        covered = self.tx.receipt["cover_slot_covered"]
        self.assertEqual(("portrait", "竖封面3:4", "through its own cover box", True), (covered["role"], covered["slot"], covered["pressed"], covered["pressed_once"]))
        self.assertEqual({"x": 15, "y": 20}, covered["at"])  # Nothing smaller lies on it, so it is pressed at the slot's centre.

    async def test_a_slot_that_takes_its_first_trial_press_is_pressed_as_before_with_no_look_or_wait(self):
        """As both of 阿哈摇's slots did in its first run: nothing more is looked at, waited for or recorded. From round 1's final check, the
        pointer is put on the slot's centre and held there before the trial press, and both presses go to that same point."""
        with patch.object(browser_forms, "douyin_slot_look", new=AsyncMock(side_effect=AssertionError("looked"))) as looked:
            await self.post(extra=POINTER_HELD)
        looked.assert_not_called()
        self.assertEqual(["wide", "tall"], self.pressed())
        for role in ("landscape", "portrait"):
            self.assertEqual({"state": "UI_ACCEPTED", "remote_pixels_verified": False}, self.tx.receipt[role + "_cover"])
        held = [write["held_ms"] for write in self.writes if write["word"] == "pointer" and write["slot"]]
        self.assertEqual(4, len(held))  # A trial press and a press on each slot.
        for milliseconds in held:
            self.assertGreaterEqual(milliseconds, 50)

    async def test_a_layer_of_the_page_over_part_of_a_slot_is_never_pressed_and_the_slot_is_pressed_where_nothing_lies_over_it(self):
        """From field runs on 647e002 and 7fa6c10, something lay over a cover slot and took its press. A person presses the part of the slot they
        can see, and so does CrossCast, once a trial press there has gone through."""
        self.portrait_only()
        await self.post(extra=laid_over(across=0.6) + POINTER_HELD)
        self.assertEqual(["tall"], self.pressed())
        # From a review of a490e80: nothing held a spot press, or a press through, to the point the pointer had been held on.
        self.assertGreaterEqual(min(self.held_up_to_the_press()), 50)
        cover = self.tx.receipt["portrait_cover"]
        self.assertEqual(("UI_ACCEPTED", False, "at a spot where nothing lay over it"), (cover["state"], cover["remote_pixels_verified"], cover["pressed"]))
        self.assertEqual({"x": 21, "y": 20}, cover["at"])  # The slot is 30 by 40, and the layer covers the part left of 18.
        self.assertEqual(("div", ["toast-q9"], "page", True), (cover["over_it"]["tag"], cover["over_it"]["classes"], cover["over_it"]["place"], cover["over_it"]["off_body"]))
        self.assertIn("in the way: div, intercepts pointer events", cover["refused"])
        self.assertEqual(["publish"], self.went_out())

    async def test_a_button_of_the_slots_own_where_nothing_else_lies_is_never_pressed(self):
        """From a review: a spot was open where anything inside the slot was on top, so a press went to a remove button in its corner, which a
        press at its centre never reached. A spot is open now only where what is on top holds what a press at the centre would reach, or,
        from the field run on a16202b, where it is the slot's own picture, which a button never is."""
        self.portrait_only()
        said = await self.stopped(extra=IN_THE_SLOT + laid_over(across=0.6))
        self.assertEqual([], self.pressed())
        self.assertIn("It lies outside the slot's own cover box, so it wasn't pressed.", said)

    async def test_a_layer_that_goes_away_is_waited_for_and_the_record_says_how_long(self):
        """As a layer Douyin shows while it readies the cover area, after an upload or a reopened draft, might: the slot is pressed once it is gone.
        From a review: it went within the time one more trial press would have waited, so it goes later than that here."""
        self.portrait_only()
        with patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 30):
            await self.post(extra=laid_over(goes=3000))
        self.assertEqual(["tall"], self.pressed())
        cover = self.tx.receipt["portrait_cover"]
        self.assertEqual(("UI_ACCEPTED", "at its centre", "div"), (cover["state"], cover["pressed"], cover["over_it"]["tag"]))
        self.assertGreaterEqual(cover["waited_seconds"], 2.5)
        self.assertLess(cover["waited_seconds"], 30)
        self.assertEqual(["publish"], self.went_out())

    async def test_a_look_the_page_doesnt_answer_is_a_look_that_saw_nothing_open(self):
        """From a review: a slot taken off the page for longer than a look waits for it, or a second cover- element in its cover box, ended the
        wait with a bare driver error. The wait goes on, and the slot is pressed once it is back; one that still can't be looked at stops."""
        self.portrait_only()
        with patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 15):
            await self.post(extra=laid_over(goes=300) + TAKEN_AWAY)
        self.assertEqual(["tall"], self.pressed())
        self.assertGreaterEqual(self.tx.receipt["portrait_cover"]["waited_seconds"], 2)
        self.tx.receipt, self.writes[:] = {}, []
        said = await self.stopped(extra=laid_over() + SECOND_SLOT)
        self.assertEqual([], self.pressed())
        self.assertIn("Its last look failed, so nothing was pressed through: Browser command failed (Error): Locator.scroll_into_view_if_needed: ", said)
        self.assertIn("strict mode violation", self.tx.receipt["cover_slot_covered"]["unseen"])

    async def test_a_press_after_the_wait_that_opens_no_window_stops_and_is_never_made_again(self):
        """From a review: nothing showed that a press at the centre after the wait is held to its window as the press through is."""
        self.portrait_only()
        with patch.object(browser_forms, "DOUYIN_WINDOW_MS", 1000):
            said = await self.stopped(extra=NO_WINDOW + laid_over(goes=500))
        self.assertEqual(["tall"], self.pressed())
        self.assertIn("Douyin's 竖封面3:4 slot was pressed once at its centre, and Douyin's cover window didn't open, so nothing was published "
                      "and nothing was pressed again.", said)
        self.assertIs(True, self.tx.receipt["cover_slot_covered"]["pressed_once"])

    async def test_a_layer_that_stays_is_never_pressed_and_none_of_its_words_go_anywhere(self):
        """It holds words that name people, as Douyin's description lists do. The stop names it by its tag and class alone, in its message, the
        receipt and the lines on stderr, and it comes when the wait is over."""
        self.portrait_only()
        payload, errors = {}, io.StringIO()
        with patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 1.5), contextlib.redirect_stderr(errors), progress.relay("douyin", payload):
            progress.attach(payload)
            try:
                said = await self.stopped(extra=laid_over(words=NAMES))
            finally:
                progress.attach({})
        self.assertEqual([], self.pressed())
        self.assertIn(" After a wait of ", said)
        self.assertIn(", the last thing seen over its centre was div.toast-q9 hung off the page's body, absolute, over all of the slot and ", said)
        self.assertIn("It lies outside the slot's own cover box, so it wasn't pressed.", said)
        covered = self.tx.receipt["cover_slot_covered"]
        over_it = covered["over_it"]
        self.assertEqual(("portrait", None, "div", ["toast-q9"], "page", True, False), (covered["role"], covered["pressed"], over_it["tag"], over_it["classes"], over_it["place"], over_it["off_body"], over_it["control"]))
        self.assertGreaterEqual(covered["waited_seconds"], 1.5)
        self.assertLess(covered["waited_seconds"], 6.5)
        stages = [json.loads(line)["stage"] for line in errors.getvalue().splitlines() if line.startswith("{")]
        self.assertEqual(["waiting for a cover slot to clear", "reading the page"], stages[-2:])
        for secret in SECRETS:
            for where in (said, json.dumps(self.tx.receipt, ensure_ascii=False), errors.getvalue()):
                self.assertNotIn(secret, where)

    async def test_a_part_of_the_slots_own_cover_box_is_pressed_through_once_nothing_else_will_do(self):
        """A person's press on the slot would land on it, and the cover box is what they would be pressing. It is pressed only once the wait is
        over and the post would otherwise stop, and then the cover window has to open. A 横封面4:3 slot that can't be left out is pressed
        through the same way."""
        self.portrait_only()
        await self.post(tall=MASK_OPENS, extra=POINTER_HELD)
        self.assertEqual(["mask"], self.pressed())
        self.assertGreaterEqual(min(self.held_up_to_the_press()), 50)
        cover = self.tx.receipt["portrait_cover"]
        self.assertEqual(("UI_ACCEPTED", False, "through its own cover box"), (cover["state"], cover["remote_pixels_verified"], cover["pressed"]))
        self.assertEqual(("cover box", ["mask-x1y2"], False), (cover["over_it"]["place"], cover["over_it"]["classes"], cover["over_it"]["control"]))
        self.assertGreaterEqual(cover["waited_seconds"], browser_forms.DOUYIN_COVERED_SECONDS)
        self.assertNotIn("cover_slot_covered", self.tx.receipt)
        self.assertEqual(["publish"], self.went_out())
        self.tx.cover, self.tx.size, self.tx.receipt, self.writes[:] = lambda role="landscape": str(self.image), (1920, 1080), {}, []
        await self.post(wide=MASK_OPENS)
        self.assertEqual((["mask", "tall"], "through its own cover box"), (self.pressed(), self.tx.receipt["landscape_cover"]["pressed"]))

    async def test_an_icon_of_the_cover_box_at_the_slots_centre_is_never_pressed(self):
        """From a review: pressed through at the slot's centre, the press went to a small icon of the mask there, whose own press took the slot's
        picture away. Only a part of the cover box as big as the slot is pressed through, where it is itself on top. From a review of a490e80:
        round 1 stopped when the icon was a button, and round 2 presses the mask elsewhere, where a person's press on it would go, which
        nothing held; a button that shows only with the pointer can't be told from one that is always there on a mask that comes with it."""
        self.portrait_only()
        for tag in ("i", "button"):
            with self.subTest(tag=tag):
                self.tx.receipt, self.writes[:] = {}, []
                await self.post(tall=ICON_ON_MASK % tag)
                self.assertEqual(["mask"], self.pressed())
                cover = self.tx.receipt["portrait_cover"]
                self.assertEqual(("through its own cover box", tag, ["icon-del"], tag == "button"),
                                 (cover["pressed"], cover["over_it"]["tag"], cover["over_it"]["classes"], cover["over_it"]["control"]))
                self.assertNotEqual({"x": 15, "y": 20}, cover["at"])

    async def test_a_mask_that_comes_with_the_pointer_is_pressed_through_at_once(self):
        """From a review, a press moves the pointer onto the slot, so a mask its cover box shows under the pointer comes up with it. Round 1
        waited it out in full, though every press brings the pointer back. From round 1's final check and an offline probe of this driver, it
        is open with the pointer away and over the slot with the pointer on it, so it came with the pointer, and it is pressed through at
        once, as a person's press would land on it: shown by the stylesheet or by a script."""
        self.portrait_only()
        for tall, kind in ((HOVER_MASK, "hover-m1"), (HOVER_MASK_BY_SCRIPT, "hover-m2")):
            with self.subTest(kind=kind):
                self.tx.receipt, self.writes[:] = {}, []
                with patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 60):
                    await self.post(tall=tall)
                self.assertEqual(["mask"], self.pressed())
                cover = self.tx.receipt["portrait_cover"]
                self.assertIn("in the way: div, intercepts pointer events", cover["refused"])
                self.assertEqual(("UI_ACCEPTED", "through its own cover box", [kind], True, None),
                                 (cover["state"], cover["pressed"], cover["over_it"]["classes"], cover["came_with_pointer"], cover["over_it_away"]))
                self.assertLess(cover["waited_seconds"], 10)
                self.assertNotIn("cover_slot_covered", self.tx.receipt)
                self.assertEqual(["publish"], self.went_out())

    async def test_what_holds_the_slot_is_never_taken_for_something_over_it(self):
        """From a review: a card of the cover box holding the slot was counted as a control over it, though a press on the slot reaches that card
        anyway, and a hit inside a holder with a button's role was not open, though Playwright's own check takes it. A slot that takes no
        pointer events, with its own cover box on top, is no layer over it either, and that box is never pressed."""
        self.portrait_only()
        await self.post(extra=MASK_IN_A_CARD)
        self.assertEqual(["mask"], self.pressed())
        self.assertEqual(("through its own cover box", False), (self.tx.receipt["portrait_cover"]["pressed"], self.tx.receipt["portrait_cover"]["over_it"]["control"]))
        self.tx.receipt, self.writes[:] = {}, []
        await self.post(extra=BADGE_IN_A_BUTTON + laid_over(goes=500))
        self.assertEqual((["card"], "at its centre"), (self.pressed(), self.tx.receipt["portrait_cover"]["pressed"]))
        self.tx.receipt, self.writes[:] = {}, []
        said = await self.stopped(extra=NO_POINTER)
        self.assertEqual([], self.writes)
        self.assertIn(", it took no pointer events at its centre, where div.coverControl-a, which holds it, was on top. So nothing was pressed through.", said)
        self.assertIs(True, self.tx.receipt["cover_slot_covered"]["over_it"]["holds_slot"])
        # From a review of the field run on fc60a30, the map tells what holds the slot apart from anything over it.
        self.assertEqual(["hhhhh"] * 5, self.tx.receipt["cover_slot_covered"]["slot_map_away"])

    async def test_a_control_over_a_slot_is_never_pressed_and_the_post_stops(self):
        """A button over the slot, or something inside one, could do whatever its own press does, so it is never pressed, in the cover box too.
        From a review: only a button showed this, so controls of other kinds are here too. From round 1's final check, an embedded frame and a
        player with controls are among them, since a press on either goes into whatever it holds."""
        self.portrait_only()
        for tall, name in ((A_BUTTON, "button.edit-b1"), (IN_A_BUTTON, "span.icon-c3 inside button.edit-b1"), *((html, name) for name, html in OTHER_CONTROLS)):
            with self.subTest(name=name):
                self.tx.receipt, self.writes[:] = {}, []
                with patch.object(browser_forms, "DOUYIN_WINDOW_MS", 1000):
                    said = await self.stopped(tall=tall)
                self.assertEqual([], self.writes)
                self.assertIn(f" was {name} in the slot's own cover box, a control, ", said)
                self.assertIn("It's a control, or lies inside one, so it wasn't pressed.", said)
                self.assertIs(True, self.tx.receipt["cover_slot_covered"]["over_it"]["control"])

    async def test_what_comes_over_the_slot_after_the_wait_is_looked_at_before_any_press_through(self):
        """From a review: nothing showed that the press through is decided on one more look just before it, or that a layer that comes after
        that look is held back from its press, as the guard on the press now does. Here a button takes the mask's place as the wait ends, and
        then a layer of the page comes over the slot just after the look, and neither is pressed, nor is the mask."""
        self.portrait_only()
        real = browser_forms.douyin_slot_last

        async def before(page, slot, covered, leave_out):
            await self.page.evaluate(BUTTON_FOR_THE_MASK)
            return await real(page, slot, covered, leave_out)

        async def after(page, slot, covered, leave_out):
            decided = await real(page, slot, covered, leave_out)
            await self.page.evaluate(LATE_LAYER)
            return decided
        with patch.object(browser_forms, "douyin_slot_last", before):
            said = await self.stopped(tall=MASK_OPENS)
        self.assertEqual([], self.pressed())
        self.assertIn(" was button.edit-b1 in the slot's own cover box, a control, ", said)
        self.tx.receipt, self.writes[:] = {}, []
        # From a review of a490e80: on a page that can scroll, Playwright's own press tried again after scrolling, and the layer fixed in the
        # window no longer lay over the point it went to, so the mask was pressed where no look had been made. The press is held to the look.
        for extra in ("", SPACER):
            with self.subTest(scrolls=bool(extra)):
                self.tx.receipt, self.writes[:] = {}, []
                with patch.object(browser_forms, "douyin_slot_last", after):
                    said = await self.stopped(tall=MASK_OPENS, extra=extra)
                self.assertEqual([], self.pressed())
                self.assertIn("Douyin's 竖封面3:4 slot couldn't take the press through its own cover box, so nothing was published. Nothing was pressed: "
                              "something else was on top at its point by then [in the way: div, intercepts pointer events].", said)
                self.assertIs(False, self.tx.receipt["cover_slot_covered"]["pressed_once"])

    async def test_a_stop_during_the_wait_ends_it_with_nothing_pressed(self):
        self.portrait_only()
        looks, real = [], browser_forms.douyin_slot_look

        async def looked(slot, **ask):
            looks.append(slot)
            if len(looks) == 2:
                atomic_json(home() / "STOP.json", {"reason": "A person paused it."})
            return await real(slot, **ask)
        with patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 60), patch.object(browser_forms, "douyin_slot_look", looked), self.assertRaises(VpubError) as caught:
            await self.post(tall=A_BUTTON)
        self.assertEqual("stopped", caught.exception.code)
        self.assertEqual([], self.writes)
        self.assertEqual(2, len(looks))

    async def test_a_stop_or_a_platform_check_after_the_last_look_holds_the_press_through(self):
        """From a review: nothing showed that the checks before the press come after the look that decides it. From round 1's final check, the
        test of that wrote its stop before the last look, so checks made before that look passed it too. Here the stop, and then a platform
        check, come from inside the look made with the pointer on the very point the press through would go to."""
        self.portrait_only()
        real, decided = browser_forms.douyin_slot_look, []

        async def looked(slot, **ask):
            seen = await real(slot, **ask)
            if ask.get("layer") is not None:
                decided.append(True)
                if stop:
                    atomic_json(home() / "STOP.json", {"reason": "A person paused it."})
            return seen

        async def check(page):
            if decided:
                raise VpubError("human_verification_required", "The platform requires human verification.")
        for stop, code in ((True, "stopped"), (False, "human_verification_required")):
            with self.subTest(code=code):
                self.tx.receipt, self.writes[:], decided[:] = {}, [], []
                try:
                    with patch.object(browser_forms, "douyin_slot_look", looked), patch.object(browser_forms, "risk_check", check if not stop else AsyncMock()), \
                            self.assertRaises(VpubError) as caught:
                        await self.post(tall=MASK_OPENS)
                    self.assertEqual((code, [], [True]), (caught.exception.code, self.pressed(), decided))
                finally:  # From a review of a490e80: a stop left behind by a first part that failed made the second fail as stopped too.
                    with contextlib.suppress(FileNotFoundError):
                        (home() / "STOP.json").unlink()

    async def test_a_platform_check_ends_the_wait_at_once(self):
        """From a review: nothing showed the platform check at each look, without which one over the slot would be waited out like a layer.
        From round 1's final check, the test of that raised at the first check, which comes before any wait, so a check made only once would
        have passed it too. The check here raises only at its third call, during a long wait, which then ends at once."""
        self.portrait_only()
        looks, real = [], browser_forms.douyin_slot_look

        async def looked(slot, **ask):
            looks.append(ask)
            return await real(slot, **ask)
        check = AsyncMock(side_effect=[None, None, VpubError("human_verification_required", "The platform requires human verification.")])
        started = time.monotonic()
        with patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 60), patch.object(browser_forms, "risk_check", check), \
                patch.object(browser_forms, "douyin_slot_look", looked), self.assertRaises(VpubError) as caught:
            await self.post(tall=A_BUTTON)
        self.assertEqual(("human_verification_required", []), (caught.exception.code, self.writes))
        self.assertEqual(3, check.await_count)
        self.assertGreater(len(looks), 2)
        self.assertLess(time.monotonic() - started, 30)

    async def test_the_wait_ends_before_the_commands_own_cut_off(self):
        """From a review: like X's waits, it leaves the command its margin, so the stop comes with its own record."""
        self.portrait_only()
        started = time.monotonic()
        with patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 60), patch.object(browser_runtime, "command_deadline", started + browser_forms.X_COMMAND_MARGIN_SECONDS + 3):
            await self.stopped(tall=A_BUTTON)
        self.assertLess(self.tx.receipt["cover_slot_covered"]["waited_seconds"], 10)
        self.assertLess(time.monotonic() - started, 30)

    async def test_an_earlier_stops_account_of_a_slot_goes_once_its_cover_is_on(self):
        """From a review: nothing showed this, and a resumed receipt would report a block that a later pass got past. From the field run on
        fc60a30 and a review of that, an account of a slot this pass didn't set, the 4:3 one here, stayed in the receipt of a post that went
        out, so it goes too once 发布 has been pressed; the one whose cover went on goes as soon as it does."""
        self.portrait_only()
        for role in ("portrait", "landscape"):
            with self.subTest(role=role):
                self.tx.receipt, self.writes[:] = {"cover_slot_covered": {"role": role, "pressed": None}}, []
                await self.post()
                self.assertNotIn("cover_slot_covered", self.tx.receipt)
                self.assertEqual(["publish"], self.went_out())

    async def test_the_pointer_is_put_on_a_point_until_what_it_brings_up_has_settled(self):
        """From an offline probe of this driver: what comes up under a pointer held still isn't hovered itself until the pointer moves again,
        so a mask that came with the pointer brought up an icon of its own only at the press. The aim puts the pointer on the same point again
        until what is on top there is what it hovers, so a press made there brings up nothing more."""
        await self.open(form() + BOTH_COVERS % ("", HOVER_MASK + ICON_ON_HOVER % "i"))
        control = self.page.locator('[class^="coverControl-"]').filter(has=self.page.get_by_text("竖封面3:4", exact=True))
        aimed = await browser_forms.douyin_slot_aim(self.page, control.locator('[class^="cover-"]:not([class^="cover-tip"])'))
        self.assertIs(True, aimed["settled"])
        on_top = "p => { const e = document.elementFromPoint(p.x, p.y); return [e.className, e.matches(':hover')]; }"
        self.assertEqual(["icon-h1", True], await self.page.evaluate(on_top, {"x": aimed["x"], "y": aimed["y"]}))

    async def test_an_icon_or_a_button_that_shows_on_such_a_mask_under_the_pointer_is_never_pressed(self):
        """From round 1's final check: the press through went to an icon or a button of the mask's own at the bare spot nearest the slot's
        centre, which shows only under the pointer, since the look had been made with the pointer away. Now each spot is looked at with the
        pointer on it, and the mask is pressed only where it is itself on top. With an icon that follows the pointer there is no such spot,
        and nothing is pressed."""
        self.portrait_only()
        for tag in ("i", "button"):
            with self.subTest(tag=tag):
                self.tx.receipt, self.writes[:] = {}, []
                await self.post(tall=HOVER_MASK + ICON_ON_HOVER % tag)
                self.assertEqual(["mask"], self.pressed())
                cover = self.tx.receipt["portrait_cover"]
                self.assertEqual(("through its own cover box", True, tag, ["icon-h1"]), (cover["pressed"], cover["came_with_pointer"], cover["over_it"]["tag"], cover["over_it"]["classes"]))
                self.assertNotEqual({"x": 15, "y": 20}, cover["at"])
        self.tx.receipt, self.writes[:] = {}, []
        said = await self.stopped(tall=HOVER_MASK + ICON_THAT_FOLLOWS)
        self.assertEqual([], self.pressed())
        self.assertIn(" It showed only while the pointer was over the slot. Wherever a bare part of it as big as the slot lay on top, something else "
                      "was on top once the pointer was there, so it wasn't pressed.", said)
        self.assertIs(True, self.tx.receipt["cover_slot_covered"]["came_with_pointer"])

    async def test_a_button_of_the_slots_own_that_shows_only_under_the_pointer_is_never_pressed(self):
        """Round 1's final check's first case: a layer of the page over the left part of the slot, and a remove button of the slot's own over
        the right part that shows only under the pointer. The look with the pointer away found the right part open, and the press there went
        to the button. Now each spot is looked at with the pointer on it, so the button is seen first, and the post stops."""
        self.portrait_only()
        for inside in (HOVER_IN_THE_SLOT, HOVER_IN_THE_SLOT_BY_SCRIPT):
            with self.subTest(by_script=inside is HOVER_IN_THE_SLOT_BY_SCRIPT):
                self.tx.receipt, self.writes[:] = {}, []
                said = await self.stopped(extra=inside + laid_over(across=0.6))
                self.assertEqual([], self.pressed())
                self.assertIn(" It stayed with the pointer away from the slot. It lies outside the slot's own cover box, so it wasn't pressed.", said)

    async def test_a_layer_that_comes_between_a_trial_press_and_the_press_is_never_pressed_through_the_slot(self):
        """From round 1's final check and an offline probe of this driver: a layer can come between a trial press that went through and the
        press right after it, which nothing guarded. The browser refuses that press, nothing is pressed, and the slot goes on as if its trial
        press had been refused that way: here the layer stays, is a bare part of the slot's own cover box, and is pressed through."""
        self.portrait_only()
        await self.post(tall=BETWEEN_PRESSES, extra=ESCAPES)
        self.assertEqual(["mask"], self.pressed())
        cover = self.tx.receipt["portrait_cover"]
        self.assertIn("in the way: div, intercepts pointer events", cover["refused"])
        # From a review of a490e80: the record read as if the trial press had been refused, and nothing said it had passed.
        self.assertIs(True, cover["refused_after_trial"])
        self.assertEqual(("through its own cover box", False, ["mask-b1"]), (cover["pressed"], cover["came_with_pointer"], cover["over_it"]["classes"]))
        self.assertEqual(1, self.escapes())
        self.assertEqual(["publish"], self.went_out())

    async def test_a_first_press_that_opens_no_window_stops_and_is_never_made_again(self):
        """From round 1's final check: the press that follows a trial press that went through is held to its window, as the covered route's
        presses are."""
        self.portrait_only()
        with patch.object(browser_forms, "DOUYIN_WINDOW_MS", 1000):
            said = await self.stopped(extra=NO_WINDOW)
        self.assertEqual(["tall"], self.pressed())
        self.assertIn("Douyin's 竖封面3:4 slot was pressed once at its centre, and Douyin's cover window didn't open, so nothing was published "
                      "and nothing was pressed again.", said)
        covered = self.tx.receipt["cover_slot_covered"]
        self.assertEqual(("portrait", "at its centre", True, None), (covered["role"], covered["pressed"], covered["pressed_once"], covered["over_it"]))
        # From a review of a490e80: a press that opens no window is where the lead that the hidden window isn't drawing matters most.
        self.assertEqual({"visibility", "frame_within_a_second", "running_animations"}, set(covered["drawing"]))

    async def test_a_tip_from_elsewhere_that_shows_only_under_the_pointer_is_never_pressed(self):
        """A neighbour's hover tip over the slot's centre, outside its cover box, which comes with the pointer: the slot is pressed at a spot of
        its own that holds with the pointer on it, and with the tip over all of the slot, the post stops at once and says it showed only while
        the pointer was over the slot."""
        self.portrait_only()
        with patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 60):
            await self.post(extra=hover_tip())
            self.assertEqual(["tall"], self.pressed())
            cover = self.tx.receipt["portrait_cover"]
            self.assertEqual(("at a spot where nothing lay over it", True, ["tip-h1"], "page"),
                             (cover["pressed"], cover["came_with_pointer"], cover["over_it"]["classes"], cover["over_it"]["place"]))
            self.assertLess(cover["waited_seconds"], 10)
            self.tx.receipt, self.writes[:] = {}, []
            said = await self.stopped(extra=hover_tip(full=True))
        self.assertEqual([], self.pressed())
        self.assertIn(", the last thing seen over its centre was div.tip-h1 hung off the page's body, absolute, over all of the slot and ", said)
        self.assertIn(" It showed only while the pointer was over the slot. It lies outside the slot's own cover box, so it wasn't pressed.", said)
        self.assertLess(self.tx.receipt["cover_slot_covered"]["waited_seconds"], 10)

    async def test_a_4_3_slot_under_what_comes_with_the_pointer_is_left_out_at_once(self):
        """A 4:3 slot that may be left out is never pressed through. Under a mask that came with the pointer, waiting can't clear it, so it is
        left out at once, nothing is pressed there, and its note says the mask showed only while the pointer was over the slot."""
        with patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 60):
            await self.post(wide=HOVER_MASK)
        self.assertEqual(["tall"], self.pressed())
        skipped = self.tx.receipt["landscape_cover"]
        self.assertEqual(("SKIPPED", True, None, None), (skipped["state"], skipped["came_with_pointer"], skipped["pressed"], skipped["over_it_away"]))
        self.assertLess(skipped["waited_seconds"], 10)
        self.assertRegex(skipped["note"], "^" + re.escape("The video's size couldn't be read from its file, and Douyin's 横封面4:3 slot had something over it that "
                         "showed only while the pointer was over the slot, once Escape had been pressed in the description at the end of its words: the last "
                         "thing seen over its centre was div.hover-m1 in the slot's own cover box, absolute, over all of the slot and ") + r"\d+"
                         + re.escape("% of the window. So the planned 4:3 cover wasn't set and nothing was pressed there.") + "$")
        self.assertEqual("UI_ACCEPTED", self.tx.receipt["portrait_cover"]["state"])
        self.assertEqual(["publish"], self.went_out())

    async def test_a_4_3_slot_whose_last_look_failed_says_so_in_its_note(self):
        """From round 1's final check: when every look failed, the note said nothing was seen over the centre, yet it took no trial press,
        though no trial had been made, and only the record said why."""
        await self.post(wide=OVER_IT, extra=SECOND_WIDE_SLOT)
        self.assertEqual(["tall"], self.pressed())
        skipped = self.tx.receipt["landscape_cover"]
        self.assertIn(": no look could see what lay over its centre. Its last look failed: Browser command failed (Error): Locator.scroll_into_view_if_needed: ", skipped["note"])
        self.assertIn("strict mode violation", skipped["unseen"])
        self.assertTrue(skipped["note"].endswith(". So the planned 4:3 cover wasn't set and nothing was pressed there."))

    async def test_a_centre_that_came_open_but_took_no_trial_press_is_said_to_have_been_open(self):
        """From round 1's final check: a look that found the centre open, whose trial press was then refused, left what an earlier look had
        seen there, and the stop told that as the last thing seen over the centre. The record keeps it, and flags the open centre. From a
        review of a490e80, it says why: a trial press refused, or one that passed after the slot had moved."""
        self.portrait_only()
        for why, says in (("refused", "its centre was open at the last look, yet it took no trial press"),
                          ("moved", "its centre was open at the last look and took its trial press, but the slot had moved since the pointer was put there")):
            with self.subTest(why=why):
                self.tx.receipt, self.writes[:] = {}, []
                with patch.object(browser_forms, "douyin_slot_trial", new=AsyncMock(return_value=why)), patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 5):
                    said = await self.stopped(extra=laid_over(goes=2000))
                self.assertEqual([], self.pressed())
                self.assertIn(f", {says}. So nothing was pressed through.", said)
                self.assertNotIn("the last thing seen", said)
                covered = self.tx.receipt["cover_slot_covered"]
                self.assertEqual((True, why, "div", None), (covered["open_but_refused"], covered["centre_trial"], covered["over_it"]["tag"], covered["over_it_away"]))
                self.assertEqual(why == "moved", covered.get("moved", 0) > 0)

    async def test_a_centre_that_came_open_before_every_later_look_failed_is_told_as_the_last_one_seen(self):
        """From the final check of 073cf63: a look found the centre open and its trial press didn't go through, every look after it failed,
        and the stop said both that the centre was open at the last look and that the last look had failed."""
        self.portrait_only()

        async def trial(slot, aimed, at=None):
            await self.page.evaluate(SECOND_TALL_SLOT)
            return "refused"
        with patch.object(browser_forms, "douyin_slot_trial", trial), patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 4):
            said = await self.stopped(extra=laid_over(goes=300))
        self.assertEqual([], self.pressed())
        self.assertIn(", its centre was open at the last look with the pointer on it that could see it, yet it took no trial press. Its last look "
                      "failed, so nothing was pressed through: Browser command failed (Error): ", said)
        self.assertNotIn("open at the last look,", said)
        covered = self.tx.receipt["cover_slot_covered"]
        self.assertEqual((True, "refused"), (covered["open_but_refused"], covered["centre_trial"]))
        self.assertIn("strict mode violation", covered["unseen"])

    async def test_what_lay_over_a_slot_is_kept_when_the_cover_step_stops_after_its_window_opened(self):
        """From round 1's final check: what lay over a slot was saved only once its cover was on, or when the slot step itself stopped, so a
        cover window that then hung kept nothing of it. It is saved as soon as the window opens, and the window's own stop says whether the
        page was drawing."""
        self.portrait_only()
        with patch.object(browser_edits, "DOUYIN_COVER_SECONDS", 1), self.assertRaises(VpubError) as caught:
            await self.post(tall=MASK_OPENS, extra=DONE_STAYS)
        self.assertEqual("cover_not_ready", caught.exception.code)
        self.assertRegex(caught.exception.message, DRAWING_SAID)
        self.assertEqual(([], ["mask"]), (self.went_out(), self.pressed()))
        covered = self.tx.receipt["cover_slot_covered"]
        self.assertEqual(("portrait", "through its own cover box", True, "It was pressed once, and Douyin's cover window opened.", ["mask-x1y2"]),
                         (covered["role"], covered["pressed"], covered["pressed_once"], covered["note"], covered["over_it"]["classes"]))
        self.assertNotIn("portrait_cover", self.tx.receipt)

    async def test_whether_the_page_was_drawing_is_kept_with_what_lay_over_a_slot(self):
        """For the lead that the hidden window draws no frames, gathered once, at the first look that finds the slot covered: fixed words, true or
        false and a count, and nothing else."""
        self.portrait_only()
        said = await self.stopped(extra=laid_over(words=NAMES))
        drawing = self.tx.receipt["cover_slot_covered"]["drawing"]
        self.assertEqual({"visibility", "frame_within_a_second", "running_animations"}, set(drawing))
        for name, kinds in DRAWING_KINDS.items():
            self.assertIn(drawing[name], kinds)
            self.assertIn(type(drawing[name]), (bool, str))
        self.assertTrue(type(drawing["running_animations"]) is int or drawing["running_animations"] == "unknown")
        # From a review of a490e80: the drawing record holds only fixed words, so this looks at all of the receipt and the stop's message.
        for secret in SECRETS:
            for where in (said, json.dumps(self.tx.receipt, ensure_ascii=False)):
                self.assertNotIn(secret, where)

    async def test_what_shows_after_the_look_that_decided_a_press_is_never_pressed(self):
        """From a review of a490e80: something of the slot's own, or of its cover box's mask, that showed a little after the half second the
        pointer is given, between the look that decided a press and the press, took it, since Playwright's own check takes anything inside
        what it presses: a probe found a remove button pressed so at 600 ms. Each press is held to its look now at the moment it is made, so
        neither is pressed, and nothing else is: a remove button of the slot's own over the clear part a spot press goes to, and an icon over
        all of the mask a press through goes to, each shown just after its look."""
        self.portrait_only()
        real_trial, real_look = browser_forms.douyin_slot_trial, browser_forms.douyin_slot_look

        async def trial(slot, aimed, at=None):
            why = await real_trial(slot, aimed, at)
            if why is None and at is not None:
                await self.page.evaluate(LATER_SHOWN)
            return why

        async def looked(slot, **ask):
            seen, layers = await real_look(slot, **ask)
            if seen.get("on_bare"):
                await self.page.evaluate(ICON_LATER)
            return seen, layers
        with patch.object(browser_forms, "douyin_slot_trial", trial):
            said = await self.stopped(extra=LATER_IN_THE_SLOT + laid_over(across=0.6))
        self.assertEqual([], self.pressed())
        self.assertIn("Douyin's 竖封面3:4 slot couldn't take the press at a spot where nothing lay over it, so nothing was published. Nothing was pressed: "
                      "something else was on top at its point by then [in the way: button, intercepts pointer events].", said)
        self.tx.receipt, self.writes[:] = {}, []
        with patch.object(browser_forms, "douyin_slot_look", looked):
            said = await self.stopped(tall=MASK_OPENS)
        self.assertEqual([], self.pressed())
        self.assertIn("Douyin's 竖封面3:4 slot couldn't take the press through its own cover box, so nothing was published. Nothing was pressed: "
                      "something else was on top at its point by then [in the way: i, intercepts pointer events].", said)
        self.assertIs(False, self.tx.receipt["cover_slot_covered"]["pressed_once"])

    async def test_a_press_held_back_part_of_the_way_through_says_where_and_is_never_made_again(self):
        """From the final check of 073cf63: when something new came on top part of the way through a press, the guard held back the rest of it,
        and the stop said the press had gone through and Douyin's cover window hadn't opened, with nothing in the record about the guard. A
        mask whose own mousedown lays a pressed look over it, pressed through after the wait, and a layer of the page's own that the slot's own
        mousedown lays over it, on the press after a first trial press: each press is held back from its pointerup on, nothing is pressed
        again, and the stop and the record say where it was cut short and what was in the way, by its tag alone. From a review of that, the
        layer holds words that name people, which go nowhere, and a mask its own mousedown makes a control is told as the pressed part itself."""
        self.portrait_only()
        why = {"span": "something else was on top at its point by then [in the way: span]", "div": "something else was on top at its point by then [in the way: div]",
               "itself": "by then what it was pressing there was no longer as the look had found it [in the way: itself]"}
        for tall, extra, pressed, tag in ((PRESSED_LOOK, "", "through its own cover box", "span"), ("", LAID_AT_MOUSEDOWN, "at its centre", "div"),
                                          (TURNS_CONTROL, "", "through its own cover box", "itself")):
            with self.subTest(pressed=pressed, tag=tag):
                self.tx.receipt, self.writes[:] = {}, []
                payload, errors = {}, io.StringIO()
                with patch.object(browser_forms, "DOUYIN_WINDOW_MS", 1000), contextlib.redirect_stderr(errors), progress.relay("douyin", payload):
                    progress.attach(payload)
                    try:
                        said = await self.stopped(tall=tall, extra=extra)
                    finally:
                        progress.attach({})
                self.assertEqual([], self.pressed())
                self.assertIn(f"Douyin's 竖封面3:4 slot was pressed once {pressed}, but CrossCast held back its pointerup and anything after it, since "
                              f"{why[tag]}, and Douyin's cover window didn't open, so nothing was published and nothing was pressed again.", said)
                covered = self.tx.receipt["cover_slot_covered"]
                self.assertEqual((pressed, True, {"from": "pointerup", "in_the_way": tag}), (covered["pressed"], covered["pressed_once"], covered["cut_short"]))
                for secret in SECRETS:
                    for where in (said, json.dumps(self.tx.receipt, ensure_ascii=False), errors.getvalue()):
                        self.assertNotIn(secret, where)
        # Whatever the page's world might hand back, only an event's name from the fixed list and a plain tag go anywhere.
        for state in ({"held": "keydown", "in_the_way": "<div title=张三>"}, {"held": None, "in_the_way": "DIV"}, {"held": "pointerup", "in_the_way": None}):
            cut = browser_forms.douyin_cut_short(state)
            self.assertEqual("other", cut["in_the_way"])
            self.assertIn(cut["from"], browser_forms.DOUYIN_PRESS_EVENTS + ("other",))
            self.assertNotIn("张三", browser_forms.douyin_cut_short_said(cut))

    async def test_a_bare_part_that_shrinks_while_pressed_still_takes_its_press_through(self):
        """From a review of the change after 073cf63: the guard asked at every event of a press through that the bare part still covered all of
        the slot, so a mask with a pressed look that draws it in, as cards often have, had its press held back and stopped the post, since the
        browser shows that look before the press's first event. That part has to be bare just before the button goes down; at the press's own
        events it needs only to be that very part on top there, in its cover box and in no control."""
        self.portrait_only()
        await self.post(tall=SHRINKS_WHEN_PRESSED)
        self.assertEqual(["mask"], self.pressed())
        cover = self.tx.receipt["portrait_cover"]
        self.assertEqual(("UI_ACCEPTED", "through its own cover box"), (cover["state"], cover["pressed"]))
        self.assertNotIn("cut_short", cover)
        self.assertEqual(["publish"], self.went_out())

    async def test_a_first_press_cut_short_whose_window_opens_keeps_the_cut_until_its_cover_is_on(self):
        """From a review of the change after 073cf63: a first press cut short whose cover window opened anyway kept no sign of the cut, though the
        window may then hang. The cut is kept until the cover is on, and goes then."""
        self.portrait_only()
        opens_at_mousedown = LAID_AT_MOUSEDOWN.replace("document.body.append(layer);", "document.body.append(layer); document.getElementById('d').hidden = false;")
        self.assertNotEqual(LAID_AT_MOUSEDOWN, opens_at_mousedown)
        with patch.object(browser_edits, "DOUYIN_COVER_SECONDS", 1), self.assertRaises(VpubError) as caught:
            await self.post(extra=opens_at_mousedown + DONE_STAYS)
        self.assertEqual("cover_not_ready", caught.exception.code)
        covered = self.tx.receipt["cover_slot_covered"]
        self.assertEqual(("at its centre", True, {"from": "pointerup", "in_the_way": "div"}), (covered["pressed"], covered["pressed_once"], covered["cut_short"]))
        self.assertTrue(covered["note"].endswith("and Douyin's cover window opened."))
        self.tx.receipt, self.writes[:] = {}, []
        await self.post(extra=opens_at_mousedown)
        self.assertNotIn("cover_slot_covered", self.tx.receipt)
        self.assertEqual("UI_ACCEPTED", self.tx.receipt["portrait_cover"]["state"])

    async def test_a_slot_whose_centre_the_description_lies_over_is_pressed_on_its_own_picture_at_once(self):
        """From the field run on a16202b: a div of the description's, inside its box's own wrapper, lay over a band through the 竖封面3:4 slot's
        centre for the whole wait, and the post stopped though most of the slot lay clear, since a spot was open only where what was on top
        held what a press at the centre would reach, a small mark of the slot's own there. The slot's own picture counts now, shown as a
        background or as an img, so the slot is pressed on its picture without the wait, the description's row is never
        pressed, and the record maps the slot with none of the row's words."""
        self.portrait_only()
        for kind, part in (("background", SLOT_PICTURE), ("img", SLOT_IMG)):
            with self.subTest(kind=kind):
                self.tx.receipt, self.writes[:] = {}, []
                with patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 60):
                    await self.post(extra=part + MENTION_BAND)
                self.assertEqual(["tall"], self.pressed())
                cover = self.tx.receipt["portrait_cover"]
                self.assertEqual(("UI_ACCEPTED", "at a spot where nothing lay over it", False, "description", ["publish-mention-wrapper-T1"]),
                                 (cover["state"], cover["pressed"], cover["came_with_pointer"], cover["over_it_away"]["place"], cover["over_it_away"]["holder"]["classes"]))
                self.assertEqual(["bbbbb", "bbbbb", "DDDDD", "bbbbb", "bbbbb"], cover["slot_map_away"])
                # From a review of the field run on fc60a30, where the slot and the description box lay, in whole pixels.
                self.assertEqual([30, 40], cover["where"]["slot"][2:])
                self.assertEqual(4, len(cover["where"]["description"]))
                self.assertLess(cover["waited_seconds"], 10)
                for secret in SECRETS:
                    self.assertNotIn(secret, json.dumps(self.tx.receipt, ensure_ascii=False))

    async def test_a_slot_whose_clear_part_is_no_picture_of_its_own_is_never_pressed_there_and_the_stop_says_so(self):
        """The same, with a part of the slot's own over all of it in the picture's place that isn't its picture: a button, two pictures each
        over half of it, as a card split in two might show, or a plain part, each with a press of its own. From a review, nothing in the markup
        shows a handler a script put on a part, so none of these is pressed, and the stop and the map say so."""
        self.portrait_only()
        for kind, part, letter in (("button", SLOT_BUTTON, "c"), ("halves", SLOT_HALVES, "s"), ("plain", SLOT_PLAIN, "s")):
            with self.subTest(kind=kind):
                self.tx.receipt, self.writes[:] = {}, []
                said = await self.stopped(extra=part + MENTION_BAND)
                self.assertEqual([], self.pressed())
                self.assertIn("It lies outside the slot's own cover box, so it wasn't pressed. No clear spot of the slot's own was one a press could go to.", said)
                covered = self.tx.receipt["cover_slot_covered"]
                self.assertEqual([letter * 5, letter * 5, "DDDDD", letter * 5, letter * 5], covered["slot_map_away"])
                self.assertNotIn("spot_tries", covered)

    async def test_a_part_of_the_slots_own_that_comes_after_the_look_at_a_spot_is_never_pressed(self):
        """From a review: a spot press on the slot's picture was held to any big part of the slot's own, so a replace mask of its own, showing a
        picture, that came just after the spot's trial press took the press. It is held to the very part the look found there now."""
        self.portrait_only()
        real = browser_forms.douyin_slot_trial

        async def trial(slot, aimed, at=None):
            why = await real(slot, aimed, at)
            if why is None and at is not None:
                await self.page.evaluate(REPLACE_LATER)
            return why
        with patch.object(browser_forms, "douyin_slot_trial", trial), patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 60):
            said = await self.stopped(extra=SLOT_PICTURE + MENTION_BAND)
        self.assertEqual([], self.pressed())
        self.assertIn("Douyin's 竖封面3:4 slot couldn't take the press at a spot where nothing lay over it, so nothing was published. Nothing was pressed: "
                      "something else was on top at its point by then [in the way: div, intercepts pointer events].", said)

    async def test_a_bare_part_of_the_cover_box_over_the_rest_of_the_slot_is_pressed_through_when_the_description_lies_over_its_centre(self):
        """With the description's row over the slot's centre and a mask of the slot's own cover box that shows under the pointer over the rest:
        each clear spot of the slot is aimed at, and the mask is on top there with the pointer there, so it is counted as closed; after the
        wait the mask, a bare part of the slot's own cover box, is pressed through where it is itself on top."""
        self.portrait_only()
        await self.post(tall=HOVER_MASK, extra=MENTION_BAND)
        self.assertEqual(["mask"], self.pressed())
        cover = self.tx.receipt["portrait_cover"]
        self.assertEqual(("UI_ACCEPTED", "through its own cover box", "description"), (cover["state"], cover["pressed"], cover["over_it"]["place"]))
        self.assertGreater(cover["spot_tries"].get("closed", 0), 0)
        # From a final check: the look from a clear spot, which found the mask, is kept.
        self.assertEqual(["mmmmm", "mmmmm", "DDDDD", "mmmmm", "mmmmm"], cover["slot_map_spot"])

    async def test_on_a_form_just_uploaded_the_covers_go_on_before_a_long_description(self):
        """From the field runs on a16202b and fc60a30: 肥鱼抢麦's long description went in first, and then something of the description's lay
        over every point of its 竖封面3:4 slot, so the post stopped with nothing pressed. On a form just uploaded the covers go on while the
        description is still empty now, the words go in after them, and the post goes out once with nothing of the description's pressed."""
        self.portrait_only()
        self.tx.media, self.tx.options["description"] = (lambda: "clip.mp4"), LONG_WORDS
        self.html = form() + BOTH_COVERS % ("", "") + "<span>重新上传</span>" + LONG_WORDS_COVER

        async def uploaded(page, path, trigger=None, accept=None):
            await page.goto("https://creator.douyin.com/creator-micro/content/post/video")
        with patch.object(browser_forms, "finish", new=AsyncMock(return_value={"state": "SUBMITTED_UNVERIFIED"})), \
                patch.object(browser_forms, "works", new=AsyncMock(return_value={})), patch.object(browser_forms, "upload_file", new=uploaded):
            await browser_forms.publish_douyin(self.page, self.tx)
        self.assertEqual(["tall"], self.pressed())
        self.assertEqual("UI_ACCEPTED", self.tx.receipt["portrait_cover"]["state"])
        # The words did lie over the slot once they were in, and the post still went out once.
        self.assertEqual(["spilled", "publish"], self.went_out())
        self.assertEqual([LONG_WORDS], [write["words"].strip() for write in self.writes if write["word"] == "publish"])

    async def test_a_kept_draft_gets_its_covers_before_its_long_description_too(self):
        """From a review of setting covers first: a cover step that stopped on a form just uploaded leaves a draft with no words, and the resume
        typed them before the covers, the order that buried 肥鱼抢麦's slot; a draft an earlier build left with its words in is the same. Either
        way the covers go on with the box empty now, and the words go in after them and go out once, as planned."""
        self.portrait_only()
        for kind, held in (("empty", ""), ("words", LONG_WORDS)):
            with self.subTest(kind=kind):
                self.tx.receipt, self.writes[:] = {"media_selected": True, "before_ids": [], "draft_offered_before": False}, []
                self.tx.resuming, self.tx.media, self.tx.options["description"] = True, (lambda: "clip.mp4"), LONG_WORDS
                self.upload_page = ResumeFromKeptDraftTests.OFFER
                self.html = form().replace(">Words</div>", f">{held}</div>") + BOTH_COVERS % ("", "") + "<span>重新上传</span>" + LONG_WORDS_COVER
                with patch.object(browser_forms, "DOUYIN_OFFER_SECONDS", 1), \
                        patch.object(browser_forms, "finish", new=AsyncMock(return_value={"state": "SUBMITTED_UNVERIFIED"})), \
                        patch.object(browser_forms, "works", new=AsyncMock(return_value={})), \
                        patch.object(browser_forms, "upload_file", new=AsyncMock(side_effect=RuntimeError("selected"))) as selected:
                    await browser_forms.publish_douyin(self.page, self.tx)
                selected.assert_not_called()
                self.assertEqual(["tall"], self.pressed())
                # Up to 发布; this copy of the site then shows the same form again, words and all.
                went = self.went_out()
                self.assertEqual(["spilled"] * (2 if held else 1) + ["publish"], went[:went.index("publish") + 1])
                self.assertEqual([LONG_WORDS], [write["words"].strip() for write in self.writes if write["word"] == "publish"])
                self.assertIs(True, self.tx.receipt["description_typed"])

    async def test_a_cover_stop_on_a_form_just_uploaded_says_the_description_isnt_in_yet(self):
        """From a check of setting covers first: a stop in the cover step left Douyin's kept draft with no description, and only the stop's
        hint said so, which a run's receipt doesn't keep, so a person finishing that draft by hand would have posted it with no words. The
        stop's own message says so now, nothing goes out, and the receipt says the words aren't in."""
        self.portrait_only()
        self.tx.media, self.tx.options["description"] = (lambda: "clip.mp4"), LONG_WORDS
        self.html = form() + BOTH_COVERS % ("", "") + "<span>重新上传</span>" + laid_over()

        async def uploaded(page, path, trigger=None, accept=None):
            await page.goto("https://creator.douyin.com/creator-micro/content/post/video")
        with patch.object(browser_forms, "finish", new=AsyncMock(return_value={"state": "SUBMITTED_UNVERIFIED"})), \
                patch.object(browser_forms, "works", new=AsyncMock(return_value={})), patch.object(browser_forms, "upload_file", new=uploaded), \
                self.assertRaises(VpubError) as caught:
            await browser_forms.publish_douyin(self.page, self.tx)
        self.assertEqual("cover_blocked", caught.exception.code)
        self.assertTrue(caught.exception.message.endswith(browser_forms.DOUYIN_WORDS_NOT_IN))
        self.assertEqual(([], []), (self.pressed(), self.went_out()))
        self.assertIs(False, self.tx.receipt["description_typed"])

    async def test_a_form_uploaded_again_gets_its_covers_whatever_an_earlier_form_had(self):
        """From a final check of setting covers first: a resume that found no kept draft uploaded the video again to a new form, and the
        receipt still said the 竖封面3:4 cover an earlier form had was on, so the new form, and a later resume from its kept draft, went out
        without it. Those records go with the upload now, and the cover goes on the new form."""
        self.portrait_only()
        self.tx.resuming, self.tx.media = True, (lambda: "clip.mp4")
        self.tx.receipt = {"media_selected": True, "before_ids": [], "draft_offered_before": False,
                           "portrait_cover": {"state": "UI_ACCEPTED", "remote_pixels_verified": False}}
        self.html = form() + BOTH_COVERS % ("", "") + "<span>重新上传</span>"

        async def uploaded(page, path, trigger=None, accept=None):
            await page.goto("https://creator.douyin.com/creator-micro/content/post/video")
        with patch.object(browser_forms, "DOUYIN_OFFER_SECONDS", 1), patch.object(browser_forms, "finish", new=AsyncMock(return_value={"state": "SUBMITTED_UNVERIFIED"})), \
                patch.object(browser_forms, "works", new=AsyncMock(return_value={})), patch.object(browser_forms, "upload_file", new=uploaded):
            await browser_forms.publish_douyin(self.page, self.tx)
        self.assertEqual(["tall"], self.pressed())
        self.assertEqual(("UI_ACCEPTED", ["publish"]), (self.tx.receipt["portrait_cover"]["state"], self.went_out()))

    async def test_a_kept_drafts_words_stay_when_no_cover_is_left_to_press(self):
        """From a final check of setting covers first: a video taller than it is wide never has its 4:3 cover pressed, but that cover counted as
        left to set, so a kept draft whose 竖封面3:4 cover was already on had its words taken out and typed again. They stay now, and nothing
        is pressed."""
        self.tx.size = (1080, 1920)
        self.tx.receipt = {"media_selected": True, "before_ids": [], "draft_offered_before": False,
                           "portrait_cover": {"state": "UI_ACCEPTED", "remote_pixels_verified": False}}
        self.tx.resuming, self.tx.media, self.tx.options["description"] = True, (lambda: "clip.mp4"), LONG_WORDS
        self.upload_page = ResumeFromKeptDraftTests.OFFER
        self.html = form().replace(">Words</div>", f">{LONG_WORDS}</div>") + BOTH_COVERS % ("", "") + "<span>重新上传</span>" + LONG_WORDS_COVER
        with patch.object(browser_forms, "DOUYIN_OFFER_SECONDS", 1), patch.object(browser_forms, "finish", new=AsyncMock(return_value={"state": "SUBMITTED_UNVERIFIED"})), \
                patch.object(browser_forms, "works", new=AsyncMock(return_value={})), \
                patch.object(browser_forms, "upload_file", new=AsyncMock(side_effect=RuntimeError("selected"))):
            await browser_forms.publish_douyin(self.page, self.tx)
        self.assertEqual([], self.pressed())
        went = self.went_out()
        self.assertEqual(["spilled", "publish"], went[:went.index("publish") + 1])
        self.assertEqual("SKIPPED", self.tx.receipt["landscape_cover"]["state"])

    async def test_on_a_form_just_uploaded_a_4_3_slot_left_out_is_looked_at_once(self):
        """From a review of setting covers first: nothing held that the rest of the form leaves the covers alone once they were set before the
        words. A 4:3 slot left out on a form just uploaded, as one is on a video whose size can't be read with something over its slot, is
        looked at once, and not again with the words in."""
        self.tx.media = lambda: "clip.mp4"
        self.html = form() + BOTH_COVERS % (OVER_IT, "") + "<span>重新上传</span>"
        real, looked = browser_forms.douyin_slot_window, []

        async def window(page, tx, editor, slot, role, label, size, tall_too):
            looked.append(role)
            return await real(page, tx, editor, slot, role, label, size, tall_too)

        async def uploaded(page, path, trigger=None, accept=None):
            await page.goto("https://creator.douyin.com/creator-micro/content/post/video")
        with patch.object(browser_forms, "douyin_slot_window", window), patch.object(browser_forms, "finish", new=AsyncMock(return_value={"state": "SUBMITTED_UNVERIFIED"})), \
                patch.object(browser_forms, "works", new=AsyncMock(return_value={})), patch.object(browser_forms, "upload_file", new=uploaded):
            await browser_forms.publish_douyin(self.page, self.tx)
        self.assertEqual(["landscape", "portrait"], looked)
        self.assertEqual("SKIPPED", self.tx.receipt["landscape_cover"]["state"])
        self.assertEqual((["tall"], ["publish"]), (self.pressed(), self.went_out()))

    async def test_a_post_that_goes_out_without_its_covers_drops_a_stopped_runs_account_of_its_slot(self):
        """From the field run on fc60a30: vpub resume --no-cover finished the post, and its receipt still said what had lain over a slot in the
        run it stopped."""
        self.tx.cover = lambda role="landscape": None
        self.tx.receipt = {"cover_slot_covered": {"role": "portrait", "slot": "竖封面3:4", "pressed": None}}
        await self.post()
        self.assertNotIn("cover_slot_covered", self.tx.receipt)
        self.assertEqual(([], ["publish"]), (self.pressed(), self.went_out()))

    async def test_a_picture_of_the_slots_own_made_a_control_by_its_press_has_the_rest_of_it_held_back(self):
        """From a final check: the guard held a spot press to the very part the look found, but not to its being no control, so a picture that
        a script made a control part of the way through the press took the rest of it. It is held back from that event on now, and told as
        what was being pressed itself."""
        self.portrait_only()
        with patch.object(browser_forms, "DOUYIN_WINDOW_MS", 1000), patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 60):
            said = await self.stopped(extra=PICTURE_TURNS_CONTROL + MENTION_BAND)
        self.assertEqual([], self.pressed())
        self.assertIn("Douyin's 竖封面3:4 slot was pressed once at a spot where nothing lay over it, but CrossCast held back its pointerup and anything "
                      "after it, since by then what it was pressing there was no longer as the look had found it [in the way: itself], and Douyin's "
                      "cover window didn't open", said)
        self.assertEqual({"from": "pointerup", "in_the_way": "itself"}, self.tx.receipt["cover_slot_covered"]["cut_short"])

    async def test_a_press_at_the_centre_held_back_says_the_centre_was_open_and_took_its_trial_press(self):
        """From a review of a490e80: a layer that came over the slot's centre after its trial press passed was pressed round on a page that can
        scroll, where Playwright's own press tried again after scrolling, and otherwise the stop said the centre had taken no trial press and
        told what an earlier look had seen there. Here nothing is pressed either way, and the stop says what the last look found."""
        self.portrait_only()
        real = browser_forms.douyin_slot_trial

        async def trial(slot, aimed, at=None):
            why = await real(slot, aimed, at)
            if why is None and at is None:
                await self.page.evaluate(LATE_LAYER)
            return why
        for extra in ("", SPACER):
            with self.subTest(scrolls=bool(extra)):
                self.tx.receipt, self.writes[:] = {}, []
                with patch.object(browser_forms, "douyin_slot_trial", trial), patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 10):
                    said = await self.stopped(extra=laid_over(goes=500) + extra)
                self.assertEqual([], self.pressed())
                self.assertIn("Douyin's 竖封面3:4 slot couldn't take the press at its centre, so nothing was published. Nothing was pressed: something "
                              "else was on top at its point by then [in the way: div, intercepts pointer events]. After a wait of ", said)
                self.assertIn(", its centre was open at the last look and took its trial press.", said)
                self.assertNotIn("the last thing seen", said)

    async def test_a_layer_that_comes_after_the_first_trial_press_is_never_pressed_round(self):
        """From a review of a490e80: a layer of the page's own, fixed in the window, that came over the slot after its first trial press passed
        let Playwright's own press, which tries again after scrolling, go to the slot at a point no look had seen, and the post went out with
        nothing in its record. The press after a first trial press is held to that point now: nothing is pressed, the slot goes on as if its
        trial press had been refused, and the layer, which stays and lies outside the cover box, stops the post."""
        self.portrait_only()
        for extra in ("", SPACER):
            with self.subTest(scrolls=bool(extra)):
                self.tx.receipt, self.writes[:] = {}, []
                said = await self.stopped(extra=FIXED_BETWEEN + ESCAPES + extra)
                self.assertEqual(([], 1), (self.pressed(), self.escapes()))
                self.assertIn("Douyin's 竖封面3:4 slot couldn't take a press, so nothing was published. It took its first trial press, but not the press "
                              "after it. Nothing was pressed: something else was on top at its point by then [in the way: div, intercepts pointer events]. ", said)
                self.assertIn("It lies outside the slot's own cover box, so it wasn't pressed.", said)
                self.assertIs(True, self.tx.receipt["cover_slot_covered"]["refused_after_trial"])

    async def test_a_trial_press_that_playwright_moved_is_not_followed_by_a_press(self):
        """From a review of a490e80: only a page that can scroll lets Playwright's own trial press, which tries again after scrolling, pass at a
        point the pointer was never put on, and no test reached the check that keeps such a slot from being pressed. A layer fixed in the
        window comes over the slot just after the aim: on a page that can scroll the trial press passes elsewhere, and on one that can't it is
        refused, and neither is taken."""
        for extra, why in ((SPACER, "moved"), ("", "refused")):
            with self.subTest(why=why):
                await self.open(form() + BOTH_COVERS % ("", "") + extra)
                control = self.page.locator('[class^="coverControl-"]').filter(has=self.page.get_by_text("竖封面3:4", exact=True))
                slot = control.locator('[class^="cover-"]:not([class^="cover-tip"])')
                aimed = await browser_forms.douyin_slot_aim(self.page, slot)
                self.assertIs(True, aimed["settled"])
                await self.page.evaluate(LATE_LAYER)
                self.assertEqual(why, await browser_forms.douyin_slot_trial(slot, aimed))

    async def test_what_comes_by_itself_between_the_first_two_looks_is_waited_for(self):
        """From a review of a490e80: a layer that came by itself just after the look with the pointer away, as one Douyin shows while it readies
        the cover area might, was taken to have come with the pointer: a mask of the cover box was pressed through at once and opened nothing,
        and a toast of the page's own stopped the post at once. A second pair of looks has to find the same now, so both are waited for, and
        the slot is pressed at its centre once they go. The description's list lies over the slot until Escape, as in the field runs."""
        self.portrait_only()
        real = browser_forms.douyin_look_away
        for in_box, kind in ((True, "ready-r1"), (False, "toast-t1")):
            with self.subTest(kind=kind):
                self.tx.receipt, self.writes[:], looks = {}, [], []

                async def away(page, slot, covered):
                    seen = await real(page, slot, covered)
                    if not looks:
                        looks.append(seen)
                        await self.page.evaluate(ARRIVES, [in_box, 2500], isolated_context=False)
                    return seen
                with patch.object(browser_forms, "douyin_look_away", away), patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 30):
                    await self.post(extra=OPEN_LIST)
                self.assertEqual(["tall"], self.pressed())
                cover = self.tx.receipt["portrait_cover"]
                self.assertEqual(("at its centre", False, [kind]), (cover["pressed"], cover["came_with_pointer"], cover["over_it"]["classes"]))
                self.assertIsNone(looks[0]["over_it"])
                self.assertGreaterEqual(cover["waited_seconds"], 2)

    async def test_a_slot_that_lifts_under_the_pointer_is_pressed_once_what_lay_over_it_goes(self):
        """From a review of a490e80: a slot that lifted under the pointer had moved by the time its trial press came, which was then thrown away,
        so a 竖封面3:4 slot was pressed only after the whole wait, and a 4:3 slot that may be left out was left out. The pointer follows the
        point now, so each is pressed soon after the layer over it goes."""
        for slot, role in ((1, "portrait"), (0, "landscape")):
            with self.subTest(role=role):
                self.tx.receipt, self.writes[:] = {}, []
                with patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 30):
                    await self.post(extra=LIFTS + laid_over(goes=1500, slot=slot))
                self.assertEqual(["wide", "tall"], self.pressed())
                cover = self.tx.receipt[role + "_cover"]
                self.assertEqual(("UI_ACCEPTED", "at its centre"), (cover["state"], cover["pressed"]))
                self.assertLess(cover["waited_seconds"], 15)

    async def test_a_point_the_pointer_never_settles_on_is_never_pressed_and_the_stop_says_so(self):
        """From a review of a490e80: no stand-in left the pointer unsettled, as a window that doesn't keep what is hovered might, so nothing
        showed that such a point isn't pressed, and the stops then said a trial press had been refused or something else was on top. A first
        trial press still decides a slot's first press, which goes ahead."""
        with patch.object(browser_forms, "DOUYIN_SETTLED", "p => false"):
            await self.post()
            self.assertEqual(["wide", "tall"], self.pressed())
            self.portrait_only()
            self.tx.receipt, self.writes[:] = {}, []
            said = await self.stopped(tall=MASK_OPENS)
            self.assertEqual([], self.pressed())
            self.assertIn("Wherever a bare part of it as big as the slot lay on top, the pointer never settled there, so it wasn't pressed.", said)
            self.assertGreater(self.tx.receipt["cover_slot_covered"]["unsettled"]["bare part"], 0)
            self.tx.receipt, self.writes[:] = {}, []
            with patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 3):
                said = await self.stopped(extra=laid_over(goes=500))
        self.assertEqual([], self.pressed())
        self.assertIn(", its centre was open at the last look, but the pointer never settled there, so no trial press was made. So nothing was "
                      "pressed through.", said)
        covered = self.tx.receipt["cover_slot_covered"]
        self.assertEqual((True, "unsettled"), (covered["open_but_refused"], covered["centre_trial"]))
        self.assertGreater(covered["unsettled"]["centre"], 0)

    async def test_a_first_press_refused_any_other_way_stops_with_nothing_pressed(self):
        """From a review of a490e80: nothing showed that the press after a first trial press stops when it is refused for anything but something
        over the slot. Here the slot is hidden during that trial press: nothing is pressed, no Escape goes to the description, and the post
        stops."""
        self.portrait_only()
        said = await self.stopped(extra=HIDES_BETWEEN + ESCAPES)
        self.assertEqual(([], 0), (self.pressed(), self.escapes()))
        self.assertIn("Douyin's 竖封面3:4 slot took its trial press, then couldn't take the press at its centre, so nothing was published. Nothing was "
                      "pressed: it no longer showed.", said)

    async def test_a_4_3_slot_under_a_tip_that_comes_with_the_pointer_is_pressed_at_a_spot_of_its_own(self):
        """A 4:3 slot that may be left out, under a neighbour's hover tip across its centre that comes with the pointer: before it is left out, a
        spot of its own that holds with the pointer on it is pressed, as one is during the wait, and it is never pressed through. From a review
        of a490e80, which found nothing held this reading of the brief."""
        await self.post(extra=hover_tip(slot=0))
        self.assertEqual(["wide", "tall"], self.pressed())
        cover = self.tx.receipt["landscape_cover"]
        self.assertEqual(("UI_ACCEPTED", "at a spot where nothing lay over it", True, ["tip-h1"]),
                         (cover["state"], cover["pressed"], cover["came_with_pointer"], cover["over_it"]["classes"]))

    async def test_a_mask_that_comes_with_the_pointer_once_a_layer_over_it_goes_is_pressed_through_without_the_rest_of_the_wait(self):
        """The wait's own switch, which a review of a490e80 found no stand-in reached: a layer of the page's own lies over the slot and goes after
        a while, and then a mask of the cover box that comes with the pointer lies there. It came with the pointer, so it is pressed through
        then, well before the wait is up."""
        self.portrait_only()
        with patch.object(browser_forms, "DOUYIN_COVERED_SECONDS", 60):
            await self.post(tall=HOVER_MASK, extra=laid_over(goes=1500))
        self.assertEqual(["mask"], self.pressed())
        cover = self.tx.receipt["portrait_cover"]
        self.assertEqual(("through its own cover box", True, ["hover-m1"], None),
                         (cover["pressed"], cover["came_with_pointer"], cover["over_it"]["classes"], cover["over_it_away"]))
        self.assertGreaterEqual(cover["waited_seconds"], 1)
        self.assertLess(cover["waited_seconds"], 30)

    async def test_the_pointer_is_put_where_playwrights_own_press_then_goes(self):
        """From a review of a490e80: every stand-in slot had no border and lay in the window, so an aim off by a border, or made before a scroll,
        couldn't be seen. Here the slot has borders of two widths at fractional places, and in one case lies partly below the window, and
        Playwright's own trial press, at its centre or at an offset, then moves the pointer nowhere else."""
        for space, at in ((33.37, None), (33.37, {"x": 7.77, "y": 5.55}), (700.5, None)):
            with self.subTest(space=space, at=at):
                await self.open(BORDERED % space)
                slot = self.page.locator(".cover-b")
                aimed = await browser_forms.douyin_slot_aim(self.page, slot, at)
                self.assertIs(True, aimed["settled"])
                await self.page.evaluate("window.moves.splice(0)", isolated_context=False)
                await slot.click(trial=True, timeout=3000, **({"position": at} if at else {}))
                moves = await self.page.evaluate("window.moves", isolated_context=False)
                self.assertEqual([], [move for move in moves if abs(move[0] - aimed["x"]) > 0.01 or abs(move[1] - aimed["y"]) > 0.01])

    async def test_the_look_at_a_slot_holds_no_words_and_no_attribute_values(self):
        """What lies over a slot can name people, in its words or in any attribute. The look keeps its tag, a few class tokens of code, a role
        from a fixed list and its style, for it and for what holds it, and nothing else. From a review, the link here has no role, so its tag
        alone makes it a control."""
        await self.open('<!doctype html><meta charset="utf-8"><body><div class="coverControl-a" style="position:relative"><div class="cover-b" style="width:60px;height:60px"></div>'
                        '<div id="张三的层" class="pop-a1 名字" title="李四的标题" data-owner="王五" role="赵六的角色" style="position:absolute;inset:0;cursor:url(https://example.org/u/zhangsan.png), auto">'
                        '<a id="zhangsan" href="https://example.org/u/zhangsan" title="张三" aria-label="@张三" data-user="张三" class="layer-b2 用户张三 one two three four" '
                        'style="display:block;width:100%;height:100%">@张三 给你发了一条私信 <img alt="张三" src="https://example.org/u/zhangsan.png"></a>'
                        '<input placeholder="李四" value="王五"></div></div>')
        seen = await self.page.locator(".cover-b").evaluate(browser_forms.DOUYIN_SLOT_LOOK)
        said = json.dumps(seen, ensure_ascii=False)
        for secret in (*SECRETS, "example.org", "zhangsan", "标题", "角色"):
            self.assertNotIn(secret, said)
        # From the field runs on a16202b and fc60a30, a look also maps its grid in fixed letters, the link of the layer over all of it being
        # another part of the cover box here, and gives where the slot, its cover box and the description box lie, in whole pixels.
        self.assertEqual(({"open", "spots", "over_it", "through", "map", "where"}, False, [], None, ["kkkkk"] * 5),
                         (set(seen), seen["open"], seen["spots"], seen["through"], seen["map"]))
        self.assertEqual(([60, 60], None), (seen["where"]["slot"][2:], seen["where"]["description"]))
        self.assertEqual(4, len(seen["where"]["cover_box"]))
        over_it = seen["over_it"]
        self.assertEqual({"tag", "classes", "role", "modal", "position", "z_index", "opacity", "pointer_events", "cursor", "slot_covered", "window_covered",
                          "box", "place", "off_body", "control", "holds_slot", "holder", "slot_in_window", "window"}, set(over_it))
        self.assertTrue(all(type(n) is int for n in over_it["box"] + over_it["holder"]["box"]))
        self.assertEqual(("a", ["layer-b2", "one", "two", "three"], None, True, "cover box", "pointer", 1),
                         (over_it["tag"], over_it["classes"], over_it["role"], over_it["control"], over_it["place"], over_it["cursor"], over_it["slot_covered"]))
        holder = over_it["holder"]
        self.assertEqual(("div", ["pop-a1"], "other", "other", "absolute"), (holder["tag"], holder["classes"], holder["role"], holder["cursor"], holder["position"]))


def mp4_box(kind, body):
    return struct.pack(">I4s", 8 + len(body), kind) + body


def mp4_header(width, height, turned=False, version=0):
    """What a track's header holds as an encoder writes it: its times, matrix and size."""
    matrix = (0, 0x10000, 0, -0x10000, 0, 0, 0, 0, 0x40000000) if turned else (0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    return bytes([version, 0, 0, 7]) + bytes(32 if version else 20) + bytes(16) + struct.pack(">9i", *matrix) + struct.pack(">II", width << 16, height << 16)


def mp4_track(width, height, handler=b"vide", turned=False, version=0):
    """A track as an encoder writes one: a header with its times, matrix and size, and its media's handler."""
    media = mp4_box(b"mdhd", bytes(24)) + mp4_box(b"hdlr", bytes(8) + handler + bytes(12) + b"\0")
    return mp4_box(b"trak", mp4_box(b"tkhd", mp4_header(width, height, turned, version)) + mp4_box(b"mdia", media))


def mp4(*tracks, header_last=False, long_media=False):
    media = struct.pack(">I4sQ", 1, b"mdat", 16 + 64) + bytes(64) if long_media else mp4_box(b"mdat", bytes(64))
    header = mp4_box(b"moov", mp4_box(b"mvhd", bytes(100)) + b"".join(tracks))
    return mp4_box(b"ftyp", b"isom" + bytes(4) + b"isommp42") + (media + header if header_last else header + media)


class VideoShapeTests(unittest.TestCase):
    """Files built the way encoders write MP4s. Read this way, both field runs' videos, the one whose 横封面4:3 slot was covered and the one
    whose slot took its 4:3 cover, are 1080x1920."""
    def size_of(self, data):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.mp4"
            path.write_bytes(data)
            return video_shape.frame_size(path)

    def test_the_first_video_tracks_size_is_read_as_it_shows(self):
        for data, size in ((mp4(mp4_track(1080, 1920)), (1080, 1920)), (mp4(mp4_track(1920, 1080), header_last=True), (1920, 1080)),
                           (mp4(mp4_track(0, 0, b"soun"), mp4_track(1920, 1080)), (1920, 1080)), (mp4(mp4_track(1920, 1080, turned=True)), (1080, 1920)),
                           (mp4(mp4_track(1080, 1920, version=1), long_media=True), (1080, 1920))):
            with self.subTest(size=size):
                self.assertEqual(size, self.size_of(data))

    def test_anything_it_cannot_read_has_no_size(self):
        for data in (b"", b"not a video at all", b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42", mp4(mp4_track(1080, 1920), header_last=True)[:-40],
                     mp4(mp4_track(1080, 1920, b"soun"))):
            with self.subTest(data=data[:24]):
                self.assertIsNone(self.size_of(data))
        self.assertIsNone(video_shape.frame_size(None))
        self.assertIsNone(video_shape.frame_size(Path(tempfile.gettempdir()) / "no-such-clip-anywhere.mp4"))

    def test_a_header_that_makes_no_sense_has_no_size(self):
        """From a review: an empty track header at the end of the movie header raised IndexError, and the size is read after the upload, where
        that would have stopped a post that had gone up. Each box is read only as far as it goes, so a short one never reads on into the next."""
        video = mp4_box(b"mdia", mp4_box(b"hdlr", bytes(8) + b"vide" + bytes(13)))
        for case, data in (("an empty track header, last of all", mp4(mp4_box(b"trak", video + mp4_box(b"tkhd", b"")))),
                           ("a track header too short to hold its size", mp4(mp4_box(b"trak", mp4_box(b"tkhd", mp4_header(1080, 1920)[:-8]) + video))),
                           ("a handler too short to say what it is, before a box that would", mp4(mp4_box(b"trak", mp4_box(b"tkhd", mp4_header(1080, 1920))
                                                                                                            + mp4_box(b"mdia", mp4_box(b"hdlr", bytes(4)) + mp4_box(b"vide", bytes(13))))))):
            with self.subTest(case=case):
                self.assertIsNone(self.size_of(data))

    def from_memory(self, data):
        """Read as from a file, with none written, so that every cut and every changed byte can be tried in a moment."""
        with patch.object(video_shape, "open", create=True, new=lambda path, mode: io.BytesIO(data)):
            return video_shape.frame_size("clip.mp4")

    def test_a_file_cut_short_or_changed_anywhere_never_raises(self):
        """A file cut short anywhere has its whole size or none, and one with any byte changed has some size or none."""
        for whole in (mp4(mp4_track(1080, 1920)), mp4(mp4_track(1080, 1920, version=1), header_last=True, long_media=True)):
            self.assertEqual((1080, 1920), self.from_memory(whole))
            for cut in range(len(whole)):
                self.assertIn(self.from_memory(whole[:cut]), (None, (1080, 1920)), f"cut at {cut}")
            for at in range(len(whole)):
                for value in (0, 1, 0xff):
                    size = self.from_memory(whole[:at] + bytes([value]) + whole[at + 1:])
                    self.assertTrue(size is None or len(size) == 2 and min(size) > 0, f"byte {at} set to {value}: {size}")

    def test_a_file_that_gets_shorter_as_it_is_read_has_no_size(self):
        """A file still being written or copied can hold less by the time it is read than when its size was taken. A header read short is no
        header, though the video's own track may be whole in what came back."""
        whole, cut = mp4(mp4_track(1080, 1920), mp4_track(0, 0, b"soun")), 0

        class Shrinks(io.BytesIO):
            def tell(self):
                at = super().tell()
                self.truncate(cut)
                return at
        for cut in range(len(whole) - len(mp4_box(b"mdat", bytes(64)))):
            with patch.object(video_shape, "open", create=True, new=lambda path, mode: Shrinks(whole)):
                self.assertIsNone(video_shape.frame_size("clip.mp4"), f"cut at {cut}")

    def test_an_attempt_reads_its_own_video(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"VPUB_HOME": folder}):
            path = Path(folder) / "clip.mp4"
            path.write_bytes(mp4(mp4_track(1080, 1920)))
            plan = {"confirmation": "c", "video_sha256": "0" * 64, "options": {"title": TITLE}}
            self.assertEqual((1080, 1920), browser_backends.Transaction("douyin", OWNER, {**plan, "video": str(path)}).video_size())
            self.assertIsNone(browser_backends.Transaction("douyin", OWNER, plan).video_size())


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
        """A draft whose lines ran together is typed again too, or the read-back before 发布 would stop every resume."""
        for shown, words in (("Something else", "Words"), ("Words and more", "Words\nand more")):
            with self.subTest(shown=shown):
                tx, self.writes[:] = self.stopped(), []
                tx.options["description"] = words
                await self.resume(tx, html=form().replace(">Words</div>", f">{shown}</div>") + "<span>重新上传</span>")
                went_out = [(write["word"], [line for line in write["words"].split("\n") if line.strip()]) for write in self.writes]
                self.assertEqual([("publish", words.split("\n"))], went_out)  # The form's own check holds the press if they still differ.

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

    async def read(self, receipt, text, reviewing=False):
        with patch.object(browser_backends, "works", new=AsyncMock(return_value={"7": {"id": "7", "text": text}})), \
                patch.object(browser_backends, "douyin_card_under_review", new=AsyncMock(return_value=reviewing)):
            return await browser_backends.verify(self.page(), "douyin", OWNER, receipt)

    async def test_a_review_mark_from_submission_is_read_again_from_the_works_card(self):
        """From a field run: UNDER_REVIEW written when it was submitted stayed four hours after the work was public."""
        receipt = {"identity": OWNER, "id": "7", "title": TITLE, "expected_text": TITLE, "platform_status": "UNDER_REVIEW"}
        published = await self.read(receipt, TITLE)
        self.assertEqual("SUBMISSION_VERIFIED", published["state"])
        self.assertNotIn("platform_status", published)
        reviewing = await self.read(receipt, TITLE, reviewing=True)
        self.assertEqual(("SUBMISSION_VERIFIED", "UNDER_REVIEW"), (reviewing["state"], reviewing["platform_status"]))
        self.assertIn("under review", reviewing["note"])

    async def test_a_work_that_is_gone_keeps_no_review_mark_from_before(self):
        """From a field run: a record whose work had been deleted by hand still said UNDER_REVIEW, from when it was submitted."""
        receipt = {"identity": OWNER, "id": "7", "title": TITLE, "expected_text": TITLE, "platform_status": "UNDER_REVIEW"}
        gone = await self.read(receipt, "another work")
        self.assertEqual("SUBMITTED_UNVERIFIED", gone["state"])
        self.assertNotIn("platform_status", gone)
        still = await self.read(receipt, "another work", reviewing=True)
        self.assertEqual(("SUBMITTED_UNVERIFIED", "UNDER_REVIEW"), (still["state"], still["platform_status"]))
        self.assertIn("can't be checked yet", still["note"])

    async def test_a_chapter_error_from_an_earlier_run_is_not_reported_again(self):
        """From a field run: a last run's TimeoutError sat beside PENDING_WORK_ID and was read as a new one."""
        receipt = {"identity": OWNER, "id": "7", "state": "SUBMITTED_UNVERIFIED", "pending_chapters": {"chapters": []},
                   "chapters_error": "TimeoutError", "chapters_error_message": "Locator.click: Timeout"}
        result = await browser_backends.complete_pending_chapters(self.page(), "douyin", OWNER, receipt)
        self.assertEqual("PENDING_WORK_ID", result["chapters_state"])
        self.assertNotIn("chapters_error", result)
        self.assertNotIn("chapters_error_message", result)

    async def test_a_record_with_no_title_reads_no_card_for_its_review_mark(self):
        """With no title any lone card would match, and it may be another work's."""
        receipt = {"identity": OWNER, "id": "7", "expected_text": TITLE}
        with patch.object(browser_backends, "douyin_card_under_review", new=AsyncMock(return_value=True)) as looked:
            with patch.object(browser_backends, "works", new=AsyncMock(return_value={"7": {"id": "7", "text": TITLE}})):
                result = await browser_backends.verify(self.page(), "douyin", OWNER, receipt)
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertNotIn("platform_status", result)
        looked.assert_not_called()

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
        return (datetime.now(timezone.utc) + timedelta(days=2)).replace(second=0, microsecond=0).isoformat()

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

    def test_a_caption_is_held_to_douyins_1000_units_as_it_is_typed(self):
        """From a review: each check counted the plan's words, without the space typed at the end of a line whose last #topic or @name is an
        @name, so a caption at the limit would have been typed and then cut by Douyin. A plan, an edit and the upload itself count what is typed:
        here, the plan's words and one space."""
        text = {"expected_identity": OWNER, "title": "标题", "content_language": "zh-Hans", "metadata_language": "zh-Hans", "metadata_mode": "target_only", "tags": ["tag"]}
        for length, fits in ((985, False), (984, True)):
            description = "好" * length + "\n参考：@上善无形"
            caption = douyin_caption(description, ["tag"])
            with self.subTest(length=length):
                self.assertEqual((length + 15, length + 16), (len(caption.encode("utf-16-le")) // 2, douyin_caption_units(caption)))
                page, tx = AsyncMock(), StubTx(description=description, tags=["tag"])
                with patch.object(browser_forms, "works", new=AsyncMock(side_effect=RuntimeError("went on to the upload"))):
                    if fits:
                        self.assertEqual(description, self.plan(description=description, tags=["tag"])["description"])
                        self.assertEqual(caption, edits.validate_patch("douyin", {**text, "description": description})["caption"])
                        with self.assertRaisesRegex(RuntimeError, "went on to the upload"):
                            asyncio.run(browser_forms.publish_douyin(page, tx))
                        continue
                    self.refused("invalid_description", description=description, tags=["tag"])
                    for refuse in (lambda: edits.validate_patch("douyin", {**text, "description": description}), lambda: asyncio.run(browser_forms.publish_douyin(page, tx))):
                        with self.assertRaises(VpubError) as caught:
                            refuse()
                        self.assertEqual("description_too_long", caught.exception.code)
                        self.assertIn("1000 UTF-16 units as typed", caught.exception.message)
                    self.assertEqual([], page.mock_calls)

    def test_a_draft_keeps_only_what_a_draft_can_hold(self):
        self.assertEqual("draft_only", self.plan(submission_mode="draft_only")["submission_mode"])
        self.refused("invalid_submission_mode", submission_mode="draft_only", schedule=self.future())
        self.refused("invalid_submission_mode", submission_mode="draft_only", collection_title="合集一")
        self.refused("invalid_submission_mode", submission_mode="draft_only", method="native")
        self.refused("invalid_submission_mode", submission_mode="later")


if __name__ == "__main__":
    unittest.main()

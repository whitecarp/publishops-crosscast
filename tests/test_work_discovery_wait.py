"""Douyin's creator list fills in some time after its page shows, so an early look is not an answer. In a real browser against a small page."""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from video_publisher import work_discovery_browser as discovery
from video_publisher.core import VpubError, atomic_json, home
from shared_chrome import ChromeCase, async_playwright


class WaitTests(ChromeCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()

        async def answer(route):
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body="<!doctype html><body>Loading")
        await self.page.route("**/*", answer)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def scan(self, fill_on_tick=None, fill="暂无作品"):
        ticks = []

        async def tick(ms):
            ticks.append(ms)
            if len(ticks) == fill_on_tick:
                await self.page.evaluate("words => { document.body.innerText = words; }", fill)
        with patch.object(self.page, "wait_for_timeout", tick):
            return await discovery.scan(self.page, "douyin", "me", 5, 1), ticks

    async def test_it_waits_for_the_page_to_say_what_it_holds(self):
        result, ticks = await self.scan(fill_on_tick=6)
        self.assertEqual([], result["items"])
        self.assertEqual(6, len(ticks))

    async def test_a_page_that_never_says_stops_after_its_limit(self):
        with self.assertRaises(VpubError) as caught:
            await self.scan()
        self.assertEqual("works_layout_unverified", caught.exception.code)


class ListingWaitTests(ChromeCase):
    """From a field read: nothing at 5 seconds, then a card by 15, so ten seconds could answer that an account had no works."""
    CARD = '<div class="video-card-content-1"><div class="info-title-text-1" onclick="window.open(\'/creator-micro/work-management/work-detail/123\')">My work</div></div>'

    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()

        async def answer(route):
            body = "<!doctype html><body>Detail" if "/work-detail/" in route.request.url else "<!doctype html><body>"
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
        await self.context.route("**/*", answer)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def works(self, comes_on_tick=None, fill=""):
        from video_publisher.browser_backends import works
        ticks = []

        async def tick(ms):
            ticks.append(ms)
            if len(ticks) == comes_on_tick:
                await self.page.evaluate("words => { document.body.innerHTML = words; }", fill)
        with patch.object(self.page, "wait_for_timeout", tick):
            return await works(self.page, "douyin", "me"), ticks

    async def test_a_list_that_fills_late_is_still_read(self):
        found, ticks = await self.works(comes_on_tick=15, fill=self.CARD)
        self.assertEqual(["123"], list(found))
        self.assertGreaterEqual(len(ticks), 15)

    async def test_a_list_that_says_it_has_ended_is_not_waited_on(self):
        found, ticks = await self.works(comes_on_tick=3, fill="<p>没有更多作品</p>")
        self.assertEqual({}, found)
        self.assertEqual(3, len(ticks))

    async def test_a_list_that_never_loads_is_no_answer_when_one_is_needed(self):
        from video_publisher.browser_backends import works
        ticks = []

        async def tick(ms):
            ticks.append(ms)
        with patch.object(self.page, "wait_for_timeout", tick), self.assertRaises(VpubError) as caught:
            await works(self.page, "douyin", "me", must_answer=True)
        self.assertEqual("list_not_ready", caught.exception.code)
        self.assertEqual(45, len(ticks))

    async def test_a_hidden_title_is_no_answer_either(self):
        found, ticks = await self.works(comes_on_tick=2, fill=self.CARD.replace('class="video-card-content-1"', 'class="video-card-content-1" hidden'))
        self.assertEqual({}, found)
        self.assertEqual(45, len(ticks))

    async def test_those_words_kept_hidden_do_not_end_the_wait(self):
        found, ticks = await self.works(comes_on_tick=3, fill="<p hidden>没有更多作品</p>")
        self.assertEqual({}, found)
        self.assertEqual(45, len(ticks))


class DetailPageTests(unittest.IsolatedAsyncioTestCase):
    """A work's detail page opens in its own tab, and its address is all that's needed. A field run saw it not finish loading in 15 seconds."""
    CARD = '<div class="video-card-content-1"><div class="info-title-text-1" onclick="window.open(\'/creator-micro/work-management/work-detail/123\')">My work</div></div>'

    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()

        async def answer(route):
            if route.request.url.endswith("/never.png"):
                return  # Never answered, so the detail page never finishes loading.
            body = '<!doctype html><body><img src="/never.png">Detail' if "/work-detail/" in route.request.url else "<!doctype html><body>" + self.CARD
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
        await self.context.route("**/*", answer)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_listing_works_takes_the_address_without_waiting_for_the_page_to_finish(self):
        from video_publisher.browser_backends import works
        started = asyncio.get_running_loop().time()
        found = await works(self.page, "douyin", "me")
        self.assertEqual(["123"], list(found))
        self.assertLess(asyncio.get_running_loop().time() - started, 12)

    async def test_discovery_does_too(self):
        started = asyncio.get_running_loop().time()
        result = await discovery.scan(self.page, "douyin", "me", 5, 1)
        self.assertEqual(["123"], [item["id"] for item in result["items"]])
        self.assertLess(asyncio.get_running_loop().time() - started, 14)

    async def test_a_caption_mentioning_a_time_to_come_does_not_hide_a_published_work(self):
        from video_publisher.browser_backends import works
        self.CARD = self.CARD.replace("My work", "My work 2099年01月02日 03:04 定时发布")
        self.assertEqual(["123"], list(await works(self.page, "douyin", "me")))
        self.assertEqual(["123"], [item["id"] for item in (await discovery.scan(self.page, "douyin", "me", 5, 1))["items"]])

    async def test_a_card_that_opens_nothing_does_not_stop_the_others_being_read(self):
        from video_publisher import browser_backends
        self.CARD = '<div class="video-card-content-1"><div class="info-title-text-1">Opens nothing</div></div>' + self.CARD
        with patch.object(browser_backends, "POPUP_MS", 500):
            self.assertEqual(["123"], list(await browser_backends.works(self.page, "douyin", "me")))


    async def test_a_card_that_moves_this_tab_does_not_lose_the_others(self):
        """From a review: nothing brought the list back, so the rest would have been read from the wrong page."""
        from video_publisher import browser_backends
        self.CARD = '<div class="video-card-content-1"><div class="info-title-text-1" onclick="location.href = \'/elsewhere\'">Moves this tab</div></div>' + self.CARD
        with patch.object(browser_backends, "POPUP_MS", 500):
            self.assertEqual(["123"], list(await browser_backends.works(self.page, "douyin", "me")))


class PrivateCardTests(unittest.IsolatedAsyncioTestCase):
    """From a field run: a private work's title opens nothing, and its card's own menu opens its edit page, whose address names it."""
    WORK = "7123456789012345678"
    EDITOR = f"/creator-micro/content/post/video?mid={WORK}&enter_from=edit_item"

    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.title, self.caption, self.extra = "Secret test", "First line<br><br>Second line", ""
        self.opens = f"location.href='{self.EDITOR}'"
        self.hover = "this.parentElement.classList.add('semi-dropdown-showing'); document.getElementById('menu').hidden = false"
        self.menu_hidden = "hidden"
        self.cards = self.card("Secret test First line Second line")
        self.pressed, self.editors, self.tabs = [], [], []
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()

        async def answer(route):
            path = urlsplit(route.request.url).path
            if path == "/pressed":
                self.pressed.append(1)
                return await route.fulfill(status=200, body="")
            if path == "/stop-now":
                atomic_json(home() / "STOP.json", {"reason": "A person paused it."})
                return await route.fulfill(status=200, body="")
            if path == "/another-tab":
                self.tabs.append(await self.context.new_page())
                return await route.fulfill(status=200, body="")
            if path == "/creator-micro/content/post/video":
                self.editors.append(route.request.url)
                body = f'<input placeholder="填写作品标题，为作品获得更多流量" value="{self.title}"><div contenteditable="true">{self.caption}</div>{self.extra}'
            else:
                body = f"""{self.cards}<ul class="semi-dropdown-menu edit-menu-1" id="menu" {self.menu_hidden}><li class="drop-item-1 semi-dropdown-item" onclick="{self.opens}">编辑作品</li></ul>"""
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body="<!doctype html><body>" + body)
        await self.context.route("**/*", answer)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    def card(self, words, private=True, menu=True):
        return (f"""<div class="video-card-content-1"><div class="info-title-text-1" onclick="fetch('/pressed')">{words}</div>""" + ("<span>私密</span>" if private else "") +
                (f"""<div class="edit-btn-1"><span onmouseenter="{self.hover}">编辑作品</span></div>""" if menu else "") + "</div>")

    async def works(self):
        from video_publisher.browser_backends import MANAGE, works
        found = await works(self.page, "douyin", "me")
        self.assertEqual(MANAGE, self.page.url)  # The list being read stays where it is.
        self.assertEqual([self.page], self.context.pages)
        return found

    async def test_a_private_work_is_found_when_its_menu_opens_the_edit_page_in_place(self):
        found = await self.works()
        self.assertEqual([self.WORK], list(found))
        self.assertEqual("Secret test First line Second line", found[self.WORK]["text"])
        self.assertEqual([], self.pressed)  # Its title opens nothing, so the 15-second wait is not spent on it.

    async def test_a_private_work_is_found_when_its_menu_opens_a_new_tab(self):
        self.opens = f"window.open('{self.EDITOR}')"
        self.assertEqual([self.WORK], list(await self.works()))

    async def test_discovery_finds_it_too(self):
        result = await discovery.scan(self.page, "douyin", "me", 5, 1)
        self.assertEqual([self.WORK], [item["id"] for item in result["items"]])
        self.assertEqual(0, result["omitted_cards"])

    async def test_adopting_it_records_the_title_alone(self):
        """A card's title node goes on to hold the caption, and covers compare the title on its own."""
        result = await discovery.execute(self.page, "douyin", "me", {"action": "adopt_work", "work_id": self.WORK, "pages": 1})
        self.assertEqual((self.WORK, "Secret test", True), (result["work_id"], result["title"], result["owner_verified"]))

    async def test_a_card_whose_title_opens_nothing_also_tries_its_menu(self):
        from video_publisher import browser_backends
        self.cards = self.card("Secret test First line Second line", private=False)
        with patch.object(browser_backends, "POPUP_MS", 500):
            self.assertEqual([self.WORK], list(await self.works()))
        self.assertEqual(1, len(self.pressed))

    async def test_an_edit_page_that_says_something_else_is_not_taken_for_this_card(self):
        self.title = "Another work"
        self.assertEqual({}, await self.works())

    async def test_two_cards_that_say_the_same_are_not_told_apart(self):
        self.cards = self.card("Secret test First line Second line") * 2
        self.assertEqual({}, await self.works())
        self.assertEqual([], self.editors)

    async def test_a_card_with_no_menu_is_left_out_without_stopping_the_others(self):
        self.cards = self.card("No menu here", menu=False) + self.cards
        self.assertEqual([self.WORK], list(await self.works()))

    async def verify(self, **changes):
        from video_publisher.browser_backends import verify
        receipt = {"identity": "me", "title": "Secret test", "expected_text": "Secret test", "expected_caption": "First line\n\nSecond line", "visibility": "private",
                   "final_submit_attempted": True, "before_ids": [], "same_title_cards_before": 0, "state": "SUBMITTED_UNVERIFIED", **changes}
        return await verify(self.page, "douyin", "me", receipt)

    async def test_a_new_private_work_is_verified_from_its_card_and_edit_page(self):
        result = await self.verify()
        self.assertEqual(("SUBMISSION_VERIFIED", self.WORK), (result["state"], result["id"]))

    async def test_a_card_that_said_the_same_before_the_upload_could_be_the_old_one(self):
        result = await self.verify(same_title_cards_before=1)
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
        self.assertNotIn("id", result)

    async def test_no_count_from_before_the_upload_proves_nothing(self):
        for missing in (None, "0", False, 0.0):
            with self.subTest(missing=missing):
                result = await self.verify(same_title_cards_before=missing)
                self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
                self.assertNotIn("id", result)

    async def test_a_private_work_already_listed_before_the_upload_is_not_new(self):
        self.assertNotIn("id", await self.verify(before_ids=[self.WORK]))
        self.assertNotIn("id", await self.verify(before_ids=[int(self.WORK)]))  # Not the same ID, so it proves nothing either way.

    async def test_a_private_work_with_another_caption_is_not_verified(self):
        """From a review: left unconfirmed, it still put that other work's ID on this upload's receipt."""
        result = await self.verify(expected_caption="Something else")
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
        self.assertNotIn("id", result)

    async def test_a_private_receipt_that_already_has_its_id_is_still_held_to_its_words(self):
        self.assertEqual("SUBMISSION_VERIFIED", (await self.verify(id=self.WORK))["state"])
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.verify(id=self.WORK, expected_caption="Something else"))["state"])
        self.assertEqual("SUBMITTED_UNVERIFIED", (await self.verify(id=int(self.WORK)))["state"])

    async def test_a_tab_the_press_did_not_open_is_neither_believed_nor_closed(self):
        """From a review: words that match don't prove a page came from this card's menu."""
        from video_publisher.browser_backends import works
        self.opens = "fetch('/another-tab')"
        unrelated = await self.context.new_page()
        self.assertEqual({}, await works(self.page, "douyin", "me"))
        self.assertEqual([self.page, unrelated] + self.tabs, self.context.pages)
        self.assertEqual(1, len(self.tabs))

    async def test_an_extra_tab_beside_the_edit_page_means_no_answer(self):
        from video_publisher.browser_backends import works
        self.opens = f"fetch('/another-tab').then(() => {{ location.href = '{self.EDITOR}'; }})"
        self.assertEqual({}, await works(self.page, "douyin", "me"))
        self.assertEqual([self.page] + self.tabs, self.context.pages)

    async def test_a_menu_already_open_is_not_taken_for_this_cards(self):
        self.menu_hidden = ""
        self.assertEqual({}, await self.works())
        self.assertEqual([], self.editors)

    async def test_a_menu_no_trigger_claims_is_not_pressed(self):
        self.hover = "document.getElementById('menu').hidden = false"
        self.cards = self.card("Secret test First line Second line")
        self.assertEqual({}, await self.works())
        self.assertEqual([], self.editors)

    async def test_a_card_with_two_title_nodes_is_left_alone(self):
        self.cards = self.card("Secret test First line Second line").replace("<span>私密</span>", '<div class="info-title-text-2">Secret test First line Second line</div><span>私密</span>')
        self.assertEqual({}, await self.works())
        self.assertEqual([], self.editors)

    async def test_a_warning_that_shows_while_the_menu_opens_stops_it_before_the_press(self):
        self.hover += "; document.body.insertAdjacentHTML('beforeend', '<div role=alert>Verify your identity</div>')"
        self.cards = self.card("Secret test First line Second line")
        with self.assertRaises(VpubError) as caught:
            await self.works()
        self.assertEqual("human_verification_required", caught.exception.code)
        self.assertEqual([], self.editors)

    async def test_a_stop_that_comes_while_the_menu_opens_stops_it_before_the_press(self):
        self.hover = "const box = this.parentElement; fetch('/stop-now').then(() => { box.classList.add('semi-dropdown-showing'); document.getElementById('menu').hidden = false; })"
        self.cards = self.card("Secret test First line Second line")
        with self.assertRaises(VpubError) as caught:
            await self.works()
        self.assertEqual("stopped", caught.exception.code)
        self.assertEqual([], self.editors)

    async def test_an_error_about_the_account_is_not_taken_for_a_title_that_opens_nothing(self):
        from video_publisher.browser_backends import works
        self.cards = self.card("Secret test First line Second line", private=False)
        with patch.object(self.page, "expect_popup", side_effect=VpubError("wrong_account", "Another account.")):
            with self.assertRaises(VpubError) as caught:
                await works(self.page, "douyin", "me")
        self.assertEqual("wrong_account", caught.exception.code)
        self.assertEqual([], self.editors)

    def test_only_the_exact_address_of_an_edit_page_names_a_work(self):
        from video_publisher.browser_backends import douyin_editor_address, douyin_editor_work
        self.assertEqual(self.WORK, douyin_editor_work(douyin_editor_address(self.WORK)))
        base = f"https://creator.douyin.com/creator-micro/content/post/video?mid={self.WORK}&enter_from=edit_item"
        for address in (base.replace("https:", "http:"), base.replace("creator.douyin.com", "creator.douyin.com.example.org"), base.replace("creator.douyin.com", "someone@creator.douyin.com"),
                        base.replace("creator.douyin.com", "creator.douyin.com:8443"), base.replace("/post/video", "/manage"), base + "&mid=1", base + "#mid=1", base.replace("&enter_from=edit_item", ""), base.replace("mid=" + self.WORK, "mid=12a")):
            with self.subTest(address=address):
                self.assertIsNone(douyin_editor_work(address))

    async def test_a_warning_on_the_edit_page_stops_everything(self):
        self.extra = '<div role="alert">Verify your identity</div>'
        with self.assertRaises(VpubError) as caught:
            await self.works()
        self.assertEqual("human_verification_required", caught.exception.code)
        self.assertEqual([self.page], self.context.pages)


if __name__ == "__main__":
    unittest.main()

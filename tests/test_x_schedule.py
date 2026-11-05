"""Scheduling on X through Media Studio's own calendar, in a real browser against a small copy of what a signed-in account showed."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from video_publisher import browser_backends, browser_forms, browser_runtime, cli, x_pages
from video_publisher.core import VpubError, atomic_json, home, load_plan
from shared_chrome import ChromeCase, async_playwright

OWNER = "owner"


def page_html(keep_clock=True, final_after="Done", shows=None, closes=True, done_closes=True, final_elsewhere=False, row_extra="", both_elsewhere=False):
    """Follows what a field run saw: the calendar has its own Clear and Save, and only its Save changes the buttons below it, sending nothing."""
    months = "".join(f'<option value="{i}">{name}</option>' for i, name in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()))
    years = "".join(f'<option value="{year}">{year}</option>' for year in range(2026, 2032))
    final_button = '<button type="button" id="final" class="Button Button--primary tweetbtn">Post</button>'
    opener = '<button type="button" id="open" class="Button Button--link">Schedule</button>'
    row = f'<div class="row">{opener}{row_extra}{final_button}</div>'
    return f"""<!doctype html><body><div id="page"><div id="composer"><video src="https://studio.x.com/this-file.mp4"></video><textarea name="tweetData.text" placeholder="What's happening?">Words</textarea>
      <div id="panel" hidden>
        <fieldset class="CalendarNavigation-monthFormSelect"><select id="m">{months}</select></fieldset>
        <fieldset class="CalendarNavigation-yearFormSelect"><select id="y">{years}</select></fieldset>
        <div class="Calendar Calendar--fixedHeight" id="days"></div>
        <input type="text" class="clock" id="clock" value="12:24 AM">
        <button type="button" class="Button Button--small">Clear</button>
        <button type="button" id="calendar-save" class="Button Button--primary Button--small">Save</button>
      </div>
      <input type="text" name="other" value="not a time">
      {"" if both_elsewhere else f'<div class="row">{opener}{row_extra}{"" if final_elsewhere else final_button}</div>'}</div>
      {f'<div id="another-composer"><video src="https://studio.x.com/another-file.mp4"></video><div class="row">{final_button}</div></div>' if final_elsewhere else ""}
      {f'<div id="another-composer"><video src="https://studio.x.com/another-file.mp4"></video>{row}</div>' if both_elsewhere else ""}</div>
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
          cell.onclick = () => {{ for (const c of days.children) c.classList.remove('is-rangeStart'); cell.classList.add('is-rangeStart'); }};
          days.append(cell);
        }}
      }}
      m.onchange = draw; y.onchange = draw; draw();
      document.getElementById('open').onclick = () => {{ document.getElementById('panel').hidden = false; }};
      document.getElementById('calendar-save').onclick = () => {{
        const names = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(' ');
        const day = document.querySelector('.is-rangeStart .Calendar-dayText').textContent;
        document.getElementById('open').textContent = {repr(shows) if shows else "names[Number(m.value)] + ' ' + day + ', ' + document.getElementById('clock').value"};
        document.getElementById('panel').hidden = {'true' if closes else 'false'};
        final.textContent = {final_after!r};
      }};
      {'' if keep_clock else "document.getElementById('clock').onchange = e => { e.target.value = '12:24 AM'; };"}
      final.onclick = () => fetch('/save', {{method: 'POST', body: JSON.stringify({{month: m.value, year: y.value, day: document.querySelector('.is-rangeStart .Calendar-dayText').textContent, clock: document.getElementById('clock').value}})}}){".then(() => { final.hidden = true; })" if done_closes else ""};
      </script>"""


class OpeningMediaTests(ChromeCase):
    """The address of the uploaded file's page is kept once it changes, whether or not the page has finished loading."""
    LIBRARY = f"""<!doctype html><body><a href="https://analytics.twitter.com/user/{OWNER}/home">Analytics</a><button>Upload Media</button>
      <p onclick="location.href='/library/13_77/edit'">clip.mp4</p>"""
    @staticmethod
    def form_for(filename):
        return f"""<div class="form"><div><a download="{filename}" href="#">Download</a><div>{filename}</div><button>Change thumbnail</button>
          <button type="button" onclick="document.getElementById('words').hidden = false">Post</button></div>
          <input placeholder="Title" value="original"><textarea placeholder="Type a brief description here">original</textarea><textarea id="words" placeholder="What's happening?" hidden></textarea></div>"""

    FORM = form_for.__func__("clip.mp4")
    MEDIA = f"<!doctype html><body><img src=\"/never.png\"><div id=\"everything\"><button>Upload Media</button><p>clip.mp4</p>{FORM}</div>"

    class Tx:
        identity = OWNER
        options = {"title": "Title", "description": "Words", "post_text": "Words", "submission_mode": "draft_only"}

        def __init__(self):
            self.receipt = {"before_ids": [], "media_selected": True, "upload_filename": "clip.mp4"}

        def save(self, **changes):
            self.receipt.update(changes)
            return dict(self.receipt)

        def cover(self):
            return None

    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page(locale="en-US")
        self.page.set_default_timeout(5000)

        async def answer(route):
            path = urlsplit(route.request.url).path
            if path == "/never.png":
                return  # Never answered, so the page never finishes loading.
            if path == "/stop-now":
                atomic_json(home() / "STOP.json", {"reason": "A person paused it."})
                return await route.fulfill(status=200, body="")
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=self.MEDIA if path.endswith("/edit") else self.LIBRARY)
        await self.page.route("**/*", answer)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_a_post_button_that_is_gone_for_a_moment_after_the_details_is_waited_for(self):
        """From a field run: one look straight after filling the details found no such button, and a moment later it was there."""
        self.MEDIA += """<script>document.querySelector('input[placeholder=Title]').addEventListener('change', () => {
          const post = [...document.querySelectorAll('button[type=button]')].find(button => button.textContent === 'Post');
          post.hidden = true; setTimeout(() => { post.hidden = false; }, 3000); });</script>"""
        self.assertEqual("PREVIEW_READY", (await browser_forms.publish_x(self.page, self.Tx()))["state"])

    async def test_a_hidden_copy_of_the_name_does_not_stop_the_click(self):
        """From a review: two copies of the name, one hidden, and no click at all in ten minutes."""
        self.LIBRARY = self.LIBRARY.replace("<p onclick", '<span hidden>clip.mp4</span><p onclick')
        tx = self.Tx()
        self.assertEqual("PREVIEW_READY", (await browser_forms.publish_x(self.page, tx))["state"])
        self.assertEqual("https://studio.x.com/library/13_77/edit", tx.receipt["media_url"])

    async def test_a_hidden_copy_of_the_form_does_not_stand_in_front_of_the_one_showing(self):
        """From a review: the first match was hidden, so the form showing behind it never counted."""
        hidden = re.sub(r'<textarea id="words".*?</textarea>', "", self.FORM.replace('<div class="form">', '<div class="form" hidden>'))
        self.LIBRARY = self.library_that_opens_in_place(hidden + self.FORM)
        tx = self.Tx()
        self.assertEqual("PREVIEW_READY", (await browser_forms.publish_x(self.page, tx))["state"])
        self.assertEqual("Title", await self.page.locator('div.form:visible').get_by_placeholder("Title", exact=True).input_value())

    async def test_two_forms_showing_for_one_file_are_not_filled(self):
        await self.stops_without_filling(self.library_that_opens_in_place(self.FORM + self.FORM))

    async def test_two_names_showing_are_not_clicked_and_the_timeout_says_so(self):
        self.LIBRARY = self.LIBRARY.replace("<p onclick", "<p>clip.mp4</p><p onclick")
        message = await self.stops_without_filling(self.LIBRARY)
        self.assertIn("'clicks': 0", message)
        self.assertIn("'names_showing': 2", message)
        self.assertIn("vpub resume", message)
        self.assertEqual("https://studio.x.com/library", self.page.url)

    async def test_a_page_that_opens_with_no_form_says_where_it_stopped_and_when(self):
        """From a field run: one click, the name twice and this file's link, and ten minutes with no form. A fresh page then opened it at once."""
        self.MEDIA = """<!doctype html><body><p>clip.mp4</p><p>clip.mp4</p><a download="clip.mp4" href="#">Download</a><button hidden>Change thumbnail</button>"""
        message = await self.stops_without_filling(self.LIBRARY)
        for said in ("'clicks': 1", "'names_showing': 2", "'links_showing': 1", "'change_buttons': 1", "'change_showing': 0", "'other_links': 0", "'path': '/library/<n>_<n>/edit'", "'first_click_at': 0", "'link_first_at'", "vpub resume"):
            self.assertIn(said, message)
        self.assertNotIn("13_77", message)

    def library_with_details(self, then="", tile_goes=""):
        """What a field run's click five seconds in opened: this file's link and title box, still on /library, with no Change thumbnail. The name in the details opens the wrong page, to show if it gets clicked."""
        details = """<div id="details"><h2>Media details</h2><p onclick="location.href='/library/99_99/edit'">clip.mp4</p><a download="clip.mp4" href="#">Download</a><input placeholder="Title" value="original"><textarea placeholder="Type a brief description here"></textarea></div>"""
        return f"""<!doctype html><body><a href="https://analytics.twitter.com/user/{OWNER}/home">Analytics</a><div id="library"><button>Upload Media</button>
          <p onclick="if (window.opened) {{ {then} return; }} window.opened = true; {tile_goes} document.getElementById('spot').innerHTML = document.getElementById('held').innerHTML;">clip.mp4</p></div>
          <div id="spot"></div><template id="held">{details}</template>"""

    async def test_a_second_click_on_the_tile_gets_from_the_files_details_to_its_form(self):
        """From two field runs in a row: ten minutes in those details, because two names showing stopped any further click. No page was left, so no upload could be cut off."""
        self.LIBRARY = self.library_with_details(then="location.href = '/library/13_77/edit';")
        tx = self.Tx()
        with patch.object(browser_forms, "X_OPEN_SECONDS", 1):
            self.assertEqual("PREVIEW_READY", (await browser_forms.publish_x(self.page, tx))["state"])
        self.assertEqual("https://studio.x.com/library/13_77/edit", tx.receipt["media_url"])
        self.assertEqual(2, tx.receipt["open_look"]["clicks"])

    async def test_details_that_stay_are_clicked_past_on_the_tile_and_filled_in_no_form(self):
        with patch.object(browser_forms, "X_OPEN_SECONDS", 1):
            message = await self.stops_without_filling(self.library_with_details())
        self.assertNotIn("'clicks': 1", message)
        for said in ("'names_showing': 2", "'links_showing': 1", "'change_buttons': 0", "'path': '/library'", "vpub resume"):
            self.assertIn(said, message)
        self.assertEqual("https://studio.x.com/library", self.page.url)

    async def test_with_no_name_out_among_the_tiles_nothing_more_is_clicked(self):
        library = self.library_with_details(tile_goes="this.hidden = true; document.getElementById('held').content.getElementById('details').insertAdjacentHTML('beforeend', '<p>clip.mp4</p>');")
        with patch.object(browser_forms, "X_OPEN_SECONDS", 1):
            message = await self.stops_without_filling(library)
        self.assertIn("'clicks': 1", message)
        self.assertIn("'names_showing': 2", message)

    async def test_two_names_out_among_the_tiles_are_not_guessed_between(self):
        library = self.library_with_details(tile_goes="this.insertAdjacentHTML('afterend', '<p>clip.mp4</p>');")
        with patch.object(browser_forms, "X_OPEN_SECONDS", 1):
            message = await self.stops_without_filling(library)
        self.assertIn("'clicks': 1", message)
        self.assertIn("'names_showing': 3", message)

    async def test_a_resume_has_no_new_upload_to_wait_for(self):
        with patch.object(browser_forms, "X_FIRST_CLICK_SECONDS", 1000):
            self.assertEqual("PREVIEW_READY", (await browser_forms.publish_x(self.page, self.Tx()))["state"])

    def library_with_label(self, script, tile_waits=True):
        """Library with a loading label that the script drives. While the label shows, a click on the tile does nothing, as a click on a file still going up got no form."""
        library = type(self).LIBRARY.replace("<p onclick=\"", "<p onclick=\"if (document.getElementById('going')) return; ") if tile_waits else type(self).LIBRARY
        return library + f"""<p id="going">Loading 10%</p><script>{script}</script>"""

    @staticmethod
    def cut(first):
        """wait_until with the usual wait for the form cut to `first` seconds and every other wait as asked, and the seconds asked."""
        real, asked = browser_forms.wait_until, []

        async def soon(page, predicate, *, seconds=120, label="page state"):
            asked.append(seconds)
            return await real(page, predicate, seconds=first if seconds >= 600 else seconds, label=label)
        return soon, asked

    async def test_a_file_still_going_up_is_waited_for_while_its_number_moves(self):
        """From a field run: a 61 MB file's tile said Loading 73% thirty seconds in, and leaving before 100% lost the upload. The label outlasts the usual wait here."""
        self.LIBRARY = self.library_with_label("let done = 10; const timer = setInterval(() => { done += 5; const going = document.getElementById('going');"
                                               " if (done > 90) { going.remove(); clearInterval(timer); } else { going.textContent = 'Loading ' + done + '%'; } }, 500);")
        tx = self.Tx()
        soon, asked = self.cut(4)
        with patch.object(browser_forms, "wait_until", soon), patch.object(browser_forms, "X_LOADING_WAIT_SECONDS", 37), patch.object(browser_forms, "X_LOADING_CLEAR_SECONDS", 1):
            self.assertEqual("PREVIEW_READY", (await browser_forms.publish_x(self.page, tx))["state"])
        self.assertIn(37, asked)
        self.assertGreaterEqual(tx.receipt["open_look"]["loading_moves"], 1)
        self.assertEqual(1, tx.receipt["open_look"]["clicks"])

    async def test_a_number_that_stays_put_stops_holding_the_click_back(self):
        """From a review: a label stuck at one number may not be this upload at all, and must not stop every click for good. One at 100 holds nothing back."""
        for number, held in ((73, True), (100, False)):
            with self.subTest(number=number):
                self.LIBRARY = type(self).LIBRARY + f'<p id="going">Loading {number}%</p>'
                tx = self.Tx()
                with patch.object(browser_forms, "X_LOADING_STALL_SECONDS", 2), patch.object(browser_forms, "X_LOADING_CLEAR_SECONDS", 1):
                    self.assertEqual("PREVIEW_READY", (await browser_forms.publish_x(self.page, tx))["state"])
                look = tx.receipt["open_look"]
                self.assertEqual(1, look["clicks"])
                if held:
                    self.assertGreaterEqual(look["first_click_at"], 2)
                    self.assertNotIn("loading_moves", look)
                else:
                    self.assertEqual(0, look["first_click_at"])
                    self.assertNotIn("loading", look)

    async def test_a_wait_that_would_outlast_its_command_ends_with_its_own_answer(self):
        """From a review: the worker cuts a command off at 30 minutes, which would close the browser mid-upload with nothing recorded."""
        self.LIBRARY = self.library_with_label("let done = 10; setInterval(() => { done = done % 90 + 7; document.getElementById('going').textContent = 'Loading ' + done + '%'; }, 400);")
        tx = self.Tx()
        with patch.object(browser_runtime, "command_deadline", time.monotonic() + 15), patch.object(browser_forms, "X_COMMAND_MARGIN_SECONDS", 0), self.assertRaises(VpubError) as caught:
            await browser_forms.publish_x(self.page, tx)
        self.assertEqual("upload_too_slow", caught.exception.code)
        self.assertIn("existing_media_url", caught.exception.hint)

    async def test_a_file_whose_form_once_showed_is_not_chosen_again(self):
        """Only a file never opened can have been lost: one whose form showed is in Library, and choosing it again would make two tiles of one name. This Tx has no file to choose."""
        self.LIBRARY = self.LIBRARY.replace(">clip.mp4</p>", "></p>")
        tx = self.Tx()
        tx.receipt["open_look"] = {"clicks": 1}
        soon, _ = self.cut(1)
        with patch.object(browser_forms, "wait_until", soon), self.assertRaises(VpubError) as caught:
            await browser_forms.publish_x(self.page, tx)
        self.assertEqual("processing_timeout", caught.exception.code)
        self.assertIn("never uploads it again", caught.exception.message)
        self.assertNotIn("lost_uploads", tx.receipt)

    def test_a_bigger_file_is_left_alone_for_longer(self):
        self.assertEqual(browser_forms.X_FIRST_CLICK_SECONDS + 56.5, browser_forms.x_first_click_hold(113_000_000))

    async def test_after_a_timeout_a_resume_opens_the_file_on_a_fresh_page(self):
        """From a field run: ten minutes on the page it uploaded from, and then a fresh page opened the same file with one click. No second upload."""
        ready = self.LIBRARY
        await self.stops_without_filling(self.LIBRARY.replace("<p onclick", "<p>clip.mp4</p><p onclick"))
        self.LIBRARY = ready
        tx = self.Tx()
        self.assertEqual("PREVIEW_READY", (await browser_forms.publish_x(self.page, tx))["state"])
        self.assertEqual("https://studio.x.com/library/13_77/edit", tx.receipt["media_url"])

    async def test_a_stop_that_comes_as_the_file_opens_holds_its_details_back(self):
        self.LIBRARY = self.LIBRARY.replace("location.href='/library/13_77/edit'", "fetch('/stop-now').then(() => { location.href = '/library/13_77/edit'; })")
        with self.assertRaises(VpubError) as caught:
            await browser_forms.publish_x(self.page, self.Tx())
        self.assertEqual("stopped", caught.exception.code)
        self.assertEqual("original", await self.page.get_by_placeholder("Title", exact=True).input_value())

    async def test_a_click_that_does_nothing_at_first_comes_again_while_the_name_is_on_the_tile_alone(self):
        self.LIBRARY = self.LIBRARY.replace("location.href=", "if (++window.clicks > 2) location.href=") + "<script>window.clicks = 0</script>"
        tx = self.Tx()
        with patch.object(browser_forms, "X_OPEN_SECONDS", 1):
            self.assertEqual("PREVIEW_READY", (await browser_forms.publish_x(self.page, tx))["state"])
        self.assertEqual("https://studio.x.com/library/13_77/edit", tx.receipt["media_url"])

    def library_that_opens_in_place(self, held, delay=0):
        return f"""<!doctype html><body><a href="https://analytics.twitter.com/user/{OWNER}/home">Analytics</a><button>Upload Media</button><div id="spot"></div>
          <p onclick="if (window.clicks) return; window.clicks = 1; setTimeout(() => {{ document.getElementById('spot').innerHTML = document.getElementById('held').innerHTML; }}, {delay})">clip.mp4</p>
          <template id="held">{held}</template>"""

    async def stops_without_filling(self, library, seconds=3):
        self.LIBRARY = library
        real = browser_forms.wait_until

        async def limited(page, predicate, *, label="page state", **_):
            return await real(page, predicate, seconds=seconds, label=label)
        with patch.object(browser_forms, "wait_until", limited), self.assertRaises(VpubError) as caught:
            await browser_forms.publish_x(self.page, self.Tx())
        self.assertEqual("processing_timeout", caught.exception.code)
        for box in await self.page.get_by_placeholder("Title", exact=True).all():
            self.assertEqual("original", await box.input_value())
        return caught.exception.message

    async def test_a_form_that_opens_with_the_address_unchanged_counts_and_is_never_clicked_again(self):
        """From a field run: 15 seconds after the click the address was unchanged and the name showed twice, and a second click stopped on the two matches."""
        self.LIBRARY = self.library_that_opens_in_place(self.FORM, 1500)
        tx = self.Tx()
        with patch.object(browser_forms, "X_OPEN_SECONDS", 1):
            self.assertEqual("PREVIEW_READY", (await browser_forms.publish_x(self.page, tx))["state"])
        self.assertNotIn("media_url", tx.receipt)
        self.assertEqual("Title", await self.page.get_by_placeholder("Title", exact=True).input_value())

    async def test_another_files_form_is_never_filled(self):
        """From a review: one click on this file's tile, another file's form opening, and this file's name elsewhere on the page. Its form got filled, and composing from it could post the wrong video."""
        other = self.form_for("other.mp4")
        for name, held in (("name hidden elsewhere", f"<p hidden>clip.mp4</p>{other}"), ("name visible elsewhere", f"<p>clip.mp4</p>{other}"), ("no name elsewhere", other),
                           ("a form with no link at all", self.FORM.replace('<a download="clip.mp4" href="#">Download</a>', ""))):
            with self.subTest(name):
                await self.stops_without_filling(self.library_that_opens_in_place(held))

    async def test_a_form_beside_this_files_link_is_not_its_form(self):
        link = '<a download="clip.mp4" href="#">Download</a>'
        for name, held in (("behind Upload Media", f"{link}<div><button>Upload Media</button>{self.form_for('other.mp4')}</div>"), ("from a review: another file's form with its own link, in the same wider element", link + self.form_for("other.mp4"))):
            with self.subTest(name):
                await self.stops_without_filling(self.library_that_opens_in_place(held))

    def test_a_file_name_with_quotes_is_still_one_name(self):
        self.assertEqual('a[download="a\\"b\\\\c.mp4"]', browser_forms.x_file_link('a"b\\c.mp4'))

    async def test_a_file_that_never_opens_stops_and_leaves_it_for_a_resume(self):
        self.LIBRARY = self.LIBRARY.replace("location.href=", "window.ignored=")
        tx = self.Tx()
        real = browser_forms.wait_until

        async def soon(page, predicate, *, seconds=120, label="page state"):
            return await real(page, predicate, seconds=2, label=label)
        with patch.object(browser_forms, "X_OPEN_SECONDS", 1), patch.object(browser_forms, "wait_until", soon), self.assertRaises(VpubError) as caught:
            await browser_forms.publish_x(self.page, tx)
        self.assertEqual("processing_timeout", caught.exception.code)
        self.assertNotIn("media_url", tx.receipt)
        self.assertEqual((True, "clip.mp4"), (tx.receipt["media_selected"], tx.receipt["upload_filename"]))

    def test_an_address_gives_the_files_page_only_once_it_names_the_file(self):
        for address, wanted in (("https://studio.x.com/library/13_77/edit", {"media_url": "https://studio.x.com/library/13_77/edit"}), ("https://studio.x.com/library/13_77/compose", {"media_url": "https://studio.x.com/library/13_77/edit"}),
                                ("https://studio.x.com/library", {}), ("https://elsewhere.example/library/13_77/edit", {}), ("http://studio.x.com/library/13_77/edit", {}),
                                ("https://elsewhere.example/view?next=https://studio.x.com/library/13_77/edit", {}), ("https://elsewhere.example/quoted/https://studio.x.com/library/13_77/edit", {}),
                                ("https://studio.x.com/library?quoted=https://studio.x.com/library/13_77/edit", {}), ("https://studio.x.com.elsewhere.example/library/13_77/edit", {})):
            with self.subTest(address=address):
                self.assertEqual(wanted, browser_forms.x_media_address(address))

    async def test_a_media_page_still_loading_does_not_stop_the_upload_and_its_address_is_kept(self):
        tx = self.Tx()
        self.assertEqual("PREVIEW_READY", (await browser_forms.publish_x(self.page, tx))["state"])
        self.assertIs(True, tx.receipt["media_details_filled"])
        self.assertEqual("https://studio.x.com/library/13_77/edit", tx.receipt["media_url"])
        self.assertEqual("Words", await self.page.get_by_placeholder("What's happening?", exact=True).input_value())


class Calendar(ChromeCase):
    quick_pauses = True  # This copy of the site has no timers of its own, so a pause can end once the page is quiet.
    async def asyncSetUp(self):
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

    async def test_only_done_counts_as_the_final_press(self):
        """From a field run: the calendar's own Save was taken for the final press, so the receipt said it had gone, and nothing had."""
        await self.open()
        tx = StubTx({"schedule": self.next_month_day().isoformat()})
        with patch.object(browser_forms, "verify_scheduled_x", new=AsyncMock(return_value={"state": "SUBMISSION_VERIFIED"})):
            result = await self.send(tx)
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertEqual(["Done"], tx.pressed)
        self.assertEqual(1, len(self.saved))
        self.assertEqual("3:07 PM", self.saved[0]["clock"])
        self.assertTrue(tx.receipt["schedule"]["at"])

    async def send(self, tx, form="#page", video="https://studio.x.com/this-file.mp4"):
        return await browser_forms.x_send_scheduled(self.page, tx, await self.page.locator(form).element_handle(), video)

    async def refuses(self, html, code="schedule_unverified", form="#page", video="https://studio.x.com/this-file.mp4", **options):
        await self.open(html)
        tx = StubTx({"schedule": self.next_month_day().isoformat(), **options})
        with self.assertRaises(VpubError) as caught:
            await self.send(tx, form, video)
        self.assertEqual(code, caught.exception.code)
        self.assertIn("Nothing was posted", caught.exception.message)
        self.assertEqual([], tx.pressed)
        self.assertEqual([], self.saved)
        return caught.exception.message

    async def test_a_stop_says_what_its_checks_saw(self):
        """From a field run: Done on the page, one button greyed out, and the same words for every cause."""
        greyed = page_html().replace("final.textContent = 'Done';", "final.textContent = 'Done'; final.disabled = true;")
        message = await self.refuses(greyed)
        for said in ("'says_done': 1", "'final_enabled': False", "'finals': 1", "'calendar_open': 0", "'times_showing': 1", "'time_is_this_schedule': True", "'words_are_the_plans': True", "'words_length': 5", "'tied_to_the_file': True", "'final_class_says_off': False", "'final_aria_says_off': False"):
            self.assertIn(said, message)
        self.assertNotIn("tweetbtn", message)
        self.assertNotIn("Words", message.split("Last look")[1].replace("words", ""))

    async def test_a_stop_for_another_files_composer_says_so(self):
        message = await self.refuses(page_html(), video="https://studio.x.com/another-file.mp4")
        self.assertIn("'tied_to_the_file': False", message)

    async def test_a_final_button_that_still_says_post_is_never_pressed(self):
        """It could post at once, in public, with the time ignored."""
        await self.refuses(page_html(final_after="Post"))

    async def test_a_final_button_with_any_other_word_is_never_pressed(self):
        await self.refuses(page_html(final_after="Publish now"))

    async def test_another_time_on_the_button_stops_it(self):
        await self.refuses(page_html(shows="Jan 1, 1:11 AM"))

    async def test_no_time_on_the_button_stops_it(self):
        await self.refuses(page_html(shows="Schedule"))

    async def test_a_calendar_that_stays_open_stops_it(self):
        await self.refuses(page_html(closes=False))

    async def test_words_that_changed_in_the_composer_stop_it(self):
        await self.refuses(page_html(), code="metadata_mismatch", post_text="Other words")

    async def changed_during_the_last_safety_wait(self, script, code):
        """From a review: all of it was read, then the safety checks waited, and what had been read had changed by the press."""
        class Changes(StubTx):
            async def submit(self, page, button, still_holds=None):
                await page.evaluate(script)
                await super().submit(page, button, still_holds)
        await self.open()
        tx = Changes({"schedule": self.next_month_day().isoformat()})
        with self.assertRaises(VpubError) as caught:
            await self.send(tx)
        self.assertEqual(code, caught.exception.code)
        self.assertEqual(([], []), (tx.pressed, self.saved))

    async def test_a_button_that_turns_back_to_post_during_the_last_safety_wait_is_not_pressed(self):
        await self.changed_during_the_last_safety_wait("document.getElementById('final').textContent = 'Post'", "schedule_unverified")

    async def test_words_that_change_during_the_last_safety_wait_are_not_sent(self):
        await self.changed_during_the_last_safety_wait("document.querySelector('textarea').value = 'Other words'", "metadata_mismatch")

    async def test_a_time_that_changes_during_the_last_safety_wait_is_not_sent(self):
        await self.changed_during_the_last_safety_wait("document.getElementById('open').textContent = 'Jan 1, 1:11 AM'", "schedule_unverified")

    async def test_a_label_that_changes_in_the_very_last_moment_still_cannot_be_pressed(self):
        """The press itself asks for the word Done."""
        from patchright.async_api import Error as BrowserError

        class TurnsBack(StubTx):
            async def submit(self, page, button, still_holds=None):
                await still_holds()
                await page.evaluate("document.getElementById('final').textContent = 'Post'")
                await button.click(timeout=1000)
        await self.open()
        with self.assertRaises(BrowserError):
            await self.send(TurnsBack({"schedule": self.next_month_day().isoformat()}))
        self.assertEqual([], self.saved)

    async def test_a_done_button_in_another_composer_is_not_this_ones(self):
        """From a review: the words and the time from one composer, and the only final button on the page in another."""
        await self.refuses(page_html(final_elsewhere=True))

    async def test_the_time_and_done_together_in_another_composer_are_not_this_ones(self):
        """From a review: they sat side by side, and only a wider element held them with the words."""
        await self.refuses(page_html(both_elsewhere=True))

    async def test_a_composer_outside_this_files_form_is_not_its_own(self):
        await self.refuses(page_html(), form="#panel")

    async def test_a_composer_showing_another_video_is_not_this_files(self):
        await self.refuses(page_html(), video="https://studio.x.com/another-file.mp4")

    async def test_with_no_video_on_record_nothing_final_is_pressed(self):
        """From a review: with nothing to compare, any video or none would pass."""
        await self.refuses(page_html(), video=None)

    async def test_a_composer_that_is_really_the_whole_form_is_not_one(self):
        """From a review: the words in one part, the time and Done in another, and only the form around both, holding the one video."""
        await self.refuses(page_html(both_elsewhere=True).replace('<div id="another-composer"><video src="https://studio.x.com/another-file.mp4"></video>', '<div id="another-composer">'))

    async def test_a_row_with_more_in_it_than_the_time_and_done_is_not_trusted(self):
        await self.refuses(page_html(row_extra='<button type="button">Another action</button>'))

    async def test_a_row_with_an_icon_beside_them_is_still_their_row(self):
        await self.open(page_html(row_extra='<button type="button" aria-label="Help"><svg width="9" height="9"></svg></button>'))
        tx = StubTx({"schedule": self.next_month_day().isoformat()})
        with patch.object(browser_forms, "verify_scheduled_x", new=AsyncMock(return_value={"state": "SUBMITTED_UNVERIFIED"})):
            await self.send(tx)
        self.assertEqual(["Done"], tx.pressed)

    async def test_a_composer_that_stays_open_after_done_is_not_left_behind(self):
        """From a review: the wait for it to close could time out, and the page moved on regardless, which could cut off what Done had started."""
        await self.open(page_html(done_closes=False))
        tx = StubTx({"schedule": self.next_month_day().isoformat()})
        with patch.object(browser_forms, "X_DONE_SECONDS", 1), patch.object(browser_forms, "verify_scheduled_x", new=AsyncMock(side_effect=AssertionError("left the page"))):
            result = await self.send(tx)
        self.assertEqual(("SUBMITTED_UNVERIFIED", "readback_pending"), (result["state"], result["phase"]))
        self.assertIn("do not post again", result["note"])
        self.assertEqual(["Done"], tx.pressed)
        self.assertEqual(1, len(self.saved))

    async def test_a_composer_that_closes_after_done_is_then_read_back(self):
        await self.open()
        tx = StubTx({"schedule": self.next_month_day().isoformat()})
        with patch.object(browser_forms, "verify_scheduled_x", new=AsyncMock(return_value={"state": "SUBMISSION_VERIFIED", "phase": "scheduled"})) as read_back:
            result = await self.send(tx)
        read_back.assert_awaited_once()
        self.assertEqual("scheduled", result["phase"])

    def test_the_time_on_the_button_is_read_as_a_date_and_a_time(self):
        evening, just_after_midnight, just_after_noon = {"date": [2026, 11, 4], "time": [18, 26]}, {"date": [2026, 11, 4], "time": [0, 5]}, {"date": [2026, 11, 4], "time": [12, 5]}
        for words, schedule, wanted in (("Nov 4, 6:26 PM", evening, True), ("Nov 4, 2026, 6:26 PM", evening, True), ("Nov 4, 2027, 6:26 PM", evening, False), ("Nov 4, 6:26 AM", evening, False),
                                        ("Nov 5, 6:26 PM", evening, False), ("Dec 4, 6:26 PM", evening, False), ("Schedule", evening, False), ("Done", evening, False), ("Xyz 4, 6:26 PM", evening, False),
                                        ("Nov 4, 12:05 AM", just_after_midnight, True), ("Nov 4, 12:05 PM", just_after_midnight, False), ("Nov 4, 12:05 PM", just_after_noon, True),
                                        ("Nov 4, 18:26 PM", evening, False), ("Nov 4, 0:05 AM", just_after_midnight, False), ("Nov 4, 24:05 PM", just_after_noon, False), ("Nov 4, 6:75 PM", evening, False)):
            with self.subTest(words=words, schedule=schedule):
                self.assertIs(wanted, browser_forms.x_chosen_time_says(words, schedule))

    async def test_a_schedule_that_fails_saves_nothing(self):
        await self.open(page_html(keep_clock=False))
        with self.assertRaises(VpubError):
            await self.send(StubTx({"schedule": self.next_month_day().isoformat()}))
        self.assertEqual([], self.saved)


class StubTx:
    identity = OWNER

    def __init__(self, options):
        self.options, self.receipt, self.pressed = {"post_text": "Words", **options}, {}, []

    def save(self, **changes):
        self.receipt.update(changes)
        return dict(self.receipt)

    async def submit(self, page, button, still_holds=None):
        if still_holds:
            await still_holds()
        self.pressed.append(await button.inner_text())
        await button.click(timeout=5000)


class FinalPressTests(unittest.IsolatedAsyncioTestCase):
    """The real receipt's order: the safety checks wait, then what was read is read again, and only then is the press recorded and made."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"VPUB_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.tx = browser_backends.Transaction("x", OWNER, {"confirmation": "a" * 64, "video_sha256": "0" * 64, "options": {"title": "T"}})
        self.button = AsyncMock()
        self.page = AsyncMock(url="https://studio.x.com/library/1_2/edit")

    async def press(self, still_holds):
        with patch.object(browser_backends, "risk_check", new=AsyncMock()):
            await self.tx.submit(self.page, self.button, still_holds=still_holds)

    async def test_a_check_that_fails_records_nothing_and_presses_nothing(self):
        with self.assertRaises(VpubError):
            await self.press(AsyncMock(side_effect=VpubError("schedule_unverified", "No.")))
        self.button.click.assert_not_awaited()
        self.assertIs(False, self.tx.receipt["final_submit_attempted"])

    async def test_a_check_that_holds_is_followed_by_one_recorded_press(self):
        async def holds():
            self.assertIs(False, self.tx.receipt["final_submit_attempted"])
        await self.press(holds)
        self.button.click.assert_awaited_once()
        self.assertIs(True, self.tx.receipt["final_submit_attempted"])


class ReadBackTests(unittest.IsolatedAsyncioTestCase):
    def receipt(self, **extra):
        return {"identity": OWNER, "title": "My title", "before_ids": ["1"], "schedule": {"at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}, **extra}

    def page(self, body):
        page = AsyncMock()
        page.locator = lambda selector: AsyncMock(inner_text=AsyncMock(return_value=body), all=AsyncMock(return_value=[]))
        page.get_by_text = lambda *a, **k: AsyncMock(all=AsyncMock(return_value=[]))
        return page

    async def after_its_time(self, posts, **extra):
        moment = datetime.now(timezone.utc) - timedelta(minutes=5)
        receipt = self.receipt(schedule={"at": moment.isoformat()}, **extra)
        rows = [{"published_at": moment.isoformat(), "has_video": True, "media_id": "77", **post} for post in posts]
        receipt.setdefault("media_url", "https://studio.x.com/library/13_77/edit")
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

    async def test_a_different_video_or_none_it_can_read_leaves_only_a_candidate_for_a_person(self):
        for number in ("88", None):
            with self.subTest(number=number):
                result, read = await self.after_its_time([{"id": "2", "text": "My title", "media_id": number}])
                self.assertEqual(("SUBMITTED_UNVERIFIED", "2"), (result["state"], result["candidate_id"]))
                self.assertIn("/status/2", result["candidate_url"])
                self.assertNotIn("id", result)
                self.assertIn("do not post again", result["note"])
                read.assert_not_awaited()

    async def test_no_studio_number_on_the_receipt_leaves_only_a_candidate(self):
        result, read = await self.after_its_time([{"id": "2", "text": "My title"}], media_url="")
        self.assertEqual("2", result["candidate_id"])
        read.assert_not_awaited()

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


class ScheduleRowTests(ChromeCase):
    """Before its time, against a copy of what a field run read on Studio's schedule page once a post had been scheduled: seven cells and no ID, with its date, time, words and media in that order."""
    quick_pauses = True
    WORDS = "My title\n\nThe words of the post."
    SCHEDULE = {"date": [2026, 11, 8], "time": [21, 11]}  # A Sunday.

    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page(locale="en-US")
        self.page.set_default_timeout(5000)
        await self.page.route("**/*", self.handle)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def handle(self, route):
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=self.html)

    def row(self, day="Nov 8", weekday="Sun", clock="9:11 PM", words=None, media="Video"):
        return f"""<tr class="oLHZoSmM"><td class="UxBgyW8K"><div><div class="pJ3TNqK2">{day}</div><div>{weekday}</div></div></td><td class="Table-cell--alignLeft"><div>{clock}</div></td>
          <td class="Table-cell--alignLeft"><div class="xicUfVmZ" style="white-space: pre-wrap"><span>{self.WORDS if words is None else words}</span><span></span></div><div class="SC5SKZZJ"><div class="pJnPcxVd" style="background-image: url(thumb.jpg)"></div><p></p></div></td>
          <td class="Table-cell--alignLeft"><div>{media}</div></td><td class="Table-cell--alignLeft"><div></div></td>
          <td class="Swu6H6Cr"><button aria-label="Delete" class="gRfJYjDA" style="display: none"><span class="Icon Icon--delete"></span></button></td><td class="_1U0n1L4m--Table-cell--fill"></td></tr>"""

    def table(self, *rows, after="", headings=("Date", "Time", "Post", "Media", "Monetization", "")):
        head = "".join(f"<th>{name}</th>" for name in headings)
        return f'<table><thead><tr>{head}</tr></thead><tbody id="rows">{"".join(rows)}</tbody></table>{after}'

    async def read(self, *rows, after="", **extra):
        self.html = "<!doctype html><body>" + self.table(*rows, after=after)
        at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        receipt = {"identity": OWNER, "title": "My title", "expected_text": self.WORDS, "before_ids": ["1"], "state": "SUBMITTED_UNVERIFIED", "schedule_rows_before": 0, "schedule": {"at": at, **self.SCHEDULE}, **extra}
        with patch.object(browser_backends, "X_SCHEDULE_SECONDS", 1):
            return await browser_backends.verify_scheduled_x(self.page, OWNER, receipt)

    async def test_one_row_with_its_words_date_time_and_a_video_settles_that_it_is_scheduled(self):
        result = await self.read(self.row())
        self.assertEqual(("SUBMISSION_VERIFIED", "scheduled", "scheduled", "studio_schedule_row", None), (result["state"], result["phase"], result["visibility"], result["verification_source"], result["error"]))
        self.assertEqual((1, 1), (result["schedule_rows_with_its_words"], result["schedule_rows_matching"]))
        self.assertEqual("https://studio.x.com/schedule", result["review_url"])
        self.assertNotIn("id", result)
        self.assertIn("doesn't say which video", result["note"])
        self.assertIn("Verify again after that time", result["note"])

    async def test_a_row_that_was_already_there_does_not_pass_for_this_post(self):
        """From a review: an unchanged earlier row with the same words and minute confirmed a Done that did nothing."""
        result = await self.read(self.row(), schedule_rows_before=1, final_submit_attempted=True)
        self.assertEqual(("SUBMITTED_UNVERIFIED", "readback_pending", "studio_schedule_page"), (result["state"], result["phase"], result["verification_source"]))
        self.assertEqual(1, result["schedule_rows_matching"])
        self.assertIn("Before the upload it showed 1", result["note"])

    async def test_a_row_with_nothing_to_show_it_is_new_stays_unconfirmed(self):
        for before in (None, False, "0", 0.0, []):
            with self.subTest(before=before):
                result = await self.read(self.row(), schedule_rows_before=before)
                self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
                self.assertIn("an unknown number", result["note"])

    async def test_a_receipt_from_before_that_read_stays_unconfirmed(self):
        self.html = "<!doctype html><body>" + self.table(self.row())
        receipt = {"identity": OWNER, "title": "My title", "expected_text": self.WORDS, "schedule": {"at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), **self.SCHEDULE}}
        result = await browser_backends.verify_scheduled_x(self.page, OWNER, receipt)
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])

    async def test_a_row_that_is_not_showing_does_not_count(self):
        """From a review: a hidden row, kept through a redraw, confirmed a post while the table showed nothing."""
        spaced = self.row().replace("</div><div>Sun", "</div> <div>Sun")
        for name, row in (("not displayed", spaced.replace('<tr class=', '<tr style="display: none" class=', 1)), ("hidden", spaced.replace('<tr class=', '<tr style="visibility: hidden" class=', 1)),
                          ("see-through", spaced.replace('<tr class=', '<tr style="opacity: 0" class=', 1))):
            with self.subTest(name):
                result = await self.read(row)
                self.assertEqual(("SUBMITTED_UNVERIFIED", 0), (result["state"], result["schedule_rows_matching"]))

    async def test_a_row_in_any_other_table_does_not_count(self):
        """From a review: the schedule table was empty and a hidden table beside it held the row."""
        spaced = self.row().replace("</div><div>Sun", "</div> <div>Sun")
        for name, after in (("hidden", f'<table style="display: none"><tbody>{spaced}</tbody></table>'), ("showing, with no headings", f"<table><tbody>{spaced}</tbody></table>"),
                            ("showing, with other headings", self.table(spaced, headings=("Date", "Time", "Words", "Media")))):
            with self.subTest(name):
                result = await self.read(after=after)
                self.assertEqual(("SUBMITTED_UNVERIFIED", 0), (result["state"], result["schedule_rows_matching"]))

    async def test_a_row_inside_one_of_the_tables_cells_does_not_count(self):
        result = await self.read(f"<tr><td><table><tbody>{self.row()}</tbody></table></td></tr>")
        self.assertEqual(("SUBMITTED_UNVERIFIED", 0), (result["state"], result["schedule_rows_matching"]))

    async def test_two_tables_that_could_both_be_it_settle_nothing(self):
        result = await self.read(self.row(), after=self.table())
        self.assertEqual(("SUBMITTED_UNVERIFIED", 0), (result["state"], result["schedule_rows_matching"]))

    async def test_a_table_in_another_order_is_not_read(self):
        self.html = "<!doctype html><body>" + self.table(self.row(), headings=("Time", "Date", "Post", "Media"))
        at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with patch.object(browser_backends, "X_SCHEDULE_SECONDS", 1):
            result = await browser_backends.verify_scheduled_x(self.page, OWNER, {"title": "My title", "expected_text": self.WORDS, "schedule_rows_before": 0, "schedule": {"at": at, **self.SCHEDULE}})
        self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])

    async def test_a_table_redrawn_while_it_waits_is_read_again(self):
        redraw = """<script>window.pending = 1; setTimeout(() => { document.querySelector('table').remove(); document.body.insertAdjacentHTML('beforeend', %s); window.pending = 0; }, 300);</script>""" % json.dumps(self.table(self.row()))
        self.assertEqual("SUBMISSION_VERIFIED", (await self.read(after=redraw))["state"])

    async def baseline(self, html):
        self.html = "<!doctype html><body>" + html
        with patch.object(browser_backends, "X_SCHEDULE_SECONDS", 1):
            return await browser_backends.x_schedule_baseline(self.page, self.WORDS, self.SCHEDULE)

    async def test_the_read_before_the_upload_counts_rows_like_this_post(self):
        empty = f"<p>{browser_backends.X_SCHEDULE_EMPTY}</p>"
        for name, html, wanted in (("says it is empty, with its headings", self.table(after=empty), 0), ("says it is empty, with no table", empty, 0), ("says it is empty in a row of its own", self.table(f'<tr><td colspan="6">{empty}</td></tr>'), 0), ("other posts only", self.table(self.row(words="Other words"), self.row(clock="10:00 PM")), 0),
                                   ("one like it", self.table(self.row(), self.row(words="Other words")), 1), ("two like it", self.table(self.row(), self.row()), 2)):
            with self.subTest(name):
                self.assertEqual(wanted, await self.baseline(html))

    async def test_the_read_before_the_upload_does_not_guess(self):
        empty = browser_backends.X_SCHEDULE_EMPTY
        blank = "<tr>" + "<td>&nbsp;</td>" * 7 + "</tr>"
        for name, html in (("headings and no rows yet", self.table()), ("an empty page", ""), ("its empty message hidden", self.table(after=f'<p style="display: none">{empty}</p>')),
                           ("something in place of its rows", self.table('<tr><td colspan="6">Loading</td></tr>')), ("blank rows", self.table(blank, blank)),
                           ("a row with only its day so far", self.table(self.row(clock="", words="", media=""))), ("a row with no time yet", self.table(self.row(clock="Loading..."))),
                           ("a row with no words yet", self.table(self.row(words=""))), ("a row with no media yet", self.table(self.row(media=""))), ("only a day in its one cell", self.table("<tr><td>Nov 8 Sun</td></tr>")),
                           ("a finished row beside an unfinished one", self.table(self.row(words="Other words"), self.row(clock=""))), ("an unfinished row and its empty message", self.table(self.row(clock=""), after=f"<p>{empty}</p>")),
                           ("two tables", self.table(self.row(), after=self.table(after=f"<p>{empty}</p>")))):
            with self.subTest(name):
                self.assertIsNone(await self.baseline(html))

    async def test_the_read_before_the_upload_waits_for_rows_to_come(self):
        late = """<script>window.pending = 1; setTimeout(() => { document.getElementById('rows').insertAdjacentHTML('beforeend', %s); window.pending = 0; }, 300);</script>""" % json.dumps(self.row())
        self.assertEqual(1, await self.baseline(self.table(after=late)))

    async def test_another_posts_unfinished_row_does_not_hide_this_posts_row(self):
        result = await self.read(self.row(words="", media=""), self.row())
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertEqual((1, 1), (result["schedule_rows_with_its_words"], result["schedule_rows_matching"]))

    async def test_a_row_that_does_not_match_settles_nothing(self):
        for name, row in (("another time", self.row(clock="9:12 PM")), ("the morning", self.row(clock="9:11 AM")), ("another day", self.row(day="Nov 9", weekday="Mon")), ("another month", self.row(day="Dec 8", weekday="Tue")),
                          ("this date in another year", self.row(weekday="Mon")), ("more words", self.row(words=self.WORDS + " And more")), ("fewer words", self.row(words="My title")),
                          ("no video", self.row(media="Image")), ("no media", self.row(media=""))):
            with self.subTest(name):
                result = await self.read(row)
                self.assertEqual(("SUBMITTED_UNVERIFIED", "readback_pending", 0), (result["state"], result["phase"], result["schedule_rows_matching"]))
                self.assertIn("Do not post again", result["note"])

    async def test_an_empty_page_or_two_rows_that_could_both_be_it_settle_nothing(self):
        for rows, wanted in (((), (0, 0)), ((self.row(), self.row()), (2, 2))):
            with self.subTest(rows=len(rows)):
                result = await self.read(*rows)
                self.assertEqual("SUBMITTED_UNVERIFIED", result["state"])
                self.assertEqual(wanted, (result["schedule_rows_with_its_words"], result["schedule_rows_matching"]))

    async def test_the_same_words_at_another_time_do_not_get_in_the_way(self):
        result = await self.read(self.row(clock="10:00 PM"), self.row())
        self.assertEqual("SUBMISSION_VERIFIED", result["state"])
        self.assertEqual((2, 1), (result["schedule_rows_with_its_words"], result["schedule_rows_matching"]))

    async def test_a_row_that_shows_a_moment_after_the_page_is_still_found(self):
        late = '''<script>window.pending = 1; setTimeout(() => { document.getElementById('rows').insertAdjacentHTML('beforeend', %s); window.pending = 0; }, 300);</script>''' % json.dumps(self.row())
        self.assertEqual("SUBMISSION_VERIFIED", (await self.read(after=late))["state"])

    async def test_a_row_that_has_gone_takes_a_scheduled_receipt_back_to_unconfirmed(self):
        result = await self.read(state="SUBMISSION_VERIFIED", phase="scheduled", verification_source="studio_schedule_row")
        self.assertEqual(("SUBMITTED_UNVERIFIED", "readback_pending", "studio_schedule_page"), (result["state"], result["phase"], result["verification_source"]))

    def test_a_rows_date_and_time_are_read_with_the_day_of_the_week(self):
        for day, clock, wanted in (("Nov 8\nSun", "9:11 PM", True), ("Nov 8 Sun", " 9:11 PM ", True), ("Nov 8\nMon", "9:11 PM", False), ("Nov 8", "9:11 PM", False), ("Nov 8\nSun", "9:11 AM", False),
                                   ("Xyz 8\nSun", "9:11 PM", False), ("", "", False), ("Nov 8\nSun", "", False)):
            with self.subTest(day=day, clock=clock):
                self.assertIs(wanted, browser_backends.x_row_says_schedule(day, clock, self.SCHEDULE))


class OwnPostsTests(ChromeCase):
    """The words and the moment come from each post's own row, not from everything the row shows."""
    ROW = """<article><a href="/{author}/status/{post}"><time datetime="2026-11-03T15:07:00.000Z">Nov 3</time></a><div data-testid="User-Name">Someone</div>
      <div data-testid="tweetText">{words}</div><div role="group" aria-label="3 likes"><a href="/compose/post?in_reply_to={post}">Reply</a></div>{extra}</article>"""
    VIDEO = '<div data-testid="videoPlayer"><video poster="https://pbs.twimg.com/amplify_video_thumb/77/img/a.jpg"></video></div>'

    async def asyncSetUp(self):
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
        self.assertEqual([("1", "Exactly these words", "2026-11-03T15:07:00.000Z", True, "77"), ("4", "No video here", "2026-11-03T15:07:00.000Z", False, None)],
                         [(post["id"], post["text"], post["published_at"], post["has_video"], post["media_id"]) for post in posts])


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
        self.value.update(title="Schedule fixture", cover="cover.jpg", tags=["tag"])
        self.value["platforms"] = {"x": {"account": "default", "method": "browser", "visibility": "account", "ai_generated": True}}
        self.path = self.root / "publish.json"

    def plan(self):
        atomic_json(self.path, self.value)
        return load_plan(self.path)

    def test_a_clip_already_in_media_studio_can_be_named(self):
        self.value["platforms"]["x"]["existing_media_url"] = "https://studio.x.com/library/13_77/edit"
        self.assertEqual("https://studio.x.com/library/13_77/edit", self.plan()["platforms"]["x"]["existing_media_url"])

    def test_only_a_clips_own_page_on_the_browser_route_can_be_named(self):
        self.value.pop("cover")
        for address, method in (("https://example.com/library/13_77/edit", "browser"), ("https://studio.x.com/library/13_77/edit?x=1", "browser"), ("https://studio.x.com/library", "browser"), (7, "browser"),
                                ("https://studio.x.com/library/13_77/edit", "native"), ("https://studio.x.com/library/13_77/edit", "api")):
            with self.subTest(address=address, method=method):
                self.value["platforms"]["x"].update(existing_media_url=address, method=method)
                with self.assertRaises(VpubError) as caught:
                    self.plan()
                self.assertEqual("invalid_media_url", caught.exception.code)

    def test_instagram_takes_no_such_thing(self):
        self.value["platforms"] = {"instagram": {"account": "default", "method": "browser", "visibility": "account", "ai_generated": True, "existing_media_url": "https://studio.x.com/library/13_77/edit"}}
        with self.assertRaises(VpubError) as caught:
            self.plan()
        self.assertEqual("invalid_manifest", caught.exception.code)

    def test_the_browser_route_takes_a_future_time(self):
        when = (datetime.now(timezone.utc) + timedelta(days=2)).replace(second=0, microsecond=0).isoformat()
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

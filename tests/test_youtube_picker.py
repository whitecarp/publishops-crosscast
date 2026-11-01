"""A box in Studio's playlist picker and its schedule's time box, in a real browser against small pages. A real channel's first box refused a click for 15 seconds, and its time box went back to its own time."""
import unittest
from unittest.mock import patch

from video_publisher import browser_forms

BOX = """<!doctype html><body><div id="box" role="checkbox" tabindex="0" aria-checked="false" style="%s"></div>
<script>
const box = document.getElementById('box');
window.clicks = 0; window.keys = 0;
const flip = () => box.setAttribute('aria-checked', box.getAttribute('aria-checked') === 'true' ? 'false' : 'true');
box.addEventListener('click', () => { window.clicks++; flip(); });
box.addEventListener('keydown', event => { if (event.key === ' ') { window.keys++; flip(); } });
</script>"""


class TickTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def tick(self, style):
        await self.page.set_content(BOX % style)
        box = self.page.locator('[role="checkbox"]')
        with patch.object(browser_forms, "YT_TICK_MS", 500):
            await browser_forms.yt_tick(self.page, box)
        return await box.get_attribute("aria-checked"), await self.page.evaluate("[window.clicks, window.keys]", isolated_context=False)

    async def test_a_box_under_its_own_container_is_clicked_through_the_element_around_them(self):
        await self.page.set_content("""<!doctype html><body><ytcp-checkbox-lit style="display: block; width: 20px"><div id="checkbox-container" style="position: relative; height: 20px">
            <div role="checkbox" aria-checked="false" style="height: 20px"></div><div style="position: absolute; inset: 0"></div></div></ytcp-checkbox-lit>
            <script>window.keys = 0; document.querySelector('ytcp-checkbox-lit').onclick = () => document.querySelector('[role=checkbox]').setAttribute('aria-checked', 'true');
            document.addEventListener('keydown', () => { window.keys++; });</script>""")
        box = self.page.locator('[role="checkbox"]')
        with self.assertRaises(Exception):
            await box.click(timeout=300)
        with patch.object(browser_forms, "YT_TICK_MS", 500):
            await browser_forms.yt_tick(self.page, box)
        self.assertEqual(("true", 0), (await box.get_attribute("aria-checked"), await self.page.evaluate("window.keys", isolated_context=False)))

    async def test_a_box_that_can_be_clicked_gets_one_click_and_no_key(self):
        self.assertEqual(("true", [1, 0]), await self.tick("width: 20px; height: 20px"))

    async def test_a_box_with_no_size_is_ticked_from_the_keyboard(self):
        self.assertEqual(("true", [0, 1]), await self.tick("width: 0; height: 0; overflow: hidden"))

    async def test_a_box_covered_by_something_else_is_ticked_from_the_keyboard(self):
        await self.page.set_content(BOX % "width: 20px; height: 20px" + '<div style="position: fixed; inset: 0"></div>')
        box = self.page.locator('[role="checkbox"]')
        with patch.object(browser_forms, "YT_TICK_MS", 500):
            await browser_forms.yt_tick(self.page, box)
        self.assertEqual(("true", [0, 1]), (await box.get_attribute("aria-checked"), await self.page.evaluate("[window.clicks, window.keys]", isolated_context=False)))


# A field check typed 14:07, and Enter moved it to 15:07 while leaving the box kept it. Nothing here says that is exactly how Studio builds it.
ENTER_MOVES_IT = """<!doctype html><body><input id="clock" value="00:00">
<script>
const clock = document.getElementById('clock');
clock.addEventListener('keydown', event => { if (event.key === 'Enter') { const [hour, minute] = clock.value.split(':'); clock.value = String(+hour + 1).padStart(2, '0') + ':' + minute; } });
%s
</script>"""
TYPED_ONLY = """<!doctype html><body><input id="clock" value="10:00">
<script>
const clock = document.getElementById('clock');
let typed = false;
clock.addEventListener('keydown', event => { if (event.key === 'Enter') { if (!typed) clock.value = '10:00'; } else { typed = true; } });
</script>"""
LIST_ONLY = """<!doctype html><body><input id="clock" value="10:00"><div id="times" hidden><div role="option">08:45</div><div role="option">09:00</div><div role="option">09:15</div></div>
<script>
const clock = document.getElementById('clock');
clock.addEventListener('keydown', event => event.preventDefault());
clock.addEventListener('click', () => { document.getElementById('times').hidden = false; });
for (const row of document.querySelectorAll('[role=option]')) row.onclick = () => { clock.value = row.textContent; document.getElementById('times').hidden = true; };
</script>"""


class TimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = await self.browser.new_page()
        self.clock = self.page.locator("#clock")

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_a_time_put_in_all_at_once_is_lost_and_one_typed_a_key_at_a_time_stays(self):
        await self.page.set_content(TYPED_ONLY)
        await self.clock.fill("09:00")
        await self.clock.press("Enter")
        self.assertEqual("10:00", await self.clock.input_value())
        await self.page.set_content(TYPED_ONLY)
        self.assertEqual({"typed": "09:00", "after_leaving": "09:00"}, await browser_forms.yt_set_time(self.page, self.clock, "09:00"))
        self.assertEqual("09:00", await self.clock.input_value())

    async def test_a_box_that_takes_no_typing_gets_the_time_from_its_list(self):
        await self.page.set_content(LIST_ONLY)
        self.assertEqual({"typed": "10:00", "after_leaving": "10:00", "listed": 1, "from_list": "09:00"}, await browser_forms.yt_set_time(self.page, self.clock, "09:00"))
        self.assertEqual("09:00", await self.clock.input_value())

    async def test_a_time_the_list_does_not_offer_leaves_it_for_the_read_back_to_refuse(self):
        await self.page.set_content(LIST_ONLY)
        self.assertEqual({"typed": "10:00", "after_leaving": "10:00", "listed": 0, "after_enter": "10:00"}, await browser_forms.yt_set_time(self.page, self.clock, "09:07"))
        self.assertEqual("10:00", await self.clock.input_value())

    async def test_a_time_typed_and_left_never_meets_the_enter_that_moves_it(self):
        await self.page.set_content(ENTER_MOVES_IT % "")
        self.assertEqual({"typed": "14:07", "after_leaving": "14:07"}, await browser_forms.yt_set_time(self.page, self.clock, "14:07"))
        self.assertEqual("14:07", await self.clock.input_value())

    async def test_when_leaving_moves_it_too_the_wrong_time_is_left_for_the_read_back_to_refuse(self):
        await self.page.set_content(ENTER_MOVES_IT % """clock.addEventListener('blur', () => { const [hour, minute] = clock.value.split(':'); clock.value = String(+hour + 1).padStart(2, '0') + ':' + minute; });""")
        steps = await browser_forms.yt_set_time(self.page, self.clock, "14:07")
        self.assertEqual(("15:07", "15:07"), (steps["after_leaving"], steps["after_enter"]))


if __name__ == "__main__":
    unittest.main()

"""CLI browser execution with durable phases and exactly one final submit."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import re
import time
from pathlib import Path
import unicodedata
from urllib.parse import parse_qs, urlsplit

from .core import VpubError, atomic_json, file_sha, home, now, presses_on_record, read_json, receipt_turn, snapshot_file, stop_check, wrong_file
from .browser_runtime import failure_message, failure_record, look_until, plain_address, platform_at, risk_check, unique_visible, visible
from .progress import stage
from .x_pages import own_posts, post_fields, post_row, post_time

MANAGE = "https://creator.douyin.com/creator-micro/content/manage"
PRESS_TRIAL_MS = 20000  # As long as the press itself may wait for its button.
PRESS_RETRIAL_MS = 10000  # After whatever covered it was closed.
STUDIO_VISIBILITY = {"public": {"Public", "公开", "公開"}, "unlisted": {"Unlisted", "不公开列出", "不公開"}, "private": {"Private", "私享", "私人"}}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
X_CHOSEN_TIME = re.compile(r'([A-Z][a-z]{2}) (\d{1,2})(?:, (\d{4}))?, (1[0-2]|[1-9]):([0-5]\d) ([AP]M)')
X_SCHEDULE_DAY = re.compile(r'([A-Z][a-z]{2} \d{1,2}) ([A-Z][a-z]{2})')
X_SCHEDULE_CLOCK = re.compile(r'(1[0-2]|[1-9]):[0-5]\d [AP]M')
X_SCHEDULE_PAGE = "https://studio.x.com/schedule"
X_SCHEDULE_HEADINGS = ["Date", "Time", "Post", "Media"]
X_SCHEDULE_EMPTY = "Schedule posts from your Library or Producer to see them here"
X_SCHEDULE_TABLE = """headings => {
  const shown = node => node.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) && node.getBoundingClientRect().height > 0;
  const heads = row => shown(row) && headings.every((name, at) => row.cells[at]?.tagName === 'TH' && row.cells[at].textContent.trim() === name);
  const tables = [...document.querySelectorAll('table')].filter(table => shown(table) && [...table.rows].some(heads));
  if (tables.length > 1) return null;
  return tables.length ? [...tables[0].rows].filter(row => shown(row) && row.cells[0]?.tagName === 'TD').map(row => [...row.cells].map(cell => cell.innerText)) : [];
}"""
X_SCHEDULE_SECONDS = 15
X_CLOCK_GRACE = timedelta(minutes=5)
X_DISCOVERY_SOURCE = 'native_prepress_and_unique_own_post'
INSTAGRAM_CAPTION = r"""({words, owner}) => {
  const said = text => (text || '').replace(/\s+/g, ' ').trim();
  const bare = text => (text || '').replace(/\s+/g, '');
  const people = e => [...e.querySelectorAll('a[href]')].filter(a => /^\/[^/]+\/$/.test(a.getAttribute('href')));
  let head = [...document.querySelectorAll('time')].find(e => e.getClientRects().length);
  while (head && !people(head).length) head = head.parentElement;
  if (!head) return false;
  while (head.parentElement && bare(head.parentElement.innerText) === bare(head.innerText)) head = head.parentElement;
  const row = head.parentElement;
  const rest = row ? [...row.children].filter(e => e !== head && e.getClientRects().length && said(e.innerText)) : [];
  return rest.length === 1 && said(rest[0].innerText) === words && bare(row.innerText).length === bare(head.innerText).length + bare(rest[0].innerText).length
    && people(head).every(a => a.getAttribute('href').toLowerCase() === '/' + owner.toLowerCase() + '/');
}"""
# Studio writes dates and times in its interface language. Each style is (pattern, read, write).
STUDIO_DATES = (
    (r"(\d{4})年(\d{1,2})月(\d{1,2})日", lambda m: (int(m[1]), int(m[2]), int(m[3])), lambda d: f"{d.year}年{d.month}月{d.day}日"),
    (r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})", lambda m: (int(m[3]), MONTHS.index(m[1]) + 1, int(m[2])) if m[1] in MONTHS else None,
     lambda d: f"{MONTHS[d.month - 1]} {d.day}, {d.year}"),
    (r"(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})", lambda m: (int(m[3]), MONTHS.index(m[2]) + 1, int(m[1])) if m[2] in MONTHS else None,
     lambda d: f"{d.day} {MONTHS[d.month - 1]} {d.year}"),
)
# The 12-hour style comes first so "6:00 PM" is never read as 06:00.
STUDIO_TIMES = (
    (r"(\d{1,2}):(\d{2}) ?([AaPp][Mm])", lambda m: (int(m[1]) % 12 + (12 if m[3].upper() == "PM" else 0), int(m[2])),
     lambda d: f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"),
    (r"(\d{1,2}):(\d{2})", lambda m: (int(m[1]), int(m[2])), lambda d: f"{d.hour:02d}:{d.minute:02d}"),
)


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip()


def work_url(platform, identity, work_id):
    patterns = {"x": r"\d+", "douyin": r"\d+", "instagram": r"[A-Za-z0-9_-]+", "youtube": r"[\w-]{11}"}
    if not re.fullmatch(patterns[platform], str(work_id)):
        raise VpubError("invalid_id", "Use a platform work ID, not an arbitrary URL.")
    if platform == "x":
        return f"https://x.com/{identity}/status/{work_id}"
    if platform == "douyin":
        return f"https://www.douyin.com/video/{work_id}"
    if platform == "youtube":
        return f"https://www.youtube.com/watch?v={work_id}"
    return f"https://www.instagram.com/reel/{work_id}/"


def instagram_address_is(address, identity, work_id):
    """Whether an address is this work's own page on Instagram. Field reads found it at /p/, /reel/ and /reels/, some with the owner's name in front."""
    parts = urlsplit(address)
    # Account names ignore case; the work's shortcode must match exactly.
    return parts.hostname == "www.instagram.com" and re.fullmatch(rf"(?i:(/{re.escape(identity)})?/(p|reel|reels))/{re.escape(str(work_id))}/?", parts.path) is not None


async def click_named(scope, name):
    loc = scope.get_by_role("button", name=re.compile(name, re.I))
    target = await unique_visible(loc, name)
    await target.click(timeout=15000)
    return target


async def wait_until(page, predicate, *, seconds=120, label="page state"):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        await risk_check(page)
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(1)
    raise VpubError("processing_timeout", f"Timed out waiting for {label}.")


async def before_a_file_goes_up(page):
    """Choosing the file is what starts an upload. From a review: a warning can show while the page loads, and another command can stop everything after this one began, and both reached this point unchecked."""
    await risk_check(page)
    stop_check(platform_at(page.url))


async def choose_file(page, box, path):
    """Every file reaches a page here or in choose_in_picker, the video and the covers alike. A review found a stop could arrive while a cover's dialog opened, and the cover still went up."""
    await before_a_file_goes_up(page)
    await box.set_input_files(str(path), timeout=60000)


async def choose_in_picker(page, opener, path):
    """Opening the picker takes time too, so the check comes once it is open."""
    async with page.expect_file_chooser(timeout=15000) as chosen:
        await opener.click(timeout=15000)
    picker = await chosen.value
    await before_a_file_goes_up(page)
    await picker.set_files(str(path), timeout=60000)


async def upload_file(page, path, trigger=None, accept=None):
    await before_a_file_goes_up(page)
    inputs = page.locator('input[type="file"]')
    candidates = []
    for item in await inputs.all():
        value = (await item.get_attribute("accept") or "").lower()
        if accept is None or accept in value:
            candidates.append(item)
    if len(candidates) == 1:
        await choose_file(page, candidates[0], path)
    elif trigger:
        await choose_in_picker(page, trigger, path)
    else:
        raise VpubError("layout_changed", "Could not identify a unique file input for this upload.")


class Transaction:
    def __init__(self, platform, identity, plan, resume=False, prior_receipt=None):
        self.platform, self.identity, self.plan, self.resuming = platform, identity, plan, resume
        self.options = plan["options"]
        self.path = home() / "browser-receipts" / (plan["confirmation"] + "-" + platform + ".json")
        if self.path.exists() and not resume:
            raise VpubError("attempt_exists", "This independent browser attempt already has a durable receipt.", "Inspect/verify that attempt. Never reset it to upload or submit again.")
        self.receipt = {"state": "PREPARING", "method": "browser", "platform": platform,
                        "identity": identity, "title": self.options["title"], "phase": "initial",
                        "video_sha256": plan["video_sha256"], "final_submit_attempted": False,
                        "media_selected": False, "started_at": now()}
        # No receipt means this attempt never reached the page, so it can start from the beginning. A review found that file going missing was enough to upload again, though the attempt's own record said a file was chosen.
        if resume and self.path.exists():
            saved = read_json(self.path)
            if saved.get('video_sha256') != plan['video_sha256'] or saved.get('identity') != identity:
                raise VpubError('receipt_conflict', 'Resume must use the same account and media.')
            self.receipt = {**saved, **presses_on_record(prior_receipt or {}, saved)}
        elif resume and prior_receipt and (prior_receipt.get('media_selected') or prior_receipt.get('final_submit_attempted')):
            raise VpubError('receipt_missing', "This attempt's browser receipt is gone, and its record says a file was already chosen, so nothing was uploaded again.", 'Inspect the platform, and use vpub verify to read the work back. Never upload it again.')
        elif platform=='douyin' and self.options.get('chapters'):
            self.receipt['pending_chapters']={'chapters':self.options['chapters'],
                                             'duration_seconds':self.options['duration_seconds']}
            self.receipt['chapters_state']='PENDING_WORK_ID'

    def save(self, **changes):
        for key in ("review_url", "draft_url", "media_url"):
            if changes.get(key):
                changes[key] = plain_address(changes[key])
        self.receipt.update(changes, updated_at=now())
        with receipt_turn(self.path):
            atomic_json(self.path, self.receipt)
        return dict(self.receipt)

    def media_name(self):
        return "crosscast-" + self.plan["video_sha256"][:16] + Path(self.plan["video"]).suffix

    def media(self):
        src = Path(self.plan["video"])
        # Each platform owns its alias. If hardlinks are unavailable, two
        # browsers must not replace the same copy while one is uploading it.
        target = src.parent / ("upload-" + self.platform) / self.media_name()
        dst = snapshot_file(src, target, self.plan["video_sha256"], link=True)
        self.save(phase="selecting_media", media_selected=True, upload_filename=dst.name)
        stage("uploading")
        return dst

    def validate_assets(self):
        if file_sha(self.plan["video"]) != self.plan["video_sha256"]:
            raise wrong_file(self.plan["video"], self.plan["video_sha256"], "Snapshot changed before browser upload.")
        for asset in self.plan.get("covers", {}).get(self.platform, {}).values():
            if file_sha(asset["path"]) != asset["sha256"]:
                raise wrong_file(asset["path"], asset["sha256"], "A cover snapshot changed before upload.")
        if self.plan.get("cover") and file_sha(self.plan["cover"]) != self.plan["cover_sha256"]:
            raise wrong_file(self.plan["cover"], self.plan["cover_sha256"], "Cover snapshot changed before upload.")

    def cover(self, role="landscape"):
        asset = self.plan.get("covers", {}).get(self.platform, {}).get(role)
        shared = self.plan.get("cover") if role == "landscape" else None
        if not asset and not shared and self.platform == 'instagram' and role == 'landscape':
            asset = self.plan.get('covers', {}).get('instagram', {}).get('portrait')
        return asset["path"] if asset else shared

    async def submit(self, page, button, still_holds=None, again=False, uncover=None):
        """`again` is only for a press the page has shown did not publish anything. `uncover`, given what refused a trial press,
        may close what covers the button, once, and says what it was."""
        if self.options.get("submission_mode") == "draft_only":
            raise VpubError("draft_only", "Final submission is forbidden in draft-only mode.")
        if self.receipt["final_submit_attempted"] and not again:
            raise VpubError("already_submitted", "A final submission was already attempted.")
        await risk_check(page)
        stop_check(self.platform)
        await self.can_press(button, uncover)
        if still_holds:
            await still_holds()  # What was read before that wait may not be true after it.
        stop_check(self.platform)
        self.save(state="UNKNOWN", phase="submitting", final_submit_attempted=True, final_presses=self.receipt.get("final_presses", 0) + 1, review_url=page.url)
        stage("pressing")
        # Persist BEFORE clicking. A timeout must never cause another click.
        stop_check(self.platform)  # Saving the receipt can wait on its own lock.
        await button.click(timeout=20000)
        stage("waiting for platform confirmation")

    async def can_press(self, button, uncover=None):
        """A trial press, which clicks nothing. A field run's Schedule button was covered three times running, and each attempt was recorded
        as pressed though Chrome had refused the click, which left the draft with no way out. A refusal here records nothing as pressed."""
        import patchright.async_api as driver
        controls = tuple(kind for kind in (getattr(driver, "Locator", None), getattr(driver, "ElementHandle", None)) if isinstance(kind, type))
        if not controls or not isinstance(button, controls):
            return None  # Only a page's own control has a trial press; a stand-in for one presses as it is.
        BrowserError = driver.Error
        try:
            return await button.click(trial=True, timeout=PRESS_TRIAL_MS)
        except BrowserError as exc:
            refused = failure_message(exc)
        covered = await uncover(refused) if uncover else None
        if covered:
            self.save(press_covered=covered)
            try:
                return await button.click(trial=True, timeout=PRESS_RETRIAL_MS)
            except BrowserError as exc:
                refused = failure_message(exc)
        raise VpubError("press_blocked", f"The final button couldn't be pressed, so nothing was sent and nothing is recorded as pressed. {refused}",
                        "vpub status gives the command that picks this attempt up again. It never presses twice.")


async def links(page, platform, identity):
    rows = await page.locator("a[href]").evaluate_all("es => es.map(e => ({url:e.href,text:(e.closest('article,tr') || e).innerText || e.querySelector('img')?.alt || ''}))")
    result = {}
    for row in rows:
        u = urlsplit(row["url"])
        if platform == "x":
            match = re.fullmatch(r"/" + re.escape(identity) + r"/status/(\d+)", u.path, re.I) if u.hostname in {"x.com", "www.x.com"} else None
        elif platform == "instagram":
            found = re.fullmatch(r"/(?:" + re.escape(identity) + r"/)?(reel|p)/([\w-]+)/?", u.path, re.I) if u.hostname == "www.instagram.com" else None
            if found:
                # A field read found a photo's link at /p/. Written as a Reel, its address would say something it isn't.
                result[found[2]] = {"id": found[2], "url": f"https://www.instagram.com/{found[1].lower()}/{found[2]}/", "text": row["text"]}
            continue
        else:
            match = re.fullmatch(r"/video/(\d+)", u.path) if u.hostname in {"www.douyin.com", "douyin.com"} else None
            if not match and u.hostname == "creator.douyin.com":
                mid = parse_qs(u.query).get("mid", [""])[0]
                if mid.isdigit():
                    result[mid] = {"id": mid, "url": work_url(platform, identity, mid), "text": row["text"]}
        if match:
            k = match[1]
            result[k] = {"id": k, "url": work_url(platform, identity, k), "text": row["text"]}
    return result


YT_EDIT_DRAFT = re.compile('^(编辑草稿|Edit draft)$', re.I)
POPUP_MS = 15000
DOUYIN_CARD_TIME = re.compile(r"(\d{4})年(\d{2})月(\d{2})日 (\d{2}):(\d{2})")
DOUYIN_CARD = '[class^="video-card-content-"]'
DOUYIN_NAME = '[class^="info-title-text-"]'
DOUYIN_SCHEDULED = {'state': 'SUBMISSION_VERIFIED', 'phase': 'scheduled', 'error': None, 'verification_source': 'creator_card_title_and_time',
                    'note': 'Douyin shows this new card with its title and scheduled time, and no ID until the time comes. Verify again after that time.'}
X_SCHEDULED = {'state': 'SUBMISSION_VERIFIED', 'phase': 'scheduled', 'error': None, 'verification_source': 'studio_schedule_row',
               'note': "Studio's schedule page shows one row with exactly its words, its date and time, and a video, and showed none like it before the upload. A row has no ID until the time comes and doesn't say which video. Verify again after that time."}


def douyin_card_is_waiting(text):
    """A card still under review or scheduled for later opens no work's page. A field check found a scheduled card's only sign is a time still to come."""
    if re.search('审核中|定时发布', text):
        return True
    found = DOUYIN_CARD_TIME.search(text)
    return bool(found) and datetime(*map(int, found.groups())) > datetime.now()


async def douyin_card_status(card):
    """A card's words apart from its title node, which a field check found goes on to hold the caption. A person's words can mention any date or state; only the platform's count."""
    return normalize(await card.evaluate("""card => { const title = card.querySelector('[class^="info-title-text-"]'); return title ? card.innerText.replace(title.innerText, '') : card.innerText; }"""))


async def douyin_card_under_review(page, title):
    """Whether the one card with this title says Douyin is still reviewing it. A field run's chapters waited on such a card and only ever said TimeoutError."""
    cards = page.locator(DOUYIN_CARD).filter(has_text=title)
    return await cards.count() == 1 and '审核中' in await douyin_card_status(cards)


def douyin_card_words(text):
    """What was written. Invisible marks and how much spacing there is are the page's own. Punctuation and where a space falls are part of the words: reviews showed 1.2 and 12, and now here and nowhere, must not read the same."""
    return normalize(''.join(c for c in unicodedata.normalize('NFC', text or '') if unicodedata.category(c) != 'Cf'))


async def douyin_type_caption(page, editor, caption):
    """A caption set in one go came out with a star at each new paragraph, on the card and, for a real video, on its public page, and the person never wrote those. So it is typed as a person would, a line at a time."""
    await editor.click()
    await editor.press('ControlOrMeta+A')
    await editor.press('Backspace')
    for number, line in enumerate(caption.split('\n')):
        if number:
            await page.keyboard.press('Enter')
        if line:
            await page.keyboard.insert_text(line)


def douyin_card_says(title, caption):
    """What a card's title node shows for this title and caption: a field check found it holds both, with a star where each new paragraph begins if the caption was set in one go."""
    return {douyin_card_words(title + ' ' + caption), douyin_card_words(title + ' ' + re.sub(r'\s*\n\s*', ' *', caption.strip()))}


async def douyin_cards_saying(page, wanted):
    """The cards with a title node saying one of these. A card with more than one counts if any of them does, so a count of what was already there can't come out low."""
    cards = []
    for card in await page.locator(DOUYIN_CARD).all():
        for name in await card.locator(DOUYIN_NAME).all():
            if douyin_card_words(await name.inner_text()) in wanted:
                cards.append(card)
                break
    return cards


async def douyin_cards_titled(page, title, caption=''):
    """The Creator Center cards with exactly this title, or exactly this title and then this caption, which a field check found a card's title node holds, with a star where each new paragraph begins. A longer title or another caption is another work."""
    return await douyin_cards_saying(page, {douyin_card_words(title)} | douyin_card_says(title, caption))


def douyin_card_stamp(when):
    """Its upload form writes the time one way and its works list another, as a field check found."""
    local = when.astimezone()
    return f'{local.year}年{local.month:02d}月{local.day:02d}日 {local.hour:02d}:{local.minute:02d}'


async def douyin_card_time(card):
    """The time in a card's own time node, which a field check found outside its title node, or None unless there is exactly one."""
    nodes = card.locator('[class^="info-time-"]:not([class^="info-title-text-"] *)')
    if await nodes.count() != 1:
        return None
    found = [match[0] for match in DOUYIN_CARD_TIME.finditer(normalize(await nodes.first.inner_text()))]
    return found[0] if len(found) == 1 else None


async def douyin_cards_at(cards, stamp):
    """The cards that show this time, or might: one whose time cannot be read could be at any time."""
    return [card for card in cards if await douyin_card_time(card) in (stamp, None)]


async def douyin_scheduled_card(page, receipt):
    """Before its time a scheduled work has a card and no ID anywhere. One more card than before with exactly this title, the only one whose own time node shows this time, and marked private if it was meant to be, is this one.
    An older card with this title and time could pass for it, so unless none was there before the upload, this stays unverified."""
    schedule = receipt.get('schedule') or {}
    if not schedule.get('card_stamp') or 'same_title_cards_before' not in receipt or receipt.get('same_time_cards_before') != 0:
        return False
    cards = await douyin_cards_titled(page, receipt.get('title') or '', receipt.get('expected_caption') or '')
    if len(cards) != receipt['same_title_cards_before'] + 1:
        return False
    matching = await douyin_cards_at(cards, schedule['card_stamp'])
    if len(matching) != 1 or await douyin_card_time(matching[0]) != schedule['card_stamp']:
        return False
    return receipt.get('visibility') != 'private' or await matching[0].locator('[class*="private-mark-"]').count() > 0


def douyin_editor_address(work_id):
    return f'https://creator.douyin.com/creator-micro/content/post/video?mid={work_id}&enter_from=edit_item'


def douyin_editor_work(address):
    """The work whose edit page this address is, or None. Only the exact address a field run saw counts, with one work named."""
    parts = urlsplit(address)
    asked = parse_qs(parts.query, keep_blank_values=True)
    found = asked.get('mid', [])
    usable = (parts.scheme == 'https' and parts.netloc == 'creator.douyin.com' and parts.path == '/creator-micro/content/post/video' and not parts.fragment
              and asked.get('enter_from') == ['edit_item'] and len(found) == 1 and re.fullmatch('[0-9]{1,40}', found[0]))
    return found[0] if usable else None


async def douyin_editor_boxes(page, work_id):
    """The title and description boxes of a work's edit page, once this is that page and its title has come."""
    title = page.locator('input[placeholder*="填写作品标题"]')
    await title.wait_for(timeout=45000)
    await risk_check(page)
    if douyin_editor_work(page.url) != str(work_id):
        raise VpubError('wrong_work', 'Douyin opened another page instead of this work; nothing changed.')
    for _ in range(20):
        if normalize(await title.input_value()):
            break
        await page.wait_for_timeout(500)
    return title, await unique_visible(page.locator('[contenteditable=true]'), 'Douyin description')


async def douyin_id_from_menu(page, shown):
    """A private work's title opens nothing, a field run found, and its card's own 编辑作品 menu opens its edit page, whose address names it.
    This looks in a tab of its own, so the list being read stays where it is, and presses only that menu item; nothing is saved. What opens has to be that tab or one tab it opened, and say what the card says, or the answer is None. A review found matching words alone could take another tab's page for it."""
    from patchright.async_api import Error as BrowserError
    tabs = list(page.context.pages)
    side = await page.context.new_page()

    async def target():
        others = [tab for tab in page.context.pages if tab not in tabs and tab is not side]
        if not others:
            return side if douyin_editor_work(side.url) else None
        return others[0] if len(others) == 1 and await others[0].opener() is side and douyin_editor_work(others[0].url) and not douyin_editor_work(side.url) else None
    try:
        await side.goto(MANAGE, wait_until='domcontentloaded', timeout=60000)
        await side.locator(DOUYIN_NAME).first.wait_for(state='visible', timeout=20000)
        await risk_check(side)
        cards = await douyin_cards_saying(side, {shown})
        if len(cards) != 1 or await cards[0].locator(DOUYIN_NAME).count() != 1:
            return None
        label = cards[0].get_by_text('编辑作品', exact=True)
        item = side.locator('ul.semi-dropdown-menu[class*="edit-menu-"] > li.semi-dropdown-item').filter(has_text=re.compile(r'^\s*编辑作品\s*$'))
        if await label.count() != 1 or await visible(item):
            return None  # A menu already open is no card's in particular.
        await label.hover(timeout=10000)
        # The menu sits outside the card, and only the trigger it marks as showing says whose it is.
        trigger = cards[0].locator('[class*="edit-btn-"].semi-dropdown-showing')

        async def its_menu():
            return await trigger.count() == 1 and await visible(item)
        if not await look_until(side, its_menu, 5000) or await side.locator('.semi-dropdown-showing').count() != 1:
            return None
        press = await unique_visible(item, 'Douyin edit menu item')
        await risk_check(side)
        stop_check('douyin')
        await press.click(timeout=10000)
        opened = await look_until(side, target, 10000)
        if not opened:
            return None
        work = douyin_editor_work(opened.url)
        title, words = await douyin_editor_boxes(opened, work)

        async def same():
            return shown in douyin_card_says(await title.input_value(), await words.inner_text())
        return work if await look_until(opened, same, 5000) and await target() is opened else None
    except BrowserError:
        return None
    except VpubError as failure:
        if failure.code != 'layout_changed':
            raise
        return None
    finally:
        # Only what this opened: another tab is not its to close, and a tab whose opener has closed no longer says so, so this looks first.
        owned = [tab for tab in page.context.pages if tab is side or (tab not in tabs and await tab.opener() is side)]
        for tab in owned:
            await tab.close()


def douyin_new_works(platform, receipt, works):
    """Of the works new since an upload began and carrying its title, those that can be that upload.
    A private work's ID takes its card's menu to read, which can work for a card one time and not another, so the list read before the upload may have left out an older private work. Only no card saying the same beforehand proves a matching one now is new, and its whole text has to match. Otherwise another work's ID goes on this upload's receipt, even if the receipt stays unconfirmed. From Codex's reviews."""
    if platform != 'douyin' or receipt.get('visibility') != 'private':
        return works
    count, before = receipt.get('same_title_cards_before'), receipt.get('before_ids')
    if type(count) is not int or count != 0 or 'expected_caption' not in receipt or type(before) is not list or not all(type(known) is str for known in before):
        return []
    wanted = douyin_card_says(receipt.get('title') or '', receipt['expected_caption'])
    return [work for work in works if douyin_card_words(work.get('text')) in wanted]


def x_new_works(platform, receipt, works):
    """Of the works new since an upload began and carrying its title, those that can be that upload.
    The list read before it holds only the posts that first showed, so an older post with the same words can look new, and would be taken for this one if this one never went out. An X post's ID says when it was made. The grace is for this computer's clock."""
    if platform != 'x' or not receipt.get('started_at'):
        return works
    began = datetime.fromisoformat(receipt['started_at']) - X_CLOCK_GRACE
    return [work for work in works if datetime.fromisoformat(post_time(work['id'])) >= began]


async def x_new_posts(page, identity, receipt):
    """Discover one new own post by its posted words, not the separate Studio title.
    The native path binds the source-named file, Studio URL, composer video and
    text before recording its final attempt. With that context, correlate one
    fresh exact own video post, as the custom-cover field recovery did. A readable
    conflicting media ID rejects it; a hidden ID is not a contradiction.
    This is not independent verification of posted bytes. The counter records
    intent before clicking, and an identical external concurrent post cannot be
    excluded. Missing native context or multiple exact posts stays uncertain.
    """
    text_fields = ('identity', 'video_sha256', 'upload_filename', 'media_url', 'expected_text', 'started_at')
    if any(type(receipt.get(key)) is not str for key in text_fields):
        return []
    if (type(identity) is not str or not re.fullmatch(r'[A-Za-z0-9_]+', identity)
            or receipt['identity'].casefold() != identity.casefold()
            or receipt.get('method') != 'browser' or receipt.get('platform') != 'x' or receipt.get('schedule')):
        return []
    if (any(receipt.get(key) is not True for key in ('media_selected', 'file_video_seen', 'metadata_readback', 'final_submit_attempted'))
            or type(receipt.get('final_presses')) is not int or receipt['final_presses'] != 1):
        return []
    source = receipt['video_sha256']
    if (not re.fullmatch(r'[0-9a-f]{64}', source)
            or not re.fullmatch(r'crosscast-' + source[:16] + r'\.[A-Za-z0-9]+', receipt['upload_filename'])):
        return []
    words = normalize(receipt['expected_text'])
    media = re.fullmatch(r'https://studio\.x\.com/library/\d+_(\d+)/edit', receipt['media_url'])
    before = receipt.get('before_ids')
    if (not words or not media or type(before) is not list
            or any(type(known) is not str or not re.fullmatch(r'[0-9]+', known) for known in before)):
        return []
    try:
        started = datetime.fromisoformat(receipt['started_at'].replace('Z', '+00:00'))
        until = datetime.now(timezone.utc) + X_CLOCK_GRACE
        if started.tzinfo is None or started > until:
            return []
        began = started - X_CLOCK_GRACE
    except (ValueError, OverflowError):
        return []
    matches = [post for post in await own_posts(page, identity)
               if post['id'] not in before and post['has_video'] and normalize(post['text']) == words
               and began <= datetime.fromisoformat(post_time(post['id'])) <= until]
    # Count before checking media: a conflicting or hidden ID must not collapse two posts into one.
    if len(matches) != 1 or (matches[0]['media_id'] is not None and matches[0]['media_id'] != media[1]):
        return []
    return [{**matches[0], 'url': work_url('x', identity, matches[0]['id']), 'discovery_source': X_DISCOVERY_SOURCE}]


async def instagram_caption_says(page, identity, words):
    """Whether the post's own caption is exactly these words. Reviews found they passed from anywhere on the page, from the owner's own comment, and from inside a longer caption. A field read of a real post found a row holding the owner's link and a time, then the whole caption, with no list, article or lasting class around it. So the row comes from the first time on the page rather than from the words, and all of its words have to match.
    That the caption's row comes before any comment is how Instagram lays out a post, not something that read showed: it was of a photo page with no comments on it, so a Reel's page is still to be seen. A review found the limit of that: an owner's comment with these words would pass on a post with no caption, or with another caption that has no time beside it, or with the comment above the caption. Until a real page with comments has been read, a pass here is not proof of the caption."""
    return await page.evaluate(INSTAGRAM_CAPTION, {"words": normalize(words), "owner": identity})


async def x_post_reads_as_this_upload(page, receipt):
    """Whether a post we sent reads back from its own row: exactly its words, and its own video. Reviews found a reply's words, or a quoted post's video, passed on the rest of the page.
    A field read found a real post's video in its own row. A custom cover leaves no number to read, so a number can only rule a post out.
    This checks an already identified post. Discovery without an ID requires the native attempt context in x_new_posts."""
    from patchright.async_api import Error as BrowserError
    media = re.search(r"/library/\d+_(\d+)", receipt.get("media_url") or "")
    row = post_row(page, receipt["id"]).first

    async def reads_right():
        try:
            fields = await post_fields(row)
        except BrowserError:
            return False
        return bool(fields and fields["id"] == str(receipt["id"]) and normalize(fields["text"]) == normalize(receipt.get("expected_text") or receipt.get("title")) and fields["has_video"]
                    and not (media and fields["media_id"] and fields["media_id"] != media[1]))
    return bool(await look_until(page, reads_right, 6000))


async def douyin_card_id(page, name, card):
    """The ID behind a card of the works list, or None if it can't be told, which says nothing about the other cards."""
    from patchright.async_api import Error as BrowserError
    if not await card.get_by_text('私密', exact=True).count():
        try:
            async with page.expect_popup(timeout=POPUP_MS) as opened:
                await name.click()
            detail = await opened.value
        except BrowserError:
            detail = None
        if detail:
            try:
                # Its address is all this needs, and a field run saw this page not finish loading in 15 seconds.
                await detail.wait_for_url(re.compile(r'/work-detail/\d+'), timeout=15000, wait_until='commit')
                match = re.fullmatch(r'/creator-micro/work-management/work-detail/(\d+)', urlsplit(detail.url).path)
                return match[1] if match and urlsplit(detail.url).hostname == 'creator.douyin.com' else None
            except BrowserError:
                return None  # A page that never says which work it is can't tell this card's ID, which says nothing about the others.
            finally:
                await detail.close()
        # From a review: a click that opens no new tab could move this one, and the rest would then be read from the wrong page. Never seen on a real account.
        if urlsplit(page.url).path != urlsplit(MANAGE).path:
            await page.goto(MANAGE, wait_until="domcontentloaded", timeout=60000)
            return None
    return await douyin_id_from_menu(page, douyin_card_words(await name.inner_text()))


DOUYIN_LIST_SECONDS = 45
DOUYIN_LIST_ENDS = re.compile('没有更多作品|暂无作品|暂无内容')


async def works(page, platform, identity, must_answer=False):
    if platform == "youtube":
        from .youtube_studio import works as studio_works
        return await studio_works(page, identity)
    url = MANAGE if platform == "douyin" else f"https://www.instagram.com/{identity}/" if platform == "instagram" else f"https://x.com/{identity}"
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    # A field read found Douyin's list empty at 5 seconds and filled by 15, so ten seconds could answer that an account had no works.
    for _ in range(DOUYIN_LIST_SECONDS if platform == 'douyin' else 10):
        await page.wait_for_timeout(1000)
        await risk_check(page)
        found = await links(page, platform, identity)
        if found:
            return found
        if platform == 'douyin':
            # Creator Center cards use click handlers rather than hrefs.
            # Opening a work's own page or edit page is read-only; nothing is saved or deleted here.
            titles = page.locator('[class^="info-title-text-"]')
            if await titles.locator('visible=true').count():
                for title in (await titles.all())[:10]:
                    text = await title.inner_text()
                    card = title.locator('xpath=ancestor::div[starts-with(@class,"video-card-content-")]')
                    # A title outside any card says nothing CrossCast can read, and asking its card for words would wait out the whole timeout.
                    if not await card.count() or douyin_card_is_waiting(await douyin_card_status(card)):
                        continue
                    k = await douyin_card_id(page, title, card)
                    if k:
                        found[k] = {'id': k, 'url': work_url(platform, identity, k), 'text': text}
                return found
            if await page.get_by_text(DOUYIN_LIST_ENDS).locator('visible=true').count():
                return {}
    if must_answer:
        raise VpubError('list_not_ready', "The account's list of works didn't load, so nothing could be compared with it. No video was selected.", 'Try again in a moment.')
    return {}


def studio_visibility(text):
    """The setting named in Studio's visibility control, in its English or Chinese interface."""
    lines = {line.strip() for line in (text or "").splitlines()}
    return next((value for value, labels in STUDIO_VISIBILITY.items() if lines & labels), None)


def studio_style(styles, text):
    """The style a whole Studio date or time is written in, or None."""
    text = normalize(text)
    return next((style for style in styles if (match := re.fullmatch(style[0], text)) and style[1](match)), None)


def studio_read(styles, text):
    style = studio_style(styles, text)
    return style[1](re.fullmatch(style[0], normalize(text))) if style else None


def studio_moments(text):
    """Every date and every time mentioned in a Studio summary such as "Scheduled for ...", as numbers."""
    rest = normalize(text)
    dates = {read(m) for pattern, read, _ in STUDIO_DATES for m in re.finditer(pattern, rest)} - {None}
    times = set()
    for pattern, read, _ in STUDIO_TIMES:
        times |= {read(m) for m in re.finditer(pattern, rest)}
        rest = re.sub(pattern, " ", rest)
    return dates, times


def studio_offsets(text):
    """The GMT offsets Studio's scheduler names, in minutes."""
    return {(-1 if sign in "-−" else 1) * (int(hours) * 60 + int(minutes or 0))
            for sign, hours, minutes in re.findall(r"GMT\s*([+\-−])\s*(\d{1,2})(?::?(\d{2}))?", text or "")}


def studio_zone_offsets_agree(offsets, local, current):
    """A trusted named zone may label itself with today's or the target-date offset.

    This checks a display label, never converts the scheduled wall time by that label.
    Both callers first establish the target's local time from the named zone's rules.
    """
    valid = {local.utcoffset().total_seconds() / 60, current.utcoffset().total_seconds() / 60}
    return len(offsets) <= 1 and offsets <= valid


def youtube_schedule_passed(schedule):
    """Whether a scheduled video's time has come, or None if its record can't say."""
    try:
        return datetime.now(timezone.utc) >= datetime.fromisoformat(schedule["at"])
    except (KeyError, TypeError, ValueError):
        return None


def youtube_schedule_zone_agrees(shown, schedule):
    """Check the approved instant in its trusted zone; display offsets follow the pre-Save rule."""
    # UTC+01:00 is an offset label, not a second named zone called UTC.
    shown = re.sub(r'(?<![\w/])UTC(?=\s*[+\-−])', 'GMT', shown)
    # An IANA key such as Etc/GMT+5 is a name, not a GMT+5 display.
    names = set(re.findall(r'(?<![\w/])(?:UTC|[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)+)(?![\w/])', shown))
    offsets_text = shown
    for name in names:
        if name != 'UTC':
            offsets_text = offsets_text.replace(name, '')
    offsets = studio_offsets(offsets_text)
    if not names and not offsets and not schedule.get('timezone'):
        return True  # Older wall-clock-only readback remains bounded by the pre-Save check.
    try:
        local = datetime.fromisoformat(schedule['local'])
        intended = datetime.fromisoformat(schedule['at'])
        if local.utcoffset() is None or intended.utcoffset() is None or local != intended:
            return False
        name = schedule.get('timezone')
        if names and names != {name}:
            return False
        if name:
            from zoneinfo import ZoneInfo
            zone = timezone.utc if name == 'UTC' else ZoneInfo(name)
            target = intended.astimezone(zone)
            if target.isoformat() != local.isoformat() or target.replace(fold=1 - target.fold).utcoffset() != target.utcoffset():
                return False
            return studio_zone_offsets_agree(offsets, target, datetime.now(zone))
        # Legacy offset-only evidence cannot establish target-date zone rules.
        return not offsets or offsets == {local.utcoffset().total_seconds() / 60}
    except (KeyError, TypeError, ValueError):
        return False


async def verify_youtube(page, receipt):
    # Studio opens this page only for the signed-in channel's own videos, so reading it also proves ownership.
    url = work_url("youtube", None, receipt["id"])
    def on_this_video():
        address = urlsplit(page.url)
        return (address.scheme == "https" and address.netloc == "studio.youtube.com"
                and address.path == f"/video/{receipt['id']}/edit")

    stop_check('youtube')
    await page.goto(f"https://studio.youtube.com/video/{receipt['id']}/edit", wait_until="domcontentloaded", timeout=60000)
    title = page.locator("#title-textarea #textbox").first
    seen = {}
    schedule = receipt.get("schedule")
    passed = youtube_schedule_passed(schedule) if schedule else None
    try:
        if not on_this_video():
            raise VpubError('wrong_work', "Studio did not open this attempt's video.")
        await title.wait_for(state="visible", timeout=30000)
        for _ in range(20):
            seen["title"] = normalize(await title.inner_text())
            if seen["title"]:
                break
            await page.wait_for_timeout(1000)
        seen["description"] = normalize(await page.locator("#description-textarea #textbox").first.inner_text())
        # The one field read on a real channel came three seconds after the title, so it gets that long to fill in here too.
        shown = await look_until(page, lambda: page.locator("ytcp-video-metadata-visibility #visibility-text").first.inner_text(timeout=1000), 3000)
        seen["visibility"] = studio_visibility(shown)
        if schedule:
            dates, times = studio_moments(shown)
            seen['schedule_timezone'] = youtube_schedule_zone_agrees(shown, schedule)
            if seen["visibility"] != "public" and tuple(schedule["date"]) in dates and tuple(schedule["time"]) in times:
                seen["visibility"] = "scheduled"
        if receipt.get("playlists"):
            listed = normalize(await page.locator("ytcp-video-metadata-playlists").first.inner_text())
            seen["playlists"] = all(normalize(title) in listed for title in receipt["playlists"])
    except Exception:
        pass
    await risk_check(page)
    stop_check('youtube')
    bound = on_this_video()
    # A field run's Publish left a draft: its page has a button to edit it, and no visibility setting at all.
    still_a_draft = bound and not seen.get("visibility") and await visible(page.get_by_text(YT_EDIT_DRAFT)) is not None
    expected = {"title": normalize(receipt["title"]) if receipt.get("title") else None,
                "description": normalize(receipt["expected_text"]) if "expected_text" in receipt else None,
                # A scheduled video shows its date and time until then, and afterwards it is public like any other.
                "visibility": ("public" if passed else "scheduled") if schedule else receipt.get("visibility"),
                "playlists": True if receipt.get("playlists") else None}
    differs = sorted(name for name, value in expected.items() if value is not None and seen.get(name) != value)
    if not bound:
        differs.append("video_id")
    if schedule and passed is None:
        differs.append("schedule_time")
    if schedule and seen.get('schedule_timezone') is False:
        differs.append('schedule_timezone')
    present = bool(seen.get("title")) and not differs
    note = ("Current title, description and public visibility read back after the intended schedule; actual publication time and on-time release were not established." if present and passed else
            "Title, description, visibility or schedule, and playlists read back from the channel's own Studio page; cover pixels and playback are separate." if present else
            "Studio still shows this as a draft, so viewers can't see it. Press Publish on this same draft with vpub resume; do not upload again." if still_a_draft else
            "Expected work/text not yet verified; do not upload again.")
    return {**receipt, "url": url, "state": "SUBMISSION_VERIFIED" if present else "SUBMITTED_UNVERIFIED",
            "phase": "complete" if present else "readback_pending", "error": None if present else receipt.get("error"),
            "verification_source": "independent_browser_dom", "verified_at": now(), "readback_differs": differs, "note": note, "still_a_draft": still_a_draft,
            **({"schedule_evidence": {"intended_at": schedule.get("at"), "scheduled_time_elapsed": passed, "on_time_publication_verified": False}} if schedule else {})}


def x_chosen_time_says(words, schedule):
    """Whether words like Nov 4, 6:26 PM, which a field run saw on the button that opened the calendar, are this schedule's date and time."""
    found = X_CHOSEN_TIME.fullmatch(normalize(words))
    if not found or found[1] not in MONTHS:
        return False
    date = (int(found[3] or schedule['date'][0]), MONTHS.index(found[1]) + 1, int(found[2]))
    return date == tuple(schedule['date']) and (int(found[4]) % 12 + (12 if found[6] == 'PM' else 0), int(found[5])) == tuple(schedule['time'])


def x_row_says_schedule(day, clock, schedule):
    """Whether a row's Nov 8 Sun and 9:11 PM, as a field read found them on Studio's schedule page, are this schedule's. They carry no year, so the day of the week has to agree too."""
    found = X_SCHEDULE_DAY.fullmatch(normalize(day))
    return bool(found) and found[2] == WEEKDAYS[datetime(*schedule['date']).weekday()] and x_chosen_time_says(f'{found[1]}, {normalize(clock)}', schedule)


def x_schedule_moment(when):
    """The date and time Studio shows for a moment. Its calendar, clock and schedule page all use this computer's time zone."""
    local = when.astimezone()
    return {'at': when.isoformat(), 'local': local.isoformat(), 'date': [local.year, local.month, local.day], 'time': [local.hour, local.minute], 'timezone_basis': 'computer'}


def x_row_has_words(cells, words):
    return len(cells) >= len(X_SCHEDULE_HEADINGS) and normalize(cells[2]) == words


def x_row_is_a_post(cells):
    """A field read found the empty page keeps its message in a row of its own, in a single cell. A row with a post's cells or its day is a post's, however far it has got."""
    return len(cells) >= len(X_SCHEDULE_HEADINGS) or bool(X_SCHEDULE_DAY.fullmatch(normalize(cells[0])))


def x_row_is_finished(cells):
    """A row showing everything a field read's did: Nov 8 Sun, 9:11 PM, the words and the media."""
    return (len(cells) >= len(X_SCHEDULE_HEADINGS) and bool(X_SCHEDULE_DAY.fullmatch(normalize(cells[0]))) and bool(X_SCHEDULE_CLOCK.fullmatch(normalize(cells[1])))
            and bool(normalize(cells[2])) and bool(normalize(cells[3])))


def x_row_has_schedule(cells, schedule):
    return x_row_says_schedule(cells[0], cells[1], schedule) and normalize(cells[3]) == "Video"


async def x_schedule_rows(page):
    """The cells of each row showing in the one table with these headings on Studio's schedule page, read together so that nothing is combined across a redraw. A review found rows in hidden or other tables could count."""
    return await page.evaluate(X_SCHEDULE_TABLE, X_SCHEDULE_HEADINGS)


async def x_schedule_read(page):
    """All the posts Studio's schedule page holds, once it can be taken at its word. Reviews found a table can show its headings, something in place of its rows, or rows still filling in, and any of those would say nothing like this post is there.
    So every post's row has to be finished, and no rows is only an answer once the page says so in its own words."""
    rows = await x_schedule_rows(page)
    if rows is None:
        return None
    posts = [cells for cells in rows if x_row_is_a_post(cells)]
    if posts:
        return {"rows": posts} if all(x_row_is_finished(cells) for cells in posts) else None
    return {"rows": []} if await visible(page.get_by_text(X_SCHEDULE_EMPTY, exact=True)) else None


async def x_schedule_baseline(page, post_text, schedule):
    """How many rows already look like this post, read before anything of its own could be there. None if the page won't say."""
    await page.goto(X_SCHEDULE_PAGE, wait_until="domcontentloaded", timeout=60000)
    read = await look_until(page, lambda: x_schedule_read(page), X_SCHEDULE_SECONDS * 1000)
    await risk_check(page)
    if not read:
        return None
    return sum(x_row_has_words(cells, normalize(post_text)) and x_row_has_schedule(cells, schedule) for cells in read["rows"])


async def verify_scheduled_x(page, identity, receipt):
    """A scheduled post has no address until it goes out. Before its time, a field read found its row on Studio's schedule page shows its words, date and time, and that it has a video, in that
    order, with no ID or link. A review showed a row already there would pass for it, so it only counts if the read before the upload found none like it. One such row settles that it's scheduled.
    It can't say which video, which is why the final press is tied to the file's own, and an identical row added by someone else since that read would pass too.
    After its time, one new post with exactly its words, at about that time, showing the video Media Studio numbered, settles it. Without that number it is named as a candidate for a person to check."""
    moment = datetime.fromisoformat(receipt["schedule"]["at"])
    if datetime.now(timezone.utc) >= moment:
        words = normalize(receipt.get("expected_text") or receipt["title"])
        await works(page, "x", identity)  # Opens the account's page and waits for its posts.
        matches = [post for post in await own_posts(page, identity) if post["id"] not in receipt.get("before_ids", []) and normalize(post["text"] or "") == words and post["has_video"] and posted_around(post, moment)]
        if len(matches) != 1:
            return {**receipt, "state": "SUBMITTED_UNVERIFIED", "phase": "readback_pending", "note": f"Its time has passed and {len(matches)} posts on the first page match it exactly. Inspect the account; do not post again."}
        address = work_url("x", identity, matches[0]["id"])
        media = re.search(r"/library/\d+_(\d+)/", receipt.get("media_url") or "")
        if not media or matches[0].get("media_id") != media[1]:
            return {**receipt, "state": "SUBMITTED_UNVERIFIED", "phase": "readback_pending", "candidate_id": matches[0]["id"], "candidate_url": address,
                    "note": "One new post has its exact words at about its time, but nothing here proves it carries this video. Check that post; do not post again."}
        return await verify(page, "x", identity, {**receipt, "id": matches[0]["id"], "url": address})
    await page.goto(X_SCHEDULE_PAGE, wait_until="domcontentloaded", timeout=60000)
    words = normalize(receipt.get("expected_text") or receipt["title"])

    # Another post's unfinished row doesn't stop this one being read: only the read before the upload needs them all.
    async def rows_with_its_words():
        return [cells for cells in await x_schedule_rows(page) or [] if x_row_has_words(cells, words)]
    rows = await look_until(page, rows_with_its_words, X_SCHEDULE_SECONDS * 1000)
    await risk_check(page)
    matching = [cells for cells in rows if x_row_has_schedule(cells, receipt["schedule"])]
    before = receipt.get("schedule_rows_before")
    found = {"visibility": "scheduled", "review_url": X_SCHEDULE_PAGE, "verified_at": now(), "schedule_rows_with_its_words": len(rows), "schedule_rows_matching": len(matching)}
    if len(matching) == 1 and type(before) is int and before == 0:
        return {**receipt, **found, **X_SCHEDULED}
    return {**receipt, **found, "state": "SUBMITTED_UNVERIFIED", "phase": "readback_pending", "verification_source": "studio_schedule_page",
            "note": f"Studio's schedule page shows {len(rows)} row(s) with exactly its words, and {len(matching)} with its date and time and a video. Before the upload it showed {before if type(before) is int else 'an unknown number'} like that. One new row was expected, so check them there. Do not post again."}


def posted_around(post, moment):
    """X publishes a scheduled post within minutes of its time. A post with no readable time is not evidence."""
    try:
        return timedelta(minutes=-1) <= datetime.fromisoformat(str(post.get("published_at")).replace("Z", "+00:00")) - moment <= timedelta(minutes=30)
    except (ValueError, TypeError):
        return False


async def verify(page, platform, identity, receipt):
    if receipt.get('identity') and receipt['identity'].casefold() != identity.casefold():
        raise VpubError('wrong_account', 'Original receipt belongs to another signed-in account.')
    if platform == 'x' and receipt.get('schedule') and not receipt.get('id'):
        return await verify_scheduled_x(page, identity, receipt)
    if not receipt.get('id') and receipt.get('final_submit_attempted') and 'before_ids' in receipt:
        found = await works(page, platform, identity)
        if platform == 'x':
            matches = await x_new_posts(page, identity, receipt)
        else:
            matches = douyin_new_works(platform, receipt, [v for k,v in found.items() if k not in receipt['before_ids'] and
                                                         receipt.get('title') and normalize(receipt['title']) in normalize(v['text'])])
        if len(matches) == 1:
            receipt = {**receipt, 'id':matches[0]['id'], 'url':matches[0]['url']}
            if platform == 'x':
                receipt['discovery_source'] = matches[0]['discovery_source']
    if platform == "youtube" and not receipt.get("id") and receipt.get("draft_id"):
        # A final Save that outlasted its wait left only the upload's own ID, which is the video's ID,
        # and the video may have been saved since. Only a full read-back gives the receipt that ID.
        checked = await verify_youtube(page, {**receipt, "id": receipt["draft_id"]})
        if checked["state"] == "SUBMISSION_VERIFIED":
            return checked
        return {**{key: value for key, value in checked.items() if key not in ("id", "url")},
                **{key: receipt[key] for key in ("id", "url") if key in receipt}}
    if not receipt.get("id"):
        if not receipt.get("final_submit_attempted"):
            # Nothing was pressed, so there is nothing of it to find. A field run's verify called such an attempt SUBMITTED_UNVERIFIED,
            # after which every way forward refused it: run again skipped it, delete needed a verified post, and verify could never find one.
            return {**receipt, "state": "PREPARATION_INCOMPLETE", "platform_writes": 0,
                    "note": "This attempt stopped before its final press, so nothing was submitted. vpub resume picks it up again and never presses twice."}
        if platform == 'douyin' and await douyin_scheduled_card(page, receipt):
            return {**receipt, **DOUYIN_SCHEDULED, 'verified_at': now()}
        if platform == 'douyin' and receipt.get('title') and await douyin_card_under_review(page, receipt['title']):
            return {**receipt, 'state':'SUBMITTED_UNVERIFIED', 'phase':'platform_review',
                    'platform_status':'UNDER_REVIEW', 'review_url':MANAGE, 'error':None,
                    'note':'Matching submitted card is under platform review; verify later, never reupload.'}
        return {**receipt, "state": "SUBMITTED_UNVERIFIED", "note": "No durable work ID; inspect the original work, never reupload automatically."}
    if platform == "youtube":
        return await verify_youtube(page, receipt)
    url = work_url(platform, identity, receipt["id"])
    owned = None
    if platform == 'instagram':
        # Current Reel pages are not articles. Confirm ownership from this
        # account's grid first, then read the actual permalink and caption.
        owned = (await works(page, platform, identity)).get(str(receipt['id']))
    if platform == "douyin":
        found = await works(page, platform, identity)
        present = str(receipt["id"]) in found
    else:
        # A field run found a Reel's own address goes on to its viewer, which has no caption to read, while /p/ shows the same post with one.
        await page.goto(f"https://www.instagram.com/p/{receipt['id']}/" if platform == 'instagram' else url, wait_until="domcontentloaded", timeout=60000)
        if platform == 'x':
            try:
                await post_row(page, receipt['id']).first.wait_for(timeout=20000)
            except Exception:
                pass
        expected = receipt.get("expected_text")
        if platform == "instagram":
            # Everything read below, as soon as all of it shows. A page that never shows it gets the same four seconds.
            async def shown():
                return (await visible(page.locator('a[href="/'+identity+'/"]')) and await visible(page.locator('video'))
                        and (not expected or await instagram_caption_says(page, identity, expected)))
            await look_until(page, shown, 4000)
        await risk_check(page)
        present = str(receipt["id"]) in await links(page, platform, identity)
        if platform == "instagram":
            # An arbitrary video/player or the browser address is not evidence.
            author = await visible(page.locator('a[href="/'+identity+'/"]'))
            present = bool(owned and author and await visible(page.locator('video')) and instagram_address_is(page.url, identity, receipt['id']))
        if expected and platform == "instagram":
            present = present and await instagram_caption_says(page, identity, expected)
        elif expected:
            actual = normalize(await page.locator("body").inner_text())
            present = present and normalize(expected) in actual
        if platform == 'x' and present and receipt.get('final_submit_attempted'):
            present = await x_post_reads_as_this_upload(page, receipt)
    if platform == 'douyin' and receipt.get('expected_text'):
        present = present and receipt['expected_text'] in found.get(str(receipt['id']), {}).get('text', '')
    if platform == 'douyin' and present and receipt.get('visibility') == 'private' and 'expected_caption' in receipt:
        # Its card and its edit page are all there is to compare, so the whole text has to be right.
        present = type(receipt['id']) is str and douyin_card_words(found[receipt['id']]['text']) in douyin_card_says(receipt.get('title') or '', receipt['expected_caption'])
    if platform == 'douyin' and present and receipt.get('schedule') and datetime.now(timezone.utc) < datetime.fromisoformat(receipt['schedule']['at']):
        # Before its time, only the work showing that time proves the schedule took, rather than it having gone out already.
        present = receipt['schedule']['stamp'] in found.get(str(receipt['id']), {}).get('text', '')
    note = "Work and expected text verified on the signed-in page; cover pixels and anonymous playback are separate."
    if platform == 'x' and receipt.get('discovery_source') == X_DISCOVERY_SOURCE:
        note = ("One fresh own video post matches the bound native attempt and exact text. This is correlation, not independent verification of posted video bytes; "
                "an identical external concurrent post cannot be excluded. Cover pixels and anonymous playback are separate.")
    return {**receipt, "url": url, "state": "SUBMISSION_VERIFIED" if present else "SUBMITTED_UNVERIFIED",
            'phase':'complete' if present else 'readback_pending', 'error':None if present else receipt.get('error'),
            "verification_source": "independent_browser_dom", "verified_at": now(),
            "note": note if present else "Expected work/text not yet verified; do not upload again."}


async def execute(page, platform, identity, payload):
    action = payload["action"]
    if action == 'edit':
        from .browser_edits import execute as edit
        return await edit(page, platform, identity, payload)
    if action == "works":
        return {"identity": identity, "items": list((await works(page, platform, identity)).values()),
                "scope": "Visible first page only; not a complete account export."}
    if action == "verify":
        result=await verify(page, platform, identity, payload["receipt"])
        return await complete_pending_chapters(page,platform,identity,result)
    if action not in {"publish", "resume"}:
        raise VpubError("unsupported_action", "Use status, works, publish or verify.")
    tx = Transaction(platform, identity, payload["plan"], resume=action == 'resume', prior_receipt=payload.get('prior_receipt'))
    if tx.receipt['final_submit_attempted']:
        result=await verify(page, platform, identity, tx.receipt)
        # The read-back has just shown nothing went public, and the video is already there, so finishing it can't make a second one.
        if not (action == 'resume' and result.get('still_a_draft')):
            return tx.save(**await complete_pending_chapters(page,platform,identity,result))
        tx.save(**result)
    for stale in ('hint', 'blocked_phase'):
        tx.receipt.pop(stale, None)
    tx.save(state='PREPARING', error=None, user_action_required=False)
    try:
        tx.validate_assets()
        from . import browser_forms
        result=await getattr(browser_forms, "publish_" + platform)(page, tx)
        return tx.save(**await complete_pending_chapters(page,platform,identity,result))
    except Exception as e:
        # Local diagnostics, deliberately outside the repository.
        debug = home() / 'browser-debug' / (payload['plan']['confirmation']+'-'+platform)
        debug.mkdir(parents=True, exist_ok=True)
        try:
            await failure_record(page, e, debug/'failure.json', picture=debug/'last-page.png')
        except Exception:
            pass
        human = isinstance(e, VpubError) and e.code in {"human_verification_required", "auth_expired"}
        state = "WAITING_FOR_USER" if human else "UNKNOWN" if tx.receipt["final_submit_attempted"] else "PREPARATION_INCOMPLETE"
        return tx.save(state=state, blocked_phase=tx.receipt['phase'], phase="blocked", review_url=page.url,
                       error={"code": e.code if isinstance(e, VpubError) else "browser_step_failed",
                              "message": e.message if isinstance(e, VpubError) else failure_message(e)},
                       user_action_required=human,
                       hint="Progress is saved here. Whether the platform kept a draft isn't known, so look there before running it again. Nothing is uploaded or submitted again automatically.")


async def complete_pending_chapters(page,platform,identity,receipt):
    pending=receipt.get('pending_chapters')
    if platform!='douyin' or not pending or receipt.get('chapters_state')=='VERIFIED': return receipt
    if not receipt.get('id') or receipt.get('state')!='SUBMISSION_VERIFIED':
        return {**receipt,'chapters_state':'PENDING_WORK_ID','next':'Run vpub verify for this receipt after platform review; chapters are not yet applied.'}
    from .browser_edits import execute as edit
    path=home()/'edits'/('douyin-chapters-'+receipt['id']+'.json')
    if path.exists() and read_json(path).get('state') in {'EDIT_SUBMITTING','EDIT_SUBMITTED_UNVERIFIED'}:
        return {**receipt,'chapters_state':'SAVE_UNVERIFIED','next':'Inspect the existing saved chapters; no automatic second Save.'}
    if path.exists() and read_json(path).get('state')=='EDIT_VERIFIED':
        return {**receipt,'chapters_state':'VERIFIED','chapters_receipt':read_json(path)}
    try:
        result=await edit(page,platform,identity,{'receipt':receipt,
            'patch':{**pending,'expected_identity':identity},'edit_receipt_path':str(path)})
        return {**receipt,'chapters_state':'VERIFIED' if result['state']=='EDIT_VERIFIED' else 'SAVE_UNVERIFIED','chapters_receipt':result}
    except Exception as exc:
        # The words go with the code: a field run's three tries each left only TimeoutError, which says nothing about where it stopped.
        waiting=isinstance(exc,VpubError) and exc.code=='not_public_yet'
        return {**receipt,'chapters_state':'INCOMPLETE','chapters_error':exc.code if isinstance(exc,VpubError) else type(exc).__name__,
                'chapters_error_message':exc.message if isinstance(exc,VpubError) else failure_message(exc),
                'next':'Douyin is still reviewing this video. Run vpub verify for this receipt once it is public, and its chapters go on then. Never upload it again.' if waiting
                       else 'The video exists. Fix/retry only the chapter step; do not upload the video again.'}

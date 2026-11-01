"""CLI browser execution with durable phases and exactly one final submit."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import re
import time
from pathlib import Path
import unicodedata
from urllib.parse import urlsplit

from .core import VpubError, atomic_json, file_sha, home, now, read_json, snapshot_file, stop_check
from .browser_runtime import failure_message, failure_record, look_until, plain_address, risk_check, unique_visible, visible
from .progress import stage
from .x_pages import own_posts

MANAGE = "https://creator.douyin.com/creator-micro/content/manage"
STUDIO_VISIBILITY = {"public": {"Public", "公开", "公開"}, "unlisted": {"Unlisted", "不公开列出", "不公開"}, "private": {"Private", "私享", "私人"}}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
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
    raise VpubError("processing_timeout", f"Timed out waiting for {label}; existing upload retained.")


async def upload_file(page, path, trigger=None, accept=None):
    inputs = page.locator('input[type="file"]')
    candidates = []
    for item in await inputs.all():
        value = (await item.get_attribute("accept") or "").lower()
        if accept is None or accept in value:
            candidates.append(item)
    if len(candidates) == 1:
        await candidates[0].set_input_files(str(path), timeout=60000)
    elif trigger:
        async with page.expect_file_chooser(timeout=15000) as chosen:
            await trigger.click(timeout=15000)
        await (await chosen.value).set_files(str(path), timeout=60000)
    else:
        raise VpubError("layout_changed", "Could not identify a unique file input for this upload.")


class Transaction:
    def __init__(self, platform, identity, plan, resume=False):
        self.platform, self.identity, self.plan = platform, identity, plan
        self.options = plan["options"]
        self.path = home() / "browser-receipts" / (plan["confirmation"] + "-" + platform + ".json")
        if self.path.exists() and not resume:
            raise VpubError("attempt_exists", "This independent browser attempt already has a durable receipt.", "Inspect/verify that attempt. Never reset it to upload or submit again.")
        self.receipt = {"state": "PREPARING", "method": "browser", "platform": platform,
                        "identity": identity, "title": self.options["title"], "phase": "initial",
                        "video_sha256": plan["video_sha256"], "final_submit_attempted": False,
                        "media_selected": False, "started_at": now()}
        # No receipt means this attempt never reached the page, so it can start from the beginning.
        if resume and self.path.exists():
            saved = read_json(self.path)
            if saved.get('video_sha256') != plan['video_sha256'] or saved.get('identity') != identity:
                raise VpubError('receipt_conflict', 'Resume must use the same account and media.')
            self.receipt = saved
        elif platform=='douyin' and self.options.get('chapters'):
            self.receipt['pending_chapters']={'chapters':self.options['chapters'],
                                             'duration_seconds':self.options['duration_seconds']}
            self.receipt['chapters_state']='PENDING_WORK_ID'

    def save(self, **changes):
        for key in ("review_url", "draft_url", "media_url"):
            if changes.get(key):
                changes[key] = plain_address(changes[key])
        self.receipt.update(changes, updated_at=now())
        atomic_json(self.path, self.receipt)
        return dict(self.receipt)

    def media(self):
        src = Path(self.plan["video"])
        # The upload's file name, as a link to the private snapshot rather than a second copy of the video.
        dst = snapshot_file(src, src.with_name("crosscast-" + self.plan["video_sha256"][:16] + src.suffix), self.plan["video_sha256"], link=True)
        self.save(phase="selecting_media", media_selected=True, upload_filename=dst.name)
        stage("uploading")
        return dst

    def validate_assets(self):
        if file_sha(self.plan["video"]) != self.plan["video_sha256"]:
            raise VpubError("source_changed", "Snapshot changed before browser upload.")
        for asset in self.plan.get("covers", {}).get(self.platform, {}).values():
            if file_sha(asset["path"]) != asset["sha256"]:
                raise VpubError("source_changed", "A cover snapshot changed before upload.")
        if self.plan.get("cover") and file_sha(self.plan["cover"]) != self.plan["cover_sha256"]:
            raise VpubError("source_changed", "Cover snapshot changed before upload.")

    def cover(self, role="landscape"):
        asset = self.plan.get("covers", {}).get(self.platform, {}).get(role)
        shared = self.plan.get("cover") if role == "landscape" else None
        if not asset and not shared and self.platform == 'instagram' and role == 'landscape':
            asset = self.plan.get('covers', {}).get('instagram', {}).get('portrait')
        return asset["path"] if asset else shared

    async def submit(self, page, button):
        if self.options.get("submission_mode") == "draft_only":
            raise VpubError("draft_only", "Final submission is forbidden in draft-only mode.")
        if self.receipt["final_submit_attempted"]:
            raise VpubError("already_submitted", "A final submission was already attempted.")
        await risk_check(page)
        stop_check(self.platform)
        self.save(state="UNKNOWN", phase="submitting", final_submit_attempted=True, review_url=page.url)
        stage("pressing")
        # Persist BEFORE clicking. A timeout must never cause another click.
        await button.click(timeout=20000)


async def links(page, platform, identity):
    rows = await page.locator("a[href]").evaluate_all("es => es.map(e => ({url:e.href,text:(e.closest('article,tr') || e).innerText || e.querySelector('img')?.alt || ''}))")
    result = {}
    for row in rows:
        u = urlsplit(row["url"])
        if platform == "x":
            match = re.fullmatch(r"/" + re.escape(identity) + r"/status/(\d+)", u.path, re.I) if u.hostname in {"x.com", "www.x.com"} else None
        elif platform == "instagram":
            match = re.fullmatch(r"/(?:" + re.escape(identity) + r"/)?(?:reel|p)/([\w-]+)/?", u.path, re.I) if u.hostname == "www.instagram.com" else None
        else:
            match = re.fullmatch(r"/video/(\d+)", u.path) if u.hostname in {"www.douyin.com", "douyin.com"} else None
            if not match and u.hostname == "creator.douyin.com":
                from urllib.parse import parse_qs
                mid = parse_qs(u.query).get("mid", [""])[0]
                if mid.isdigit():
                    result[mid] = {"id": mid, "url": work_url(platform, identity, mid), "text": row["text"]}
        if match:
            k = match[1]
            result[k] = {"id": k, "url": work_url(platform, identity, k), "text": row["text"]}
    return result


POPUP_MS = 15000
DOUYIN_CARD_TIME = re.compile(r"(\d{4})年(\d{2})月(\d{2})日 (\d{2}):(\d{2})")
DOUYIN_CARD = '[class^="video-card-content-"]'
DOUYIN_SCHEDULED = {'state': 'SUBMISSION_VERIFIED', 'phase': 'scheduled', 'error': None, 'verification_source': 'creator_card_title_and_time',
                    'note': 'Douyin shows this new card with its title and scheduled time, and no ID until the time comes. Verify again after that time.'}


def douyin_card_is_waiting(text):
    """A card still under review or scheduled for later opens no work's page. A field check found a scheduled card's only sign is a time still to come."""
    if re.search('审核中|定时发布', text):
        return True
    found = DOUYIN_CARD_TIME.search(text)
    return bool(found) and datetime(*map(int, found.groups())) > datetime.now()


async def douyin_card_status(card):
    """A card's words apart from its title node, which a field check found goes on to hold the caption. A person's words can mention any date or state; only the platform's count."""
    return normalize(await card.evaluate("""card => { const title = card.querySelector('[class^="info-title-text-"]'); return title ? card.innerText.replace(title.innerText, '') : card.innerText; }"""))


def douyin_card_words(text):
    """What was written. Spacing and invisible marks are the page's own, and punctuation is part of the words: a review showed 1.2 and 12 must not read the same."""
    return ''.join(c for c in unicodedata.normalize('NFC', text or '') if not c.isspace() and unicodedata.category(c) != 'Cf')


async def douyin_cards_titled(page, title, caption=''):
    """The Creator Center cards with exactly this title, or exactly this title and then this caption, which a field check found a card's title node holds, with a star where each new paragraph begins. A longer title or another caption is another work."""
    wanted = {douyin_card_words(title), douyin_card_words(title + caption), douyin_card_words(title + re.sub(r'\s*\n\s*', '*', caption.strip()))}
    cards = []
    for card in await page.locator(DOUYIN_CARD).all():
        names = card.locator('[class^="info-title-text-"]')
        if await names.count() and douyin_card_words(await names.first.inner_text()) in wanted:
            cards.append(card)
    return cards


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


async def works(page, platform, identity):
    if platform == "youtube":
        from .youtube_studio import works as studio_works
        return await studio_works(page, identity)
    url = MANAGE if platform == "douyin" else f"https://www.instagram.com/{identity}/" if platform == "instagram" else f"https://x.com/{identity}"
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    for _ in range(10):
        await page.wait_for_timeout(1000)
        await risk_check(page)
        found = await links(page, platform, identity)
        if found:
            return found
        if platform == 'douyin':
            # Creator Center cards use click handlers rather than hrefs.
            # Opening a work detail is read-only; never enter edit/delete here.
            titles = page.locator('[class^="info-title-text-"]')
            if await titles.count():
                for title in (await titles.all())[:10]:
                    text = await title.inner_text()
                    card = title.locator('xpath=ancestor::div[starts-with(@class,"video-card-content-")]')
                    if douyin_card_is_waiting(await douyin_card_status(card)):
                        continue
                    try:
                        async with page.expect_popup(timeout=POPUP_MS) as opened:
                            await title.click()
                        detail = await opened.value
                    except Exception:
                        continue  # One card that opens nothing says nothing about the others.
                    try:
                        # Its address is all this needs, and a field run saw this page not finish loading in 15 seconds.
                        await detail.wait_for_url(re.compile(r'/work-detail/\d+'), timeout=15000, wait_until='commit')
                        match = re.fullmatch(r'/creator-micro/work-management/work-detail/(\d+)', urlsplit(detail.url).path)
                        if match and urlsplit(detail.url).hostname == 'creator.douyin.com':
                            k = match[1]
                            found[k] = {'id': k, 'url': work_url(platform, identity, k), 'text': text}
                    finally:
                        await detail.close()
                return found
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


async def verify_youtube(page, receipt):
    # Studio opens this page only for the signed-in channel's own videos, so reading it also proves ownership.
    url = work_url("youtube", None, receipt["id"])
    await page.goto(f"https://studio.youtube.com/video/{receipt['id']}/edit", wait_until="domcontentloaded", timeout=60000)
    title = page.locator("#title-textarea #textbox").first
    seen = {}
    try:
        await title.wait_for(state="visible", timeout=30000)
        for _ in range(20):
            seen["title"] = normalize(await title.inner_text())
            if seen["title"]:
                break
            await page.wait_for_timeout(1000)
        seen["description"] = normalize(await page.locator("#description-textarea #textbox").first.inner_text())
        shown = await page.locator("ytcp-video-metadata-visibility #visibility-text").first.inner_text()
        seen["visibility"] = studio_visibility(shown)
        if receipt.get("schedule"):
            dates, times = studio_moments(shown)
            if tuple(receipt["schedule"]["date"]) in dates and tuple(receipt["schedule"]["time"]) in times:
                seen["visibility"] = "scheduled"
        if receipt.get("playlists"):
            listed = normalize(await page.locator("ytcp-video-metadata-playlists").first.inner_text())
            seen["playlists"] = all(normalize(title) in listed for title in receipt["playlists"])
    except Exception:
        pass
    await risk_check(page)
    expected = {"title": normalize(receipt["title"]) if receipt.get("title") else None,
                "description": normalize(receipt["expected_text"]) if "expected_text" in receipt else None,
                "visibility": "scheduled" if receipt.get("schedule") else receipt.get("visibility"),
                "playlists": True if receipt.get("playlists") else None}
    differs = sorted(name for name, value in expected.items() if value is not None and seen.get(name) != value)
    present = bool(seen.get("title")) and not differs
    return {**receipt, "url": url, "state": "SUBMISSION_VERIFIED" if present else "SUBMITTED_UNVERIFIED",
            "phase": "complete" if present else "readback_pending", "error": None if present else receipt.get("error"),
            "verification_source": "independent_browser_dom", "verified_at": now(), "readback_differs": differs,
            "note": "Title, description, visibility or schedule, and playlists read back from the channel's own Studio page; cover pixels and playback are separate." if present else "Expected work/text not yet verified; do not upload again."}


async def verify_scheduled_x(page, identity, receipt):
    """A scheduled post has no address until it goes out. Words on Studio's schedule page don't prove which post that is, its time
    or its video, so before its time this only reports them. After its time, one new post with exactly its words, at about that time,
    showing the video Media Studio numbered, settles it. Without that number it is named as a candidate for a person to check."""
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
    await page.goto("https://studio.x.com/schedule", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(4000)
    await risk_check(page)
    mentions = normalize(await page.locator("body").inner_text()).count(normalize(receipt["title"]))
    return {**receipt, "state": "SUBMITTED_UNVERIFIED", "phase": "readback_pending", "visibility": "scheduled", "schedule_page_mentions": mentions,
            "verification_source": "studio_schedule_page", "verified_at": now(),
            "note": f"Studio's schedule page shows this title {mentions} time(s). That does not prove the post, its time or its video, so check them there. Do not post again."}


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
        matches = [v for k,v in found.items() if k not in receipt['before_ids'] and
                   receipt.get('title') and normalize(receipt['title']) in normalize(v['text'])]
        if len(matches) == 1:
            receipt = {**receipt, 'id':matches[0]['id'], 'url':matches[0]['url']}
    if not receipt.get("id"):
        if platform == 'douyin' and await douyin_scheduled_card(page, receipt):
            return {**receipt, **DOUYIN_SCHEDULED, 'verified_at': now()}
        if platform == 'douyin' and receipt.get('title'):
            cards = page.locator('[class^="video-card-content-"]').filter(has_text=receipt['title'])
            if await cards.count() == 1 and '审核中' in await douyin_card_status(cards):
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
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if platform == 'x':
            try:
                await page.locator('article').filter(has_text=receipt.get('title','')).first.wait_for(timeout=20000)
            except Exception:
                pass
        expected = receipt.get("expected_text")
        if platform == "instagram":
            # Everything read below, as soon as all of it shows. A page that never shows it gets the same four seconds.
            async def shown():
                return (await visible(page.locator('a[href="/'+identity+'/"]')) and await visible(page.locator('video'))
                        and (not expected or normalize(expected) in normalize(await page.locator("body").inner_text())))
            await look_until(page, shown, 4000)
        await risk_check(page)
        present = str(receipt["id"]) in await links(page, platform, identity)
        if platform == "instagram":
            # An arbitrary video/player or the browser address is not evidence.
            author = await visible(page.locator('a[href="/'+identity+'/"]'))
            present = bool(owned and author and await visible(page.locator('video')) and
                           urlsplit(page.url).path.rstrip('/').endswith('/'+receipt['id']))
        if expected:
            actual = normalize(await page.locator("body").inner_text())
            present = present and normalize(expected) in actual
    if platform == 'douyin' and receipt.get('expected_text'):
        present = present and receipt['expected_text'] in found.get(str(receipt['id']), {}).get('text', '')
    if platform == 'douyin' and present and receipt.get('schedule') and datetime.now(timezone.utc) < datetime.fromisoformat(receipt['schedule']['at']):
        # Before its time, only the work showing that time proves the schedule took, rather than it having gone out already.
        present = receipt['schedule']['stamp'] in found.get(str(receipt['id']), {}).get('text', '')
    return {**receipt, "url": url, "state": "SUBMISSION_VERIFIED" if present else "SUBMITTED_UNVERIFIED",
            'phase':'complete' if present else 'readback_pending', 'error':None if present else receipt.get('error'),
            "verification_source": "independent_browser_dom", "verified_at": now(),
            "note": "Work and expected text verified on the signed-in page; cover pixels and anonymous playback are separate." if present else "Expected work/text not yet verified; do not upload again."}


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
    tx = Transaction(platform, identity, payload["plan"], resume=action == 'resume')
    if tx.receipt['final_submit_attempted']:
        result=await verify(page, platform, identity, tx.receipt)
        return tx.save(**await complete_pending_chapters(page,platform,identity,result))
    if action == 'resume' and tx.receipt['media_selected'] and platform != 'x':
        raise VpubError('inspect_existing_draft', 'An upload already started. Resume requires inspecting that existing draft; uploading again is prohibited.')
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
                       hint="Inspect the existing draft/work. Progress is saved; no automatic upload or submit retry.")


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
        return {**receipt,'chapters_state':'INCOMPLETE','chapters_error':exc.code if isinstance(exc,VpubError) else type(exc).__name__,
                'next':'The video exists. Fix/retry only the chapter step; do not upload the video again.'}

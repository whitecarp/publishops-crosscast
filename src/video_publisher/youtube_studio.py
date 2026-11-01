"""YouTube through its own Studio and watch pages, in CrossCast's dedicated browser. No official API."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from .browser_backends import normalize, studio_visibility, work_url
from .browser_runtime import risk_check, unique_visible, visible
from .core import VpubError, atomic_json, file_sha, home, now, read_json, stop_check
from .operations_api import metric

STUDIO = "https://studio.youtube.com"
PLAYLIST_ROWS = """rows => rows.map(row => ({href: row.querySelector('#playlist-title-link')?.getAttribute('href') || '', title: row.querySelector('#playlist-title-link')?.innerText || '',
    visibility: row.querySelector('.visibility-label')?.innerText || '', videos: row.querySelector('.videos-count-cell')?.innerText || '', updated: row.querySelector('.last-time-updated-cell')?.innerText || ''}))"""
VIDEO_LINKS = "rows => rows.map(row => ({href: row.querySelector('a#video-title')?.getAttribute('href') || '', title: row.querySelector('a#video-title')?.innerText || ''}))"
SAVE_BUTTON = 'ytd-watch-metadata button[aria-label*="Save"], ytd-watch-metadata button[aria-label*="保存"], ytd-watch-metadata button[aria-label*="儲存"]'
SAVE_ROWS = "yt-sheet-view-model toggleable-list-item-view-model"
SAVE_TITLES = "rows => rows.map(row => (row.innerText || '').split('\\n').map(line => line.trim()).filter(Boolean)[0] || '')"
CREATE_BUTTON = 'button[aria-label="Create"], button[aria-label="创建"], button[aria-label="建立"]'
CONFIRM_BUTTON = "ytcp-confirmation-dialog #confirm-button, ytcp-dialog #confirm-button, tp-yt-paper-dialog #confirm-button"
PRIVACY = {"private": "PRIVATE", "unlisted": "UNLISTED", "public": "PUBLIC"}
EDIT_ITEM = re.compile(r"^\s*(Edit|修改|編輯)\s*$")
DELETE_ITEM = re.compile(r"^\s*(Delete|删除|刪除)\s*$")
# A Studio list is read to its end, within reason; a longer one is reported as not complete.
TABLE_PAGES = 20
# After a change the page is left alone until its own requests have stopped for this many half-seconds, up to the limit.
PAGE_REQUESTS = "/youtubei/v1/"
QUIET_TICKS = 10
MOST_TICKS = 90
METRICS = {"views": {"Views", "观看次数", "觀看次數"},
           "watch_time_hours": {"Watch time (hours)", "观看时长（小时）", "觀看時長（小時）", "觀看時間 (小時)"},
           "subscribers": {"Subscribers", "订阅人数", "訂閱人數"}}
COMMENT_ROWS = """threads => threads.map(thread => [...thread.querySelectorAll('ytcp-comment[comment-id]')].map(row => ({
    id: row.getAttribute('comment-id'),
    text: row.querySelector('#content-text')?.innerText || '',
    author: row.querySelector('.author-text')?.innerText || '',
    href: row.querySelector('a#name')?.getAttribute('href') || '',
    time: row.querySelector('.published-time-text')?.innerText || ''})))"""
WORK_ROWS = """rows => rows.map(row => {
    const link = row.querySelector('a#video-title');
    const cell = name => row.querySelector('.tablecell-' + name)?.innerText || '';
    return {href: row.querySelector('a[href^="/video/"][href$="/edit"]')?.getAttribute('href') || '', title: link?.innerText || '',
            visibility: cell('visibility'), date: cell('date'), views: cell('views'), comments: cell('comments'), likes: cell('ratings') || cell('likes')};
})"""


def count_of(text):
    from .operations_browser import number
    return number(str(text or "").strip())


def handle_of(href):
    """The @handle in an author link, in one spelling, or None."""
    path = unquote(urlsplit(href or "").path).strip("/")
    return path[1:].casefold() if path.startswith("@") and len(path) > 1 else None


def comment_items(threads, own):
    """One row per comment. A root knows whether the channel has replied among the replies Studio showed."""
    items = []
    for rows in threads:
        if not rows:
            continue
        handles = [handle_of(row["href"]) for row in rows]
        root = rows[0]["id"]
        for index, row in enumerate(rows):
            items.append({"id": row["id"], "parent_id": None if index == 0 else root, "text": row["text"], "author": row["author"].strip(),
                          "author_id": "@" + handles[index] if handles[index] else None, "is_owner": bool(own and handles[index] == own),
                          "published_display": row["time"].strip(), "reply_count": len(rows) - 1 if index == 0 else None,
                          "owner_replied": any(handle == own for handle in handles[1:]) if own and index == 0 else None})
    return items


def work_items(rows, identity):
    items = {}
    for row in rows:
        found = re.fullmatch(r"/video/([\w-]{11})/edit", row.get("href") or "")
        if found:
            items[found[1]] = {"id": found[1], "url": work_url("youtube", identity, found[1]), "title": normalize(row.get("title")), "text": normalize(row.get("title")),
                               "visibility": studio_visibility(row.get("visibility")), "published_display": normalize(row.get("date")),
                               "views": count_of(row.get("views")), "comments": count_of(row.get("comments")), "likes": count_of(row.get("likes"))}
    return items


def studio_metrics(blocks):
    """Studio's key figures by their label, in its English or Chinese interface. A dash stays unavailable."""
    metrics = {}
    for name, labels in METRICS.items():
        raw = next((block["total"].strip() for block in blocks if block["label"].strip() in labels), None)
        value = count_of(raw)
        metrics[name] = {**metric(value, "YouTube Studio, lifetime", "hours" if name == "watch_time_hours" else "count"), "display": raw,
                         "approximate": bool(raw and re.search("[KkMm万亿]", raw))}
    return metrics


async def appears(page, target, seconds):
    for _ in range(seconds * 2):
        await risk_check(page)
        if await target.count():
            return True
        await page.wait_for_timeout(500)
    return False


async def act_and_settle(page, act):
    """Do something, then stay until the page has finished its own requests. Whether it worked is for the read-back to say.

    On a real channel Studio greyed Save out at once and sent the change about 6 seconds later, after its own checks.
    A delete was answered at once, then sent again after those checks. Leaving at either point cancelled the change.
    """
    busy = {"open": 0, "seen": 0}

    def sent(request):
        if PAGE_REQUESTS in request.url:
            busy["open"] += 1
            busy["seen"] += 1

    def ended(request):
        if PAGE_REQUESTS in request.url:
            busy["open"] = max(0, busy["open"] - 1)

    listeners = (("request", sent), ("requestfinished", ended), ("requestfailed", ended))
    for event, listener in listeners:
        page.on(event, listener)
    try:
        await act()
        quiet, last = 0, busy["seen"]
        for _ in range(MOST_TICKS):
            await page.wait_for_timeout(500)
            quiet = quiet + 1 if not busy["open"] and busy["seen"] == last else 0
            last = busy["seen"]
            if quiet >= QUIET_TICKS:
                return
    finally:
        for event, listener in listeners:
            page.remove_listener(event, listener)


async def own_handle(page, identity):
    """Comments name their authors by handle, so the channel's own handle is what marks its comments."""
    path = home() / "accounts/default/youtube-handle.json"
    saved = read_json(path) if path.exists() else None
    if isinstance(saved, dict) and saved.get("identity") == identity:
        return saved.get("handle")
    await page.goto(f"https://www.youtube.com/channel/{identity}", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)
    await risk_check(page)
    found = re.search(r'"vanityChannelUrl":"https?://www\.youtube\.com/@([^"]+)"', await page.content())
    handle = unquote(found[1]).casefold() if found else None
    if handle:
        atomic_json(path, {"identity": identity, "handle": handle})
    return handle


async def works(page, identity):
    """The newest videos on Studio's first page of the channel's content."""
    await page.goto(f"{STUDIO}/channel/{identity}/videos/upload", wait_until="domcontentloaded", timeout=60000)
    rows = page.locator("ytcp-video-row")
    await appears(page, rows, 20)
    return work_items(await rows.evaluate_all(WORK_ROWS), identity)


async def stats(page, identity, work):
    await page.goto(f"{STUDIO}/video/{work}/analytics/tab-overview/period-lifetime", wait_until="domcontentloaded", timeout=60000)
    blocks = page.locator("yta-key-metric-block")
    if not await appears(page, blocks, 30):
        raise VpubError("owner_unverified", "Studio did not open this video for the signed-in channel.", "Use one of this channel's own video IDs.")
    await page.wait_for_timeout(2000)
    rows = [{"label": await block.locator("#metric-label").first.inner_text(), "total": await block.locator("#metric-total").first.inner_text()} for block in await blocks.all()]
    metrics = studio_metrics(rows)
    row = (await works(page, identity)).get(work, {})
    # These two appear only when the video is on the content list's first page.
    metrics["comments"] = metric(row.get("comments"), "YouTube Studio content list")
    metrics["likes"] = metric(row.get("likes"), "YouTube Studio content list")
    return {"state": "OBSERVED", "identity": identity, "url": work_url("youtube", identity, work), "title": row.get("title"), "source": "youtube_studio_analytics",
            "ownership_evidence": "studio_analytics_opened", "metrics": metrics}


async def read_comments(page, identity, work, limit):
    own = await own_handle(page, identity)
    await page.goto(f"{STUDIO}/video/{work}/comments", wait_until="domcontentloaded", timeout=60000)
    threads = page.locator("ytcp-comment-thread")
    await appears(page, threads, 6)
    await risk_check(page)
    if f"/video/{work}/" not in page.url:
        raise VpubError("owner_unverified", "Studio did not open this video for the signed-in channel.", "Use one of this channel's own video IDs.")
    # Studio lists only unanswered comments until its filter is removed, which would hide every thread already answered.
    for remove in await page.locator("ytcp-chip #delete-icon").all():
        if await remove.is_visible():
            await remove.click(timeout=10000)
            await page.wait_for_timeout(1500)
    await appears(page, threads, 6)
    # Studio loads more comments as the page scrolls, and replies when their button is opened. None of this changes anything.
    stalled, seen = 0, await threads.count()
    while seen < limit and stalled < 2:
        await page.mouse.wheel(0, 2400)
        await page.wait_for_timeout(1000)
        now_seen = await threads.count()
        stalled = stalled + 1 if now_seen == seen else 0
        seen = now_seen
    # Studio usually shows replies already, and the same button then hides them. It is disabled on a thread with none.
    for thread in (await threads.all())[:limit]:
        button = thread.locator("#show-replies-button").first
        if (await thread.locator("ytcp-comment[comment-id]").count() == 1 and await button.count() and await button.is_visible()
                and not await button.evaluate("e => e.hasAttribute('disabled')")):
            await button.click(timeout=10000)
            await page.wait_for_timeout(800)
    items = comment_items(await threads.evaluate_all(COMMENT_ROWS), own)
    return {"items": items[:limit], "next_cursor": None, "complete": False, "work_id": work,
            "scope": "Published comments Studio loaded on this page; not the full history. Replies are those Studio showed."}


def find_reply(items, text, before, parent):
    """A new comment by this channel, with these words, under this comment."""
    return [c for c in items if c["is_owner"] and c["id"] not in before and normalize(c["text"]) == normalize(text) and c["parent_id"] == parent]


async def operations(page, identity, payload):
    action, work = payload["operation"], payload["work_id"]
    if action == "stats":
        return await stats(page, identity, work)
    if action in {"comments", "replies"}:
        if payload.get("cursor"):
            raise VpubError("cursor_unsupported", "Studio comments expose the loaded page only; no fabricated page cursor.")
        if payload.get("moderation_status"):
            raise VpubError("unsupported_operation", "The held-for-review queue needs the paid API add-on.")
        result = await read_comments(page, identity, work, payload.get("limit", 20))
        if action == "replies":
            result["items"] = [c for c in result["items"] if payload.get("comment_id") in (c["id"], c["parent_id"])]
        return {"state": "OBSERVED", "identity": identity, "url": work_url("youtube", identity, work), "source": "youtube_studio_comments", **result}
    if action not in {"reply", "verify_reply"}:
        raise VpubError("unsupported_operation", "This YouTube operation needs the paid API add-on.")
    request = payload["reply_request"]
    target = request["comment"]
    if identity != request["expected_identity"]:
        raise VpubError("wrong_account", "Account changed since comment collection.")
    parent = target.get("parent_id") or target["id"]
    if action == "verify_reply":
        # Read only; no reply control is touched.
        saved = payload.get("existing_reply", {})
        items = (await read_comments(page, identity, work, 100))["items"]
        found = find_reply(items, saved.get("submitted_text") or request["text"], set(saved.get("before_reply_ids", [target["id"]])), parent)
        return {"state": "REPLY_VERIFIED" if len(found) == 1 else "REPLY_UNVERIFIED", "reply_id": found[0]["id"] if len(found) == 1 else saved.get("reply_id"), "platform_writes": 0,
                "verification_source": "studio_comment_list" if len(found) == 1 else None, "hint": None if len(found) == 1 else "No confirmed reply read-back; do not resend."}
    current = (await read_comments(page, identity, work, 100))["items"]
    if len([c for c in current if c["id"] == target["id"] and normalize(c["text"]) == normalize(target["text"])]) != 1:
        raise VpubError("comment_changed", "The exact comment is not visible or changed; collect it again.")
    row = page.locator(f'ytcp-comment[comment-id="{target["id"]}"]').first
    await row.locator("#reply-button button").first.click(timeout=15000)
    editors = page.locator("ytcp-commentbox textarea#textarea")
    for _ in range(20):
        if await visible(editors):
            break
        await page.wait_for_timeout(250)
    editor = await unique_visible(editors, "reply editor")
    await editor.fill(request["text"])
    if await editor.input_value() != request["text"]:
        raise VpubError("metadata_mismatch", "The reply box did not keep the intended text; nothing was sent.")
    button = await unique_visible(page.locator("ytcp-commentbox #submit-button button"), "reply submit")
    await risk_check(page)
    stop_check("youtube")
    if not await button.is_enabled():
        raise VpubError("reply_not_ready", "YouTube has not enabled reply submission.")
    path = Path(payload["reply_receipt_path"])
    saved = read_json(path)
    saved.update(before_reply_ids=[c["id"] for c in current], submitted_text=request["text"], state="REPLY_SUBMITTING")
    atomic_json(path, saved)
    await act_and_settle(page, lambda: button.click(timeout=20000))
    saved["state"] = "REPLY_UNVERIFIED"
    atomic_json(path, saved)
    await page.wait_for_timeout(3000)
    await risk_check(page)
    found = find_reply((await read_comments(page, identity, work, 100))["items"], request["text"], set(saved["before_reply_ids"]), parent)
    if len(found) == 1:
        return {"state": "REPLY_VERIFIED", "platform_writes": 1, "reply_id": found[0]["id"], "verification_source": "studio_comment_list"}
    return {"state": "REPLY_UNVERIFIED", "platform_writes": 1, "reply_id": None, "hint": "Submitted once and not yet read back. Use comments verify-reply; no automatic resend."}


async def pressed(button):
    return {"true": True, "false": False}.get(await button.get_attribute("aria-pressed") if button else None)


async def open_watch_page(page, work):
    await page.goto(f"https://www.youtube.com/watch?v={work}", wait_until="domcontentloaded", timeout=60000)
    # The player keeps a hidden copy; the one under the video is the first with a pressed state in the row of actions.
    buttons = page.locator("ytd-watch-metadata ytd-menu-renderer button[aria-pressed], like-button-view-model button, ytd-segmented-like-dislike-button-renderer button")
    await appears(page, buttons, 25)
    if urlsplit(page.url).path != "/watch":
        raise VpubError("wrong_work", "The page left the exact requested video.")
    # The button shows before its state does.
    for _ in range(20):
        button = await visible(buttons)
        if await pressed(button) is not None:
            break
        await page.wait_for_timeout(500)
    return button


async def save_row(page, title):
    """YouTube's own Save window on the watch page. It lists the channel's playlists and marks the ones holding this video."""
    button = await visible(page.locator(SAVE_BUTTON))
    if button is None:
        raise VpubError("layout_changed", "YouTube's Save button is not on this page; nothing changed.")
    await button.click(timeout=15000)
    rows = page.locator(SAVE_ROWS)
    if not await appears(page, rows, 15):
        raise VpubError("layout_changed", "YouTube's Save window did not open; nothing changed.")
    found = [number for number, line in enumerate(await rows.evaluate_all(SAVE_TITLES)) if normalize(line) == normalize(title)]
    if len(found) != 1:
        raise VpubError("playlist_not_found" if not found else "playlist_ambiguous", f"YouTube's Save window shows {len(found)} playlists with this title; nothing changed.",
                        "Give playlists different titles.")
    return rows.nth(found[0]).locator("button").first


async def engagement(page, identity, payload):
    """Likes and saves on the video's own watch page. A save is the video's place in one chosen playlist."""
    collection = payload.get("collection")
    title = await playlist_title(page, identity, collection) if collection else None
    button = await open_watch_page(page, payload["work_id"])
    liked = await pressed(button)
    row = await save_row(page, title) if collection else None
    saved = await pressed(row) if row else None
    observed = {"identity": identity, "liked": liked, "saved": saved, "collection": collection, "source": "official_page_controls",
                "save_semantics": "membership in the selected playlist" if collection else None}
    if payload["operation"] == "status":
        return observed
    if payload["operation"] != "set":
        raise VpubError("invalid_action", "Unsupported engagement action.")
    current, control = (saved, row) if payload["kind"] == "save" else (liked, button)
    if current is None:
        raise VpubError("state_unavailable", "The button's state is unavailable; nothing changed.")
    if current is payload["desired"]:
        return {"state": "NO_CHANGE", "platform_writes": 0}
    await risk_check(page)
    stop_check("youtube")
    from .engagement import submitting
    submitting(payload["receipt_path"])
    await act_and_settle(page, lambda: control.click(timeout=15000))
    await risk_check(page)
    return {"state": "ACTION_UNVERIFIED", "platform_writes": 1}


def playlist_items(rows):
    items = {}
    for row in rows:
        found = re.fullmatch(r"/playlist/([\w-]+)/edit", row.get("href") or "")
        if found:
            items[found[1]] = {"id": found[1], "title": normalize(row.get("title")), "visibility": studio_visibility(row.get("visibility")),
                               "videos": count_of(row.get("videos")), "updated_display": normalize(row.get("updated"))}
    return items


def playlist_videos(rows):
    items = {}
    for row in rows:
        found = re.fullmatch(r"/video/([\w-]{11})/edit", row.get("href") or "")
        if found:
            items[found[1]] = {"id": found[1], "title": normalize(row.get("title"))}
    return items


async def studio_table(page, rows, script, parse):
    """Every page of a Studio list. It is complete only when its own Next button says there is no more."""
    items = {}
    for _ in range(TABLE_PAGES):
        items.update(parse(await rows.evaluate_all(script)))
        after = await visible(page.locator("#navigate-after"))
        if after is None:
            return list(items.values()), bool(items)
        if await after.get_attribute("aria-disabled") == "true":
            return list(items.values()), True
        first = await rows.first.inner_text()
        await after.click(timeout=15000)
        for _ in range(20):
            await page.wait_for_timeout(500)
            if not await rows.count() or await rows.first.inner_text() != first:
                break
    return list(items.values()), False


async def open_playlist_list(page, identity):
    await page.goto(f"{STUDIO}/channel/{identity}/content/playlists", wait_until="domcontentloaded", timeout=60000)
    rows = page.locator("ytcp-playlist-row")
    await appears(page, rows, 12)
    await risk_check(page)
    if f"/channel/{identity}/" not in page.url:
        raise VpubError("wrong_account", "Studio opened another channel's page; nothing changed.")
    return rows


async def read_playlists(page, identity):
    return await studio_table(page, await open_playlist_list(page, identity), PLAYLIST_ROWS, playlist_items)


async def playlist_title(page, identity, playlist):
    """YouTube's Save window names playlists only by title, so an ID becomes the title Studio shows for it."""
    found = [row for row in (await read_playlists(page, identity))[0] if row["id"] == playlist]
    if len(found) != 1:
        raise VpubError("collection_not_visible", "This channel has no playlist with that ID in Studio; nothing changed.")
    return found[0]["title"]


async def studio_confirm(page):
    """Studio may ask once more before deleting. Confirms it if it does."""
    for _ in range(12):
        button = await visible(page.locator(CONFIRM_BUTTON))
        if button:
            await button.click(timeout=10000)
            return
        await page.wait_for_timeout(500)


async def wait_ready(page, button, tries=20):
    from .browser_forms import yt_button_ready
    for _ in range(tries):
        if await yt_button_ready(button):
            return True
        await page.wait_for_timeout(500)
    return False


async def set_privacy(page, trigger, privacy):
    await trigger.click(timeout=15000)
    item = page.locator(f'tp-yt-paper-item[test-id="{PRIVACY[privacy]}"]')
    await appears(page, item, 10)
    await (await unique_visible(item, "playlist visibility")).click(timeout=15000)
    await page.wait_for_timeout(400)
    if studio_visibility(await trigger.inner_text()) != privacy:
        raise VpubError("playlist_unverified", "Studio did not keep the chosen visibility; nothing was saved.")


async def playlist_fields(page, playlist):
    await page.goto(f"{STUDIO}/playlist/{playlist}/edit", wait_until="domcontentloaded", timeout=60000)
    title = page.locator("#title-textarea #textbox").first
    try:
        await title.wait_for(state="visible", timeout=30000)
    except Exception:
        await risk_check(page)
        raise VpubError("owner_unverified", "Studio did not open this playlist for the signed-in channel; nothing changed.") from None
    await risk_check(page)
    for _ in range(20):
        if normalize(await title.inner_text()):
            break
        await page.wait_for_timeout(500)
    return title, page.locator("#description-textarea #textbox").first


def visibility_trigger(page):
    return page.locator("ytcp-text-menu#visibility-menu").locator("xpath=preceding-sibling::ytcp-text-dropdown-trigger[1]")


async def rename_playlist(page, identity, payload, before_submit):
    playlist, description, privacy = payload["collection_id"], payload.get("description"), payload.get("privacy")
    title, notes = await playlist_fields(page, playlist)
    await title.fill(payload["title"])
    if description is not None:
        await notes.fill(description)
    if privacy:
        await set_privacy(page, visibility_trigger(page), privacy)
    button = page.locator("ytcp-button#save").first
    if not await wait_ready(page, button):
        raise VpubError("edit_not_ready", "Studio did not enable Save; nothing was saved.")
    await risk_check(page)
    before_submit(payload, identity)
    await act_and_settle(page, lambda: button.click(timeout=20000))
    title, notes = await playlist_fields(page, playlist)
    kept = normalize(await title.inner_text()) == normalize(payload["title"]) and (description is None or normalize(await notes.inner_text()) == normalize(description))
    if privacy and studio_visibility(await visibility_trigger(page).inner_text()) != privacy:
        kept = False
    if not kept:
        raise VpubError("playlist_unverified", "The saved playlist does not show what was asked for; check it before trying again.")
    return {"identity": identity, "submitted": True, "collection_id": playlist}


async def create_playlist(page, identity, payload, before_submit):
    from .collections import receipt_path
    await open_playlist_list(page, identity)
    await (await unique_visible(page.locator(CREATE_BUTTON), "Create button")).click(timeout=15000)
    item = page.locator('tp-yt-paper-item[test-id="new-playlist"]')
    await appears(page, item, 10)
    await (await unique_visible(item, "New playlist")).click(timeout=15000)
    dialog = page.locator("ytcp-playlist-creation-dialog").first
    boxes = dialog.locator("#textbox")
    if not await appears(page, boxes, 15):
        raise VpubError("layout_changed", "Studio's new-playlist window did not open; nothing was created.")
    await boxes.nth(0).fill(payload["title"])
    if payload.get("description"):
        await boxes.nth(1).fill(payload["description"])
    await set_privacy(page, dialog.locator("ytcp-dropdown-trigger").first, payload.get("privacy") or "private")
    button = dialog.locator("ytcp-button#create-button").first
    if not await wait_ready(page, button):
        raise VpubError("edit_not_ready", "Studio did not enable Create; nothing was created.")
    await risk_check(page)
    before_submit(payload, identity)
    await button.click(timeout=20000)
    await button.wait_for(state="hidden", timeout=30000)
    await risk_check(page)
    # The new ID shows only in the list, as the one row that was not there before, and the list can take a moment to catch up.
    known = set(read_json(receipt_path(payload["receipt_key"]))["before_ids"])
    new = []
    for _ in range(4):
        new = [row["id"] for row in (await read_playlists(page, identity))[0] if row["id"] not in known and row["title"] == normalize(payload["title"])]
        if new:
            break
        await page.wait_for_timeout(3000)
    return {"identity": identity, "submitted": True, "collection_id": new[0] if len(new) == 1 else None}


async def delete_playlist(page, identity, payload, before_submit):
    link = f'a#playlist-title-link[href="/playlist/{payload["collection_id"]}/edit"]'
    rows = await open_playlist_list(page, identity)
    row = rows.filter(has=page.locator(link))
    for _ in range(TABLE_PAGES):
        if await row.count():
            break
        after = await visible(page.locator("#navigate-after"))
        if after is None or await after.get_attribute("aria-disabled") == "true":
            raise VpubError("collection_not_visible", "This playlist is not in Studio's list; nothing changed.")
        await after.click(timeout=15000)
        await page.wait_for_timeout(1500)
    title = normalize(await row.first.locator("#playlist-title-link").inner_text())
    await row.first.hover()
    await row.first.locator("#overflow-menu-button").first.click(timeout=15000)
    item = page.locator('tp-yt-paper-item[test-id="DELETE_FOREVER"]')
    await appears(page, item, 10)
    await (await unique_visible(item, "Delete playlist")).click(timeout=15000)
    # Studio asks once more and names the playlist, so that name is checked before anything is deleted.
    # The window's outer element never counts as visible, so its button is what shows it has opened.
    buttons = page.locator("ytcp-confirmation-dialog#playlist-delete-dialog ytcp-button#confirm-button")
    confirm = None
    for _ in range(20):
        confirm = await visible(buttons)
        if confirm:
            break
        await page.wait_for_timeout(500)
    if confirm is None:
        await risk_check(page)
        raise VpubError("layout_changed", "Studio did not ask to confirm the delete; nothing was deleted.")
    if not title or title not in normalize(await confirm.locator("xpath=ancestor::ytcp-confirmation-dialog[1]").inner_text()):
        raise VpubError("wrong_collection", "Studio's confirmation names another playlist; nothing was deleted.")
    await risk_check(page)
    before_submit(payload, identity)
    await act_and_settle(page, lambda: confirm.click(timeout=15000))
    await risk_check(page)
    return {"identity": identity, "submitted": True, "collection_id": payload["collection_id"]}


async def playlists(page, identity, payload):
    from .collections import before_submit
    operation, playlist, work = payload["operation"], payload.get("collection_id"), payload.get("work_id")
    if operation == "list":
        items, complete = await read_playlists(page, identity)
        return {"platform": "youtube", "identity": identity, "items": items, "complete": complete, "scope": "owned_playlists", "coverage": "studio_playlist_list",
                "platform_writes": 0, "observed_at": now()}
    if operation == "items" and not work:
        await page.goto(f"{STUDIO}/playlist/{playlist}/videos", wait_until="domcontentloaded", timeout=60000)
        rows = page.locator("ytcp-video-row")
        await appears(page, rows, 12)
        await risk_check(page)
        if f"/playlist/{playlist}/" not in page.url:
            raise VpubError("owner_unverified", "Studio did not open this playlist for the signed-in channel.")
        items, complete = await studio_table(page, rows, VIDEO_LINKS, playlist_videos)
        return {"identity": identity, "collection_id": playlist, "items": items, "complete": complete, "coverage": "studio_playlist_videos", "platform_writes": 0, "observed_at": now()}
    if operation in {"items", "add", "remove"}:
        title = await playlist_title(page, identity, playlist)
        await open_watch_page(page, work)
        row = await save_row(page, title)
        inside = await pressed(row)
        if inside is None:
            raise VpubError("state_unavailable", "YouTube's Save window does not show this playlist's state; nothing changed.")
        if operation == "items":
            return {"identity": identity, "collection_id": playlist, "items": [{"id": work}] if inside else [], "complete": True, "scope": "one_video",
                    "coverage": "official_save_window", "platform_writes": 0, "observed_at": now()}
        if payload["before_members"] != ([work] if inside else []):
            raise VpubError("collection_changed", "This video moved since the last check; nothing changed.")
        await risk_check(page)
        await row.click(trial=True, timeout=10000)
        before_submit(payload, identity)
        await act_and_settle(page, lambda: row.click(timeout=15000))
        await risk_check(page)
        return {"identity": identity, "submitted": True, "collection_id": playlist}
    if operation == "create":
        return await create_playlist(page, identity, payload, before_submit)
    if operation == "rename":
        return await rename_playlist(page, identity, payload, before_submit)
    if operation == "delete":
        return await delete_playlist(page, identity, payload, before_submit)
    raise VpubError("unsupported_operation", "This playlist action is not in the free YouTube route.")


async def comment_menu_item(page, row, words):
    await row.hover()
    await row.locator("#action-menu-button").first.click(timeout=10000)
    items = page.locator("ytcp-menu-service-item-renderer").filter(has_text=words)
    await appears(page, items, 8)
    return await unique_visible(items, "comment menu item")


async def comment_action(page, identity, payload):
    """Likes on any comment, and editing or deleting the channel's own, on Studio's comment page."""
    request = payload["request"]
    target, kind = request["comment"], request.get("kind")
    if identity != request["identity"]:
        raise VpubError("wrong_account", "Account changed since comment collection.")
    current = (await read_comments(page, identity, request["work_id"], 100))["items"]
    found = [c for c in current if c["id"] == target["id"]]
    if len(found) != 1:
        # Absence means something only when the whole short list was read.
        return {"identity": identity, "text": None, "liked": None, "deleted": True if len(current) < 100 else None, "source": "studio_comment_list"}
    row = page.locator(f'ytcp-comment[comment-id="{target["id"]}"]').first
    like = row.locator("ytcp-comment-toggle-button#like-button").first
    liked = await like.evaluate("e => e.isToggled", isolated_context=False) if await like.count() else None
    observed = {"identity": identity, "text": found[0]["text"], "liked": liked if type(liked) is bool else None, "deleted": False, "source": "studio_comment_list"}
    if payload["operation"] == "status":
        return observed
    if payload["operation"] != "set":
        raise VpubError("unsupported_operation", "Choose status or set.")
    from .comment_actions import submitting

    def sent():
        saved = read_json(payload["receipt_path"])
        saved.update(state="COMMENT_UNVERIFIED", platform_writes=1)
        atomic_json(payload["receipt_path"], saved)
        return {"state": "COMMENT_UNVERIFIED", "platform_writes": 1}

    await risk_check(page)
    stop_check("youtube")
    if kind == "like":
        if observed["liked"] is None:
            raise VpubError("state_unavailable", "The comment's Like state is unavailable; nothing changed.")
        if observed["liked"] is request["desired"]:
            return {"state": "NO_CHANGE", "platform_writes": 0}
        button = like.locator("ytcp-icon-button").first
        await button.click(trial=True, timeout=10000)
        submitting(payload["receipt_path"])
        await act_and_settle(page, lambda: button.click(timeout=15000))
        return sent()
    if kind == "edit":
        item = await comment_menu_item(page, row, EDIT_ITEM)
        await item.click(timeout=10000)
        editors = page.locator("ytcp-commentbox textarea#textarea")
        await appears(page, editors, 8)
        editor = await unique_visible(editors, "comment editor")
        await editor.fill(request["desired"])
        if await editor.input_value() != request["desired"]:
            raise VpubError("metadata_mismatch", "The comment box did not keep the intended text; nothing was sent.")
        button = await unique_visible(page.locator("ytcp-commentbox #submit-button button"), "comment save")
        if not await button.is_enabled():
            raise VpubError("reply_not_ready", "YouTube has not enabled saving this comment.")
        submitting(payload["receipt_path"])
        await act_and_settle(page, lambda: button.click(timeout=15000))
        return sent()
    if kind == "delete":
        item = await comment_menu_item(page, row, DELETE_ITEM)
        submitting(payload["receipt_path"])

        async def remove():
            await item.click(timeout=10000)
            await studio_confirm(page)

        await act_and_settle(page, remove)
        return sent()
    raise VpubError("paid_feature_required", "This comment action needs the paid CrossCast add-on for now.", "Comment likes, and editing or deleting the channel's own comments, are free.")


async def edit_fields(page, url):
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    title = page.locator("#title-textarea #textbox").first
    try:
        await title.wait_for(state="visible", timeout=30000)
    except Exception:
        await risk_check(page)
        raise VpubError("owner_unverified", "Studio did not open this video for the signed-in channel; nothing changed.") from None
    await risk_check(page)
    for _ in range(20):
        if normalize(await title.inner_text()):
            break
        await page.wait_for_timeout(500)
    return title, page.locator("#description-textarea #textbox").first


async def edit(page, payload, save, submit):
    """Title, description or thumbnail on the video's own edit page. Saved once, then read back from a fresh load."""
    from .browser_forms import yt_button_ready
    patch, work = payload["patch"], payload["receipt"]["id"]
    url = f"{STUDIO}/video/{work}/edit"
    title, description = await edit_fields(page, url)
    button = page.locator("ytcp-button#save").first
    evidence = {"url": url, "verification_source": "studio_edit_page"}
    if "cover" in patch:
        if payload.get("verify_only"):
            return save(state="EDIT_MISMATCH", platform_writes=0, reason="Studio does not expose the thumbnail's pixels for comparison; nothing changed.", **evidence)
        if file_sha(patch["cover"]) != patch["cover_sha256"]:
            raise VpubError("source_changed", "The cover changed before upload; nothing changed.")
        inputs = page.locator("input#file-loader")
        if not await inputs.count():
            raise VpubError("cover_unavailable", "YouTube offers no custom thumbnail on this channel yet; nothing changed.", "Custom thumbnails need a verified channel.")
        await inputs.first.set_input_files(patch["cover"], timeout=60000)
        changes = None
    else:
        before = {"title": normalize(await title.inner_text()), "description": normalize(await description.inner_text())}
        wanted = {"title": normalize(patch["title"]), "description": normalize(patch["description"])}
        if before == wanted:
            return save(state="EDIT_VERIFIED", unchanged=True, platform_writes=0, **evidence)
        if payload.get("verify_only"):
            return save(state="EDIT_MISMATCH", platform_writes=0, reason="The saved text differs; no write attempted.", **evidence)
        await title.fill(patch["title"])
        await description.fill(patch["description"])
        if {"title": normalize(await title.inner_text()), "description": normalize(await description.inner_text())} != wanted:
            raise VpubError("metadata_mismatch", "Studio's form did not keep the intended text; nothing was saved.")
        changes = wanted
    for _ in range(60):
        if await yt_button_ready(button):
            break
        await page.wait_for_timeout(500)
    else:
        raise VpubError("edit_not_ready", "Studio did not enable Save; nothing was saved.")
    save(state="EDIT_PREPARED")
    await act_and_settle(page, lambda: submit(button))
    if changes is None:
        return save(state="EDIT_VERIFIED", platform_writes=1, cover={"state": "UI_ACCEPTED", "remote_pixels_verified": False}, **evidence)
    title, description = await edit_fields(page, url)
    after = {"title": normalize(await title.inner_text()), "description": normalize(await description.inner_text())}
    return save(state="EDIT_VERIFIED" if after == changes else "EDIT_SUBMITTED_UNVERIFIED", platform_writes=1, **evidence)

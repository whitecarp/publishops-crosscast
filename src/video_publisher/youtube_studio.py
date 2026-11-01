"""YouTube through its own Studio and watch pages, in CrossCast's dedicated browser. No official API."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from .browser_backends import normalize, studio_visibility, work_url
from .browser_runtime import risk_check, unique_visible, visible
from .core import VpubError, atomic_json, file_sha, home, read_json, stop_check
from .operations_api import metric

STUDIO = "https://studio.youtube.com"
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
    await button.click(timeout=20000)
    saved["state"] = "REPLY_UNVERIFIED"
    atomic_json(path, saved)
    await page.wait_for_timeout(3000)
    await risk_check(page)
    found = find_reply((await read_comments(page, identity, work, 100))["items"], request["text"], set(saved["before_reply_ids"]), parent)
    if len(found) == 1:
        return {"state": "REPLY_VERIFIED", "platform_writes": 1, "reply_id": found[0]["id"], "verification_source": "studio_comment_list"}
    return {"state": "REPLY_UNVERIFIED", "platform_writes": 1, "reply_id": None, "hint": "Submitted once and not yet read back. Use comments verify-reply; no automatic resend."}


async def engagement(page, identity, payload):
    """Likes on the video's own watch page. Saving to a playlist stays outside this route for now."""
    if payload.get("collection") or payload.get("kind") == "save":
        raise VpubError("unsupported_operation", "Saving to a playlist is not in the free YouTube route yet; nothing changed.")
    work = payload["work_id"]
    await page.goto(f"https://www.youtube.com/watch?v={work}", wait_until="domcontentloaded", timeout=60000)
    buttons = page.locator("like-button-view-model button, ytd-segmented-like-dislike-button-renderer button")
    await appears(page, buttons, 25)
    if urlsplit(page.url).path != "/watch":
        raise VpubError("wrong_work", "The page left the exact requested video.")
    button = await visible(buttons)
    pressed = await button.get_attribute("aria-pressed") if button else None
    liked = {"true": True, "false": False}.get(pressed)
    observed = {"identity": identity, "liked": liked, "saved": None, "collection": None, "source": "official_page_controls"}
    if payload["operation"] == "status":
        return observed
    if payload["operation"] != "set":
        raise VpubError("invalid_action", "Unsupported engagement action.")
    if liked is None:
        raise VpubError("state_unavailable", "The Like button's state is unavailable; nothing changed.")
    if liked is payload["desired"]:
        return {"state": "NO_CHANGE", "platform_writes": 0}
    await risk_check(page)
    stop_check("youtube")
    from .engagement import submitting
    submitting(payload["receipt_path"])
    await button.click(timeout=15000)
    await page.wait_for_timeout(1500)
    await risk_check(page)
    return {"state": "ACTION_UNVERIFIED", "platform_writes": 1}


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
    await submit(button)
    for _ in range(120):
        if not await yt_button_ready(button):
            break
        await page.wait_for_timeout(500)
    if changes is None:
        return save(state="EDIT_VERIFIED", platform_writes=1, cover={"state": "UI_ACCEPTED", "remote_pixels_verified": False}, **evidence)
    title, description = await edit_fields(page, url)
    after = {"title": normalize(await title.inner_text()), "description": normalize(await description.inner_text())}
    return save(state="EDIT_VERIFIED" if after == changes else "EDIT_SUBMITTED_UNVERIFIED", platform_writes=1, **evidence)

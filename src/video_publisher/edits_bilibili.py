"""Corrections to a published Bilibili work through the included bili-cli. Bilibili reviews each one again."""
from . import adapters
from .chapters import chapter_lines
from .core import VpubError, file_sha, now

# bili-cli answered and said no, so the change did not go through. Anything else may have reached Bilibili.
REFUSED = {"invalid_argument", "not_logged_in", "auth_expired", "not_found", "risk_control", "captcha_required", "rate_limited"}
REVIEW = "Bilibili reviews every edit again, and the old version may show until that finishes."


def archive(work):
    found = adapters.bili(["upload", "status", work, "--raw"]).get("archive") or {}
    if found.get("bvid") != work:
        raise VpubError("wrong_work", "Bilibili returned a different work; nothing changed.")
    return found


def saved_chapters(work):
    rows = adapters.bili(["upload", "chapters", "show", work]).get("chapters") or []
    return sorted(({"start_seconds": row.get("from"), "title": row.get("title")} for row in rows), key=lambda row: row["start_seconds"] or 0)


def chapter_args(work, chapters):
    args = ["upload", "chapters", "set", work]
    for line in chapter_lines(chapters):
        args += ["--chapter", line]
    return args


def set_chapters(work, chapters):
    """Replaces the whole list, so repeating it cannot stack chapters. True once a fresh read shows them."""
    adapters.bili(chapter_args(work, chapters))
    return saved_chapters(work) == chapters


def text_of(found):
    return {"title": (found.get("title") or "").strip(), "description": (found.get("desc") or "").strip()}


def tags_of(found):
    return {tag for tag in (found.get("tag") or "").split(",") if tag}


def submit(args, checkpoint, work, landed):
    """Once only. A refusal is recorded so it can be tried again; an unclear ending keeps the receipt, which blocks a repeat."""
    try:
        return adapters.bili(args, timeout=600)
    except VpubError as exc:
        if exc.code in REFUSED and not landed():
            checkpoint({"state": "EDIT_FAILED", "id": work, "error": exc.code})
        raise


def run(account, identity, receipt, patch, checkpoint, verify_only):
    work = receipt["id"]
    signed_in = adapters.auth_status("bilibili", account, "cli")["identity"]
    if str(signed_in) != str(identity):
        raise VpubError("wrong_account", "The signed-in account differs from the publication; nothing changed.")
    evidence = {"id": work, "platform": "bilibili", "identity": identity, "at": now(), "url": f"https://www.bilibili.com/video/{work}", "verification_source": "bili_cli_creator_record"}
    if "cover" in patch:
        before = archive(work)
        if verify_only:
            return {**evidence, "state": "EDIT_MISMATCH", "platform_writes": 0, "cover_url": before.get("cover"), "reason": "Bilibili does not expose the cover's pixels for comparison; nothing changed."}
        if file_sha(patch["cover"]) != patch["cover_sha256"]:
            raise VpubError("source_changed", "The cover changed before upload; nothing changed.")
        checkpoint({"state": "EDIT_SUBMITTING", "id": work, "before_cover": before.get("cover")})
        submit(["upload", "edit", work, "--cover", patch["cover"]], checkpoint, work, lambda: archive(work).get("cover") != before.get("cover"))
        after = archive(work).get("cover")
        return {**evidence, "state": "EDIT_VERIFIED" if after and after != before.get("cover") else "EDIT_SUBMITTED_UNVERIFIED", "platform_writes": 1, "cover_url": after,
                "cover": {"state": "UI_ACCEPTED", "remote_pixels_verified": False}, "note": REVIEW}
    if "chapters" in patch:
        chapters = patch["chapters"]
        if saved_chapters(work) == chapters:
            return {**evidence, "state": "EDIT_VERIFIED", "unchanged": True, "platform_writes": 0, "chapters": chapters}
        if verify_only:
            return {**evidence, "state": "EDIT_MISMATCH", "platform_writes": 0, "reason": "The saved chapters differ; no write attempted."}
        checkpoint({"state": "EDIT_SUBMITTING", "id": work})
        submit(chapter_args(work, chapters), checkpoint, work, lambda: saved_chapters(work) == chapters)
        matched = saved_chapters(work) == chapters
        return {**evidence, "state": "EDIT_VERIFIED" if matched else "EDIT_SUBMITTED_UNVERIFIED", "platform_writes": 1, "chapters": chapters, "note": "Bilibili reviews chapters before they show on the progress bar."}
    wanted = {"title": patch["title"].strip(), "description": patch["description"].strip()}
    tags = patch.get("tags")
    before = archive(work)
    if text_of(before) == wanted and (tags is None or tags_of(before) == set(tags)):
        return {**evidence, "state": "EDIT_VERIFIED", "unchanged": True, "platform_writes": 0}
    if verify_only:
        return {**evidence, "state": "EDIT_MISMATCH", "platform_writes": 0, "reason": "The saved text differs; no write attempted."}
    checkpoint({"state": "EDIT_SUBMITTING", "id": work, "before": text_of(before)})
    args = ["upload", "edit", work, "--title", patch["title"], "--desc", patch["description"]]
    for tag in tags or []:
        args += ["--tag", tag]
    submit(args, checkpoint, work, lambda: text_of(archive(work)) == wanted)
    after = archive(work)
    # Bilibili can drop an individual tag it dislikes; that is reported, and the title and description still decide.
    dropped = sorted(set(tags or []) - tags_of(after))
    return {**evidence, "state": "EDIT_VERIFIED" if text_of(after) == wanted else "EDIT_SUBMITTED_UNVERIFIED", "platform_writes": 1, "tags_dropped": dropped, "note": REVIEW}

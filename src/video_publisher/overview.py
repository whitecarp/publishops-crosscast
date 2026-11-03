"""A local view of what needs attention, and cleanup of finished snapshots. Nothing here contacts a platform."""
from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
import shutil
import sys

from . import adapters
from .core import PLATFORMS, Ledger, VpubError, digest, file_sha, home, how_to_clear_a_stop, part_left_undone, publication_lock, read_json, stop_check
from .process_lock import alive, released

FINISHED = {"SUBMISSION_VERIFIED", "EXISTING_VERIFIED", "AGENT_REPORTED_VERIFIED", "AGENT_REPORTED_DELETED"}
DRAFTS = {"PREVIEW_READY", "DRAFT_SAVED"}
# Single actions that may have reached a platform and aren't confirmed, and the read-only command that settles each.
UNCONFIRMED = (("reply", "operations/replies", {"REPLY_SUBMITTING", "REPLY_UNVERIFIED"}, "vpub comments verify-reply {key}"),
               ("message", "operations/dm-actions", {"DM_SUBMITTING", "DM_UNVERIFIED"}, "vpub dm verify {key}"),
               ("comment_action", "operations/comment-actions", {"COMMENT_SUBMITTING", "COMMENT_UNVERIFIED"}, "vpub comments verify-action {key}"),
               ("like_or_save", "engagement/receipts", {"ACTION_SUBMITTING", "ACTION_UNVERIFIED"}, "vpub engage verify {key}"),
               ("collection_change", "operations/collections", {"SUBMITTING", "UNVERIFIED"}, "vpub collection verify {key}"),
               ("playlist_change", "operations/playlists", {"SUBMITTING", "SUBMITTED", "UNVERIFIED"}, "vpub playlist verify {key}"))
EDIT_UNCONFIRMED = {"EDIT_SUBMITTING", "EDIT_SUBMITTED_UNVERIFIED"}


def read_quietly(path, default=None):
    try:
        return read_json(path)
    except (OSError, ValueError):
        return default


def chrome_path():
    """Where Patchright's chrome channel looks. Nothing is started."""
    if sys.platform == "win32":
        bases = [os.environ.get(name) for name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")]
        candidates = [Path(base) / "Google/Chrome/Application/chrome.exe" for base in bases if base]
    elif sys.platform == "darwin":
        candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    else:
        candidates = [Path("/opt/google/chrome/chrome")]
    return next((str(path) for path in candidates if path.is_file()), shutil.which("google-chrome") or shutil.which("chrome"))


def stop_state():
    """Report a stop file without clearing it; only the operator may do that."""
    try:
        stop_check()
    except VpubError as exc:
        detail = read_quietly(home() / "STOP.json")
        detail = detail if isinstance(detail, dict) else {}
        return {"active": True, "message": exc.message, "reason": detail.get("reason") or detail.get("code"),
                "platform": detail.get("platform"), "at": detail.get("at"),
                "hint": f"Inspect the reason. Only the operator may clear a stop, with {how_to_clear_a_stop()}."}
    return {"active": False}


def workspace_busy():
    """Read the lock's owner without taking the lock, so looking never blocks a starting publish."""
    path = home() / "publisher.lock"
    if not path.exists():
        return False
    owner = read_quietly(path)
    if released(owner):
        return False
    return alive(owner.get("pid") if isinstance(owner, dict) else owner)


def native_step(key, state, receipt):
    request = "vpub browser request " + key
    return {
        "WAITING_FOR_BROWSER": (request, "No upload has started. An AI assistant with browser tools can do this on the official page."),
        "BROWSER_IN_PROGRESS": (request, "Finish on the official page, then record the result with vpub browser record."),
        "METADATA_EDIT_IN_PROGRESS": (request, "Finish the edit on the official page, then record the result with vpub browser record."),
        "WAITING_FOR_USER": (request, receipt.get("observation") or "Waiting for you on the official page."),
        "SUBMITTED_UNVERIFIED": (request, "Check the original work on the official page. Never upload it again."),
        "PLATFORM_REJECTED": (request, "The platform rejected this attempt. A retry needs fresh evidence through vpub browser retry-rejected."),
        "DELETE_PENDING": (f"vpub browser delete-record {key} --evidence FILE", "Delete once on the official page, then record what you saw."),
    }.get(state, (None, "Inspect this attempt on the official page before doing anything else."))


def next_step(row, busy):
    """The one command that moves this attempt forward, or None when only a person can."""
    key, platform, state = row["key"], row["platform"], row["state"]
    receipt = row["data"].get("receipt") or {}
    method = (row["data"].get("options") or {}).get("method")
    if state == "IN_PROGRESS" and busy:
        return None, "Running now."
    if method == "native":
        return native_step(key, state, receipt)
    if method == "cli" and not receipt.get("id"):
        return "vpub works bilibili --limit 20", "Read the existing works, then check Bilibili's creator center (including review and draft views). If you identify this exact upload, record its ID with vpub adopt. An incomplete or empty list cannot prove it was never uploaded: keep UNKNOWN if unsure. Do not re-encode, reset the record or upload again to evade the duplicate guard."
    if state == "WAITING_FOR_USER":
        return f"vpub auth login {platform}", f"Complete the official sign-in or verification, then run vpub resume {key}."
    if state in {"IN_PROGRESS", "PREPARATION_INCOMPLETE"} or state.startswith("UPLOAD") or (method == "api" and not receipt.get("id")):
        return f"vpub resume {key}", "The command stopped before finishing. Resume checks the saved state and never submits twice."
    if receipt.get("still_a_draft"):
        return f"vpub resume {key}", "Studio shows this as a draft, so viewers can't see it. Resume presses Publish on the same draft and uploads nothing."
    return f"vpub verify {key}", "Checks the original attempt and uploads nothing. It can also finish chapters or widgets its plan asked for."


def progress(row):
    """How far an unfinished attempt got, from its own durable receipt."""
    data = row["data"]
    receipt = dict(data.get("receipt") or {})
    saved = read_quietly(home() / "browser-receipts" / f"{data.get('plan_hash')}-{row['platform']}.json")
    if isinstance(saved, dict):
        receipt.update(saved)
    view = {name: receipt[name] for name in ("phase", "updated_at", "final_submit_attempted", "offset", "bytes") if name in receipt}
    if receipt.get("bytes") and isinstance(receipt.get("offset"), int):
        view["percent"] = round(100 * receipt["offset"] / receipt["bytes"], 1)
    return view


def attempt(row, busy):
    command, note = next_step(row, busy)
    data, receipt = row["data"], row["data"].get("receipt") or {}
    item = {"key": row["key"], "platform": row["platform"], "state": row["state"], "title": data.get("title"),
            "method": (data.get("options") or {}).get("method"), "id": receipt.get("id"), "url": receipt.get("url"),
            "updated_at": row["updated_at"], "next_command": command, "note": note}
    if part_left_undone(receipt):
        item.update({name: receipt[name] for name in ("chapters_state", "widgets_state", "season") if name in receipt})
    done = progress(row)
    return {**item, "progress": done} if done else item


def sign_in(platform):
    root = home() / "accounts" / "default"
    if platform == "youtube" and adapters.addon_installed():
        return {"saved": (root / "youtube-token.json").exists()}
    if platform in {"douyin", "instagram", "x", "youtube"}:
        saved = read_quietly(root / (platform + "-identity.json"))
        if isinstance(saved, dict):
            return {"saved": True, "identity": saved.get("identity"), "verified_at": saved.get("verified_at")}
        return {"saved": False}
    return {"saved": None, "note": "bili-cli keeps this sign-in."}


def background_browsers():
    sessions = []
    for path in sorted((home() / "browser-brokers").glob("*.json")):
        saved = read_quietly(path)
        if isinstance(saved, dict) and saved.get('state') == 'closed':
            continue
        pid = saved.get("pid") if isinstance(saved, dict) else None
        if type(pid) is int and alive(pid):
            sessions.append({"session": path.stem, "pid": pid})
    return sessions


def plan_files(plan):
    plan = plan or {}
    paths = [plan.get("video"), plan.get("cover")]
    for variants in (plan.get("covers") or {}).values():
        paths += [asset.get("path") for asset in variants.values()]
    return [path for path in paths if path]


def row_folders(row, root):
    """Every snapshot folder this publication's plans and browser job point into."""
    data = row["data"]
    names = {data.get("plan_hash"), (data.get("approved_plan") or {}).get("confirmation")}
    job = read_quietly(home() / "browser-jobs" / (row["key"] + ".json"))
    for plan in (data.get("upload_plan"), job.get("plan") if isinstance(job, dict) else None):
        for path in plan_files(plan):
            folder = Path(path).resolve().parent
            if folder.parent == root:
                names.add(folder.name)
    names.discard(None)
    return names


def same_as_approved(plan, check_hash, checked):
    source = Path(plan["video"])
    if source.stat().st_size != plan.get("video_bytes"):
        return False
    if not check_hash:
        return True
    key = (str(source), plan.get("video_sha256"))
    if key not in checked:
        checked[key] = file_sha(source) == plan.get("video_sha256")
    return checked[key]


def keep_reason(folder, rows, check_hash, checked):
    """Why this copy must stay, or None. A copy that might be the only one is never removable."""
    if not rows:
        return "No publication record points here, so the original cannot be checked."
    for row in rows:
        if row["state"] not in FINISHED or (row["data"].get("receipt") or {}).get("postprocess"):
            return "A publication using this copy is not finished."
    for row in rows:
        plan = row["data"].get("approved_plan") or {}
        paths = [Path(path) for path in plan_files(plan)]
        if (not plan.get("video") or any(folder in path.resolve().parents or not path.is_file() for path in paths)
                or not same_as_approved(plan, check_hash, checked)):
            return "The original video is missing or changed, so this may be the only exact copy."
    return None


def folder_bytes(folder):
    seen, total = set(), 0
    for path in folder.rglob("*"):
        if path.is_file():
            info = path.stat()
            if (info.st_dev, info.st_ino) not in seen:  # A hard link is not a second copy.
                seen.add((info.st_dev, info.st_ino))
                total += info.st_size
    return total


def snapshot_folders(rows, check_hash=True):
    root = home() / "jobs"
    if not root.is_dir():
        return []
    users = {}
    for row in rows:
        for name in row_folders(row, root):
            users.setdefault(name, []).append(row)
    checked, folders = {}, []
    for folder in sorted(path for path in root.iterdir() if path.is_dir() and not path.is_symlink()):
        rows_here = users.get(folder.name, [])
        reason = keep_reason(folder, rows_here, check_hash, checked)
        item = {"folder": str(folder), "bytes": folder_bytes(folder), "platforms": sorted({row["platform"] for row in rows_here})}
        folders.append({**item, "kept_because": reason} if reason else item)
    return folders


def read_rows():
    ledger = Ledger()
    try:
        return ledger.rows()
    finally:
        ledger.close()


def human_size(count):
    for unit in ("B", "KB", "MB"):
        if count < 1024:
            return f"{count:.0f} {unit}"
        count /= 1024
    return f"{count:.1f} GB"


def saved_receipts(folder):
    root = home() / folder
    if root.is_dir():
        for path in root.glob("*.json"):
            saved = read_quietly(path)
            if isinstance(saved, dict):
                yield path.stem, saved


def action_row(kind, key, saved, command):
    """No message or comment text and no recipient, only enough to find it: this view may be printed or logged."""
    asked = saved.get("request") if isinstance(saved.get("request"), dict) else {}
    work = None if kind == "message" else saved.get("work_id") or asked.get("work_id")
    return {"kind": kind, "key": key, "platform": saved.get("platform") or asked.get("platform"), **({"work_id": work} if work else {}),
            "state": saved.get("state"), "at": saved.get("submitted_at") or saved.get("started_at") or saved.get("at"), "next_command": command}


def edit_rows(rows):
    """An edit's record can be the worker's, with the work and no key, or the command's, with the key and perhaps no work, so its command comes from whichever it has."""
    adopted = {}
    matched = {}  # Only this report's already-loaded ledger snapshot; never cache receipts.
    for _, saved in saved_receipts("operations/adopted"):
        adopted[digest(["adopted", saved.get("platform"), saved.get("work_id")])] = saved
    for edit_id, saved in saved_receipts("edits"):
        if saved.get("state") not in EDIT_UNCONFIRMED:
            continue
        work, platform = saved.get("id"), saved.get("platform")
        lookup = (saved.get("key"), platform, work)
        cacheable = all(isinstance(value, (str, type(None))) for value in lookup)
        if not cacheable or lookup not in matched:
            row = next((row for row in rows if row["key"] == saved.get("key") or (work and row["platform"] == platform and (row["data"].get("receipt") or {}).get("id") == work)), None)
            if cacheable:
                matched[lookup] = row
        else:
            row = matched[lookup]
        taken = adopted.get(saved.get("key")) or next((one for one in adopted.values() if work and (one.get("platform"), one.get("work_id")) == (platform, work)), None)
        command = f"vpub edit {row['key']} FILE --verify-only" if row else f"vpub edit-work {taken['platform']} {taken['work_id']} FILE --verify-only" if taken else None
        yield {**action_row("edit", edit_id, {**saved, "platform": platform or (row or taken or {}).get("platform"), "work_id": work or (taken or {}).get("work_id")}, command),
               "note": "FILE is the correction that was sent. This only reads, and an unclear save stays blocked until it's confirmed."}


def widget_rows():
    for work, saved in saved_receipts("widgets"):
        open_rows = [row for row in saved.get("rows") or [] if isinstance(row, dict) and row.get("state") != "VERIFIED"]
        if open_rows:
            yield {"kind": "widgets", "key": work, "platform": "bilibili", "work_id": work, "state": "PARTIAL", "widgets": dict(Counter(str(row.get("state")) for row in open_rows)),
                   "at": saved.get("updated_at"), "next_command": f"vpub widgets verify {work}"}


def unconfirmed_actions(rows):
    """Replies, messages, likes and other single actions nobody has confirmed. Without this, they're only found by whoever kept the key."""
    found = [action_row(kind, key, saved, command.format(key=key)) for kind, folder, states, command in UNCONFIRMED for key, saved in saved_receipts(folder) if saved.get("state") in states]
    for row in found:
        if row["kind"] == "playlist_change":  # The add-on's own records, which are always YouTube's.
            row["platform"] = "youtube"
        elif row["kind"] == "collection_change" and row["platform"] == "youtube":
            row["next_command"] = f"vpub playlist verify {row['key']}"
    return sorted([*found, *edit_rows(rows), *widget_rows()], key=lambda row: str(row.get("at") or ""), reverse=True)


def summary(stop, attention, reclaimable, unconfirmed=0):
    from .operations import reporting_language
    chinese = reporting_language() == "zh"
    parts = []
    if stop["active"]:
        parts.append("发布已暂停，原因见 stop。" if chinese else "Publishing is stopped; see stop for the reason.")
    if chinese:
        parts.append(f"{attention} 条发布需要处理。" if attention else "没有需要处理的发布。")
    else:
        parts.append(f"{attention} publication{'s need' if attention != 1 else ' needs'} attention." if attention else "No publication needs attention.")
    if unconfirmed:
        parts.append(f"{unconfirmed} 个回复、私信、点赞等操作尚未确认，见 actions。" if chinese else f"{unconfirmed} other action{'s are' if unconfirmed != 1 else ' is'} still unconfirmed; see actions.")
    if reclaimable:
        size = human_size(reclaimable)
        parts.append(f"运行 vpub cleanup 可释放 {size}。" if chinese else f"vpub cleanup can free {size}.")
    return " ".join(parts)


def quietly(read, default=None):
    try:
        return read()
    except (OSError, ValueError, KeyError, VpubError):
        return default


def status(limit=20):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise VpubError("invalid_limit", "Use limit 1-200.")
    from .messages import listing as messages
    from .operations_runs import listing as sync_runs
    rows = read_rows()
    busy = workspace_busy()
    drafts = [row for row in rows if row["state"] in DRAFTS]
    attention = [row for row in rows if row["state"] not in FINISHED | DRAFTS or part_left_undone(row["data"].get("receipt") or {})]
    folders = snapshot_folders(rows, check_hash=False)
    reclaimable = sum(item["bytes"] for item in folders if "kept_because" not in item)
    stop = stop_state()
    last_sync = quietly(lambda: sync_runs(1)["items"], [])
    actions = quietly(lambda: unconfirmed_actions(rows), [])
    from .work_delete import status_rows as deletion_rows
    deletions = list(deletion_rows())
    actions.extend({**row, 'kind': 'work_deletion', 'work_id': row['id']}
                   for row in deletions if not row['deleted'])
    return {"summary": summary(stop, len(attention), reclaimable, len(actions)), "stop": stop, "workspace_busy": busy,
            "paid_api_addon": adapters.addon_installed(),
            "platforms": {p: {"default_method": adapters.default_method(p), "sign_in": sign_in(p)} for p in PLATFORMS},
            "publications": {"total": len(rows), "by_state": dict(Counter(row["state"] for row in rows)),
                             "needs_attention_total": len(attention),
                             "needs_attention": [attempt(row, busy) for row in attention[:limit]],
                             "drafts": [attempt(row, busy) for row in drafts[:limit]]},
            "actions": {"unconfirmed_total": len(actions), "unconfirmed": actions[:limit]},
            "deletions": {"total": len(deletions), "items": deletions[:limit]},
            "background_browsers": background_browsers(),
            "messages_pending": quietly(lambda: messages(limit=1)["matching_total"]),
            "last_sync": last_sync[0] if last_sync else None,
            "storage": {"snapshot_bytes": sum(item["bytes"] for item in folders), "reclaimable_bytes": reclaimable,
                        "next": "vpub cleanup" if reclaimable else None},
            "platform_writes": 0,
            "scope": "Local records only. Saved sign-ins are not a live check; use vpub auth status for that."}


def cleanup(apply=False):
    """Remove private video copies that nothing can need again. Previews unless asked to apply."""
    with publication_lock():
        from . import runtime_files
        root = home() / "jobs"
        folders = snapshot_folders(read_rows())
        runtime_reason = 'Registered runtime entries require separate safe cleanup.'
        for item in folders:
            if 'kept_because' not in item and runtime_files.registered_in(item['folder']):
                item['kept_because'] = runtime_reason
        removable = [item for item in folders if "kept_because" not in item]
        kept = [item for item in folders if "kept_because" in item]
        # Shared hardlinks may retain every byte outside these folders. Count
        # only single-link files; this deliberately underestimates shared data.
        for item in removable:
            item['bytes'] = sum(path.stat().st_size for path in Path(item['folder']).rglob('*')
                                if path.is_file() and path.stat().st_nlink == 1)
        total = sum(item["bytes"] for item in removable)
        if not apply:
            runtime = runtime_files.cleanup()
            return {"mode": "PREVIEW", "removable": removable, "kept": kept, "reclaimable_bytes": total + runtime['reclaimable_bytes'], "platform_writes": 0,
                    "runtime_files": runtime, 'byte_accounting': runtime['byte_accounting'],
                    "next": "vpub cleanup --apply" if removable or runtime['removable'] else None}
        removed = []
        for item in removable:
            folder = Path(item["folder"])
            if folder.parent != root or folder.is_symlink():
                raise VpubError("unsafe_path", "Cleanup only removes folders directly inside its own jobs directory.")
            if runtime_files.registered_in(folder):
                kept.append({**item, 'kept_because': runtime_reason})
                continue
            shutil.rmtree(folder)
            removed.append(item)
        runtime = runtime_files.cleanup(apply=True)
        return {"mode": "APPLIED", "removed": removed, "kept": kept, "freed_bytes": sum(item['bytes'] for item in removed) + runtime['freed_bytes'], "platform_writes": 0,
                "runtime_files": runtime, 'byte_accounting': runtime['byte_accounting']}

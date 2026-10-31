"""A local view of what needs attention, and cleanup of finished snapshots. Nothing here contacts a platform."""
from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
import shutil
import sys

from . import adapters
from .core import PLATFORMS, Ledger, VpubError, file_sha, home, publication_lock, read_json, stop_check
from .process_lock import alive

FINISHED = {"SUBMISSION_VERIFIED", "EXISTING_VERIFIED", "AGENT_REPORTED_VERIFIED", "AGENT_REPORTED_DELETED"}
DRAFTS = {"PREVIEW_READY", "DRAFT_SAVED"}


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
                "hint": "Inspect the reason. Only the operator may clear a stop, by deleting that file."}
    return {"active": False}


def workspace_busy():
    """Read the lock's owner without taking the lock, so looking never blocks a starting publish."""
    path = home() / "publisher.lock"
    if not path.exists():
        return False
    owner = read_quietly(path)
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
        return None, "bili-cli has no resumable session. Check Bilibili's creator center; if the video is there, record it with vpub adopt. Never upload it again."
    if state == "WAITING_FOR_USER":
        return f"vpub auth login {platform}", f"Complete the official sign-in or verification, then run vpub resume {key}."
    if state in {"IN_PROGRESS", "PREPARATION_INCOMPLETE"} or state.startswith("UPLOAD") or (method == "api" and not receipt.get("id")):
        return f"vpub resume {key}", "The command stopped before finishing. Resume checks the saved state and never submits twice."
    return f"vpub verify {key}", "Read-only check of the original attempt. Never upload it again."


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
    done = progress(row)
    return {**item, "progress": done} if done else item


def sign_in(platform):
    root = home() / "accounts" / "default"
    if platform in {"douyin", "instagram", "x"}:
        saved = read_quietly(root / (platform + "-identity.json"))
        if isinstance(saved, dict):
            return {"saved": True, "identity": saved.get("identity"), "verified_at": saved.get("verified_at")}
        return {"saved": False}
    if platform == "youtube" and adapters.addon_installed():
        return {"saved": (root / "youtube-token.json").exists()}
    return {"saved": None, "note": "bili-cli keeps this sign-in." if platform == "bilibili" else "Your assistant's browser keeps this sign-in."}


def background_browsers():
    sessions = []
    for path in sorted((home() / "browser-brokers").glob("*.json")):
        saved = read_quietly(path)
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


def summary(stop, attention, reclaimable):
    from .operations import reporting_language
    chinese = reporting_language() == "zh"
    parts = []
    if stop["active"]:
        parts.append("发布已暂停，原因见 stop。" if chinese else "Publishing is stopped; see stop for the reason.")
    if chinese:
        parts.append(f"{attention} 条发布需要处理。" if attention else "没有需要处理的发布。")
    else:
        parts.append(f"{attention} publication{'s need' if attention != 1 else ' needs'} attention." if attention else "No publication needs attention.")
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
    attention = [row for row in rows if row["state"] not in FINISHED | DRAFTS]
    folders = snapshot_folders(rows, check_hash=False)
    reclaimable = sum(item["bytes"] for item in folders if "kept_because" not in item)
    stop = stop_state()
    last_sync = quietly(lambda: sync_runs(1)["items"], [])
    return {"summary": summary(stop, len(attention), reclaimable), "stop": stop, "workspace_busy": busy,
            "paid_api_addon": adapters.addon_installed(),
            "platforms": {p: {"default_method": adapters.default_method(p), "sign_in": sign_in(p)} for p in PLATFORMS},
            "publications": {"total": len(rows), "by_state": dict(Counter(row["state"] for row in rows)),
                             "needs_attention_total": len(attention),
                             "needs_attention": [attempt(row, busy) for row in attention[:limit]],
                             "drafts": [attempt(row, busy) for row in drafts[:limit]]},
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
        root = home() / "jobs"
        folders = snapshot_folders(read_rows())
        removable = [item for item in folders if "kept_because" not in item]
        kept = [item for item in folders if "kept_because" in item]
        total = sum(item["bytes"] for item in removable)
        if not apply:
            return {"mode": "PREVIEW", "removable": removable, "kept": kept, "reclaimable_bytes": total, "platform_writes": 0,
                    "next": "vpub cleanup --apply" if removable else None}
        for item in removable:
            folder = Path(item["folder"])
            if folder.parent != root or folder.is_symlink():
                raise VpubError("unsafe_path", "Cleanup only removes folders directly inside its own jobs directory.")
            shutil.rmtree(folder)
        return {"mode": "APPLIED", "removed": removable, "kept": kept, "freed_bytes": total, "platform_writes": 0}

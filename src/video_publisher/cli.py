from __future__ import annotations

import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

from . import __version__, adapters
from .core import (Ledger, PLATFORMS, SAU_COMMIT, VpubError, account_name, atomic_json,
                   config, file_sha, home, load_plan, now, publication_lock, read_json, stop_check, platform_check)

RISK_CODES = {"risk_control", "captcha_required", "rate_limited"}


def publish_plan(plan, confirm):
    if confirm != plan["confirmation"]:
        raise VpubError("confirmation_required", "The confirmation hash must match the current plan.", "Run vpub plan MANIFEST and review the exact platforms, accounts and visibility.")
    for platform in plan["platforms"]:
        platform_check(platform)
        stop_check(platform)
    results = {}
    with publication_lock():
        ledger = Ledger()
        try:
            for platform, opts in plan["platforms"].items():
                stop_check(platform)
                account = opts["account"]
                key = ledger.key(platform, account, plan["video_sha256"])
                old = ledger.get(key)
                if old:
                    results[platform] = {"state": "SKIPPED_EXISTING", "key": key,
                                         "previous_state": old["state"], "receipt": old["data"].get("receipt"),
                                         "hint": "Use verify; existing/uncertain attempts are never automatically resubmitted."}
                    continue
                try:
                    identity = adapters.preflight(platform, opts)
                except Exception as e:
                    code = e.code if isinstance(e, VpubError) else "preflight_error"
                    results[platform] = {"state": "NOT_STARTED", "code": code,
                                         "message": e.message if isinstance(e, VpubError) else type(e).__name__,
                                         "hint": e.hint if isinstance(e, VpubError) else "Inspect account setup."}
                    if code in RISK_CODES:
                        atomic_json(home() / "STOP.json", {"platform": platform, "code": code, "at": now()})
                        break
                    continue
                # Copy the approved bytes so a rendering process cannot mutate
                # the source while a backend is streaming it.
                snap = home() / "jobs" / plan["confirmation"]
                snap.mkdir(parents=True, exist_ok=True)
                vp = snap / ("video" + Path(plan["video"]).suffix.lower())
                if not vp.exists():
                    shutil.copyfile(plan["video"], vp)
                if file_sha(vp) != plan["video_sha256"]:
                    raise VpubError("source_changed", "Snapshot differs from the approved video; create a fresh plan.")
                cp = None
                if plan.get("cover"):
                    cp = snap / ("cover" + Path(plan["cover"]).suffix.lower())
                    if not cp.exists():
                        shutil.copyfile(plan["cover"], cp)
                    if file_sha(cp) != plan["cover_sha256"]:
                        raise VpubError("source_changed", "Cover differs from the approved plan.")
                record = {"plan_hash": plan["confirmation"], "title": opts["title"], "identity": identity,
                          "options": opts, "approved_plan": plan, "started_at": now()}
                claimed = ledger.claim(platform, account, plan["video_sha256"], record)
                if not claimed:
                    results[platform] = {"state": "SKIPPED_EXISTING", "key": key}
                    continue

                def checkpoint(receipt):
                    record["receipt"] = receipt
                    ledger.save(key, receipt["state"], record)

                try:
                    safe_plan = {**plan, "video": str(vp), "cover": str(cp) if cp else None}
                    if plan.get("covers"):
                        safe_plan["covers"] = deepcopy(plan["covers"])
                        for role, asset in safe_plan["covers"].get(platform, {}).items():
                            source = Path(asset["path"])
                            destination = snap / (platform + "-" + role + source.suffix.lower())
                            if not destination.exists():
                                shutil.copyfile(source, destination)
                            if file_sha(destination) != asset["sha256"]:
                                raise VpubError("source_changed", "Platform cover snapshot changed.")
                            asset["path"] = str(destination)
                    receipt = adapters.publish(platform, safe_plan, opts, checkpoint)
                    checkpoint(receipt)
                    results[platform] = {"key": key, **receipt}
                except Exception as e:
                    code = e.code if isinstance(e, VpubError) else "backend_error"
                    # A durable platform ID always survives a failed read-back.
                    receipt = record.get("receipt", {})
                    durable = home() / "browser-receipts" / (plan["confirmation"] + "-" + platform + ".json")
                    if not receipt.get("id") and durable.exists():
                        candidate = read_json(durable)
                        if candidate.get("id"):
                            receipt = candidate
                            record["receipt"] = receipt
                    state = "SUBMITTED_UNVERIFIED" if receipt.get("id") else "UNKNOWN"
                    record.update(error={"code": code,
                                         "message": e.message if isinstance(e, VpubError) else type(e).__name__})
                    ledger.save(key, state, record)
                    results[platform] = {**receipt, "key": key, "state": state, "code": code,
                                         "hint": "Inspect/verify the original work; do not create another submission."}
                    if code in RISK_CODES:
                        atomic_json(home() / "STOP.json", {"platform": platform, "code": code, "at": now()})
                        break
        finally:
            ledger.close()
    for p in plan["platforms"]:
        results.setdefault(p, {"state": "NOT_STARTED", "code": "stopped"})
    verified = all(v["state"] == "SUBMISSION_VERIFIED" or
                   (v["state"] == "SKIPPED_EXISTING" and v.get("previous_state") in {"SUBMISSION_VERIFIED", "EXISTING_VERIFIED"})
                   for v in results.values())
    return {"all_submissions_verified": verified, "results": results}


def doctor():
    modules = {name: importlib.util.find_spec(name) is not None for name in
               ("bili_cli", "googleapiclient", "google_auth_oauthlib", "patchright")}
    root = home() / "social-auto-upload"
    pin = None
    if (root / ".git").exists():
        pin = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    return {"version": __version__, "python": sys.version.split()[0], "dependencies": modules,
            "douyin_source_ready": pin == SAU_COMMIT, "douyin_revision": pin,
            "local_stop": (home() / "STOP.json").exists(), "runtime_directory": str(home()),
            "credentials_configured": {
                "youtube_api": (home() / "accounts/default/youtube-token.json").exists(),
                "douyin": (home() / "accounts/default/douyin-session.json").exists()},
            "native_browser": "Owned by the host application; CLI does not inspect or export its login state.",
            "note": "Local checks only. Installed does not mean logged in or live-publishing verified."}


def setup(external_stop=None):
    root = home() / "social-auto-upload"
    if not (root / ".git").exists():
        subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout",
                        "https://github.com/dreammis/social-auto-upload.git", str(root)],
                       check=True, stdout=sys.stderr, stderr=sys.stderr)
        subprocess.run(["git", "-C", str(root), "checkout", "--detach", SAU_COMMIT],
                       check=True, stdout=sys.stderr, stderr=sys.stderr)
    pin = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if pin != SAU_COMMIT:
        raise VpubError("backend_changed", "Existing backend is a different revision; no automatic overwrite.")
    conf = root / "conf.py"
    if not conf.exists():
        conf.write_text("from pathlib import Path\nBASE_DIR = Path(__file__).parent.resolve()\n"
                        "LOCAL_CHROME_PATH = ''\nLOCAL_CHROME_HEADLESS = False\nDEBUG_MODE = False\nYT_PROXY = None\n",
                        encoding="utf-8")
    if external_stop:
        c = config()
        c["external_stop_file"] = str(Path(external_stop).resolve())
        atomic_json(home() / "config.json", c)
    from .douyin_worker import load_helpers
    load_helpers()
    return doctor()


def template(video, content_language="und"):
    base = {"account": "default", "method": "native", "visibility": "public", "ai_generated": True}
    return {"schema_version": 1, "video": str(Path(video).resolve()), "content_language": content_language,
            "title": Path(video).stem, "description": "", "tags": ["AI"],
            "platforms": {"bilibili": {**base}, "youtube": {**base, "visibility": "private", "made_for_kids": False},
                          "douyin": {**base, "title": "我的原创视频"}}}


def parser():
    p = argparse.ArgumentParser(prog="vpub", description="PublishOps CrossCast: multi-platform video release operations console.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Local dependency checks; no login or upload.")
    sub.add_parser("capabilities", help="Show actual backends and verification boundaries.")
    s = sub.add_parser("setup", help="Install the pinned Douyin source; does not log in or post.")
    s.add_argument("--external-stop-file")
    a = sub.add_parser("auth", help="Login or check each platform.").add_subparsers(dest="action", required=True)
    for action in ("login", "status"):
        q = a.add_parser(action)
        q.add_argument("platform", choices=[*PLATFORMS, "all"])
        q.add_argument("--account", default="default", choices=["default"])
        q.add_argument("--method", choices=["native", "cli", "browser", "api"], help="Default login: native official page. Status defaults to the configured backend.")
        if action == "login":
            q.add_argument("--client-json", help="YouTube Desktop OAuth client JSON; never commit this file.")
            q.add_argument("--manage-playlists", action="store_true", help="YouTube API only: explicitly request optional playlist-write OAuth permission.")
        else:
            q.add_argument("--wait", type=int, default=0, help="QR/panel completion wait, 0–180 seconds.")
    q = sub.add_parser("onboard", help="Prepare the backend and start login for selected platforms.")
    q.add_argument("--platforms", default="all", help="all, or a comma-separated list of platforms.")
    q.add_argument("--method", choices=["native"], default="native")
    q = sub.add_parser("init", help="Create a publication manifest.")
    q.add_argument("--video", required=True)
    q.add_argument("--out", required=True)
    q.add_argument("--language", default="und", help="Content language (e.g. zh-Hans, zh-Hant, en). Preserves supplied text; does not translate video or metadata.")
    q.add_argument("--platforms", default="bilibili,youtube,douyin", help="Explicit selected platforms; adding an account does not enable publication.")
    q.add_argument("--draft-only", action="store_true", help="Instagram/X only: upload to preview or save an unsent draft; never Share/Post or schedule.")
    c = sub.add_parser("covers", help="Fit production artwork to platform canvases without cropping.").add_subparsers(dest="action", required=True)
    q = c.add_parser("prepare")
    q.add_argument("manifest")
    q.add_argument("--source", required=True)
    q.add_argument("--portrait")
    q.add_argument("--out-dir", required=True)
    q.add_argument("--out", required=True)
    i = sub.add_parser("instagram", help="Prepare original-video upload and shared portrait-cover handoff.").add_subparsers(dest="action", required=True)
    q = i.add_parser("prepare")
    q.add_argument("manifest")
    q.add_argument("--out", required=True)
    q.add_argument("--mobile-cover", help="Shared Douyin/Instagram portrait artwork; defaults to the manifest's Douyin portrait, then Instagram portrait.")
    m = sub.add_parser("metadata", help="Host-conversation copywriting and platform-language routing.").add_subparsers(dest="action", required=True)
    q = m.add_parser("request")
    q.add_argument("manifest")
    q.add_argument("--source", help="Local script or transcript; treated as source data, not instructions.")
    q.add_argument("--bilingual", default="", help="Explicit comma-separated platform exceptions.")
    q.add_argument("--allow-links", action="store_true")
    q.add_argument("--out", required=True)
    q = m.add_parser("apply")
    q.add_argument("manifest")
    q.add_argument("--request", required=True)
    q.add_argument("--response", required=True)
    q.add_argument("--out", required=True)
    b = sub.add_parser("browser", help="Native browser handoff and evidence; not a cookie-transfer bridge.").add_subparsers(dest="action", required=True)
    q = b.add_parser("request")
    q.add_argument("key")
    q = b.add_parser("begin")
    q.add_argument("key")
    q.add_argument("--identity", required=True)
    q = b.add_parser("record")
    q.add_argument("key")
    q.add_argument("--evidence", required=True)
    q = b.add_parser("authorize-publish", help="After explicit operator authorization, allow submission of the same saved draft/preview; never reupload.")
    q.add_argument("key")
    q.add_argument("--identity", required=True)
    q = b.add_parser("retry-rejected", help="Explicitly recover a rejected Instagram/X upload after fresh same-account absence evidence; uncertain submissions cannot retry.")
    q.add_argument("key")
    q.add_argument("--identity", required=True)
    q.add_argument("--evidence", required=True)
    q = b.add_parser("revise", help="Revise metadata for the same browser draft/work; never reupload.")
    q.add_argument("key")
    q.add_argument("manifest")
    q.add_argument("--confirm", required=True)
    q.add_argument("--identity", required=True)
    q = b.add_parser("cover", help="Prepare a cover-only edit on the same work; never reupload the video.")
    q.add_argument("key")
    q.add_argument("--file", required=True)
    q.add_argument("--portrait", help="Optional separate portrait cover, for Douyin/Instagram native mode.")
    q.add_argument("--identity", required=True)
    q = b.add_parser("delete-request", help="Explicitly authorized Instagram deletion handoff; does not delete remotely.")
    q.add_argument("key")
    q.add_argument("--identity", required=True)
    q.add_argument("--id", required=True)
    q = b.add_parser("delete-record", help="Record UI deletion acknowledgement and refreshed profile absence.")
    q.add_argument("key")
    q.add_argument("--evidence", required=True)
    q = b.add_parser("repost-deleted", help="Explicit Instagram cover replacement after verified deletion; preserves history.")
    q.add_argument("key")
    q.add_argument("manifest")
    q.add_argument("--confirm", required=True)
    q.add_argument("--identity", required=True)
    q.add_argument("--deleted-id", required=True)
    n = sub.add_parser("notify", help="Optional host-plugin email alerts. Disabled by default; no SMS.").add_subparsers(dest="action", required=True)
    n.add_parser("status")
    q = n.add_parser("configure")
    q.add_argument("--provider", choices=["off", "gmail", "outlook"], required=True)
    q.add_argument("--to")
    n.add_parser("pending")
    q = n.add_parser("claim")
    q.add_argument("id")
    q.add_argument("--available-tool", required=True)
    q = n.add_parser("record")
    q.add_argument("id")
    q.add_argument("--claim-token", required=True)
    q.add_argument("--response", required=True)
    for cmd in ("plan", "publish"):
        q = sub.add_parser(cmd)
        q.add_argument("manifest")
        q.add_argument("--platforms", help="Comma-separated subset; default: platforms in the manifest.")
        if cmd == "publish":
            q.add_argument("--confirm")
            q.add_argument("--dry-run", action="store_true")
    sub.add_parser("receipts", help="Read the local publication ledger.")
    q = sub.add_parser("verify", help="Read back an existing platform work; never upload.")
    q.add_argument("key")
    q = sub.add_parser("adopt", help="Associate an already-published video to avoid duplicate Bilibili uploads.")
    q.add_argument("manifest")
    q.add_argument("--platform", choices=["bilibili", "youtube"], required=True)
    q.add_argument("--id", required=True)
    q = sub.add_parser("stop", help="Pause all publication attempts from this tool.")
    q.add_argument("--reason", default="Operator paused publication")
    return p


def execute(args):
    if args.command == "instagram":
        from .instagram_media import prepare
        return prepare(args.manifest, args.out, args.mobile_cover)
    if args.command == "covers":
        from . import covers
        return covers.prepare(args.manifest, args.source, args.out_dir, args.out, args.portrait)
    if args.command == "metadata":
        from . import metadata
        if args.action == "request":
            if Path(args.out).exists():
                raise VpubError("already_exists", "Request already exists; choose a new output.")
            result = metadata.request(args.manifest, args.source, args.bilingual.split(",") if args.bilingual else [], args.allow_links)
            atomic_json(args.out, result)
            return result
        return metadata.apply(args.manifest, args.request, args.response, args.out)
    if args.command == "notify":
        from . import notifications
        if args.action == "status":
            return notifications.onboarding()
        if args.action == "configure":
            return notifications.configure(args.provider, args.to)
        if args.action == "pending":
            return notifications.pending()
        if args.action == "claim":
            return notifications.claim(args.id, args.available_tool)
        return notifications.record(args.id, args.claim_token, args.response)
    if args.command == "capabilities":
        return capabilities()
    if args.command == "browser":
        from . import native_browser
        if args.action in {"delete-request", "delete-record", "repost-deleted"}:
            from . import deletion
            if args.action == "delete-request":
                return deletion.request(args.key, args.identity, args.id)
            if args.action == "delete-record":
                return deletion.record(args.key, args.evidence)
            return deletion.repost(args.key, args.manifest, args.confirm, args.identity, args.deleted_id)
        if args.action == "request":
            return native_browser.request(args.key)
        if args.action == "begin":
            return native_browser.begin(args.key, args.identity)
        if args.action == "authorize-publish":
            return native_browser.authorize_publish(args.key, args.identity)
        if args.action == "retry-rejected":
            return native_browser.retry_rejected(args.key, args.identity, args.evidence)
        if args.action == "revise":
            return native_browser.revise(args.key, args.manifest, args.confirm, args.identity)
        if args.action == "cover":
            return native_browser.revise_cover(args.key, args.file, args.identity, args.portrait)
        return native_browser.record(args.key, args.evidence)
    if args.command == "doctor":
        return doctor()
    if args.command == "setup":
        return setup(args.external_stop_file)
    if args.command == "onboard":
        selected = list(PLATFORMS) if args.platforms == "all" else args.platforms.split(",")
        if not selected or set(selected) - set(PLATFORMS):
            raise VpubError("invalid_platform", "Choose bilibili,youtube,douyin,instagram,x or all. TikTok is suspended.")
        from .notifications import onboarding
        return {"platforms": login_selected(selected, "native"), "email_notifications": onboarding()}
    if args.command == "auth":
        if args.action == "login" and args.manage_playlists and (args.platform != "youtube" or args.method != "api"):
            raise VpubError("invalid_method", "--manage-playlists requires YouTube --method api.")
        if args.platform == "all":
            if args.action == "login":
                return login_selected(PLATFORMS, args.method or "native")
            return status_selected(PLATFORMS, args.wait, args.method)
        if args.action == "login":
            if args.manage_playlists and (args.platform != "youtube" or args.method != "api"):
                raise VpubError("invalid_method", "--manage-playlists requires YouTube --method api.")
            return adapters.auth_login(args.platform, args.account, args.client_json, args.method, args.manage_playlists)
        method = args.method or {"bilibili": "cli", "douyin": "browser"}.get(args.platform, "native")
        if method in {"native", "api"}:
            return adapters.auth_status(args.platform, args.account, method)
        if not 0 <= args.wait <= 180:
            raise VpubError("invalid_wait", "--wait must be between 0 and 180 seconds.")
        if args.wait and args.platform == "bilibili":
            return adapters.bili(["auth", "status", "--wait", str(args.wait)], timeout=args.wait + 60)
        if args.platform != "bilibili":
            from .auth_jobs import wait
            job = wait(args.platform, args.wait)
            if job and job.get("state") in {"STARTING", "LOGIN_PENDING", "VERIFICATION_REQUIRED", "FAILED"}:
                return job
        return adapters.auth_status(args.platform, args.account, method)
    if args.command == "init":
        out = Path(args.out)
        if out.exists():
            raise VpubError("already_exists", "Manifest already exists; no overwrite.")
        chosen = args.platforms.split(",")
        if not chosen or set(chosen) - set(PLATFORMS) or len(chosen) != len(set(chosen)):
            raise VpubError("invalid_platform", "Choose a unique subset of supported platforms.")
        if args.draft_only and set(chosen) - {"instagram", "x"}:
            raise VpubError("invalid_submission_mode", "--draft-only currently supports only Instagram and X. Use an explicit private setting for other supported routes.")
        from .core import language_tag
        value = template(args.video, language_tag(args.language))
        options = value["platforms"]
        options.update(x={"account": "default", "method": "native", "visibility": "account", "ai_generated": True},
                       instagram={"account": "default", "method": "native", "visibility": "account", "ai_generated": True})
        value["platforms"] = {p: options[p] for p in chosen}
        if args.draft_only:
            for options in value["platforms"].values():
                options["submission_mode"] = "draft_only"
        atomic_json(out, value)
        return {"manifest": str(out.resolve()), "next": "Edit titles, declarations and visibility, then run vpub plan."}
    if args.command in {"plan", "publish"}:
        selected = args.platforms.split(",") if args.platforms else None
        plan = load_plan(args.manifest, selected)
        if args.command == "plan" or args.dry_run:
            return {**plan, "mode": "LOCAL_DRY_RUN", "platform_writes": 0}
        return publish_plan(plan, args.confirm)
    if args.command == "stop":
        atomic_json(home() / "STOP.json", {"reason": args.reason, "at": now()})
        return {"stopped": True}
    ledger = Ledger()
    try:
        if args.command == "receipts":
            return ledger.rows()
        if args.command == "adopt":
            plan = load_plan(args.manifest, [args.platform])
            opts = plan["platforms"][args.platform]
            identity = adapters.preflight(args.platform, opts)
            receipt = adapters.verify(args.platform, opts["account"], {"id": args.id, "method": opts.get("method"), "state": "EXISTING_VERIFIED"})
            if receipt["state"] != "SUBMISSION_VERIFIED":
                raise VpubError("not_verified", "Existing work could not be verified.")
            key = ledger.claim(args.platform, opts["account"], plan["video_sha256"], {})
            if not key:
                raise VpubError("already_exists", "A publication record already exists; use verify.")
            record = {"receipt": receipt, "identity": identity, "options": opts,
                      "association": "Operator-supplied mapping; remote video bytes were not compared."}
            ledger.save(key, "EXISTING_VERIFIED", record)
            return {"key": key, **record}
        old = ledger.get(args.key)
        if not old:
            raise VpubError("not_found", "Receipt key not found.")
        receipt = old["data"].get("receipt", {"title": old["data"].get("title")})
        result = adapters.verify(old["platform"], old["account"], receipt)
        if result["state"] == "BROWSER_VERIFICATION_REQUIRED":
            return {"key": args.key, **result}
        old["data"]["receipt"] = result
        ledger.save(args.key, result["state"], old["data"])
        return {"key": args.key, **result}
    finally:
        ledger.close()


def status_selected(platforms, wait_seconds=0, method=None):
    results = {}
    for platform in platforms:
        try:
            selected_method = method or {"bilibili": "cli", "douyin": "browser"}.get(platform, "native")
            if platform in {"douyin", "youtube"} and selected_method == "browser":
                from .auth_jobs import wait
                job = wait(platform, wait_seconds)
                if job and job.get("state") != "LOGGED_IN":
                    results[platform] = job
                    continue
            results[platform] = adapters.auth_status(platform, "default", selected_method)
        except VpubError as e:
            results[platform] = {"logged_in": False, "code": e.code, "hint": e.hint}
    return results


def login_selected(platforms, method="native"):
    results = {}
    for platform in platforms:
        try:
            if method == "native":
                results[platform] = adapters.auth_login(platform, "default", method="native")
                continue
            results[platform] = adapters.auth_status(platform, "default", method)
        except VpubError:
            try:
                results[platform] = adapters.auth_login(platform, "default", method=method)
            except VpubError as e:
                results[platform] = {"state": "FAILED", "code": e.code, "hint": e.hint}
    return results


def capabilities():
    return {"product": "PublishOps CrossCast", "version": __version__,
            "metadata_targets": {p: "zh-Hans" if p in {"bilibili", "douyin"} else "en" for p in PLATFORMS},
            "cover_fitting": "Local contain-and-pad artwork; actual UI may crop. X Studio original-ratio thumbnail saved and posted by host UI; dedicated CLI Studio cover command not implemented.",
            "instagram_media": "Instagram preparation preserves original video bytes and ratio. Use a separate upload cover and shared Douyin portrait through the mobile camera-roll picker; no inserted lead-in or transcoding.",
            "instagram_deletion": "Explicit native deletion request, UI result/absence evidence, retained history, and separately authorized same-video cover replacement. No standalone deletion API.",
            "email": "Optional, off by default; host Gmail/Outlook send tool required at execution; no SMS.",
            "chapters": "Native Bilibili/Douyin/YouTube handoff, timing validation and saved-control evidence. Douyin titles limited to 12 UTF-16 units; four live chapters submitted; user confirmed mobile visibility, full desktop read-back pending.",
            "youtube_category": "Explicit per-video category required; host-model selection, in-place revision and read-back. API validates assignability before upload; no People & Blogs fallback.",
            "playlists": "YouTube native selections or optional API membership with explicit write scope.",
            "draft_only": "Instagram/X: init --draft-only. Host must stop before Share/Post/scheduling. PREVIEW_READY and DRAFT_SAVED are unpublished observations; CLI does not control browser buttons.",
            "platforms": {p: {"native_login": True,
             "publish_backend": {"bilibili": "bili-cli or native", "douyin": "experimental browser or native", "youtube": "native (default) or optional API"}.get(p, "native host-agent workflow"),
             "standalone_cli_publish": p in {"bilibili", "youtube", "douyin"},
            "live_upload_verified": p in {"youtube", "x", "instagram"}} for p in PLATFORMS},
            "live_verification_scope": "2026-10-29: historical YouTube private upload, X draft/post and Instagram original publication verified in host UI. X Studio direct upload, saved 16:9 cover and newly authorized status read back; native AI badge not observed there, text disclosure retained. Instagram cover-lead version shared after user removed prior test, new ID read back. Operator phone recordings support selecting a separate portrait from camera roll; the ineffective one-second lead experiment is withdrawn. Full playback remains unverified.",
            "native_execution": "The host agent performs real UI actions. A CLI handoff alone is not an executed action or confirmed login.",
            "bili_cli": "Use installed bili directly for its existing search, video, inbox, comment and creator-data commands.",
            "planned_common_commands": ["account", "search", "video", "works", "analytics", "comments", "inbox"],
            "suspended_platforms": {"tiktok": "Support is currently suspended; see docs/PLATFORMS.md."},
            "not_implemented": "Unified non-Bilibili read/interaction commands, X/Instagram official API clients, and YouTube cards/clickable end screens.",
            "interaction_rule": "Login/setup does not authorize comments, messages, likes or publication."}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        args = parser().parse_args()
        value = execute(args)
        ok = not (isinstance(value, dict) and value.get("all_submissions_verified") is False)
        print(json.dumps({"ok": ok, "data": value}, ensure_ascii=False))
        raise SystemExit(0 if ok else 4)
    except VpubError as e:
        print(json.dumps({"ok": False, "error": {"code": e.code, "message": e.message, "hint": e.hint}}, ensure_ascii=False))
        raise SystemExit(2)
    except (KeyboardInterrupt, Exception) as e:
        # OAuth exceptions may embed tokens/URLs: never serialize their bodies.
        print(json.dumps({"ok": False, "error": {"code": "interrupted" if isinstance(e, KeyboardInterrupt) else "internal_error",
                                                 "message": type(e).__name__,
                                                 "hint": "Check setup/receipts. An interrupted upload must not be blindly retried."}}))
        raise SystemExit(3)

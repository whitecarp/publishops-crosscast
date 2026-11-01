"""Real platform adapters; no platform write occurs during plan/doctor."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib
import importlib.util
import json
import os
import subprocess
import sys

from . import progress, safety
from .core import HELD_BACK, NOT_ON_SALE, VpubError, stop_check, platform_check

ROUTES = {"bilibili": {"cli"}, "douyin": {"browser"}, "youtube": {"browser", "api"}, "x": {"browser", "api"}, "instagram": {"browser", "api"}}
BILI_MAIN = "from bili_cli.main import main; main()"


def bili(args, timeout=210, program=BILI_MAIN):
    # bili-cli stops a stuck command after 180 s and says why, so give it time to report that.
    stop_check("bilibili")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # The existing local wrapper applies this HTTPX compatibility fix too.
    for key in ("NO_PROXY", "no_proxy"):
        if key in env:
            env[key] = ",".join(x for x in env[key].split(",") if ":" not in x)
    try:
        cp = subprocess.run([sys.executable, "-c", program, *args],
                            capture_output=True, text=True, encoding="utf-8", env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise VpubError("backend_timeout", "bili-cli did not finish in time.", "Inspect the original work before any retry; do not repeat a submission.") from None
    try:
        result = json.loads(cp.stdout)
    except ValueError:
        raise VpubError("backend_error", "bili-cli did not return valid JSON.", "Run bili schema to diagnose; do not repeat a submission.")
    if not result.get("ok"):
        e = result.get("error", {})
        # bili-cli's own spacing between writes says when to try again. Bilibili never saw the request, so it has not objected.
        if e.get("code") == "rate_limited" and (e.get("detail") or {}).get("limit"):
            raise VpubError(HELD_BACK, e.get("message", "bili-cli held this write back."), e.get("hint", ""))
        safety.pushed_back("bilibili", e.get("code"))
        raise VpubError(e.get("code", "backend_error"), e.get("message", "bili-cli failed"), e.get("hint", ""))
    return result.get("data", {})


def paid_api():
    """Official API routes for X, Instagram and YouTube need the separate paid package."""
    try:
        import crosscast_api
    except ImportError:
        raise VpubError("paid_route_required", "Official API routes for X, Instagram and YouTube need the paid CrossCast API add-on.",
                        f"Use the free default route. {NOT_ON_SALE}") from None
    return crosscast_api


def paid_feature(module, label):
    """A feature outside the free core, loaded from the paid add-on."""
    try:
        return importlib.import_module("crosscast_api." + module)
    except ModuleNotFoundError as error:
        if not (error.name or "").startswith("crosscast_api"):
            raise
    raise VpubError("paid_feature_required", f"{label} is part of the paid CrossCast add-on.", f"{NOT_ON_SALE} Everything else keeps working without it.")


def email_reminder(key, platform, episode):
    """Queue a reminder when the add-on is installed. Without it the person sees the notice inline."""
    if not addon_installed():
        return {"state": "NOT_INSTALLED", "email_sent": False, "inline_notice_required": True}
    return paid_feature("notifications", "Email reminders").enqueue(key, platform, episode)


def email_status():
    if not addon_installed():
        return {"enabled": False, "available": False, "note": f"Email reminders are part of the paid CrossCast add-on. {NOT_ON_SALE}"}
    return paid_feature("notifications", "Email reminders").onboarding()


def youtube_api():
    paid_api()
    from crosscast_api import youtube
    return youtube


def youtube_playlists():
    """Playlist commands: the official API with the add-on, otherwise YouTube's own pages."""
    if addon_installed():
        return youtube_api().playlists
    from . import youtube_playlists as free
    return free


def yt_service(account):
    return youtube_api().client(account)


def yt_credentials(account):
    return youtube_api().credentials(account)


def publish_youtube(*args, **kwargs):
    return youtube_api().publish(*args, **kwargs)


def finish_youtube(*args, **kwargs):
    return youtube_api().finish(*args, **kwargs)


def addon_installed():
    return importlib.util.find_spec("crosscast_api") is not None


def default_method(platform):
    if platform == "youtube" and addon_installed():
        return "api"
    return {"bilibili": "cli"}.get(platform, "browser")


def browser_timeout(action):
    from .browser_broker import SIGN_IN_SECONDS
    from .browser_worker import action_timeout
    # Leaves room for the sign-in check and saving the session, so a slow page is not reported as an interrupted command.
    return action_timeout(action) + SIGN_IN_SECONDS + 30


def hidden_desktop_call(payload, timeout):
    """YouTube on Windows. The steps of one command share one Chrome on the hidden desktop, which closes itself soon after the last of them."""
    from . import browser_broker
    from .hidden_desktop import call_worker
    if browser_broker.enabled():
        if payload["action"] in browser_broker.REUSABLE:
            result = browser_broker.call(payload, timeout)
            if result is not None:
                return {"ok": True, "data": result}
        else:
            progress.stage("waiting for the browser", payload.get("stages_path"))
            browser_broker.release(payload["platform"], payload["account"])
    return call_worker(payload, timeout)


def worker_call(payload, timeout):
    progress.stage("starting the browser", payload.get("stages_path"))
    try:
        cp = subprocess.run([sys.executable, "-m", "video_publisher.browser_worker"],
                            input=json.dumps(payload), capture_output=True, encoding="utf-8",
                            timeout=timeout,
                            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    except subprocess.TimeoutExpired:
        raise VpubError("browser_timeout", "Browser command timed out; existing receipts are retained.", "Inspect the existing draft/work; do not repeat an upload or final submit.")
    try:
        return json.loads(cp.stdout)
    except ValueError:
        raise VpubError("browser_error", "Browser command returned no valid JSON receipt.")


def browser_call(platform, action, account, **kwargs):
    """Every browser route ends here, so a platform's warning always stops what follows, whichever route brought it."""
    stop_check(platform)
    payload = {"platform": platform, "action": action, "account": account, **kwargs}
    try:
        with progress.relay(platform, payload):
            return browser_answer(platform, action, account, payload)
    except VpubError as exc:
        safety.pushed_back(platform, exc.code)
        raise


def browser_answer(platform, action, account, payload):
    from . import browser_broker
    # YouTube's background browser is a real Chrome window, so it never lingers where a window could show: see hidden_desktop_call for Windows.
    if platform != "youtube" and browser_broker.enabled():
        if action in browser_broker.REUSABLE:
            result = browser_broker.call(payload, browser_timeout(action))
            if result is not None:
                return result
        else:
            progress.stage("waiting for the browser", payload.get("stages_path"))
            browser_broker.release(platform, account)
    result = hidden_desktop_call(payload, browser_timeout(action)) if platform == "youtube" and os.name == "nt" else worker_call(payload, browser_timeout(action))
    if not result.get("ok"):
        e = result["error"]
        raise VpubError(e["code"], e["message"], e.get("hint", ""))
    return result["data"]


def auth_status(platform, account, method=None):
    platform_check(platform)
    stop_check(platform)
    method = method or default_method(platform)
    if method == "native":
        from .native_browser import login
        return login(platform)
    if method not in ROUTES[platform]:
        raise VpubError("invalid_method", "This backend is unavailable on the selected platform.")
    if platform == "bilibili":
        v = bili(["auth", "status"])
        if not v.get("logged_in"):
            raise VpubError("not_logged_in", "Bilibili is not logged in.", "vpub auth login bilibili")
        return {"logged_in": True, "account": account, "identity": str(v["user"]["mid"]), "name": v["user"].get("name")}
    if platform == "youtube" and method == "api":
        return youtube_api().status(account)
    if method == "api":
        return paid_api().status(platform, account)
    from .browser_runtime import identity_path, profile_dir, remember_identity
    if identity_path(platform, account).exists():
        return browser_call(platform, "status", account)
    # A sign-in can finish after its window stopped being watched, leaving a working profile with no record. The site decides.
    profile = profile_dir(platform, account)
    if profile.is_dir() and any(profile.iterdir()):
        try:
            found = browser_call(platform, "status", account)
        except VpubError as exc:
            if exc.code != "auth_expired":
                raise
        else:
            remember_identity(platform, account, found["identity"])
            return found
    raise VpubError("not_logged_in", "No verified independent browser login.", f"Run vpub auth login {platform} --method browser.")


def auth_login(platform, account, client_json=None, method=None):
    platform_check(platform)
    stop_check(platform, signing_in=True)
    method = method or default_method(platform)
    if method == "native":
        from .native_browser import login
        return login(platform)
    if method not in ROUTES[platform]:
        raise VpubError("invalid_method", "This login method is unavailable on the selected platform.")
    if platform == "bilibili":
        return bili(["auth", "login"])
    if platform == "youtube" and method == "api":
        return youtube_api().login(account, client_json)
    if method == "api":
        return paid_api().login(platform, account, client_json)
    from .auth_jobs import start
    return start(platform, account)


def preflight(platform, options):
    if options.get("method") == "native":
        return {"logged_in": None, "method": "native", "note": "Account must be checked in the built-in browser before browser begin."}
    if platform == "youtube" and options.get("method") == "api" and options.get("playlist_ids"):
        youtube_api().check_playlist_scope(options)
    identity = auth_status(platform, options["account"], options.get("method"))
    expected = options.get("expected_uid") if platform == "bilibili" else options.get("expected_channel_id") if platform == "youtube" else options.get("expected_identity")
    if expected and str(expected).casefold() != str(identity.get("identity")).casefold():
        raise VpubError("wrong_account", "The authorized account differs from the manifest.")
    return identity


def publish_bilibili(plan, options, checkpoint):
    if options.get("submission_mode") == "draft_only":
        from .drafts_bilibili import save
        return save(plan, options, checkpoint)
    # bili-cli ticks "含AI生成内容" by default, so ordinary videos must opt out explicitly.
    args = ["upload", "video", plan["video"], "--title", options["title"],
            "--desc", options["description"],
            "--ai-declare" if options["ai_generated"] else "--no-ai-declare"]
    for tag in options["tags"]:
        args += ["--tag", tag]
    if plan.get("cover"):
        args += ["--cover", plan["cover"]]
    if options.get("tid"):
        args += ["--tid", str(options["tid"])]
    if options["visibility"] == "private":
        args += ["--private"]
    if options.get("schedule"):
        args += ["--schedule", options["schedule"]]
    if options.get("season_id"):
        args += ["--season", str(options["season_id"])]
    v = bili(args, timeout=3600)
    bvid = v.get("bvid")
    if not bvid:
        return {"state": "SUBMITTED_UNVERIFIED", "reason": "Backend returned no BV identifier."}
    receipt = {"state": "SUBMITTED", "id": bvid, "url": f"https://www.bilibili.com/video/{bvid}",
               "expected_ai_declare": options["ai_generated"], "expected_private": options["visibility"] == "private"}
    if options.get("schedule"):
        receipt["expected_schedule"] = options["schedule"]
    if options.get("season_id"):
        # The upload stands even if adding it to the collection failed; the receipt says so.
        receipt["season"] = {"id": options["season_id"], "added": v.get("season_added") is True, "error": v.get("season_error")}
    if options.get("chapters"):
        receipt.update(pending_chapters=options["chapters"], chapters_state="PENDING")
    checkpoint(receipt)
    return verify("bilibili", options["account"], receipt, checkpoint)


def moment(text):
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def bilibili_differences(receipt, info):
    """What Bilibili's own record says against what this upload asked for. Only something it reports can differ, so an older receipt says nothing."""
    differs = []
    for name, expected, actual in (("ai_declaration", receipt.get("expected_ai_declare"), info.get("ai_declare")), ("visibility", receipt.get("expected_private"), info.get("private"))):
        if isinstance(expected, bool) and isinstance(actual, bool) and actual != expected:
            differs.append(name)
    # Before its time, only the time on record proves it was scheduled rather than gone out already.
    when = receipt.get("expected_schedule")
    if when and datetime.now(timezone.utc) < moment(when) and not (info.get("scheduled_time") and abs((moment(info["scheduled_time"]) - moment(when)).total_seconds()) < 60):
        differs.append("schedule")
    return differs


def publish(platform, plan, options, checkpoint):
    if options.get("method") == "native":
        from .native_browser import queue
        return queue(platform, plan, options)
    landscape = plan.get("covers", {}).get(platform, {}).get("landscape")
    if landscape:
        plan = {**plan, "cover": landscape["path"], "cover_sha256": landscape["sha256"]}
    if platform == "bilibili":
        return publish_bilibili(plan, options, checkpoint)
    if platform == "youtube" and options.get("method") == "api":
        return youtube_api().publish(plan, options, checkpoint)
    if options.get("method") == "api":
        return paid_api().publish(platform, plan, options, checkpoint)
    result = browser_call(platform, "publish", options["account"],
                          expected_identity=options.get("expected_identity"), plan={**plan, "options": options})
    checkpoint(result)
    return result


def verify(platform, account, receipt, checkpoint=None):
    if receipt.get("method") == "native":
        return {**receipt, "state": "BROWSER_VERIFICATION_REQUIRED", "note": "Re-open the original work in the same built-in browser and record actual UI evidence. No CLI platform check was performed."}
    if receipt.get("draft_title"):
        from .drafts_bilibili import verify as verify_draft
        return verify_draft(receipt)
    video_id = receipt.get("id")
    if receipt.get("method") == "browser" and platform in {"douyin", "instagram", "x", "youtube"}:
        return browser_call(platform, "verify", account, expected_identity=receipt.get("identity"), receipt=receipt)
    if not video_id:
        raise VpubError("receipt_missing", "No platform video ID; inspect the original submission before retrying.")
    if platform in {"x", "instagram"}:
        return paid_api().verify(platform, account, receipt)
    if platform == "bilibili":
        info = bili(["upload", "status", video_id])
        result = {**receipt, "state": "SUBMISSION_VERIFIED", "platform_status": info,
                  "note": "Creator record read back; review/public visibility may still be pending."}
        differs = bilibili_differences(receipt, info if isinstance(info, dict) else {})
        if differs:
            result.update(state="SUBMITTED_UNVERIFIED", readback_differs=differs,
                          note="Bilibili recorded something other than the plan asked for (see readback_differs). Correct it on this existing work "
                               "(bili upload edit); do not upload again.")
        if "ai_declaration" in differs:
            result.update(declaration_mismatch=True,
                          note="Bilibili recorded a different AI-content declaration. Correct it on this existing work "
                               "(bili upload edit with --ai-declare or --no-ai-declare); do not upload again.")
        from .edits_bilibili import finish_chapters
        return finish_chapters(result, checkpoint)
    return youtube_api().verify_video(account, receipt, video_id)

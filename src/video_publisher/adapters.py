"""Real platform adapters; no platform write occurs during plan/doctor."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

from .core import VpubError, stop_check, platform_check

ROUTES = {"bilibili": {"cli"}, "douyin": {"browser"}, "youtube": {"browser", "api"}, "x": {"browser", "api"}, "instagram": {"browser", "api"}}


def bili(args, timeout=210):
    # bili-cli stops a stuck command after 180 s and says why, so give it time to report that.
    stop_check("bilibili")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # The existing local wrapper applies this HTTPX compatibility fix too.
    for key in ("NO_PROXY", "no_proxy"):
        if key in env:
            env[key] = ",".join(x for x in env[key].split(",") if ":" not in x)
    try:
        cp = subprocess.run([sys.executable, "-c", "from bili_cli.main import main; main()", *args],
                            capture_output=True, text=True, encoding="utf-8", env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise VpubError("backend_timeout", "bili-cli did not finish in time.", "Inspect the original work before any retry; do not repeat a submission.") from None
    try:
        result = json.loads(cp.stdout)
    except ValueError:
        raise VpubError("backend_error", "bili-cli did not return valid JSON.", "Run bili schema to diagnose; do not repeat a submission.")
    if not result.get("ok"):
        e = result.get("error", {})
        raise VpubError(e.get("code", "backend_error"), e.get("message", "bili-cli failed"), e.get("hint", ""))
    return result.get("data", {})


def paid_api():
    """Official API routes for X, Instagram and YouTube need the separate paid package."""
    try:
        import crosscast_api
    except ImportError:
        raise VpubError("paid_route_required", "Official API routes for X, Instagram and YouTube need the paid CrossCast API add-on.",
                        "Use the free default route, or install the add-on.") from None
    return crosscast_api


def youtube_api():
    paid_api()
    from crosscast_api import youtube
    return youtube


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
    from .browser_worker import action_timeout
    # Leaves room for the sign-in check, so a slow page is not reported as an interrupted command.
    return action_timeout(action) + 120


def hidden_desktop_call(payload, timeout):
    from .hidden_desktop import call_worker
    return call_worker(payload, timeout)


def worker_call(payload, timeout):
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
    payload = {"platform": platform, "action": action, "account": account, **kwargs}
    from . import browser_broker
    # YouTube's background browser is a real Chrome window, so it closes after each command instead of lingering.
    if platform != "youtube" and browser_broker.enabled():
        if action in browser_broker.REUSABLE:
            result = browser_broker.call(payload, browser_timeout(action))
            if result is not None:
                return result
        else:
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
    from .browser_runtime import profile_dir
    if not (profile_dir(platform, account).parent / (platform + "-identity.json")).exists():
        raise VpubError("not_logged_in", "No verified independent browser login.", f"Run vpub auth login {platform} --method browser.")
    return browser_call(platform, "status", account)


def auth_login(platform, account, client_json=None, method=None):
    platform_check(platform)
    stop_check(platform)
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
    v = bili(args, timeout=3600)
    bvid = v.get("bvid")
    if not bvid:
        return {"state": "SUBMITTED_UNVERIFIED", "reason": "Backend returned no BV identifier."}
    receipt = {"state": "SUBMITTED", "id": bvid, "url": f"https://www.bilibili.com/video/{bvid}",
               "expected_ai_declare": options["ai_generated"]}
    checkpoint(receipt)
    return verify("bilibili", options["account"], receipt)


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


def verify(platform, account, receipt):
    if receipt.get("method") == "native":
        return {**receipt, "state": "BROWSER_VERIFICATION_REQUIRED", "note": "Re-open the original work in the same built-in browser and record actual UI evidence. No CLI platform check was performed."}
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
        # bili-cli's status reports the recorded AI-content declaration; it must match the plan.
        expected = receipt.get("expected_ai_declare")
        actual = info.get("ai_declare") if isinstance(info, dict) else None
        if isinstance(expected, bool) and isinstance(actual, bool) and actual != expected:
            result.update(state="SUBMITTED_UNVERIFIED", declaration_mismatch=True,
                          note="Bilibili recorded a different AI-content declaration. Correct it on this existing work "
                               "(bili upload edit with --ai-declare or --no-ai-declare); do not upload again.")
        return result
    return youtube_api().verify_video(account, receipt, video_id)

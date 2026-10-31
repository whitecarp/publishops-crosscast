"""Real platform adapters; no platform write occurs during plan/doctor."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from .core import VpubError, atomic_json, config, home, read_json, stop_check, platform_check

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/youtube.force-ssl"]
PLAYLIST_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
_YT_CLIENTS = threading.local()


def bili(args, timeout=180):
    stop_check("bilibili")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # The existing local wrapper applies this HTTPX compatibility fix too.
    for key in ("NO_PROXY", "no_proxy"):
        if key in env:
            env[key] = ",".join(x for x in env[key].split(",") if ":" not in x)
    cp = subprocess.run([sys.executable, "-c", "from bili_cli.main import main; main()", *args],
                        capture_output=True, text=True, encoding="utf-8", env=env, timeout=timeout)
    try:
        result = json.loads(cp.stdout)
    except ValueError:
        raise VpubError("backend_error", "bili-cli did not return valid JSON.", "Run bili schema to diagnose; do not repeat a submission.")
    if not result.get("ok"):
        e = result.get("error", {})
        raise VpubError(e.get("code", "backend_error"), e.get("message", "bili-cli failed"), e.get("hint", ""))
    return result.get("data", {})


def yt_credentials(account):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    path = home() / "accounts" / account / "youtube-token.json"
    if not path.exists():
        raise VpubError("not_logged_in", "YouTube authorization is missing.", "vpub auth login youtube --client-json PATH")
    creds = Credentials.from_authorized_user_file(str(path))
    if not creds.valid:
        if not creds.refresh_token:
            raise VpubError("auth_expired", "YouTube needs browser authorization again.")
        try:
            creds.refresh(Request())
        except Exception:
            raise VpubError("auth_expired", "YouTube token refresh failed.", "Run auth login again; Testing-mode tokens may expire after 7 days.")
        atomic_json(path, json.loads(creds.to_json()))
    return creds


def yt_service(account):
    from googleapiclient.discovery import build
    # httplib2 connections are not thread-safe, so each worker thread keeps its own client.
    path = home() / "accounts" / account / "youtube-token.json"
    stamp = (str(path), path.stat().st_mtime_ns) if path.exists() else None
    cached = getattr(_YT_CLIENTS, "value", None)
    if stamp and cached and cached[0] == stamp and cached[1].valid:
        return cached[2]
    creds = yt_credentials(account)
    service = build("youtube", "v3", credentials=creds, cache_discovery=False)
    if path.exists():
        _YT_CLIENTS.value = ((str(path), path.stat().st_mtime_ns), creds, service)
    return service


def youtube_login(account, client_json, manage_playlists=False):
    if not client_json:
        raise VpubError("oauth_setup_required", "YouTube requires a Desktop OAuth client JSON on first setup.",
                        "Enable YouTube Data API v3 in Google Cloud; create a Desktop OAuth client. Then run vpub auth login youtube --client-json PATH.")
    from google_auth_oauthlib.flow import InstalledAppFlow
    # All features CrossCast exposes are explained and authorized on first connection.
    # Keep manage_playlists as a backwards-compatible alias, without duplicate scopes.
    scopes = list(dict.fromkeys(SCOPES))
    flow = InstalledAppFlow.from_client_secrets_file(str(Path(client_json).resolve()), scopes,
                                                    autogenerate_code_verifier=True)
    # OAuth URLs and progress belong on stderr, never mixed with the JSON result.
    with contextlib.redirect_stdout(sys.stderr):
        creds = flow.run_local_server(host="127.0.0.1", port=0, timeout_seconds=300, open_browser=False,
                                      authorization_prompt_message="Open this Google authorization URL: {url}",
                                      success_message="Authorization received. You can close this tab.",
                                      access_type="offline", prompt="consent")
    atomic_json(home() / "accounts" / account / "youtube-token.json", json.loads(creds.to_json()))
    return auth_status("youtube", account, "api")


def dy_worker(action, account, plan=None, timeout=180):
    payload = {"action": action, "account": account, "plan": plan}
    cp = subprocess.run([sys.executable, "-m", "video_publisher.douyin_worker"],
                        input=json.dumps(payload), capture_output=True, encoding="utf-8", timeout=timeout,
                        env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    try:
        result = json.loads(cp.stdout)
    except ValueError:
        raise VpubError("backend_error", "Douyin adapter did not return a receipt.", "Inspect creator center before any retry.")
    if not result.get("ok"):
        e = result["error"]
        raise VpubError(e["code"], e["message"], e.get("hint", ""))
    return result["data"]


def default_method(platform):
    return {"bilibili": "cli", "youtube": "api"}.get(platform, "browser")


def browser_timeout(action):
    return 1900 if action in {"publish", "resume", "read_all"} else 600 if action in {'edit','verify','operations_batch','chat_unlock','message_sources','discover_works','adopt_work'} else 120


def browser_call(platform, action, account, **kwargs):
    payload = {"platform": platform, "action": action, "account": account, **kwargs}
    from .browser_session import current
    session = current()
    if session is not None:
        return session.call(payload)
    from . import browser_broker
    if browser_broker.enabled():
        if action in browser_broker.REUSABLE:
            result = browser_broker.call(payload, browser_timeout(action))
            if result is not None:
                return result
        else:
            browser_broker.release(platform, account)
    try:
        cp = subprocess.run([sys.executable, "-m", "video_publisher.browser_worker"],
                            input=json.dumps(payload), capture_output=True, encoding="utf-8",
                            timeout=browser_timeout(action),
                            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    except subprocess.TimeoutExpired:
        raise VpubError("browser_timeout", "Browser command timed out; existing receipts are retained.", "Inspect the existing draft/work; do not repeat an upload or final submit.")
    try:
        result = json.loads(cp.stdout)
    except ValueError:
        raise VpubError("browser_error", "Browser command returned no valid JSON receipt.")
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
    supported = {"bilibili": {"cli"}, "douyin": {"browser"}, "youtube": {"api"}, "x": {"browser", "api"}, "instagram": {"browser", "api"}}
    if method not in supported[platform]:
        raise VpubError("invalid_method", "This backend is unavailable on the selected platform.")
    if platform == "bilibili":
        v = bili(["auth", "status"])
        if not v.get("logged_in"):
            raise VpubError("not_logged_in", "Bilibili is not logged in.", "vpub auth login bilibili")
        return {"logged_in": True, "account": account, "identity": str(v["user"]["mid"]), "name": v["user"].get("name")}
    if platform == "youtube":
        response = yt_service(account).channels().list(part="id,snippet", mine=True).execute()
        items = response.get("items", [])
        if len(items) != 1:
            raise VpubError("channel_required", "Expected exactly one authorized YouTube channel.", "Create/select the intended YouTube channel and authorize again.")
        return {"logged_in": True, "account": account, "identity": items[0]["id"], "name": items[0]["snippet"]["title"]}
    if method == "api":
        from . import api_backends
        return api_backends.status(platform, account)
    from .browser_runtime import profile_dir
    if not (profile_dir(platform, account).parent / (platform + "-identity.json")).exists():
        raise VpubError("not_logged_in", "No verified independent browser login.", f"Run vpub auth login {platform} --method browser.")
    return browser_call(platform, "status", account)


def auth_login(platform, account, client_json=None, method=None, manage_playlists=False):
    platform_check(platform)
    stop_check(platform)
    method = method or default_method(platform)
    if method == "native":
        from .native_browser import login
        return login(platform)
    supported = {"bilibili": {"cli"}, "douyin": {"browser"}, "youtube": {"api"}, "x": {"browser", "api"}, "instagram": {"browser", "api"}}
    if method not in supported[platform]:
        raise VpubError("invalid_method", "This login method is unavailable on the selected platform.")
    if platform == "bilibili":
        return bili(["auth", "login"])
    if platform == "youtube":
        return youtube_login(account, client_json, manage_playlists)
    if method == "api":
        from . import api_backends
        return api_backends.login(platform, account, client_json)
    from .auth_jobs import start
    return start(platform, account)


def preflight(platform, options):
    if options.get("method") == "native":
        return {"logged_in": None, "method": "native", "note": "Account must be checked in the built-in browser before browser begin."}
    if platform == "youtube" and options.get("playlist_ids"):
        creds = yt_credentials(options["account"])
        if not any(creds.has_scopes([s]) for s in (PLAYLIST_SCOPE, "https://www.googleapis.com/auth/youtube", "https://www.googleapis.com/auth/youtubepartner")):
            raise VpubError("playlist_scope_required", "Playlist writes require an additional optional OAuth scope, before uploading.", "Use native mode or explicitly reauthorize with --manage-playlists; existing permissions are not expanded automatically.")
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


def publish_youtube(plan, options, checkpoint, *, max_chunks=None):
    landscape = plan.get('covers', {}).get('youtube', {}).get('landscape')
    if landscape:
        plan = {**plan, 'cover':landscape['path'], 'cover_sha256':landscape['sha256']}
    if plan.get('cover'):
        from .core import file_sha
        if file_sha(plan['cover']) != plan.get('cover_sha256'):
            raise VpubError('source_changed', 'The prepared cover changed; no upload or thumbnail write attempted.')
    from .categories import validate_youtube
    category_id = validate_youtube(options.get("category_id"))
    from googleapiclient.http import MediaFileUpload
    service = yt_service(options["account"])
    owners = service.channels().list(part='id', mine=True).execute().get('items', [])
    if len(owners) != 1 or (options.get('expected_channel_id') and options['expected_channel_id'] != owners[0]['id']):
        raise VpubError('wrong_account', 'YouTube upload account does not match the prepared channel.')
    categories = service.videoCategories().list(part="snippet", id=category_id).execute().get("items", [])
    if not any(c.get("id") == category_id and c.get("snippet", {}).get("assignable") is True for c in categories):
        raise VpubError("invalid_category", "YouTube does not currently allow the selected category; no video was uploaded.")
    status = {"privacyStatus": options["visibility"],
              "selfDeclaredMadeForKids": options["made_for_kids"],
              "containsSyntheticMedia": options["ai_generated"]}
    if options.get("schedule"):
        status["publishAt"] = options["schedule"]
    body = {"snippet": {"title": options["title"], "description": options["description"],
                        "tags": options["tags"], "categoryId": category_id}, "status": status}
    req = service.videos().insert(part="snippet,status", body=body,
                                 media_body=MediaFileUpload(plan["video"], chunksize=8 * 1024 * 1024, resumable=True))
    from .upload_resume import run
    postprocess = {'cover': 'PENDING' if plan.get('cover') else 'NOT_REQUESTED',
                   'playlist_ids': list(options.get('playlist_ids', []))}
    def upload_checkpoint(value):
        checkpoint({**value, 'postprocess': postprocess, 'expected_category_id': category_id})
    try:
        result = run(req, plan, options['account'], owners[0]['id'], upload_checkpoint, max_chunks=max_chunks)
    finally:
        req.resumable.stream().close()
    if result['state'] == 'UPLOAD_PAUSED':
        return {**result, 'postprocess': postprocess, 'expected_category_id': category_id}
    video_id = result.get("id")
    if not video_id:
        return {"state": "SUBMITTED_UNVERIFIED", "reason": "Upload completed without a video ID."}
    receipt = {**result, 'postprocess': postprocess, "state": "SUBMITTED", "method": "api", "id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}", "expected_category_id": category_id}
    checkpoint(receipt)  # persist the video ID before optional cover/verification calls
    return finish_youtube(plan, options, receipt, checkpoint)


def finish_youtube(plan, options, receipt, checkpoint):
    """Complete only never-attempted post-upload steps on the same owned video."""
    from .core import file_sha, stop_check
    from googleapiclient.http import MediaFileUpload
    stop_check('youtube')
    landscape = plan.get('covers', {}).get('youtube', {}).get('landscape')
    if landscape:
        plan = {**plan, 'cover': landscape['path'], 'cover_sha256': landscape['sha256']}
    steps = receipt['postprocess']
    if steps['cover'] == 'PENDING' and (not plan.get('cover') or file_sha(plan['cover']) != plan.get('cover_sha256')):
        raise VpubError('source_changed', 'Prepared cover changed; uploaded video retained without another write.')
    service = yt_service(options['account'])
    owners = service.channels().list(part='id', mine=True).execute().get('items', [])
    videos = service.videos().list(part='snippet', id=receipt['id']).execute().get('items', [])
    if len(owners) != 1 or len(videos) != 1 or videos[0]['snippet'].get('channelId') != owners[0]['id'] or (options.get('expected_channel_id') and options['expected_channel_id'] != owners[0]['id']):
        raise VpubError('wrong_account', 'Post-upload steps require the original video and channel.')
    if steps['cover'] == 'PENDING':
        steps['cover'] = 'SUBMITTING'
        checkpoint(receipt)
        media = None
        try:
            stop_check('youtube')
            media = MediaFileUpload(plan['cover'])
            value = service.thumbnails().set(videoId=receipt['id'], media_body=media).execute()
            steps['cover'] = 'APPLIED' if value.get('items') else 'UNKNOWN'
        except Exception:
            steps['cover'] = 'UNKNOWN'
        finally:
            if media is not None:
                media.stream().close()
        checkpoint(receipt)
    if steps['cover'] in {'SUBMITTING', 'UNKNOWN'}:
        receipt['warning'] = 'Video exists; thumbnail outcome is uncertain. Inspect it; resume will not resend the thumbnail.'
    if steps.get('playlist_ids'):
        stop_check('youtube')
        ensure_youtube_playlists(service, receipt['id'], steps['playlist_ids'], receipt, checkpoint)
    verified = verify("youtube", options["account"], receipt)
    actual = verified.get("visibility")
    if actual and actual != options["visibility"]:
        verified["state"] = "VISIBILITY_MISMATCH"
        verified["expected_visibility"] = options["visibility"]
    return verified


def ensure_youtube_playlists(service, video_id, playlist_ids, receipt, checkpoint):
    """Add only missing memberships. Persist before each write; never retry it."""
    results = receipt.setdefault("playlists", {})
    for playlist_id in playlist_ids:
        try:
            items = service.playlistItems().list(part="id", playlistId=playlist_id, videoId=video_id, maxResults=1).execute().get("items", [])
            if items:
                results[playlist_id] = {"state": "VERIFIED", "item_id": items[0]["id"]}
                checkpoint(receipt)
                continue
            if playlist_id in results:
                # An earlier timeout or interrupted insert might still settle.
                results[playlist_id]["state"] = "UNKNOWN"
                checkpoint(receipt)
                continue
            results[playlist_id] = {"state": "ADDING"}
            checkpoint(receipt)
            item = service.playlistItems().insert(part="snippet", body={"snippet": {
                "playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
            results[playlist_id] = {"state": "ADDED_UNVERIFIED", "item_id": item.get("id")}
            checkpoint(receipt)
            actual = service.playlistItems().list(part="id", playlistId=playlist_id, videoId=video_id, maxResults=1).execute().get("items", [])
            if actual:
                results[playlist_id] = {"state": "VERIFIED", "item_id": actual[0]["id"]}
        except Exception:
            results[playlist_id] = {**results.get(playlist_id, {}), "state": "UNKNOWN"}
        checkpoint(receipt)
    return results


def publish(platform, plan, options, checkpoint):
    if options.get("method") == "native":
        from .native_browser import queue
        return queue(platform, plan, options)
    landscape = plan.get("covers", {}).get(platform, {}).get("landscape")
    if landscape:
        plan = {**plan, "cover": landscape["path"], "cover_sha256": landscape["sha256"]}
    if platform == "bilibili":
        return publish_bilibili(plan, options, checkpoint)
    if platform == "youtube":
        return publish_youtube(plan, options, checkpoint)
    if options.get("method") == "api":
        from . import api_backends
        return api_backends.publish(platform, plan, options, checkpoint)
    result = browser_call(platform, "publish", options["account"],
                          expected_identity=options.get("expected_identity"), plan={**plan, "options": options})
    checkpoint(result)
    return result


def verify(platform, account, receipt):
    if receipt.get("method") == "native":
        return {**receipt, "state": "BROWSER_VERIFICATION_REQUIRED", "note": "Re-open the original work in the same built-in browser and record actual UI evidence. No CLI platform check was performed."}
    video_id = receipt.get("id")
    if receipt.get("method") == "browser" and platform in {"douyin", "instagram", "x"}:
        return browser_call(platform, "verify", account, expected_identity=receipt.get("identity"), receipt=receipt)
    if not video_id:
        raise VpubError("receipt_missing", "No platform video ID; inspect the original submission before retrying.")
    if platform in {"x", "instagram"}:
        from . import api_backends
        return api_backends.verify(platform, account, receipt)
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
    items = yt_service(account).videos().list(part="status,snippet,processingDetails", id=video_id).execute().get("items", [])
    if not items:
        raise VpubError("not_found", "Uploaded video is not yet visible to the authorized account.")
    item = items[0]
    status = item.get("status", {})
    failed = status.get("uploadStatus") in {"failed", "rejected", "deleted"}
    playlist_results = receipt.get("playlists", {})
    for playlist_id in playlist_results:
        try:
            members = yt_service(account).playlistItems().list(part="id", playlistId=playlist_id, videoId=video_id, maxResults=1).execute().get("items", [])
            playlist_results[playlist_id] = {"state": "VERIFIED", "item_id": members[0]["id"]} if members else {"state": "UNVERIFIED"}
        except Exception:
            playlist_results[playlist_id] = {"state": "UNKNOWN"}
    category_id = item.get("snippet", {}).get("categoryId")
    category_mismatch = bool(receipt.get("expected_category_id") and category_id != receipt["expected_category_id"])
    postprocess = receipt.get('postprocess', {})
    pending_steps = (postprocess.get('cover', 'NOT_REQUESTED') not in {'APPLIED', 'NOT_REQUESTED'}
                     or any(playlist_results.get(pid, {}).get('state') != 'VERIFIED' for pid in postprocess.get('playlist_ids', [])))
    incomplete = pending_steps or any(x["state"] != "VERIFIED" for x in playlist_results.values()) or bool(receipt.get("warning")) or category_mismatch
    return {**receipt, "state": "PLATFORM_REJECTED" if failed else "SUBMITTED_UNVERIFIED" if incomplete else "SUBMISSION_VERIFIED",
            "visibility": status.get("privacyStatus"), "upload_status": status.get("uploadStatus"),
            "processing_status": item.get("processingDetails", {}).get("processingStatus"),
            "publish_at": status.get("publishAt"), "channel_id": item.get("snippet", {}).get("channelId"),
            "category_id": category_id, "category_mismatch": category_mismatch,
            "note": "Video ID read back; public playback depends on privacy, processing and platform review."}

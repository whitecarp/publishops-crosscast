"""Real platform adapters; no platform write occurs during plan/doctor."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys

from . import progress, safety
from .core import HELD_BACK, MENTION_HOW, NOT_ON_SALE, NothingSent, VpubError, stop_check, platform_check, real_window

ROUTES = {"bilibili": {"cli"}, "douyin": {"browser"}, "youtube": {"browser", "api"}, "x": {"browser", "api"}, "instagram": {"browser", "api"}}
BILI_MAIN = "from bili_cli.main import main; main()"


def bili(args, timeout=210, program=BILI_MAIN, signing_in=False):
    # bili-cli stops a stuck command after 180 s and says why, so give it time to report that.
    stop_check("bilibili", signing_in=signing_in)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # The existing local wrapper applies this HTTPX compatibility fix too.
    for key in ("NO_PROXY", "no_proxy"):
        if key in env:
            env[key] = ",".join(x for x in env[key].split(",") if ":" not in x)
    try:
        # bili-cli's own spacing then sleeps through gaps of up to a minute, so a batch just works. A longer one still comes back as write_spacing.
        cp = subprocess.run([sys.executable, "-c", program, *(["--wait"] if program == BILI_MAIN else []), *args],
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
        # 62003 is good news: approved and held for its scheduled release, not an objection.
        if re.search(r"(?<!\d)62003(?!\d)", str(e.get("message", ""))):
            raise VpubError("not_public_yet", "Bilibili approved this video and is holding it for its scheduled release (62003), so it can't be read or changed until then. Nothing is wrong.",
                            "Try again after its release time.")
        # Right after an upload Bilibili still reports the length as 0 seconds, so chapters and widgets look out of range.
        if e.get("code") == "invalid_argument" and re.search(r"超出视频时长\s*0\s*秒", str(e.get("message", ""))):
            e = {**e, "message": f"{e['message']} Bilibili hasn't finished processing this video, so it still reports its length as 0 seconds. Nothing was changed.",
                 "hint": "Try again in a few minutes; a real run set the same chapters about four minutes later."}
        # 79025 暂不支持跳转他人稿件 reads as an ownership problem. A field run's link pointed at a video Bilibili wouldn't show (62002).
        if re.search(r"(?<!\d)79025(?!\d)", str(e.get("message", ""))):
            e = {**e, "message": f"{e['message']} Bilibili links a widget only to this account's own videos that it can show. It gives this answer for another account's video, "
                                 "and also for one that isn't visible, such as a deleted or private one. Nothing was added.",
                 "hint": "Open the target video on Bilibili. If it doesn't play, or isn't this account's own, choose another."}
        if re.search(r"(?<!\d)20082(?!\d)", str(e.get("message", ""))):
            e = {**e, "message": f"{e['message']} This account has as many collections as Bilibili allows. Nothing was created.",
                 "hint": "Reuse or delete one of its collections first."}
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
    """A real Chrome window on Windows. YouTube's steps of one command share one Chrome on the hidden desktop, which closes itself soon after the last of them. Douyin's commands that need one have a Chrome each."""
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
        raise VpubError("browser_timeout", "Browser command timed out; existing receipts are retained.", "Read the attempt's receipt, and look on the platform before running it again. Whether it kept a draft isn't known. Do not repeat an upload or final submit.")
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
    # A real Chrome window never lingers where a window could show: see hidden_desktop_call for Windows.
    windowed = real_window(platform, action)
    if not windowed and browser_broker.enabled():
        if action in browser_broker.REUSABLE:
            result = browser_broker.call(payload, browser_timeout(action))
            if result is not None:
                return result
        else:
            progress.stage("waiting for the browser", payload.get("stages_path"))
            browser_broker.release(platform, account)
    result = hidden_desktop_call(payload, browser_timeout(action)) if windowed and os.name == "nt" else worker_call(payload, browser_timeout(action))
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


def auth_login(platform, account, client_json=None, method=None, keep_open=False):
    platform_check(platform)
    stop_check(platform, signing_in=True)
    # From a review: with the YouTube API add-on installed, YouTube's usual sign-in isn't the browser one, and --keep-open means the browser's window.
    method = method or ("browser" if keep_open else default_method(platform))
    if keep_open and (method != "browser" or "browser" not in ROUTES[platform]):
        raise VpubError("invalid_option", "--keep-open is for a dedicated browser's sign-in window.")
    if method == "native":
        from .native_browser import login
        return login(platform)
    if method not in ROUTES[platform]:
        raise VpubError("invalid_method", "This login method is unavailable on the selected platform.")
    if platform == "bilibili":
        return bili(["auth", "login"], signing_in=True)
    if platform == "youtube" and method == "api":
        return youtube_api().login(account, client_json)
    if method == "api":
        return paid_api().login(platform, account, client_json)
    from .auth_jobs import start
    return start(platform, account, keep_open=keep_open)


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


def bilibili_upload(plan, options):
    """The bili upload video command for this plan. Its dry run and the upload itself are the same command."""
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
    # A real contest entry on 2026-11-05 had to go round vpub for want of these, and no later edit can add a topic.
    if options.get("topic"):
        args += ["--topic", options["topic"]]
    if plan.get("cover43"):
        args += ["--cover43", plan["cover43"]]
    for uid in options.get("mention_uids") or []:
        args += ["--desc-at", str(uid)]
    return args


def check_bilibili_upload(args, options):
    """bili-cli's own dry run of the upload, which only reads, when a topic or mentions depend on what Bilibili says.

    The upload itself would stop on a topic Bilibili can't find, but only after this attempt was recorded, and that record keeps
    the same video from being tried again. A topic can't be added afterwards, and a mention whose @nickname the description
    doesn't hold would change the approved words, so both are settled here, where a refusal sends nothing."""
    try:
        answer = bili(["--dry-run", *args])
    except VpubError as error:
        raise dry_run_refused(error) from None
    if not isinstance(answer, dict) or answer.get("dry_run") is not True:
        # Without its dry run's mark this may have been the upload itself, so it isn't one to try again.
        raise VpubError("backend_error", "bili-cli's dry run of this upload didn't answer as a dry run, so nothing more was sent.",
                        "Look at the account's uploads before anything else. Don't upload this video again.")
    found = (answer.get("params") or {}).get("topic")
    # bili-cli takes a topic whose name matches with spaces and capitals ignored, but only the exact name was approved.
    if options.get("topic") and not (isinstance(found, dict) and found.get("name") == options["topic"]):
        raise NothingSent("topic_name_differs", "bili-cli's dry run found no topic named exactly as the plan writes it, so nothing was uploaded.",
                          "Copy the name exactly as its topic or contest page shows it, spaces and capitals included, then approve the new plan.")
    meta = answer.get("meta") or {}
    # bili-cli trims the description and writes each mention as @nickname and a space, putting any it can't find at the start.
    if options.get("mention_uids") and str(meta.get("desc") or "").strip() != options["description"].strip():
        places = unwritten_mentions(options["description"], options["mention_uids"], meta.get("desc_v2"))
        raise NothingSent("mention_not_written", "bili-cli would change Bilibili's description to fit its mentions, so nothing was uploaded.",
                          MENTION_HOW + (f" Check mention_uids {', '.join(places)}." if places else "") + " Then approve the new plan.")
    return answer


def check_upload(platform, plan, options):
    """bili-cli's dry run of a Bilibili upload with a topic or mentions, asked before vpub publish records the attempt. Returns the upload it checked, or None.

    It can take three and a half minutes, and one cut off after the record would leave what looks like an upload that may have gone out."""
    if (platform != "bilibili" or options.get("method") == "native" or options.get("submission_mode") == "draft_only"
            or not (options.get("topic") or options.get("mention_uids"))):
        return None
    args = bilibili_upload(upload_plan(platform, plan), options)
    check_bilibili_upload(args, options)
    return args


def dry_run_refused(error):
    """A refusal from bili-cli's dry run, which sent nothing. Its words for a topic or a person it can't find quote the plan back, so those are put another way."""
    if error.code == "not_found" and "话题" in error.message:
        return NothingSent("topic_not_found", "Bilibili found no topic by the plan's name, so nothing was uploaded.",
                           "Copy the name exactly as its topic or contest page shows it, then approve the new plan.")
    if error.code == "not_found" and "UID" in error.message:
        return NothingSent("mention_not_found", "Bilibili has no account for one of the plan's mention_uids, so nothing was uploaded.",
                           "Check each UID against the number in that person's space link, then approve the new plan.")
    # CrossCast's own words for a bili-cli that didn't answer warn against sending again, which a dry run can't have done.
    # A UID with no account at all comes back as Bilibili's own refusal, not as the words above, and bili-cli's hint for it is about video and comment IDs.
    hint = ("Run it again later." if error.code in {"backend_timeout", "backend_error"} else
            "Check each UID in mention_uids and any collection ID, then approve the new plan." if error.code == "not_found" else error.hint or "")
    return NothingSent(error.code, error.message, f"Nothing was uploaded: this was bili-cli's dry run, which only reads. {hint}".strip())


def unwritten_mentions(description, uids, segments):
    """Which of mention_uids, as #1 counting from 1, the description doesn't hold as @nickname and a space, going by the nicknames bili-cli's dry run found.
    A real contest entry on 2026-11-05 mixed up the @ or the space with its full-width look-alike, so each says when it is that."""
    names = {str(part.get("biz_id")): str(part.get("raw_text") or "") for part in segments or [] if isinstance(part, dict) and part.get("type") == 2}
    text, places = description.strip(), []
    for place, uid in enumerate(uids, 1):
        name = names.get(str(uid))
        if name and re.search(re.escape("@" + name) + r"(?: |\Z)", text):
            continue
        slip = (" (full-width ＠)" if name and "＠" + name in text else
                " (no ordinary space after it)" if name and re.search(re.escape("@" + name) + r"[^\S ]", text) else "")
        places.append(f"#{place}{slip}")
    return places


def publish_bilibili(plan, options, checkpoint):
    if options.get("submission_mode") == "draft_only":
        from .drafts_bilibili import save
        return save(plan, options, checkpoint)
    args = bilibili_upload(plan, options)
    # vpub publish asks it before recording the attempt and hands over the upload that passed. Any other upload is asked here, still before it is sent.
    if (options.get("topic") or options.get("mention_uids")) and plan.get("checked_upload") != args:
        check_bilibili_upload(args, options)
    v = bili(args, timeout=3600)
    found = v.get("topic") if isinstance(v.get("topic"), dict) else {}
    # bili upload status doesn't report a topic, so this answer is the only record of the topic, and any contest, the video entered.
    joined = {"topic": {"name": found.get("name") or options["topic"], "id": found.get("id"), "mission_id": found.get("mission_id")}} if options.get("topic") else {}
    bvid = v.get("bvid")
    if not bvid:
        return {"state": "SUBMITTED_UNVERIFIED", "reason": "Backend returned no BV identifier.", **joined}
    receipt = {"state": "SUBMITTED", "id": bvid, "url": f"https://www.bilibili.com/video/{bvid}",
               "expected_ai_declare": options["ai_generated"], "expected_private": options["visibility"] == "private", **joined}
    if options.get("schedule"):
        receipt["expected_schedule"] = options["schedule"]
    if options.get("season_id"):
        # The upload stands even if adding it to the collection failed; the receipt says so.
        receipt["season"] = {"id": options["season_id"], "added": v.get("season_added") is True, "error": v.get("season_error")}
    if options.get("mention_uids"):
        receipt["mention_uids"] = list(options["mention_uids"])
    if options.get("chapters"):
        receipt.update(pending_chapters=options["chapters"], chapters_state="PENDING")
    if options.get("widgets"):
        receipt.update(pending_widgets=options["widgets"], widgets_state="PENDING")
    checkpoint(receipt)
    return verify("bilibili", options["account"], receipt, checkpoint)


def topic_kept(topic, work):
    """Whether Bilibili's own record of the work holds the topic, and any contest, that the upload sent, or None if that can't be read.
    bili upload status leaves both out, but the archive view under it has them: bili-cli's edit copies them from there, as the web editor does."""
    from .edits_bilibili import archive
    try:
        found = archive(work)
    except VpubError:
        return None
    return str(found.get("topic_id")) == str(topic["id"]) and (not topic.get("mission_id") or str(found.get("mission_id")) == str(topic["mission_id"]))


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


def upload_plan(platform, plan):
    """The plan with the covers this platform's upload sends: its own landscape one, and on Bilibili the 4:3 one too."""
    landscape = plan.get("covers", {}).get(platform, {}).get("landscape")
    if landscape:
        plan = {**plan, "cover": landscape["path"], "cover_sha256": landscape["sha256"]}
    cover43 = plan.get("covers", {}).get(platform, {}).get("cover43") if platform == "bilibili" else None
    if cover43:
        plan = {**plan, "cover43": cover43["path"]}
    return plan


def publish(platform, plan, options, checkpoint):
    if options.get("method") == "native":
        from .native_browser import queue
        return queue(platform, plan, options)
    plan = upload_plan(platform, plan)
    if platform == "bilibili":
        return publish_bilibili(plan, options, checkpoint)
    if platform == "youtube" and options.get("method") == "api":
        return youtube_api().publish(plan, options, checkpoint)
    if options.get("method") == "api":
        return paid_api().publish(platform, plan, options, checkpoint)
    result = browser_call(platform, "publish", options["account"],
                          expected_identity=options.get("expected_identity"), plan={**plan, "options": options})
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
        if isinstance(receipt.get("topic"), dict) and receipt["topic"].get("id"):
            result["topic_kept"] = kept = topic_kept(receipt["topic"], video_id)
            result["note"] += {True: " Bilibili's record keeps the topic and any contest this upload entered.",
                               False: " Bilibili's record doesn't show the topic this upload sent, or its contest. A topic can't be added afterwards, "
                                      "so look at the work on Bilibili before anything else; don't upload it again.",
                               None: " Whether Bilibili kept the topic couldn't be read; vpub verify reads it again."}[kept]
        from .edits_bilibili import finish_chapters
        from .widgets_bilibili import finish as finish_widgets, seen as widgets_seen
        result = finish_widgets(finish_chapters(result, checkpoint), checkpoint)
        return {**result, "widgets_seen": widgets_seen(video_id, result.get("widgets"))}
    return youtube_api().verify_video(account, receipt, video_id)

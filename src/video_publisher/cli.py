from __future__ import annotations

import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from . import __version__, adapters
from .core import Ledger, PLATFORMS, VpubError, atomic_json, config, file_sha, home, load_plan, now, publication_lock, read_json, snapshot_file, stop_check, platform_check

RISK_CODES = {"risk_control", "captcha_required", "rate_limited"}
# The command finished, but the work it was asked to do did not.
UNFINISHED_STATES = {"INCOMPLETE", "INTERRUPTED", "UPLOAD_PAUSED", "UPLOAD_INITIATING", "UPLOAD_SESSION_CHECK", "UPLOADING", "UPLOAD_INTERRUPTED", "UPLOAD_EXPIRED", "UPLOAD_UNVERIFIED",
                     "DM_READY", "DM_SUBMITTING", "DM_UNVERIFIED", "DM_FAILED", "COMMENT_READY", "COMMENT_SUBMITTING", "COMMENT_UNVERIFIED", "COMMENT_FAILED",
                     "ACTION_READY", "ACTION_SUBMITTING", "ACTION_UNVERIFIED", "ACTION_FAILED", "PARTIAL", "WAITING_FOR_USER", "PREPARATION_INCOMPLETE", "SUBMITTED_UNVERIFIED", "UNKNOWN",
                     "EDIT_SUBMITTING", "EDIT_SUBMITTED_UNVERIFIED", "EDIT_UNAVAILABLE", "REPLY_READY", "REPLY_PENDING", "REPLY_SUBMITTING", "REPLY_UNVERIFIED", "REPLY_REJECTED",
                     "TEST_SEED_UNVERIFIED", "TEST_REPLY_UNVERIFIED", "TEST_COMMENT_NOT_VISIBLE"}
# Commands whose --out saves the JSON result. For the others, --out names the file the command produces.
SAVES_RESULT = {"dm", "read-all", "collection", "playlist", "messages", "engage", "ops", "comments", "stats", "report", "works", "status", "cleanup"}


def snapshot_plan(plan, platform):
    """Copy what this platform will upload. Anything unverifiable stops here, before a claim exists."""
    snap = home() / "jobs" / plan["confirmation"]

    def copy(source, name, sha):
        return str(snapshot_file(source, snap / (name + Path(source).suffix.lower()), sha))

    safe = {**plan, "video": copy(plan["video"], "video", plan["video_sha256"]),
            "cover": copy(plan["cover"], "cover", plan["cover_sha256"]) if plan.get("cover") else None}
    if plan.get("covers"):
        safe["covers"] = deepcopy(plan["covers"])
        for role, asset in safe["covers"].get(platform, {}).items():
            asset["path"] = copy(asset["path"], platform + "-" + role, asset["sha256"])
    return safe


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
                    from .language_policy import route
                    language=opts.get('content_language','und')
                    if language.lower().split('-')[0] not in {'und','mul','zxx'}:
                        policy=route(language,platform)
                        if opts.get('metadata_mode','target_only')!=policy['mode']:
                            raise VpubError('metadata_required','Prepare source plus platform-language copy before uploading.', 'Run vpub metadata request, let the host model supply both sections, then metadata apply.')
                    identity = adapters.preflight(platform, opts)
                    if opts.get('method') == 'browser':
                        opts = {**opts, 'expected_identity': identity['identity']}
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
                try:
                    safe_plan = snapshot_plan(plan, platform)
                except (VpubError, OSError) as e:
                    results[platform] = {"state": "NOT_STARTED", "code": e.code if isinstance(e, VpubError) else "snapshot_failed",
                                         "message": e.message if isinstance(e, VpubError) else type(e).__name__,
                                         "hint": "Nothing was uploaded to this platform. Check the files and free disk space, then create a fresh plan."}
                    continue
                record = {"plan_hash": plan["confirmation"], "title": opts["title"], "identity": identity,
                          "options": opts, "approved_plan": plan, "upload_plan": safe_plan, "started_at": now()}
                claimed = ledger.claim(platform, account, plan["video_sha256"], record)
                if not claimed:
                    results[platform] = {"state": "SKIPPED_EXISTING", "key": key}
                    continue

                def checkpoint(receipt):
                    record["receipt"] = receipt
                    ledger.save(key, receipt["state"], record)

                try:
                    receipt = adapters.publish(platform, safe_plan, opts, checkpoint)
                    checkpoint(receipt)
                    if receipt["state"] == "WAITING_FOR_USER":
                        from .notifications import enqueue
                        enqueue(key, platform, 1)
                    results[platform] = {"key": key, **receipt}
                except Exception as e:
                    code = e.code if isinstance(e, VpubError) else "backend_error"
                    if code == "browser_busy" and not record.get("receipt"):
                        # The worker was refused the profile lock before it opened a page.
                        ledger.forget(key)
                        results[platform] = {"state": "NOT_STARTED", "code": code, "message": e.message, "hint": e.hint}
                        continue
                    # A durable platform ID always survives a failed read-back.
                    receipt = record.get("receipt", {})
                    durable = home() / "browser-receipts" / (plan["confirmation"] + "-" + platform + ".json")
                    if not receipt.get("id") and durable.exists():
                        candidate = read_json(durable)
                        if candidate.get("platform") in {None, platform}:
                            receipt = candidate
                            record["receipt"] = receipt
                    state = "SUBMITTED_UNVERIFIED" if receipt.get("id") else receipt.get("state", "UNKNOWN")
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


def separate_bili_agent():
    """bili-cli ships inside this package now; an older separate install would own the same files."""
    import importlib.metadata
    try:
        return importlib.metadata.version("bili-agent")
    except importlib.metadata.PackageNotFoundError:
        return None


def included_bili_version():
    try:
        from bili_cli import __version__ as version
    except ImportError:
        return None
    return version


def doctor():
    from .overview import chrome_path, stop_state
    addon = adapters.addon_installed()
    modules = {name: importlib.util.find_spec(name) is not None for name in
               ("bili_cli", "patchright", *(("crosscast_api", "socks") if addon else ()))}
    chrome, stop = chrome_path(), stop_state()
    issues = [{"code": "dependency_missing", "message": f"Python package missing: {name}", "fix": "Reinstall CrossCast in this Python environment."}
              for name, found in modules.items() if not found]
    if not chrome:
        issues.append({"code": "chrome_missing", "message": "Google Chrome was not found.", "fix": "vpub setup --install-browser"})
    older = separate_bili_agent()
    if older:
        issues.append({"code": "separate_bili_agent", "message": f"A separate bili-agent {older} is still installed and owns the same files as the bili-cli inside CrossCast.",
                       "fix": "python -m pip uninstall -y bili-agent, then install CrossCast again in this environment."})
    if stop["active"]:
        issues.append({"code": "stopped", "message": stop["message"], "fix": stop["hint"]})
    return {"version": __version__, "python": sys.version.split()[0], "dependencies": modules,
            "bili_cli_version": included_bili_version(), "google_chrome": chrome, "paid_api_addon": addon, "ready": not issues, "issues": issues,
            "local_stop": (home() / "STOP.json").exists(), "runtime_directory": str(home()),
            "credentials_configured": {
                "youtube_api": (home() / "accounts/default/youtube-token.json").exists(),
                **{p: (home() / 'accounts/default' / (p+'-browser-session.json')).exists()
                   for p in ('douyin', 'instagram', 'x')}},
            "browser_requirement": "Google Chrome; vpub setup --install-browser installs it through Patchright when needed.",
            "native_browser": "Owned by the host application; CLI does not inspect or export its login state.",
            "note": "Local checks only. Installed does not mean logged in or live-publishing verified."}


def setup(external_stop=None, install_browser=False):
    if install_browser:
        subprocess.run([sys.executable, '-m', 'patchright', 'install', 'chrome'],
                       check=True, stdout=sys.stderr, stderr=sys.stderr)
    if external_stop:
        c = config()
        c["external_stop_file"] = str(Path(external_stop).resolve())
        atomic_json(home() / "config.json", c)
    return doctor()


def template(video, content_language="und", ai_generated=True):
    base = {"account": "default", "method": "native", "visibility": "public", "ai_generated": ai_generated}
    return {"schema_version": 1, "video": str(Path(video).resolve()), "content_language": content_language,
            "title": Path(video).stem, "description": "", "tags": ["AI"] if ai_generated else [],
            "platforms": {"bilibili": {**base}, "youtube": {**base, "visibility": "private", "made_for_kids": False},
                          "douyin": {**base, "title": "我的原创视频"}}}


def parser():
    p = argparse.ArgumentParser(prog="vpub", description="PublishOps CrossCast: multi-platform video release operations console.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Local dependency checks; no login or upload.")
    sub.add_parser("capabilities", help="Show actual backends and verification boundaries.")
    full = sub.add_parser('read-all', help='Complete supported paginated reads with durable API cursors.').add_subparsers(dest='action', required=True)
    q=full.add_parser('start');q.add_argument('platform',choices=PLATFORMS)
    q.add_argument('resource',choices=['works','comments','collections','collection-items'])
    q.add_argument('--work');q.add_argument('--collection');q.add_argument('--page-size',type=int,default=50)
    q.add_argument('--max-pages',type=int,default=100);q.add_argument('--out')
    for action in ('resume','status'):
        q=full.add_parser(action);q.add_argument('run_id');q.add_argument('--out')
        if action=='resume':q.add_argument('--max-pages',type=int,default=100)
    e=sub.add_parser('engage',help='Explicit work likes and saves; no automatic or bulk engagement.').add_subparsers(dest='action',required=True)
    q=e.add_parser('status');q.add_argument('platform',choices=PLATFORMS);q.add_argument('work');q.add_argument('--collection');q.add_argument('--out')
    q=e.add_parser('collections');q.add_argument('platform',choices=PLATFORMS);q.add_argument('--cursor');q.add_argument('--out')
    for action in ('like','save'):
        q=e.add_parser(action);q.add_argument('platform',choices=PLATFORMS);q.add_argument('work')
        q.add_argument('--remove',action='store_true');q.add_argument('--collection');q.add_argument('--dry-run',action='store_true');q.add_argument('--out')
        q.add_argument('--test-id',help='Explicit test label; retained and excluded from ordinary engagement reports.')
    q=e.add_parser('verify');q.add_argument('key');q.add_argument('--out')
    c = sub.add_parser('comments', help='Read comments and send direct single-language replies.').add_subparsers(dest='action',required=True)
    q=c.add_parser('collect',help='Bounded comment pages and visible reply expansion; no complete-history claim.')
    q.add_argument('platform',choices=PLATFORMS);q.add_argument('work');q.add_argument('--limit',type=int,default=50)
    q.add_argument('--pages',type=int,default=3);q.add_argument('--reply-pages',type=int,default=1);q.add_argument('--out')
    q=c.add_parser('review',help='YouTube owner review queue; not a full account inbox.')
    q.add_argument('platform',choices=['youtube']);q.add_argument('work');q.add_argument('--status',choices=['heldForReview','published'],default='heldForReview')
    q.add_argument('--limit',type=int,default=20);q.add_argument('--cursor');q.add_argument('--out')
    q=c.add_parser('moderate',help='Hold/approve an observed YouTube comment, without banning its author.')
    q.add_argument('platform',choices=['youtube']);q.add_argument('work');q.add_argument('comment_id')
    q.add_argument('--status',required=True,choices=['heldForReview','published']);q.add_argument('--dry-run',action='store_true');q.add_argument('--test-id');q.add_argument('--out')
    for action in ('status','like','pin','hide','edit','delete'):
        q=c.add_parser(action);q.add_argument('platform',choices=PLATFORMS);q.add_argument('work');q.add_argument('comment_id');q.add_argument('--out')
        if action!='status':q.add_argument('--dry-run',action='store_true');q.add_argument('--test-id')
        if action in {'like','pin','hide'}:q.add_argument('--remove',action='store_true')
        if action=='edit':
            body=q.add_mutually_exclusive_group(required=True);body.add_argument('--text');body.add_argument('--file')
    q=c.add_parser('verify-action');q.add_argument('key');q.add_argument('--out')
    q=c.add_parser('resume-action',help='Explicitly retry a pending hide/moderation receipt only after confirming its unchanged original state.');q.add_argument('key');q.add_argument('--out')
    d=sub.add_parser('dm',help='Explicit single-recipient private messages; no automatic or bulk sends.').add_subparsers(dest='action',required=True)
    q=d.add_parser('unlock',help='Open the official X Chat window for user-entered PIN; sends nothing.')
    q.add_argument('platform',choices=['x']);q.add_argument('--out')
    for action in ('list','read','send'):
        q=d.add_parser(action);q.add_argument('platform',choices=PLATFORMS);q.add_argument('--out')
        if action!='list':q.add_argument('recipient')
        if action!='send':q.add_argument('--limit',type=int,default=20)
        else:
            body=q.add_mutually_exclusive_group(required=True);body.add_argument('--text');body.add_argument('--file')
            q.add_argument('--language',required=True);q.add_argument('--dry-run',action='store_true');q.add_argument('--test-id')
    q=d.add_parser('verify');q.add_argument('key');q.add_argument('--out')
    q=c.add_parser('list')
    q.add_argument('platform',choices=PLATFORMS);q.add_argument('work')
    q.add_argument('--limit',type=int,default=20);q.add_argument('--cursor');q.add_argument('--out')
    q=c.add_parser('replies',help='Read a Bilibili/YouTube reply page; retain continuation cursor.')
    q.add_argument('platform',choices=['bilibili','youtube']);q.add_argument('work');q.add_argument('comment_id')
    q.add_argument('--limit',type=int,default=20);q.add_argument('--cursor');q.add_argument('--out')
    cl=sub.add_parser('collection',help='Instagram/X private collections and Douyin work collections.').add_subparsers(dest='action',required=True)
    for action in ('list','items','create','rename','delete'):
        q=cl.add_parser(action);q.add_argument('platform',choices=['instagram','x','douyin'])
        if action in ('items','rename','delete'):q.add_argument('collection')
        if action in ('create','rename'):q.add_argument('--title',required=True)
        if action=='create':q.add_argument('--cover');q.add_argument('--description')
        if action in ('create','rename','delete'):q.add_argument('--request-id',required=True)
        q.add_argument('--out')
    q=cl.add_parser('verify');q.add_argument('key');q.add_argument('--out')
    for action in ('add','remove'):
        q=cl.add_parser(action);q.add_argument('platform',choices=['douyin','instagram','x']);q.add_argument('collection');q.add_argument('work');q.add_argument('--request-id',required=True);q.add_argument('--out')
    pl=sub.add_parser('playlist',help='Owned YouTube playlists via official API.').add_subparsers(dest='action',required=True)
    for action in ('list','items'):
        q=pl.add_parser(action)
        if action=='items':q.add_argument('playlist')
        q.add_argument('--cursor');q.add_argument('--limit',type=int,default=50);q.add_argument('--out')
    q=pl.add_parser('verify');q.add_argument('key');q.add_argument('--out')
    for action in ('create','update','delete','add','remove','move'):
        q=pl.add_parser(action)
        if action!='create':q.add_argument('playlist')
        if action in ('create','update'):
            q.add_argument('--title',required=action=='create');q.add_argument('--description');q.add_argument('--privacy',choices=['private','unlisted','public'])
        if action=='add':q.add_argument('video')
        if action in ('remove','move'):q.add_argument('item')
        if action=='move':q.add_argument('--position',type=int,required=True)
        q.add_argument('--request-id',required=True);q.add_argument('--out')
    o=sub.add_parser('ops',help='Read-only watchlists and multi-platform collection.').add_subparsers(dest='action',required=True)
    m=sub.add_parser('messages',help='Unified local comments, mentions and DM previews; no automatic replies.').add_subparsers(dest='action',required=True)
    q=m.add_parser('sync');q.add_argument('--platforms',required=True);q.add_argument('--sources',default='comments,mentions,dm_preview')
    q.add_argument('--limit',type=int,default=20);q.add_argument('--pages',type=int,default=2);q.add_argument('--language',choices=['auto','zh','en'],default='auto');q.add_argument('--out')
    q.add_argument('--workers',type=int,default=3);q.add_argument('--progress',action='store_true');q.add_argument('--min-interval',type=int,default=300);q.add_argument('--full',action='store_true')
    q=m.add_parser('resume');q.add_argument('run_id');q.add_argument('--progress',action='store_true');q.add_argument('--out')
    q=m.add_parser('list');q.add_argument('--platform',choices=PLATFORMS);q.add_argument('--kind',choices=['comments','mentions','dm_preview']);q.add_argument('--identity')
    q.add_argument('--status',choices=['pending','all','unread','read','handled','deferred'],default='pending');q.add_argument('--limit',type=int,default=50)
    q.add_argument('--cursor');q.add_argument('--include-own',action='store_true');q.add_argument('--out')
    q=m.add_parser('mark');q.add_argument('key');q.add_argument('status',choices=['unread','read','handled','deferred']);q.add_argument('--out')
    q=o.add_parser('sync');q.add_argument('--platforms');q.add_argument('--limit',type=int,default=20)
    q.add_argument('--pages',type=int,default=2);q.add_argument('--language',choices=['auto','zh','en'],default='auto');q.add_argument('--out')
    q.add_argument('--reply-pages',type=int,default=1);q.add_argument('--reply-threads',type=int,default=5)
    q.add_argument('--min-interval',type=int,default=300,help='Reuse successful reads younger than this many seconds; 0 always reads.')
    q.add_argument('--full',action='store_true',help='Bypass freshness cache; still respects page budgets.')
    q.add_argument('--workers',type=int,default=3);q.add_argument('--progress',action='store_true')
    for action in ('resume','run'):
        q=o.add_parser(action);q.add_argument('run_id');q.add_argument('--out');q.add_argument('--progress',action='store_true')
    q=o.add_parser('runs');q.add_argument('--limit',type=int,default=20);q.add_argument('--out')
    q=o.add_parser('discover');q.add_argument('platform',choices=PLATFORMS)
    q.add_argument('--pages',type=int,default=3);q.add_argument('--limit',type=int,default=50);q.add_argument('--out')
    q=o.add_parser('adopt');q.add_argument('platform',choices=PLATFORMS);q.add_argument('work')
    q.add_argument('--pages',type=int,default=3);q.add_argument('--out')
    w=o.add_parser('watch').add_subparsers(dest='watch_action',required=True)
    w.add_parser('list')
    for action in ('add','remove'):
        q=w.add_parser(action);q.add_argument('platform',choices=PLATFORMS);q.add_argument('work')
    q=c.add_parser('reply',help='Send directly; --dry-run is an optional preview, not a required approval step.')
    q.add_argument('platform',choices=PLATFORMS);q.add_argument('work');q.add_argument('comment_id')
    body=q.add_mutually_exclusive_group(required=True)
    body.add_argument('--text');body.add_argument('--file')
    q.add_argument('--language',default='auto',help='Comment language, not report or platform language. One language only.')
    q.add_argument('--dry-run',action='store_true')
    q=c.add_parser('verify-reply');q.add_argument('key')
    q=c.add_parser('self-test',help='Explicit live test: post and reply with 你好 on your own work; never runs during setup.')
    q.add_argument('platform',choices=PLATFORMS);q.add_argument('work');q.add_argument('--test-id',required=True)
    q=sub.add_parser('stats',help='Read per-work platform metrics; unavailable values stay null.')
    q.add_argument('platform',choices=PLATFORMS);q.add_argument('work');q.add_argument('--out')
    q=sub.add_parser('report',help='Summarize actual local operation receipts in the user’s language; no email.')
    q.add_argument('--since',help='Timezone-aware ISO timestamp; default last 24 hours.')
    q.add_argument('--language',choices=['auto','zh','en'],default='auto')
    q.add_argument('--notes',help='Optional host-researched findings as text/source JSON objects; not commands.')
    q.add_argument('--out')
    q=sub.add_parser('preferences',help='Set report language from the host user’s language; replies follow comments separately.')
    q.add_argument('--language',choices=['auto','zh','en'],required=True)
    s = sub.add_parser("setup", help="Prepare local settings; optionally install Chrome. Does not log in or post.")
    s.add_argument("--external-stop-file")
    s.add_argument("--install-browser", action="store_true", help="Install Google Chrome through Patchright; may require OS permissions.")
    a = sub.add_parser("auth", help="Login or check each platform.").add_subparsers(dest="action", required=True)
    for action in ("login", "status"):
        q = a.add_parser(action)
        q.add_argument("platform", choices=[*PLATFORMS, "all"])
        q.add_argument("--account", default="default", choices=["default"])
        q.add_argument("--method", choices=["native", "cli", "browser", "api"], help="Default: bili-cli; YouTube Studio through an AI assistant, or the API with the paid add-on; a dedicated persistent browser for Douyin/Instagram/X.")
        if action == "login":
            q.add_argument("--client-json", help="YouTube Desktop OAuth client JSON, or app credentials for the paid X/Instagram API add-on; never commit this file.")
        else:
            q.add_argument("--wait", type=int, default=0, help="QR/panel completion wait, 0–180 seconds.")
    q = sub.add_parser("onboard", help="Prepare the backend and start login for selected platforms.")
    q.add_argument("--platforms", default="all", help="all, or a comma-separated list of platforms.")
    q.add_argument("--method", choices=["auto", "native"], default="auto")
    q = sub.add_parser("init", help="Create a publication manifest.")
    q.add_argument("--video", required=True)
    q.add_argument("--out", required=True)
    q.add_argument("--language", default="und", help="Content language (e.g. zh-Hans, zh-Hant, en). Preserves supplied text; does not translate video or metadata.")
    q.add_argument("--platforms", default="bilibili,youtube,douyin", help="Explicit selected platforms; adding an account does not enable publication.")
    q.add_argument("--draft-only", action="store_true", help="Instagram/X only: upload to preview or save an unsent draft; never Share/Post or schedule.")
    q.add_argument("--ai-generated", choices=["yes", "no"], default="yes",
                   help="Whether the video contains AI-generated content. Sets every platform's AI-content declaration; use no for ordinary videos.")
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
    q.add_argument("--provider", choices=["auto", "off", "gmail"], default="auto")
    q.add_argument("--to")
    q.add_argument("--inventory")
    q = n.add_parser("inspect", help="Detect the email provider and required host plugin setup; sends nothing.")
    q.add_argument("--email", required=True)
    q.add_argument("--inventory")
    q = n.add_parser("test", help="Queue one self-send TEST; does not enable ongoing alerts.")
    q.add_argument("--email", required=True)
    q.add_argument("--test-id", required=True)
    q.add_argument("--inventory", required=True)
    n.add_parser("pending")
    q = n.add_parser("claim")
    q.add_argument("id")
    q.add_argument("--available-tool", required=True)
    q.add_argument("--inventory", required=True)
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
    q = sub.add_parser("works", help="Read your works: API pages or browser visible first page.")
    q.add_argument("platform", choices=PLATFORMS)
    q.add_argument("--account", choices=["default"], default="default")
    q.add_argument('--limit',type=int,default=20);q.add_argument('--cursor');q.add_argument('--out')
    sub.add_parser("receipts", help="Read the local publication ledger.")
    q = sub.add_parser("status", help="What needs attention now, with the exact next command; local records only, no sign-in or platform request.")
    q.add_argument("--limit", type=int, default=20, help="Most attempts to list, 1-200.")
    q.add_argument("--out")
    q = sub.add_parser("cleanup", help="Preview private video copies of finished publications that are safe to remove; no platform request.")
    q.add_argument("--apply", action="store_true", help="Delete what the preview lists.")
    q.add_argument("--out")
    q = sub.add_parser("resume", help="Resume a saved YouTube upload session or inspect an existing browser attempt; never blindly resubmit.")
    q.add_argument("key")
    q = sub.add_parser("verify", help="Verify an existing work and complete its pending Douyin chapter step; never upload.")
    q.add_argument("key")
    q = sub.add_parser('edit', help='Correct an existing recorded work by ID; never upload/delete a video.')
    q.add_argument('key')
    q.add_argument('patch', help='JSON with expected_identity and replacement caption, Douyin portrait or chapters.')
    q.add_argument('--dry-run', action='store_true')
    q.add_argument('--verify-only', action='store_true', help='Read back the existing correction without saving; covers return evidence for visual inspection.')
    q = sub.add_parser("adopt", help="Associate an already-published video to avoid duplicate Bilibili uploads.")
    q.add_argument("manifest")
    q.add_argument("--platform", choices=["bilibili", "youtube"], required=True)
    q.add_argument("--id", required=True)
    q = sub.add_parser("stop", help="Pause all publication attempts from this tool.")
    q.add_argument("--reason", default="Operator paused publication")
    return p


def execute(args):
    result = run_command(args)
    if args.command in SAVES_RESULT and getattr(args, 'out', None):
        atomic_json(args.out, result)
    return result


def run_command(args):
    if args.command=='dm':
        from . import direct_messages as dm
        if args.action=='verify':result=dm.verify(args.key)
        elif args.action=='unlock':result=adapters.browser_call('x','chat_unlock','default',expected_identity=dm.identity('x'))
        elif args.action=='send':
            text=Path(args.file).read_text(encoding='utf-8-sig') if args.file else args.text
            result=dm.send(args.platform,args.recipient,text,args.language,args.dry_run,args.test_id)
        else:result=dm.read(args.platform,getattr(args,'recipient',None),args.limit)
        return result
    if args.command=='read-all':
        from . import full_read
        if args.action=='start':result=full_read.start(args.platform,args.resource,args.work,args.page_size,args.max_pages,args.collection)
        elif args.action=='resume':result=full_read.resume(args.run_id,args.max_pages)
        else:result=full_read.status(args.run_id)
        return result
    if args.command=='collection':
        from . import collections
        if args.action in {'list','items'}:result=collections.listing(args.platform,getattr(args,'collection',None))
        elif args.action=='verify':result=collections.verify(args.key)
        elif args.action in {'add','remove'}:result=collections.membership(args.platform,args.action,args.collection,args.work,args.request_id)
        else:result=collections.change(args.platform,args.action,getattr(args,'collection',None),getattr(args,'title',None),args.request_id,getattr(args,'cover',None),getattr(args,'description',None))
        return result
    if args.command=='playlist':
        playlists = adapters.youtube_api().playlists
        if args.action in {'list','items'}:result=playlists.listing(getattr(args,'playlist',None),args.cursor,args.limit)
        elif args.action=='verify':result=playlists.verify(args.key)
        else:result=playlists.change(args.action,**{k:getattr(args,k,None) for k in ('playlist','title','description','privacy','video','item','position','request_id')})
        return result
    if args.command=='messages':
        from . import messages
        if args.action=='sync':result=messages.sync(args.platforms,args.sources,args.limit,args.pages,args.language,args.min_interval,args.full,args.workers,args.progress)
        elif args.action=='resume':
            result=messages.resume(args.run_id,args.progress)
        elif args.action=='mark':result=messages.mark(args.key,args.status)
        else:result=messages.listing(args.platform,args.kind,args.status,args.limit,args.cursor,args.identity,args.include_own)
        return result
    if args.command=='engage':
        from . import engagement
        if args.action=='status':result=engagement.status(args.platform,args.work,args.collection)
        elif args.action=='collections':result=engagement.collections(args.platform,args.cursor)
        elif args.action=='verify':result=engagement.verify(args.key)
        else:result=engagement.set_action(args.platform,args.work,args.action,not args.remove,args.collection,args.dry_run,args.test_id)
        return result
    if args.command=='ops':
        from . import operations_sync, operations_runs, work_catalog
        if args.action=='sync':result=operations_runs.sync(args.platforms,args.limit,args.pages,args.language,args.reply_pages,args.reply_threads,args.min_interval,args.full,args.workers,args.progress)
        elif args.action=='resume':result=operations_runs.resume(args.run_id,args.progress)
        elif args.action=='run':result=operations_runs.status(args.run_id)
        elif args.action=='runs':result=operations_runs.listing(args.limit)
        elif args.action=='discover':result=work_catalog.discover(args.platform,args.limit,args.pages)
        elif args.action=='adopt':result=work_catalog.adopt(args.platform,args.work,args.pages)
        else:result=operations_sync.watch(args.watch_action,getattr(args,'platform',None),getattr(args,'work',None))
        return result
    if args.command in {'comments','stats','report','preferences'}:
        from . import operations
        if args.command=='comments':
            if args.action in {'status','like','pin','hide','edit','delete','verify-action','resume-action','moderate'}:
                from . import comment_actions
                if args.action=='status':result=comment_actions.status(args.platform,args.work,args.comment_id)
                elif args.action=='verify-action':result=comment_actions.verify(args.key)
                elif args.action=='resume-action':result=comment_actions.resume(args.key)
                else:
                    desired=args.status if args.action=='moderate' else (Path(args.file).read_text(encoding='utf-8-sig') if args.file else args.text) if args.action=='edit' else not getattr(args,'remove',False)
                    result=comment_actions.mutate(args.platform,args.work,args.comment_id,args.action,desired,args.dry_run,args.test_id)
            elif args.action=='collect':
                from .comment_collection import budgets
                from .operations_sync import collect
                budgets(args.limit,args.pages,args.reply_pages);operations.work_id(args.platform,args.work)
                if args.platform in {'bilibili','youtube'}:
                    identity=operations.backend(args.platform,'stats',args.work)['identity']
                    result=collect(args.platform,args.work,identity,args.limit,args.pages,args.reply_pages)
                else:
                    value=operations.backend(args.platform,'comments',args.work,limit=args.limit,pages=args.pages,reply_pages=args.reply_pages)
                    result=operations.save_observation(args.platform,args.work,'comments',value)
            elif args.action=='review':
                operations.work_id(args.platform,args.work)
                if not 1<=args.limit<=100 or args.cursor and len(args.cursor)>1000:raise VpubError('invalid_limit','Invalid review page limit/cursor.')
                value=operations.backend(args.platform,'comments',args.work,limit=args.limit,cursor=args.cursor,moderation_status=args.status)
                result=operations.save_observation(args.platform,args.work,'comments',value)
            elif args.action=='list': result=operations.observe(args.platform,args.work,'comments',args.limit,args.cursor)
            elif args.action=='replies':result=operations.observe(args.platform,args.work,'replies',args.limit,args.cursor,args.comment_id)
            elif args.action=='reply':
                text=Path(args.file).read_text(encoding='utf-8-sig') if args.file else args.text
                result=operations.reply(args.platform,args.work,args.comment_id,text,args.language,args.dry_run)
            elif args.action=='self-test': result=operations.self_test(args.platform,args.work,args.test_id)
            else: result=operations.verify_reply(args.key)
        elif args.command=='stats': result=operations.observe(args.platform,args.work,'stats')
        elif args.command=='preferences': result=operations.set_language(args.language)
        else: result=operations.report(args.since,args.language,args.notes)
        return result
    if args.command == 'edit':
        from .edits import run
        return run(args.key, args.patch, args.dry_run, args.verify_only)
    if args.command == "works":
        from .operations_sync import works
        result=works(args.platform,args.limit,args.cursor)
        return result
    if args.command == "resume":
        with publication_lock():
            ledger = Ledger()
            try:
                row = ledger.get(args.key)
                if not row:
                    raise VpubError('invalid_resume', 'Use an existing upload receipt key.')
                record = row['data']
                if row['platform'] == 'youtube' and record.get('options', {}).get('method') == 'api':
                    receipt = record.get('receipt', {})
                    options = {**record['options'], 'expected_channel_id': record['identity']['identity']}
                    def checkpoint(value):
                        record['receipt'] = value
                        ledger.save(args.key, value['state'], record)
                    if receipt.get('id'):
                        if receipt.get('postprocess'):
                            result = adapters.finish_youtube(record['upload_plan'], options, receipt, checkpoint)
                        else:
                            result = adapters.verify('youtube', row['account'], receipt)
                    else:
                        if not receipt.get('upload_session_key'):
                            raise VpubError('invalid_resume', 'This legacy attempt has no durable upload session. Inspect the original work.')
                        result = adapters.publish_youtube(record['upload_plan'], options, checkpoint)
                    record['receipt'] = result
                    ledger.save(args.key, result['state'], record)
                    return {'key': args.key, **result}
                if row['platform'] in {'x', 'instagram'} and record.get('options', {}).get('method') == 'api':
                    api_backends = adapters.paid_api()
                    plan, receipt = record['upload_plan'], record.get('receipt', {})
                    def checkpoint(value):
                        record['receipt'] = value
                        ledger.save(args.key, value['state'], record)
                    if file_sha(plan['video']) != plan['video_sha256']:
                        raise VpubError('source_changed', 'Source no longer matches this attempt.')
                    if receipt.get('id'):
                        result = adapters.verify(row['platform'], row['account'], receipt)
                    else:
                        result = api_backends.publish(row['platform'], plan, record['options'], checkpoint, receipt or None)
                    record['receipt'] = result
                    ledger.save(args.key, result['state'], record)
                    return {'key': args.key, **result}
                if record.get('options', {}).get('method') != 'browser':
                    raise VpubError('invalid_resume', 'This backend has no automatic resumable session.')
                plan, opts = record.get('upload_plan', record['approved_plan']), record['options']
                if file_sha(plan['video']) != plan['video_sha256']:
                    raise VpubError('source_changed', 'Source no longer matches this attempt.')
                result = adapters.browser_call(row['platform'], 'resume', row['account'],
                    expected_identity=record['identity']['identity'], plan={**plan, 'options':opts})
                record['receipt'] = result
                ledger.save(args.key, result['state'], record)
                return {'key':args.key, **result}
            finally:
                ledger.close()
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
            result = metadata.request(args.manifest, args.source, args.allow_links)
            atomic_json(args.out, result)
            return result
        return metadata.apply(args.manifest, args.request, args.response, args.out)
    if args.command == "notify":
        from . import notifications
        if args.action == "status":
            return notifications.onboarding()
        if args.action == "configure":
            return notifications.configure(args.provider, args.to, args.inventory)
        if args.action == "inspect":
            return notifications.inspect_mailbox(args.email, args.inventory)
        if args.action == "test":
            return notifications.prepare_test(args.email, args.test_id, args.inventory)
        if args.action == "pending":
            return notifications.pending()
        if args.action == "claim":
            return notifications.claim(args.id, args.available_tool, args.inventory)
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
        return setup(args.external_stop_file, args.install_browser)
    if args.command == "onboard":
        selected = list(PLATFORMS) if args.platforms == "all" else args.platforms.split(",")
        if not selected or set(selected) - set(PLATFORMS):
            raise VpubError("invalid_platform", "Choose bilibili,youtube,douyin,instagram,x or all. TikTok is suspended.")
        from .notifications import onboarding
        return {"platforms": login_selected(selected, None if args.method == "auto" else args.method),
                "requirements": {"x": capabilities()['x_requirements']} if 'x' in selected else {},
                "email_notifications": onboarding()}
    if args.command == "auth":
        if args.action == "status" and not 0 <= args.wait <= 180:
            raise VpubError("invalid_wait", "--wait must be between 0 and 180 seconds.")
        if args.platform == "all":
            if args.action == "login":
                return login_selected(PLATFORMS, args.method)
            return status_selected(PLATFORMS, args.wait, args.method)
        if args.action == "login":
            return adapters.auth_login(args.platform, args.account, args.client_json, args.method)
        method = args.method or adapters.default_method(args.platform)
        if method in {"native", "api"}:
            return adapters.auth_status(args.platform, args.account, method)
        if args.wait and args.platform == "bilibili":
            return adapters.bili(["auth", "status", "--wait", str(args.wait)], timeout=args.wait + 60)
        if args.platform != "bilibili":
            from .auth_jobs import wait
            job = wait(args.platform, args.wait)
            if job and job.get("state") in {"STARTING", "LOGIN_PENDING", "VERIFICATION_REQUIRED"}:
                return job
        return adapters.auth_status(args.platform, args.account, method)
    if args.command == "init":
        out = Path(args.out)
        if out.exists():
            raise VpubError("already_exists", "Manifest already exists; no overwrite.")
        if not Path(args.video).is_file():
            raise VpubError("invalid_video", f"Video file not found: {args.video}")
        chosen = args.platforms.split(",")
        if not chosen or set(chosen) - set(PLATFORMS) or len(chosen) != len(set(chosen)):
            raise VpubError("invalid_platform", "Choose a unique subset of supported platforms.")
        if args.draft_only and set(chosen) - {"instagram", "x"}:
            raise VpubError("invalid_submission_mode", "--draft-only currently supports only Instagram and X. Use an explicit private setting for other supported routes.")
        from .core import language_tag
        ai_generated = args.ai_generated == "yes"
        value = template(args.video, language_tag(args.language), ai_generated)
        options = value["platforms"]
        options.update(x={"account": "default", "method": "native", "visibility": "account", "ai_generated": ai_generated},
                       instagram={"account": "default", "method": "native", "visibility": "account", "ai_generated": ai_generated})
        value["platforms"] = {p: {**options[p], "method": adapters.default_method(p)} for p in chosen}
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
        path = home() / "STOP.json"
        atomic_json(path, {"reason": args.reason, "at": now()})
        return {"stopped": True, "stop_file": str(path),
                "hint": "Publishing and browser commands now refuse to run. To resume, the operator deletes this file."}
    if args.command == "status":
        from .overview import status
        return status(args.limit)
    if args.command == "cleanup":
        from .overview import cleanup
        return cleanup(args.apply)
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
        plan_hash = old['data'].get('plan_hash') or old['data'].get('approved_plan', {}).get('confirmation')
        if result.get('method') == 'browser' and plan_hash:
            atomic_json(home() / 'browser-receipts' / (plan_hash+'-'+old['platform']+'.json'), result)
        return {"key": args.key, **result}
    finally:
        ledger.close()


def status_selected(platforms, wait_seconds=0, method=None):
    results = {}
    for platform in platforms:
        try:
            selected_method = method or adapters.default_method(platform)
            if platform in {"douyin", "instagram", "x"} and selected_method == "browser":
                from .auth_jobs import wait
                job = wait(platform, wait_seconds)
                if job and job.get("state") in {"STARTING", "LOGIN_PENDING", "VERIFICATION_REQUIRED"}:
                    results[platform] = job
                    continue
            results[platform] = adapters.auth_status(platform, "default", selected_method)
        except VpubError as e:
            results[platform] = {"logged_in": False, "code": e.code, "hint": e.hint}
    return results


def login_selected(platforms, method=None):
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


def platform_routes(platform):
    """What this machine can do now, so an assistant never promises a route that is not installed."""
    addon = adapters.addon_installed()
    return {"default_method": adapters.default_method(platform), "native_login": True,
            "publish_backend": {"bilibili": "bili-cli", "youtube": "official API through the paid add-on" if addon else "AI assistant on YouTube Studio"}.get(platform, "dedicated persistent background browser"),
            "optional_api_route": ("Installed (method: api)." if addon else "Needs the paid CrossCast API add-on (method: api).") if platform in {"youtube", "x", "instagram"} else None,
            "standalone_cli_publish": platform != "youtube" or addon,
            "live_upload_verified": platform in {"youtube", "x", "instagram", "douyin"}}


def capabilities():
    from .operations import capabilities as operations_capabilities
    return {"product": "PublishOps CrossCast", "version": __version__,
            "paid_api_addon": {"installed": adapters.addon_installed(),
                               "covers": "Every official API feature listed below for YouTube, X and Instagram, including YouTube comments, stats, playlists, edits and resumable uploads in the CLI."},
            "operations":operations_capabilities(),
            "engagement": {"commands":"engage status/collections/like/save/verify; --remove undoes the selected action, --dry-run is optional.",
                "bilibili":"bili-cli likes and explicit favorite-folder membership; read membership through the pinned upstream helper.",
                "youtube":"Official API rating and selected owned playlist membership; no Watch Later, Likes-list or legacy Favorites management.",
                "douyin":"Exact video Like/Save controls; account state read from the official page's own detail response in a fresh session, never from static tooltip text.",
                "instagram":"engage uses post toolbar Like/Save; collection manages private named collections separately. collection rename/add/remove use the official page client and independent readback.",
                "x":"Exact post Like/Bookmark controls; private bookmarks, not X Lists or bookmark folders.",
                "verification":"Durable single attempt; current state checked before action and in an independent read after it. Uncertain attempts require read-only verify, never blind retries.",
                "report":"Verified operations, reversals and unresolved attempts; --test-id traffic is excluded. No automatic or bulk engagement."},
            "x_requirements": {"support_scope":"Subscribed accounts with actual Media Studio access only.",
                               "free_account_publishing":False,
                               "checks":"Confirm Studio upload access and managed account before selecting media. Login/payment alone is not publishing readiness.",
                               "note":"Legacy Basic does not include Media Studio. Validate the actual account entitlement, not only a plan name.",
                               "official_source":"https://help.x.com/en/using-x/media-studio"},
            "metadata_targets": {p: "zh-Hans" if p in {"bilibili", "douyin"} else "en" for p in PLATFORMS},
            "metadata_policy": "Bilibili/Douyin: Chinese content becomes Simplified Chinese only; other languages retain source plus Simplified Chinese. YouTube/Instagram/X: English only for English content, otherwise source plus English. Host model supplies the text; schema validation is not translation quality assurance.",
            "cover_fitting": "Landscape contain fitting; Douyin and Instagram share identical dedicated portrait bytes. No landscape-to-portrait padding fallback. Grid cropping, small text and aesthetics require separate visual checks.",
            "instagram_media": "Instagram preparation preserves original video bytes and ratio. Use a separate upload cover and shared Douyin portrait through the mobile camera-roll picker; no inserted lead-in or transcoding.",
            "instagram_deletion": "Explicit native deletion request, UI result/absence evidence, retained history, and separately authorized same-video cover replacement. No standalone deletion API.",
            "email": "Gmail only, disabled by default. Enable only on explicit request; no ordinary-setup prompt. Host verifies the exact account and live send tool, helps install/connect a missing plugin or leaves email off. Self-send only. No Outlook, QQ, NetEase, SMTP or SMS. A TEST does not enable ongoing alerts.",
            "chapters": "Douyin CLI applies planned chapters after a durable published work ID is available; verify/resume completes a pending chapter step without reupload. Exact timing/title read-back; platform suggestions are not accepted automatically. Native Bilibili/Douyin/YouTube handoffs retained.",
            "existing_work_edits": "vpub edit: YouTube API metadata, Instagram captions, available X Edit action, Douyin portrait and chapters. One field group per request; verify-only never saves. X edit availability is platform-controlled; no automatic repost.",
            "youtube_initial_permissions": "Upload, read and video/playlist management scopes are requested together; user approves in Google. Old limited tokens require reauthorization.",
            "youtube_category": "Explicit per-video category required; host-model selection, in-place revision and read-back. API validates assignability before upload; no People & Blogs fallback.",
            "playlists": "YouTube playlist list/items/create/update/add/remove/move/delete/verify: ordinary owned playlists, durable request IDs, no write retries; default private creation.",
            "collections": "collection list/items/create/rename/add/remove/delete/verify: Instagram and Douyin support exact-ID membership and collection management; X supports folder CRUD but not named membership. Writes require complete member baselines; pagination is bounded and incomplete pages never prove absence.",
            "draft_only": "Instagram/X: init --draft-only stops CLI before Share/Post. PREVIEW_READY is not a guaranteed persistent draft after closing the browser.",
            "platforms": {p: platform_routes(p) for p in PLATFORMS},
            "live_verification_scope": "2026-10-30: source-plus-English YouTube/Instagram corrections and user-accepted v3 shared portrait retained. All five hello reply tests read back; Douyin was repaired through Creator Center and verified with a fresh nested-parent read. Browser evidence differs from API parent-ID verification. Not clean-machine unattended or full-playback acceptance.",
            "native_execution": "The host agent performs real UI actions. A CLI handoff alone is not an executed action or confirmed login.",
            "bili_cli": f"CrossCast includes bili-cli {included_bili_version()}. Use its bili command directly for search, video, inbox, comment and creator-data work; bili schema lists every command.",
            "works": "Bilibili published-work pages and YouTube upload-playlist pages; Douyin/Instagram/X visible first page only.",
            "work_adoption": "ops discover scans bounded pages; ops adopt freshly verifies ownership and adds existing works to the watchlist without an upload receipt.",
            "resumable_sync": "ops/messages sync use bounded per-platform concurrency, session reuse, durable checkpoints and freshness caches. --workers 1-4; --progress emits body-free JSON to stderr. ops/messages resume skips completed reads; unfinished browser sources restart their page.",
            "upload_recovery": "New YouTube API uploads persist private resumable sessions and acknowledged offsets. resume queries the server and continues the same account/media/metadata; expired or uncertain sessions are not replaced automatically. Never-started YouTube cover/playlist steps resume against the same owned video; uncertain writes are not repeated. Dead-owner lock markers can be reclaimed without clearing receipts. X retains saved Studio asset recovery. Douyin/Instagram pre-submit interruptions still need draft inspection.",
            "full_reads": "read-all start/resume/status: YouTube works, playlists/members and comments including reply pages; Bilibili works/comments via upstream. API checkpoints advance rows and cursor atomically. Browser Douyin/Instagram collections and members, X folder lists follow official controls; explicit server end required. Browser resume replays the page and retains prior rows while incomplete; a complete pass replaces them with current observations. Other DOM feeds and X folder members remain incomplete/unverified.",
            "session_reuse": "Non-publishing browser commands share one background browser per account over a private authenticated local pipe. It exits after 5 idle minutes (browser_idle_seconds; 0 disables), 1 hour, any error or a stop file. A second command waits for the first. Publishing, resume, edit and login close it and run alone. A sent command is never replayed.",
            "status": "vpub status reads local records only: any active stop, unfinished publications with the exact next command and upload progress, background browsers, pending messages, the last sync and snapshot disk use.",
            "cleanup": "vpub cleanup previews private video copies; --apply deletes only copies whose publications are all verified and whose original video is unchanged.",
            "suspended_platforms": {"tiktok": "Login test with a Hong Kong number was unsuccessful; see docs/TIKTOK_STATUS.md."},
            "not_implemented": "Full historical analytics/inbox, a free background-browser route for YouTube, and YouTube cards/clickable end screens. Instagram mobile-cover replacement remains user-assisted. Douyin replies to nested replies are not supported.",
            "interaction_rule": "Login/setup does not authorize comments, messages, likes or publication."}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        args = parser().parse_args()
        value = execute(args)
        ok = not (isinstance(value, dict) and (value.get("all_submissions_verified") is False or value.get("state") in UNFINISHED_STATES))
        print(json.dumps({"ok": ok, "data": value}, ensure_ascii=False))
        raise SystemExit(0 if ok else 4)
    except VpubError as e:
        print(json.dumps({"ok": False, "error": {"code": e.code, "message": e.message, "hint": e.hint}}, ensure_ascii=False))
        raise SystemExit(2)
    except FileNotFoundError as e:
        print(json.dumps({"ok": False, "error": {"code": "file_not_found", "message": f"File not found: {e.filename}",
                                                 "hint": "Check the path and run the command again."}}, ensure_ascii=False))
        raise SystemExit(2)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        where = f" at line {e.lineno}, column {e.colno}" if isinstance(e, json.JSONDecodeError) else ""
        print(json.dumps({"ok": False, "error": {"code": "invalid_file", "message": f"A file could not be read as UTF-8 JSON or text{where}.",
                                                 "hint": "Fix the file and run the command again."}}))
        raise SystemExit(2)
    except (KeyboardInterrupt, Exception) as e:
        # OAuth exceptions may embed tokens/URLs: never serialize their bodies.
        print(json.dumps({"ok": False, "error": {"code": "interrupted" if isinstance(e, KeyboardInterrupt) else "internal_error",
                                                 "message": type(e).__name__,
                                                 "hint": "Check setup/receipts. An interrupted upload must not be blindly retried."}}))
        raise SystemExit(3)

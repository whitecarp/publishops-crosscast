"""A durable handoff to the host agent's real in-app browser.

No cookie export, private browser port, screen relay, or simulated login.
The host agent performs UI actions using its supported browser tool. A CLI
cannot independently inspect the host's browser and never claims that it did.
"""
from __future__ import annotations

import re
import sqlite3
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from .core import (Ledger, VpubError, atomic_json, file_sha, home, now,
                   publication_lock, read_json, snapshot_file, stop_check, platform_check, load_plan, digest)

URLS = {
    "bilibili": "https://member.bilibili.com/",
    "youtube": "https://studio.youtube.com/",
    "douyin": "https://creator.douyin.com/creator-micro/home",
    "x": "https://x.com/",
    "instagram": "https://www.instagram.com/",
}


def login(platform):
    platform_check(platform)
    return {"state": "NATIVE_BROWSER_REQUIRED", "platform": platform,
            "method": "native", "logged_in": None, "login_url": URLS[platform],
            "hint": "Open this official URL directly in the host's built-in browser. Reuse the existing tab/session; let the user complete login. Inspect the actual creator dashboard. CLI cannot read that session."}


def queue(platform, plan, options):
    platform_check(platform)
    key = Ledger.key(platform, options["account"], plan["video_sha256"])
    path = home() / "browser-jobs" / (key + ".json")
    atomic_json(path, {"key": key, "platform": platform, "created_at": now(),
                       "plan": plan, "options": options})
    return {"state": "WAITING_FOR_BROWSER", "method": "native", "key": key,
            "final_submission_allowed": options.get("submission_mode") != "draft_only",
            "request_file": str(path), "login_url": URLS[platform],
            "hint": "No upload has started. Host agent: read the browser request, verify the intended account, run browser begin, then upload through the same built-in browser. Never repeat an uncertain attempt. If submission_mode=draft_only, stop at preview/save draft: never Share/Post or schedule."}


def request(key):
    ledger = Ledger()
    try:
        old = ledger.get(key)
        if not old or old["data"].get("options", {}).get("method") != "native":
            raise VpubError("not_found", "Native browser request not found.")
        platform_check(old["platform"])
        stop_check(old["platform"])
        job = read_json(home() / "browser-jobs" / (key + ".json"))
        if job["plan"]["confirmation"] != old["data"]["plan_hash"]:
            raise VpubError("request_changed", "Browser request differs from its recorded plan.")
        approved = old["data"]["approved_plan"]
        if job["options"] != old["data"]["options"] or job["plan"]["platforms"] != approved["platforms"]:
            raise VpubError("request_changed", "Browser request metadata differs from the approved plan.")
        for field in ("video_sha256", "video_bytes", "cover_sha256"):
            if job["plan"][field] != approved[field]:
                raise VpubError("request_changed", "Browser media record differs from the approved plan.")
        for field, sha_field in (("video", "video_sha256"), ("cover", "cover_sha256")):
            if job["plan"].get(field) and file_sha(job["plan"][field]) != job["plan"][sha_field]:
                raise VpubError("source_changed", "Browser job media changed; do not upload.")
        variants = job["plan"].get("covers", {}).get(old["platform"], {})
        expected_variants = approved.get("covers", {}).get(old["platform"], {})
        if {r: a["sha256"] for r,a in variants.items()} != {r: a["sha256"] for r,a in expected_variants.items()}:
            raise VpubError("request_changed", "Platform cover variants differ from the approved plan.")
        if any(file_sha(a["path"]) != a["sha256"] for a in variants.values()):
            raise VpubError("source_changed", "A platform cover snapshot changed.")
        return {**job, "state": old["state"], "can_begin": old["state"] == "WAITING_FOR_BROWSER",
                "final_submission_allowed": job["options"].get("submission_mode") != "draft_only" and old["state"] not in {"DELETE_PENDING", "AGENT_REPORTED_DELETED", "REPOST_PREPARING"},
                "user_action_required": old["state"] == "WAITING_FOR_USER",
                "resume_existing_attempt": old["state"] == "WAITING_FOR_USER",
                "user_action": old["data"].get("receipt", {}).get("observation") if old["state"] == "WAITING_FOR_USER" else None}
    finally:
        ledger.close()


def begin(key, identity):
    # Persist BEFORE the host selects the video. Even uploading a draft is a write.
    with publication_lock():
        job = request(key)
        if not job["can_begin"]:
            raise VpubError("already_started", "This browser attempt is already started or finished. Inspect it; never start another upload.")
        if not identity or not identity.strip():
            raise VpubError("identity_required", "Read the actual account identity in the creator dashboard first.")
        if job["platform"] == "youtube" and not re.fullmatch(r"UC[\w-]{22}", identity):
            raise VpubError("invalid_identity", "Use the channel ID observed in the actual YouTube Studio URL.")
        if job["platform"] == "x" and not re.fullmatch(r"[A-Za-z0-9_]{1,15}", identity):
            raise VpubError("invalid_identity", "Use the signed-in X handle without @.")
        expected = job["options"].get("expected_channel_id") or job["options"].get("expected_identity") or job["options"].get("expected_uid")
        matches = expected.lower() == identity.lower() if expected and job["platform"] == "x" else expected == identity
        if expected and not matches:
            raise VpubError("wrong_account", "Visible channel differs from the manifest.")
        ledger = Ledger()
        try:
            old = ledger.get(key)
            old["data"]["browser_identity"] = identity
            old["data"]["browser_started_at"] = now()
            ledger.save(key, "BROWSER_IN_PROGRESS", old["data"])
        finally:
            ledger.close()
        return {**job, "state": "BROWSER_IN_PROGRESS", "identity": identity,
                "hint": "Use only this job's snapshot and approved metadata. If final_submission_allowed is false, stop at preview/save draft; never Share/Post or schedule. Otherwise complete at most one final submission. Persist any video ID even if verification fails."}


def retry_rejected(key, identity, evidence_path):
    """Operator-driven recovery after rejection plus a fresh no-work UI check."""
    proof = read_json(evidence_path)
    with publication_lock():
        job = request(key)
        ledger = Ledger()
        try:
            old = ledger.get(key)
            data = old["data"]
            if old["state"] != "PLATFORM_REJECTED" or data.get("receipt", {}).get("id") or old["platform"] not in {"instagram", "x"}:
                raise VpubError("retry_not_allowed", "Only a clearly rejected X/Instagram attempt without a work ID can be retried; unknown submissions stay blocked.")
            if identity != data.get("browser_identity"):
                raise VpubError("wrong_account", "Inspect the original signed-in account before retrying.")
            expected_url = {"instagram": f"https://www.instagram.com/{identity}/", "x": f"https://x.com/{identity}"}[old["platform"]]
            if (not isinstance(proof, dict) or set(proof) != {"identity", "page_url", "work_absent", "observation"}
                    or proof["identity"] != identity or proof["page_url"] != expected_url or proof["work_absent"] is not True
                    or not isinstance(proof["observation"], str) or not 1 <= len(proof["observation"]) <= 1000):
                raise VpubError("invalid_retry_evidence", "Fresh same-account profile evidence must establish the rejected work is absent. No cookies or page dumps.")
            data.setdefault("rejected_attempts", []).append({"at": now(), "receipt": data.get("receipt"), "recovery_evidence": proof})
            data.pop("receipt", None)
            data.pop("browser_started_at", None)
            ledger.save(key, "WAITING_FOR_BROWSER", data)
            return {"key":key, "state":"WAITING_FOR_BROWSER", "platform_writes":0,
                    "hint":"Rejection and fresh absence evidence retained. Run browser begin before selecting the same snapshot again. Never use this for an uncertain submission."}
        finally:
            ledger.close()


def authorize_publish(key, identity):
    """Explicit operator authorization to submit the existing draft, never reupload."""
    with publication_lock():
        job = request(key)
        if job["state"] not in {"PREVIEW_READY", "DRAFT_SAVED"} or job["options"].get("submission_mode") != "draft_only":
            raise VpubError("authorization_not_allowed", "Inspect and record a draft first; only an unpublished draft-only attempt can be authorized.")
        ledger = Ledger()
        try:
            old = ledger.get(key)
            if identity != old["data"].get("browser_identity"):
                raise VpubError("wrong_account", "Confirm the original account in the current browser first.")
            data = old["data"]
            data.setdefault("submission_authorizations", []).append({"at": now(), "identity": identity,
                "previous_state": old["state"], "previous_plan_hash": data["plan_hash"], "previous_receipt": data.get("receipt")})
            plan = deepcopy({k:v for k,v in data["approved_plan"].items() if k != "confirmation"})
            plan["platforms"][old["platform"]]["submission_mode"] = "publish"
            plan["confirmation"] = digest(plan)
            opts = plan["platforms"][old["platform"]]
            data.update(approved_plan=plan, plan_hash=plan["confirmation"], options=opts)
            job["plan"]["platforms"] = deepcopy(plan["platforms"])
            job["plan"]["confirmation"] = plan["confirmation"]
            job["options"] = deepcopy(opts)
            atomic_json(home() / "browser-jobs" / (key + ".json"), {k:job[k] for k in ("key", "platform", "created_at", "plan", "options")})
            ledger.save(key, "BROWSER_IN_PROGRESS", data)
            return {"key":key, "state":"BROWSER_IN_PROGRESS", "final_submission_allowed":True,
                    "resume_existing_attempt":True, "can_begin":False, "platform_writes":0,
                    "hint":"Explicit authorization recorded locally. Submit the existing preview/draft once, then read back its ID and settings. Do not select/upload the video again."}
        finally:
            ledger.close()


def revise(key, manifest, confirmation, identity):
    """Update approved text in place; keep the existing upload and its durable ID."""
    with publication_lock():
        ledger = Ledger()
        try:
            old = ledger.get(key)
            if not old or old["state"] not in {"BROWSER_IN_PROGRESS", "METADATA_EDIT_IN_PROGRESS", "AGENT_REPORTED_VERIFIED", "SUBMITTED_UNVERIFIED", "WAITING_FOR_USER"}:
                raise VpubError("revision_not_allowed", "Only a known browser draft or verified work can be revised. Inspect uncertain attempts first.")
            data = old["data"]
            if old["state"] == "SUBMITTED_UNVERIFIED" and not data.get("receipt", {}).get("id"):
                raise VpubError("revision_not_allowed", "An uncertain attempt without a durable work ID cannot be edited.")
            if data.get("options", {}).get("method") != "native" or identity != data.get("browser_identity"):
                raise VpubError("wrong_account", "Confirm the same account in the current browser page before revising.")
            platform_check(old["platform"])
            stop_check(old["platform"])
            plan = load_plan(manifest, [old["platform"]])
            if confirmation != plan["confirmation"]:
                raise VpubError("confirmation_required", "Review and confirm the new metadata plan.")
            if any(plan.get(k) != data["approved_plan"].get(k) for k in ("video_sha256", "video_bytes", "cover_sha256")):
                raise VpubError("revision_media_changed", "Metadata revision cannot replace video or cover bytes.")
            hashes = lambda p: {r:a["sha256"] for r,a in p.get("covers", {}).get(old["platform"], {}).items()}
            if hashes(plan) != hashes(data["approved_plan"]):
                raise VpubError("revision_media_changed", "Use browser cover to change cover variants.")
            opts = plan["platforms"][old["platform"]]
            editable = {"title", "description", "tags", "post_text", "content_language", "metadata_language", "metadata_mode", "playlist_ids", "playlist_titles", "chapters", "duration_seconds"}
            if old["platform"] == "youtube":
                editable.add("category_id")
            # Preserve a privacy reduction already made by the user and read
            # back from the same work. This does not issue a platform write.
            if opts["visibility"] == "private" and data.get("receipt", {}).get("visibility") == "private":
                editable.add("visibility")
            if {k: v for k, v in opts.items() if k not in editable} != {k: v for k, v in data["options"].items() if k not in editable}:
                raise VpubError("revision_scope_changed", "Revision only changes text/language; account, privacy and declarations must stay unchanged.")
            job = read_json(home() / "browser-jobs" / (key + ".json"))
            data.setdefault("metadata_revisions", []).append({"at": now(), "previous_plan_hash": data["plan_hash"],
                "previous_options": data["options"], "previous_state": old["state"], "previous_receipt": data.get("receipt")})
            data.update(plan_hash=plan["confirmation"], options=opts, approved_plan=plan, title=opts["title"])
            # Keep the already-uploaded snapshot path; no copy, claim, or publish.
            previous_covers = job["plan"].get("covers")
            job.update(plan={**plan, "video": job["plan"]["video"], "cover": job["plan"].get("cover")}, options=opts)
            if previous_covers:
                job["plan"]["covers"] = previous_covers
            atomic_json(home() / "browser-jobs" / (key + ".json"), job)
            state = "BROWSER_IN_PROGRESS" if old["state"] == "BROWSER_IN_PROGRESS" else "METADATA_EDIT_IN_PROGRESS"
            ledger.save(key, state, data)
            return {"key": key, "state": state, "receipt": data.get("receipt"), "options": opts,
                    "hint": "Edit the existing draft/work in place, save once, then record fresh evidence. Do not choose/upload a video again."}
        finally:
            ledger.close()


def record(key, evidence_path):
    evidence = read_json(evidence_path)
    allowed = {"id", "page_url", "identity", "title", "description", "tags", "visibility", "made_for_kids",
               "ai_generated", "observation", "outcome", "post_text", "cover_source_sha256", "cover_observation", "cover_evidence", "playlist_ids", "playlist_titles", "chapters", "chapters_observation", "category_id", "media_preview", "draft_saved"}
    if not isinstance(evidence, dict) or set(evidence) - allowed:
        raise VpubError("invalid_evidence", "Use only documented browser-evidence fields; never include cookies or raw page dumps.")
    outcome = evidence.get("outcome")
    if not isinstance(outcome, str) or outcome not in {"verified", "uncertain", "rejected", "action_required", "draft"}:
        raise VpubError("invalid_evidence", "outcome must be verified, uncertain, rejected, action_required or draft.")
    if not isinstance(evidence.get("observation"), str) or not 1 <= len(evidence["observation"]) <= 1000:
        raise VpubError("invalid_evidence", "Supply a short factual UI observation, at most 1000 characters.")
    # Recording an outcome remains possible after STOP: preserving receipts is safe.
    with publication_lock():
        ledger = Ledger()
        try:
            old = ledger.get(key)
            if not old or old["state"] not in {"BROWSER_IN_PROGRESS", "METADATA_EDIT_IN_PROGRESS", "WAITING_FOR_USER", "SUBMITTED_UNVERIFIED", "AGENT_REPORTED_VERIFIED", "PREVIEW_READY", "DRAFT_SAVED"}:
                raise VpubError("not_started", "Begin the native browser request before recording its result.")
            opts = old["data"]["options"]
            if opts.get("submission_mode") == "draft_only" and outcome == "verified":
                raise VpubError("draft_only", "A draft-only test cannot be recorded as a verified publication.")
            if outcome == "draft":
                u = urlsplit(evidence.get("page_url", ""))
                host = {"x": "x.com", "instagram": "www.instagram.com"}.get(old["platform"])
                if (opts.get("submission_mode") != "draft_only" or evidence.get("id")
                        or u.scheme != "https" or u.netloc != host or u.username or u.password or u.query or u.fragment
                        or evidence.get("media_preview") is not True or type(evidence.get("draft_saved")) is not bool
                        or evidence.get("post_text") != opts.get("post_text")):
                    raise VpubError("invalid_draft_evidence", "Inspect the loaded media and exact caption on the official page; state whether a draft was actually saved. Draft evidence has no published work ID.")
            if evidence.get("identity") != old["data"].get("browser_identity"):
                raise VpubError("wrong_account", "Observed account differs from the one that began the upload.")
            video_id, page_url = evidence.get("id"), evidence.get("page_url", "")
            if video_id:
                u = urlsplit(page_url)
                if u.scheme != "https" or u.username or u.password or u.fragment:
                    raise VpubError("invalid_evidence", "Use an official HTTPS work page without credentials or fragments.")
                if old["platform"] == "youtube":
                    valid = isinstance(video_id, str) and re.fullmatch(r"[\w-]{11}", video_id)
                    valid = valid and ((u.netloc == "studio.youtube.com" and u.path == f"/video/{video_id}/edit" and not u.query)
                                       or (u.netloc == "www.youtube.com" and u.path == "/watch" and parse_qs(u.query) == {"v": [video_id]}))
                elif old["platform"] == "douyin":
                    valid = isinstance(video_id, str) and video_id.isdigit() and u.netloc == "www.douyin.com" and u.path == f"/video/{video_id}" and not u.query
                elif old["platform"] == "bilibili":
                    valid = isinstance(video_id, str) and re.fullmatch(r"BV[\w]{10}", video_id) and u.netloc == "www.bilibili.com" and u.path.rstrip("/") == f"/video/{video_id}" and not u.query
                elif old["platform"] == "tiktok":
                    # Legacy receipts remain recordable; new requests are disabled.
                    valid = isinstance(video_id, str) and video_id.isdigit() and u.netloc == "www.tiktok.com" and re.fullmatch(r"/@[\w.-]+/video/" + re.escape(video_id), u.path) and not u.query
                elif old["platform"] == "x":
                    handle = old["data"]["browser_identity"]
                    valid = (isinstance(video_id, str) and re.fullmatch(r"[0-9]+", video_id)
                             and u.netloc in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
                             and u.path.lower() == f"/{handle}/status/{video_id}".lower() and not u.query)
                else:
                    valid = isinstance(video_id, str) and re.fullmatch(r"[\w-]+", video_id) and u.netloc == "www.instagram.com" and u.path.rstrip("/") in {f"/reel/{video_id}", f"/p/{video_id}"} and not u.query
                if not valid:
                    raise VpubError("invalid_evidence", "The official work URL and video ID must agree.")
            elif outcome == "verified":
                raise VpubError("invalid_evidence", "Verified evidence requires the platform video ID.")
            if outcome == "verified":
                fields = ["title", "description", "tags", "visibility", "ai_generated"]
                if opts.get("chapters"):
                    fields.append("chapters")
                    if not isinstance(evidence.get("chapters_observation"), str) or not 1 <= len(evidence["chapters_observation"]) <= 500:
                        raise VpubError("chapters_not_verified", "Inspect saved native chapter controls; text timestamps alone are not proof.")
                if old["platform"] == "youtube":
                    fields.append("made_for_kids")
                    if "category_id" in opts:
                        fields.append("category_id")
                    fields.extend(f for f in ("playlist_ids", "playlist_titles") if opts.get(f))
                if old["platform"] == "x" or (old["platform"] == "instagram" and "post_text" in opts):
                    fields.append("post_text")
                if any(type(evidence.get(f)) is not type(opts[f]) or evidence[f] != opts[f] for f in fields):
                    raise VpubError("metadata_mismatch", "Read-back must match every approved metadata/declaration field.")
                expected_cover = old["data"]["approved_plan"].get("cover_sha256")
                if expected_cover and (evidence.get("cover_source_sha256") != expected_cover or
                        not isinstance(evidence.get("cover_observation"), str) or
                        not 1 <= len(evidence["cover_observation"]) <= 500):
                    raise VpubError("cover_not_verified", "Inspect the saved cover against the planned image and record its source SHA-256 plus a short visual observation. Source hash is not a remote-byte hash.")
                variants = old["data"]["approved_plan"].get("covers", {}).get(old["platform"], {})
                proof = evidence.get("cover_evidence", {})
                for role, asset in variants.items():
                    item = proof.get(role, {}) if isinstance(proof, dict) else {}
                    if item.get("source_sha256") != asset["sha256"] or not isinstance(item.get("observation"), str) or not 1 <= len(item["observation"]) <= 500:
                        raise VpubError("cover_not_verified", "Read back each planned landscape/portrait cover separately.")
            previous = old["data"].get("receipt", {}).get("id")
            if previous and previous != video_id:
                raise VpubError("receipt_conflict", "An existing video ID cannot be replaced or cleared.")
            state = ("DRAFT_SAVED" if evidence["draft_saved"] else "PREVIEW_READY") if outcome == "draft" else {"verified": "AGENT_REPORTED_VERIFIED", "uncertain": "SUBMITTED_UNVERIFIED", "rejected": "PLATFORM_REJECTED", "action_required": "WAITING_FOR_USER"}[outcome]
            receipt = {**evidence, "state": state, "method": "native", "url": page_url,
                       "recorded_at": now(), "verification_source": "host_agent_ui_observation",
                       "note": "Host agent supplied UI evidence; CLI did not independently query the platform. Processing/public playback is separate."}
            if outcome == "draft":
                receipt["published"] = False
            old["data"]["receipt"] = receipt
            if state == "WAITING_FOR_USER" and old["state"] != state:
                old["data"]["user_action_episode"] = old["data"].get("user_action_episode", 0) + 1
            ledger.save(key, state, old["data"])
            notification = None
            if state == "WAITING_FOR_USER":
                try:
                    from .notifications import enqueue
                    notification = enqueue(key, old["platform"], old["data"].get("user_action_episode", 1))
                except (OSError, ValueError, VpubError, sqlite3.Error):
                    notification = {"state": "QUEUE_ERROR", "email_sent": False, "inline_notice_required": True}
            return {"key": key, **receipt, "user_action_required": state == "WAITING_FOR_USER",
                    "resume_existing_attempt": state == "WAITING_FOR_USER", "notification": notification}
        finally:
            ledger.close()


def revise_cover(key, cover_path, identity, portrait_path=None):
    """Prepare a cover-only edit for the existing work, preserving its video ID."""
    source = Path(cover_path).resolve()
    if not source.is_file() or source.suffix.lower() not in {".jpg", ".jpeg", ".png"} or not source.stat().st_size:
        raise VpubError("invalid_cover", "Choose a nonempty production JPG or PNG cover.")
    with publication_lock():
        ledger = Ledger()
        try:
            old = ledger.get(key)
            if not old or old["state"] not in {"BROWSER_IN_PROGRESS", "AGENT_REPORTED_VERIFIED", "METADATA_EDIT_IN_PROGRESS", "WAITING_FOR_USER"}:
                raise VpubError("revision_not_allowed", "Inspect uncertain or platform-review states before editing a cover.")
            data = old["data"]
            if data.get("options", {}).get("method") != "native" or identity != data.get("browser_identity"):
                raise VpubError("wrong_account", "Read the same account in the official browser before changing its cover.")
            platform_check(old["platform"])
            if old["platform"] == "x":
                raise VpubError("unsupported_cover", "X custom thumbnail support is not implemented.")
            if portrait_path and old["platform"] not in {"douyin", "instagram"}:
                raise VpubError("unsupported_cover", "Portrait variants currently require Douyin/Instagram native mode.")
            stop_check(old["platform"])
            sha = file_sha(source)
            target = snapshot_file(source, home() / "covers" / (sha + source.suffix.lower()), sha)
            plan = deepcopy({k: v for k, v in data["approved_plan"].items() if k != "confirmation"})
            plan.update(cover=str(target), cover_sha256=sha)
            if portrait_path:
                portrait = Path(portrait_path).resolve()
                if not portrait.is_file() or portrait.suffix.lower() not in {".jpg", ".jpeg", ".png"} or not portrait.stat().st_size:
                    raise VpubError("invalid_cover", "Choose a complete portrait JPG/PNG.")
                portrait_sha = file_sha(portrait)
                portrait_target = snapshot_file(portrait, target.parent / (portrait_sha + portrait.suffix.lower()), portrait_sha)
                plan.setdefault("covers", {})[old["platform"]] = {
                    "landscape": {"path": str(target), "sha256": sha},
                    "portrait": {"path": str(portrait_target), "sha256": portrait_sha}}
            elif old["platform"] in plan.get("covers", {}):
                plan["covers"][old["platform"]]["landscape"] = {"path":str(target), "sha256":sha}
            plan["confirmation"] = digest(plan)
            data.setdefault("cover_revisions", []).append({"at": now(), "previous_plan": data["approved_plan"],
                "previous_state": old["state"], "previous_receipt": data.get("receipt")})
            data.update(approved_plan=plan, plan_hash=plan["confirmation"])
            job_path = home() / "browser-jobs" / (key + ".json")
            job = read_json(job_path)
            job["plan"] = {**plan, "video": job["plan"]["video"]}
            atomic_json(job_path, job)
            state = old["state"] if old["state"] in {"BROWSER_IN_PROGRESS", "WAITING_FOR_USER"} else "METADATA_EDIT_IN_PROGRESS"
            ledger.save(key, state, data)
            return {"key": key, "state": state, "cover": str(target), "cover_sha256": sha,
                    "receipt": data.get("receipt"), "platform_writes": 0,
                    "hint": "Upload only this cover on the EXISTING work. Do not select the video again. Check the saved image before recording verified; if blocked, record action_required or uncertain."}
        finally:
            ledger.close()

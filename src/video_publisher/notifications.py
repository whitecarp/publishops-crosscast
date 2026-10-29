"""Opt-in email outbox for the host's Gmail/Outlook tools; no mail credentials.

The CLI cannot invoke tools inside a running Codex conversation. The host claims
one message, calls the actual connected send tool, then records its response.
Uncertain sends are never retried automatically.
"""
from __future__ import annotations

from contextlib import contextmanager
import html
import json
import re
import sqlite3
import uuid

from .core import Ledger, VpubError, atomic_json, digest, home, now, read_json

SEND_TOOLS = {
    "gmail": "mcp__codex_apps__gmail_send_email",
    "outlook": "mcp__codex_apps__microsoft_outlook_email_send_email",
}


@contextmanager
def database():
    db = sqlite3.connect(home() / "notifications.sqlite3", timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS alerts (
        id TEXT PRIMARY KEY, publication_key TEXT, episode INTEGER,
        state TEXT, data TEXT, updated_at TEXT)""")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def settings():
    path = home() / "notifications.json"
    return read_json(path) if path.exists() else {"configured": False, "enabled": False}


def configure(provider, recipient=None):
    if provider not in {"off", *SEND_TOOLS}:
        raise VpubError("invalid_email_provider", "Choose off, gmail or outlook.")
    if provider == "off":
        value = {"configured": True, "enabled": False}
    else:
        if not isinstance(recipient, str) or not re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", recipient):
            raise VpubError("email_recipient_required", "Set one notification email address chosen by the user.")
        value = {"configured": True, "enabled": True, "provider": provider,
                 "recipient": recipient, "event": "publication_user_action_required"}
    # A changed choice never redirects an already-prepared email.
    if settings() != value:
        with database() as db:
            db.execute("UPDATE alerts SET state='CANCELLED',updated_at=? WHERE state='PENDING'", (now(),))
    atomic_json(home() / "notifications.json", value)
    return {**value, "connection_verified": False, "note": "Preference saved locally. No email was sent; the host must check the connected send tool."}


def onboarding():
    value = settings()
    return {"settings": value, "optional": True, "blocks_publication": False,
            "question": None if value.get("configured") else {
                "text": "需要郵件提醒登入驗證嗎？ / Email me when sign-in verification needs my help?",
                "choices": ["暫不開啟 / Off", "Gmail", "Outlook"], "default": "off",
                "instruction": "Ask once during initial setup; save the user's choice. If unanswered, leave disabled and continue. Enabling requires their chosen recipient; do not infer it from contacts."}}


def _waiting(key, episode):
    ledger = Ledger()
    try:
        row = ledger.get(key)
        return bool(row and row["state"] == "WAITING_FOR_USER" and row["data"].get("user_action_episode", 1) == episode)
    finally:
        ledger.close()


def enqueue(key, platform, episode):
    choice = settings()
    if not choice.get("enabled"):
        return {"state": "DISABLED", "email_sent": False, "inline_notice_required": True}
    event_id = digest([key, episode])
    # Fixed templates exclude raw page text, verification URLs, tokens, filenames
    # and private account IDs. The user resumes in their existing host session.
    subject = f"[CrossCast] {platform}: 需要登入驗證 / Sign-in verification required"
    body = (f"{platform} 的發布流程需要你完成官方登入或身份驗證。\n"
            "請回到原來的 Codex／Agent 對話，在內置瀏覽器完成操作。\n"
            "現有進度已保留；請勿重複上傳影片。\n\n"
            f"The {platform} workflow needs your help with official sign-in verification.\n"
            "Return to the original agent conversation and complete it in the built-in browser.\n"
            "The existing attempt is preserved. Do not upload the video again.\n"
            f"Reference: {event_id[:12]}")
    value = {"provider": choice["provider"], "recipient": choice["recipient"],
             "subject": subject, "body": body, "created_at": now()}
    with database() as db:
        db.execute("INSERT OR IGNORE INTO alerts VALUES (?,?,?,?,?,?)",
                   (event_id, key, episode, "PENDING", json.dumps(value), now()))
        row = db.execute("SELECT state FROM alerts WHERE id=?", (event_id,)).fetchone()
    return {"id": event_id, "state": row["state"], "email_sent": False,
            "inline_notice_required": True, "host_dispatch_required": row["state"] == "PENDING"}


def pending():
    # Recover an interrupted enqueue after the publication receipt was saved.
    ledger = Ledger()
    try:
        for row in ledger.rows():
            if row["state"] == "WAITING_FOR_USER":
                enqueue(row["key"], row["platform"], row["data"].get("user_action_episode", 1))
    finally:
        ledger.close()
    with database() as db:
        rows = list(db.execute("SELECT * FROM alerts ORDER BY updated_at"))
        for row in rows:
            if row["state"] == "PENDING" and not _waiting(row["publication_key"], row["episode"]):
                db.execute("UPDATE alerts SET state='CANCELLED',updated_at=? WHERE id=?", (now(), row["id"]))
        return [{"id": r["id"], "publication_key": r["publication_key"], "state": r["state"],
                 "provider": json.loads(r["data"])["provider"]}
                for r in db.execute("SELECT * FROM alerts ORDER BY updated_at")]


def claim(event_id, available_tool):
    choice = settings()
    if not choice.get("enabled"):
        raise VpubError("email_disabled", "Email is optional and currently disabled.")
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM alerts WHERE id=?", (event_id,)).fetchone()
        if not row or row["state"] != "PENDING":
            raise VpubError("alert_not_pending", "Already claimed, completed or cancelled. Never resend an uncertain email.")
        data = json.loads(row["data"])
        if any(choice.get(k) != data[k] for k in ("provider", "recipient")) or not _waiting(row["publication_key"], row["episode"]):
            raise VpubError("alert_obsolete", "The preference or publication state changed. Do not send this alert.")
        tool = SEND_TOOLS[data["provider"]]
        if available_tool != tool:
            raise VpubError("send_tool_unavailable", "Discover the configured provider's actual SEND tool before claiming; read/draft capability is insufficient.")
        token = uuid.uuid4().hex
        data.update(claim_token=token, claimed_at=now())
        db.execute("UPDATE alerts SET state='CLAIMED',data=?,updated_at=? WHERE id=?",
                   (json.dumps(data), now(), event_id))
    if data["provider"] == "gmail":
        args = {"to": data["recipient"], "subject": data["subject"],
                "payload": {"mime_type": "text/html", "body": {"content": "<p>" + html.escape(data["body"]).replace("\n", "<br>") + "</p>"}}}
    else:
        args = {"to": [{"email": data["recipient"]}], "subject": data["subject"],
                "text_content": data["body"], "save_to_sent_items": True}
    return {"id": event_id, "claim_token": token, "state": "CLAIMED", "email_sent": False,
            "tool_name": tool, "arguments": args,
            "instruction": "Call this connected host tool ONCE. Save its actual response, then run notify record. A tool timeout/crash is UNKNOWN, never an automatic retry. CLI output is not a sent email."}


def record(event_id, token, response_path):
    evidence = read_json(response_path)
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM alerts WHERE id=?", (event_id,)).fetchone()
        if not row or row["state"] != "CLAIMED":
            raise VpubError("alert_not_claimed", "An alert can be acknowledged only once after claim.")
        data = json.loads(row["data"])
        if token != data["claim_token"]:
            raise VpubError("claim_mismatch", "Use the token returned by this alert's claim.")
        if not isinstance(evidence, dict) or evidence.get("tool_name") != SEND_TOOLS[data["provider"]]:
            raise VpubError("wrong_email_tool", "Receipt must identify the selected send tool.")
        response = evidence.get("response")
        payload = response.get("structuredContent") if isinstance(response, dict) else None
        if payload is None and isinstance(response, dict):
            for item in response.get("content", []):
                if item.get("type") == "text":
                    try:
                        payload = json.loads(item["text"])
                    except (ValueError, KeyError):
                        continue
                    break
        success = isinstance(response, dict) and not response.get("isError") and isinstance(payload, dict) and not payload.get("error")
        message_id = payload.get("id") if success and data["provider"] == "gmail" else None
        if message_id and isinstance(message_id, str):
            state = "SENT"
        elif success and data["provider"] == "outlook" and "result" in payload and payload["result"] is None:
            state = "ACCEPTED"  # Graph sendMail returns no message ID; not proof of delivery.
        else:
            state = "UNKNOWN"
        data["receipt"] = {"state": state, "message_id": message_id, "recorded_at": now(),
                           "source": "host_supplied_connector_response", "delivery_verified": False}
        db.execute("UPDATE alerts SET state=?,data=?,updated_at=? WHERE id=?",
                   (state, json.dumps(data), now(), event_id))
    return {"id": event_id, **data["receipt"], "automatic_retry": False}

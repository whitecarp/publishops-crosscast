"""Opt-in email outbox for the host's Gmail/Outlook send tool; no mail credentials.

The CLI cannot invoke tools inside the host conversation, whichever agent runs it
(Codex, Claude or another). The host claims one message with the exact name of
its own connected SEND tool, calls that tool once, then records its response.
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

# Send tools known from Codex's app connectors; `claim` returns ready-made arguments
# for these. Any other host passes its own send tool's exact name instead.
SEND_TOOLS = {
    "gmail": "mcp__codex_apps__gmail_send_email",
    "outlook": "mcp__codex_apps__microsoft_outlook_email_send_email",
}
OTHER_PROVIDER = {"gmail": re.compile(r"outlook|microsoft", re.I), "outlook": re.compile(r"gmail|google", re.I)}


def send_tool(provider, name):
    """Accept the host's own SEND tool for the chosen provider; refuse read/draft tools."""
    if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", name)
            or not re.search(r"send", name, re.I) or re.search(r"draft", name, re.I)):
        raise VpubError("send_tool_unavailable", "Pass the exact name of the host's connected SEND tool; read/draft capability is insufficient.")
    if OTHER_PROVIDER[provider].search(name):
        raise VpubError("wrong_email_tool", f"That send tool belongs to a different mail provider than the chosen {provider}.")
    return name


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
            "請回到原來的 AI 助手對話，在內置瀏覽器完成操作。\n"
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
        tool = send_tool(data["provider"], available_tool)
        token = uuid.uuid4().hex
        data.update(claim_token=token, claimed_at=now(), send_tool=tool)
        db.execute("UPDATE alerts SET state='CLAIMED',data=?,updated_at=? WHERE id=?",
                   (json.dumps(data), now(), event_id))
    body_html = "<p>" + html.escape(data["body"]).replace("\n", "<br>") + "</p>"
    message = {"to": data["recipient"], "subject": data["subject"], "text": data["body"], "html": body_html}
    args = None
    if tool == SEND_TOOLS["gmail"]:
        args = {"to": data["recipient"], "subject": data["subject"],
                "payload": {"mime_type": "text/html", "body": {"content": body_html}}}
    elif tool == SEND_TOOLS["outlook"]:
        args = {"to": [{"email": data["recipient"]}], "subject": data["subject"],
                "text_content": data["body"], "save_to_sent_items": True}
    return {"id": event_id, "claim_token": token, "state": "CLAIMED", "email_sent": False,
            "tool_name": tool, "message": message, "arguments": args,
            "instruction": "Call this connected send tool ONCE. Use `arguments` when given; otherwise map `message` (to, subject, text or html) onto the tool's own parameters. Save its actual response, then run notify record with the same tool_name. A tool timeout/crash is UNKNOWN, never an automatic retry. CLI output is not a sent email."}


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
        # Alerts claimed before send tools were host-supplied fall back to the Codex name.
        tool = data.get("send_tool") or SEND_TOOLS[data["provider"]]
        if not isinstance(evidence, dict) or evidence.get("tool_name") != tool:
            raise VpubError("wrong_email_tool", "Receipt must identify the send tool named in the claim.")
        response = evidence.get("response")
        payload = None
        if isinstance(response, dict):
            payload = response.get("structuredContent")
            if payload is None:
                for item in response.get("content") or []:
                    if isinstance(item, dict) and item.get("type") == "text":
                        try:
                            payload = json.loads(item["text"])
                        except (ValueError, KeyError, TypeError):
                            continue
                        break
            if payload is None and not {"content", "isError", "structuredContent"} & set(response):
                payload = response  # a host that saves the tool's plain JSON result
        success = isinstance(response, dict) and not response.get("isError") and isinstance(payload, dict) and not payload.get("error")
        message_id = None
        if success:
            for field in ("id", "messageId", "message_id"):
                if isinstance(payload.get(field), str) and payload[field]:
                    message_id = payload[field]
                    break
        if message_id:
            state = "SENT"
        elif success and (("result" in payload and payload["result"] is None) or payload.get("success") is True
                          or payload.get("sent") is True
                          or str(payload.get("status", "")).lower() in {"sent", "accepted", "ok", "success"}):
            state = "ACCEPTED"  # Accepted without a message ID (e.g. Graph sendMail); not proof of delivery.
        else:
            state = "UNKNOWN"
        data["receipt"] = {"state": state, "message_id": message_id, "tool_name": tool, "recorded_at": now(),
                           "source": "host_supplied_connector_response", "delivery_verified": False}
        db.execute("UPDATE alerts SET state=?,data=?,updated_at=? WHERE id=?",
                   (state, json.dumps(data), now(), event_id))
    return {"id": event_id, **data["receipt"], "automatic_retry": False}

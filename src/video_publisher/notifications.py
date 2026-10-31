"""Explicitly opt-in Gmail self-notifications; disabled by default.

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
}
PROFILE_TOOLS = {p: name.removesuffix("send_email") + "get_profile" for p, name in SEND_TOOLS.items()}
DOMAINS = {"gmail": {"gmail.com", "googlemail.com"}}
UNSUPPORTED_DOMAINS = {"outlook.com", "hotmail.com", "live.com", "msn.com", "outlook.com.cn",
                       "qq.com", "vip.qq.com", "foxmail.com", "163.com", "126.com", "yeah.net"}


def email_address(value):
    if not isinstance(value, str) or not re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", value):
        raise VpubError("email_recipient_required", "Provide one email address, or disable optional email.")
    return value.casefold()


def payload_of(response):
    if not isinstance(response, dict) or response.get("isError"):
        return {}
    payload = response.get("structuredContent")
    if payload is None:
        for item in response.get("content", []):
            if item.get("type") == "text":
                try:
                    payload = json.loads(item["text"])
                    break
                except (ValueError, KeyError):
                    continue
    return payload if isinstance(payload, dict) and not payload.get("error") else {}


def inspect_mailbox(recipient, inventory_path=None):
    """Host supplies live tool inventory and actual get_profile results, not mail secrets.

    External service login aliases do not establish Gmail support. Never
    rewrite addresses or remove +tags to force an account match.
    """
    address = email_address(recipient)
    domain = address.rsplit("@", 1)[1]
    if domain in UNSUPPORTED_DOMAINS:
        return {"email": address, "provider": None, "state": "UNSUPPORTED_PROVIDER", "optional": True,
                "blocks_publication": False, "email_sent": False,
                "action": "Email notifications support Gmail only. Use your connected Gmail address or leave email disabled; no Outlook/QQ/NetEase/SMTP route."}
    hint = next((p for p, domains in DOMAINS.items() if domain in domains), None)
    inventory = read_json(inventory_path) if inventory_path else {}
    if (not isinstance(inventory, dict) or not isinstance(inventory.get("tools", []), list)
            or not isinstance(inventory.get("plugins", {}), dict)
            or not isinstance(inventory.get("profiles", {}), dict)):
        raise VpubError("invalid_email_inventory", "Provide an object with tools, plugins and actual profile responses.")
    profiles = inventory.get("profiles", {})
    matches = [p for p in SEND_TOOLS if
               str(payload_of(profiles.get(p)).get("email", "")).casefold() == address]
    base = {"email": address, "domain_hint": hint, "optional": True,
            "blocks_publication": False, "skip_command": "vpub notify configure --provider off",
            "email_sent": False, "source": "host_supplied_live_inventory"}
    if len(matches) > 1:
        return {**base, "state": "AMBIGUOUS_ACCOUNT", "action": "Select the intended connected mailbox; do not guess or send through both."}
    provider = matches[0] if matches else hint
    if provider is None:
        return {**base, "state": "UNSUPPORTED_OR_UNRESOLVED", "provider": None,
                "action": "Email supports Gmail only. Check an existing Gmail profile for this exact address, or leave email disabled. Do not guess another provider."}
    installed = inventory.get("plugins", {}).get(provider, {}).get("installed")
    tool_available = SEND_TOOLS[provider] in inventory.get("tools", [])
    info = {**base, "provider": provider, "plugin_query": "Gmail",
            "send_tool": SEND_TOOLS[provider], "profile_tool": PROFILE_TOOLS[provider]}
    if not tool_available:
        state = "NEEDS_INSTALL" if installed is False else "NEEDS_CONNECTION" if installed is True else "NEEDS_DISCOVERY"
        return {**info, "state": state,
                "action": "Search the host plugin catalogue. If missing, help install it; if installed, help connect/activate it. Offer skip. User completes account consent. Recheck tools and profile afterwards."}
    if provider not in matches:
        return {**info, "state": "ACCOUNT_MISMATCH" if payload_of(profiles.get(provider)).get("email") else "NEEDS_CONNECTION",
                "action": "Read the actual connected profile; connect the exact address chosen by the user, or skip. Never redirect the email."}
    return {**info, "state": "READY", "account_matches": True,
            "action": "Use this connected account and this same recipient. A self-send test verifies sending; inspect the returned sender and delivery separately."}


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
    value = read_json(path) if path.exists() else {"configured": False, "enabled": False}
    if value.get("provider") not in {None, "gmail"}:
        return {"configured": False, "enabled": False, "reason": "only_gmail_supported"}
    return value


def configure(provider="auto", recipient=None, inventory_path=None):
    if provider not in {"auto", "off", *SEND_TOOLS}:
        raise VpubError("invalid_email_provider", "Email supports Gmail only. Choose auto/gmail or off.")
    if provider == "off":
        value = {"configured": True, "enabled": False}
    else:
        recipient = email_address(recipient)
        detection = inspect_mailbox(recipient, inventory_path)
        if provider == "auto":
            provider = detection.get("provider")
            if not provider:
                raise VpubError("email_provider_unresolved", detection["action"])
        elif detection.get("domain_hint") and detection["domain_hint"] != provider and detection.get("state") != "READY":
            raise VpubError("email_provider_mismatch", "Use the detected provider or a verified connected profile for this same address.")
        if detection.get("state") == "UNSUPPORTED_PROVIDER":
            raise VpubError("email_provider_mismatch", detection["action"])
        if detection.get("state") == "READY" and detection["provider"] != provider:
            raise VpubError("email_provider_mismatch", "Use the provider of the verified connected account.")
        value = {"configured": True, "enabled": True, "provider": provider,
                 "recipient": recipient, "self_only": True, "event": "publication_user_action_required"}
    # A changed choice never redirects an already-prepared email.
    if settings() != value:
        with database() as db:
            db.execute("UPDATE alerts SET state='CANCELLED',updated_at=? WHERE state='PENDING'", (now(),))
    atomic_json(home() / "notifications.json", value)
    return {**value, "connection_verified": False, "note": "Preference saved locally. No email was sent; the host must check the connected send tool."}


def onboarding():
    value = settings()
    return {"settings": value, "optional": True, "blocks_publication": False,
            "question": None, "supported_providers": ["gmail"],
            "notice": "郵件提醒預設關閉，僅支援 Gmail；需要時主動開啟。 / Email is off by default and supports Gmail only; enable it explicitly when needed.",
            "enable_command": "vpub notify configure --to YOUR_GMAIL_ADDRESS",
            "instruction": "Do not prompt or enable during ordinary setup. Only on explicit request, ask for their Gmail address, inspect live plugin/profile, help install/connect if needed or leave disabled. Send from that account to itself only. Do not infer addresses from contacts."}


def prepare_test(recipient, test_id, inventory_path):
    connection = inspect_mailbox(recipient, inventory_path)
    if connection["state"] != "READY":
        return connection
    if not isinstance(test_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", test_id):
        raise VpubError("invalid_test_id", "Use a stable test ID (1-80 letters, digits, dash or underscore); replaying it must not resend.")
    event_id = digest(["email_test", connection["provider"], connection["email"], test_id])
    data = {"kind": "test", "provider": connection["provider"], "recipient": connection["email"],
            "subject": f"[CrossCast TEST] {test_id}", "created_at": now(),
            "body": "這是一封 PublishOps CrossCast 郵件功能測試，由你連接的帳號發給自己。\n"
                    "不是登入故障，也不需要提交驗證碼或密碼。測試不會開啟長期郵件提醒。\n\n"
                    "This is a TEST of PublishOps CrossCast email notifications, sent to your own address.\n"
                    "There is no sign-in incident. No password or verification code is needed.\n"
                    "This one-off test does not enable ongoing email notifications.\n"
                    f"Test ID: {test_id}"}
    with database() as db:
        db.execute("INSERT OR IGNORE INTO alerts VALUES (?,?,?,?,?,?)",
                   (event_id, "email_test", 0, "PENDING", json.dumps(data), now()))
        state = db.execute("SELECT state FROM alerts WHERE id=?", (event_id,)).fetchone()["state"]
    return {"id": event_id, "state": state, "kind": "test", "provider": data["provider"],
            "email_sent": False, "settings_changed": False,
            "host_dispatch_required": state == "PENDING"}


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
            "請回到原來的 Codex／Agent 對話，在對應的官方登入視窗完成操作。\n"
            "現有進度已保留；請勿重複上傳影片。\n\n"
            f"The {platform} workflow needs your help with official sign-in verification.\n"
            "Return to the original agent conversation and complete it in the matching official sign-in window.\n"
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
            data = json.loads(row["data"])
            if row["state"] == "PENDING" and (data["provider"] not in SEND_TOOLS or
                    (data.get("kind") != "test" and not _waiting(row["publication_key"], row["episode"]))):
                db.execute("UPDATE alerts SET state='CANCELLED',updated_at=? WHERE id=?", (now(), row["id"]))
        return [{"id": r["id"], "publication_key": r["publication_key"], "state": r["state"],
                 "provider": json.loads(r["data"])["provider"]}
                for r in db.execute("SELECT * FROM alerts ORDER BY updated_at")]


def claim(event_id, available_tool, inventory_path=None):
    choice = settings()
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM alerts WHERE id=?", (event_id,)).fetchone()
        if not row or row["state"] != "PENDING":
            raise VpubError("alert_not_pending", "Already claimed, completed or cancelled. Never resend an uncertain email.")
        data = json.loads(row["data"])
        if data["provider"] not in SEND_TOOLS:
            raise VpubError("plugin_provider_unsupported", "Only Gmail notifications are supported. Unsupported pending alerts must not be sent.")
        if data.get("kind") != "test":
            if not choice.get("enabled"):
                raise VpubError("email_disabled", "Email is optional and currently disabled.")
            if any(choice.get(k) != data[k] for k in ("provider", "recipient")) or not _waiting(row["publication_key"], row["episode"]):
                raise VpubError("alert_obsolete", "The preference or publication state changed. Do not send this alert.")
        connection = inspect_mailbox(data["recipient"], inventory_path)
        if connection["state"] != "READY" or connection["provider"] != data["provider"]:
            raise VpubError("email_connection_required", "Verify the live send tool and matching account with notify inspect; provide the inventory before claiming.")
        tool = SEND_TOOLS[data["provider"]]
        if available_tool != tool:
            raise VpubError("send_tool_unavailable", "Discover the configured provider's actual SEND tool before claiming; read/draft capability is insufficient.")
        token = uuid.uuid4().hex
        data.update(claim_token=token, claimed_at=now())
        db.execute("UPDATE alerts SET state='CLAIMED',data=?,updated_at=? WHERE id=?",
                   (json.dumps(data), now(), event_id))
    args = {"to": data["recipient"], "subject": data["subject"],
            "payload": {"mime_type": "text/html", "body": {"content": "<p>" + html.escape(data["body"]).replace("\n", "<br>") + "</p>"}}}
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
        if data["provider"] not in SEND_TOOLS or not isinstance(evidence, dict) or evidence.get("tool_name") != SEND_TOOLS[data["provider"]]:
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
        else:
            state = "UNKNOWN"
        data["receipt"] = {"state": state, "message_id": message_id, "recorded_at": now(),
                           "source": "host_supplied_connector_response", "delivery_verified": False}
        db.execute("UPDATE alerts SET state=?,data=?,updated_at=? WHERE id=?",
                   (state, json.dumps(data), now(), event_id))
    return {"id": event_id, **data["receipt"], "automatic_retry": False}

"""Optional official-API routes for X and Instagram. Browser routes stay the default.

Public submissions are checkpointed before sending. Uncertain submissions are
never repeated; resumable media transfers retain their original media/container ID.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .core import VpubError, atomic_json, home, read_json, stop_check

CALLBACK_PORT = 8765
CALLBACK = f"http://127.0.0.1:{CALLBACK_PORT}/callback"
HTTP_TIMEOUT = 120
# Keeps one connection per host across upload segments and status checks.
http = requests.Session()

X_API = "https://api.x.com/2"
X_AUTHORIZE = "https://x.com/i/oauth2/authorize"
X_SCOPES = "tweet.read tweet.write users.read media.write offline.access"
X_SEGMENT = 4 * 1024 * 1024
X_POLL_ATTEMPTS = 120

# Meta retires Graph versions about two years after release; bump this when it does.
GRAPH_VERSION = "v24.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}"
FB_AUTHORIZE = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
IG_SCOPES = "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement"
IG_MAX_BYTES = 300 * 1024 * 1024
IG_POLL_SECONDS = 60
IG_POLL_ATTEMPTS = 15


def token_path(platform, account):
    return home() / "accounts" / account / f"{platform}-api-token.json"


def load_client(client_json, fields):
    if not client_json:
        raise VpubError("oauth_setup_required", "The API route needs a local app credentials JSON on first setup.",
                        f"Create a developer app, save {' and '.join(fields)} in a JSON file, then pass --client-json PATH. Never commit this file.")
    value = read_json(Path(client_json).resolve())
    if not isinstance(value, dict) or any(not value.get(f) for f in fields):
        raise VpubError("oauth_setup_required", f"App credentials JSON needs: {', '.join(fields)}.")
    return value


def check(response, platform):
    """Raise a typed error without echoing tokens or response bodies."""
    if response.status_code < 400:
        return response.json() if response.content else {}
    if response.status_code == 401:
        raise VpubError("auth_expired", f"{platform} API authorization was rejected.", f"Run vpub auth login {platform} --method api.")
    if response.status_code == 429:
        raise VpubError("rate_limited", f"{platform} API rate limit reached.", "Wait for the limit to reset; do not repeat a submission.")
    if response.status_code == 403:
        raise VpubError("api_access_denied", f"{platform} API refused this request (HTTP 403).", "Check the app's access level, billing and approved permissions.")
    raise VpubError("api_error", f"{platform} API returned HTTP {response.status_code}.", "Inspect the original task; do not repeat a submission.")


def authorize(url):
    """Show the official consent URL and wait once for the local callback."""
    state = secrets.token_urlsafe(24)
    found = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            found.update({k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Authorization received. You can close this tab.")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), Handler)
    server.timeout = 300
    try:
        # URLs and progress belong on stderr, never mixed with the JSON result.
        with contextlib.redirect_stdout(sys.stderr):
            print("Open this authorization URL: " + url(state))
        server.handle_request()
    finally:
        server.server_close()
    if not found.get("code") or not secrets.compare_digest(found.get("state", ""), state):
        raise VpubError("oauth_failed", "Authorization was cancelled, timed out or failed the state check.")
    return found["code"].removesuffix("#_")


# X -----------------------------------------------------------------------------

def x_token_request(client, fields):
    auth = (client["client_id"], client["client_secret"]) if client.get("client_secret") else None
    body = fields if auth else {**fields, "client_id": client["client_id"]}
    return check(http.post(X_API + "/oauth2/token", data=body, auth=auth, timeout=HTTP_TIMEOUT), "x")


def x_save(account, client, value):
    atomic_json(token_path("x", account), {"client": client, "access_token": value["access_token"],
                                           "refresh_token": value.get("refresh_token"),
                                           "expires_at": time.time() + int(value.get("expires_in", 7200)) - 60})


def x_login(account, client_json):
    client = {k: v for k, v in load_client(client_json, ["client_id"]).items() if k in {"client_id", "client_secret"}}
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    code = authorize(lambda state: X_AUTHORIZE + "?" + urlencode({
        "response_type": "code", "client_id": client["client_id"], "redirect_uri": CALLBACK, "scope": X_SCOPES,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}))
    # X authorization codes last only 30 seconds, so exchange immediately.
    x_save(account, client, x_token_request(client, {"grant_type": "authorization_code", "code": code,
                                                     "redirect_uri": CALLBACK, "code_verifier": verifier}))
    return x_status(account)


def x_headers(account):
    path = token_path("x", account)
    if not path.exists():
        raise VpubError("not_logged_in", "X API authorization is missing.", "vpub auth login x --method api --client-json PATH")
    saved = read_json(path)
    if time.time() >= saved["expires_at"]:
        if not saved.get("refresh_token"):
            raise VpubError("auth_expired", "X API authorization expired.", "vpub auth login x --method api --client-json PATH")
        value = x_token_request(saved["client"], {"grant_type": "refresh_token", "refresh_token": saved["refresh_token"]})
        # X rotates refresh tokens, so the new one must be stored right away.
        x_save(account, saved["client"], {"refresh_token": saved["refresh_token"], **value})
        saved = read_json(path)
    return {"Authorization": "Bearer " + saved["access_token"]}


def x_status(account):
    me = check(http.get(X_API + "/users/me", headers=x_headers(account), timeout=HTTP_TIMEOUT), "x")["data"]
    return {"logged_in": True, "account": account, "identity": me["username"], "user_id": me["id"], "name": me.get("name"), "method": "api"}


def x_upload(plan, account, receipt, checkpoint):
    """Upload segments, saving progress so resume never starts a second media object."""
    size = Path(plan["video"]).stat().st_size
    if not receipt.get("media_id"):
        kind = "video/quicktime" if plan["video"].lower().endswith(".mov") else "video/mp4"
        data = check(http.post(X_API + "/media/upload/initialize", headers=x_headers(account), timeout=HTTP_TIMEOUT,
                                   json={"media_type": kind, "total_bytes": size, "media_category": "tweet_video"}), "x")["data"]
        receipt.update(media_id=data["id"], media_key=data.get("media_key"), next_segment=0, state="UPLOADING")
        checkpoint(receipt)
    with open(plan["video"], "rb") as stream:
        stream.seek(receipt["next_segment"] * X_SEGMENT)
        while chunk := stream.read(X_SEGMENT):
            stop_check("x")
            check(http.post(f"{X_API}/media/upload/{receipt['media_id']}/append", headers=x_headers(account), timeout=HTTP_TIMEOUT,
                                data={"segment_index": receipt["next_segment"]}, files={"media": chunk}), "x")
            receipt["next_segment"] += 1
            checkpoint(receipt)
    if receipt.get("state") in {"FINALIZING", "PROCESSING", "UPLOAD_PAUSED"}:
        data = check(http.get(X_API + "/media/upload", headers=x_headers(account), timeout=HTTP_TIMEOUT,
                              params={"command": "STATUS", "media_id": receipt["media_id"]}), "x")["data"]
    else:
        receipt["state"] = "FINALIZING"
        checkpoint(receipt)
        data = check(http.post(f"{X_API}/media/upload/{receipt['media_id']}/finalize", headers=x_headers(account), timeout=HTTP_TIMEOUT), "x")["data"]
    receipt.update(state="PROCESSING", media_key=data.get("media_key", receipt.get("media_key")))
    checkpoint(receipt)
    info = data.get("processing_info")
    for attempt in range(X_POLL_ATTEMPTS):
        stop_check("x")
        if not info or info.get("state") == "succeeded":
            return True
        if info["state"] == "failed":
            raise VpubError("media_rejected", "X could not process this video.", "Check its format and limits; nothing was posted.")
        time.sleep(min(60, max(1, int(info.get("check_after_secs", 5)))))
        info = check(http.get(X_API + "/media/upload", headers=x_headers(account), timeout=HTTP_TIMEOUT,
                                  params={"command": "STATUS", "media_id": receipt["media_id"]}), "x")["data"].get("processing_info")
    receipt["state"] = "UPLOAD_PAUSED"
    checkpoint(receipt)
    return False


def existing_submission(receipt):
    """Returned receipts are persisted by the CLI too, not just checkpoint values."""
    if receipt.get("state") == "PLATFORM_REJECTED":
        return {**receipt, "note": "This attempt was rejected. Inspect it before any explicit retry."}
    if receipt.get("state") in {"POST_SUBMITTING", "PUBLISH_SUBMITTING", "SUBMITTED_UNVERIFIED",
                               "SUBMITTED", "SUBMISSION_VERIFIED", "UNKNOWN"}:
        return {**receipt, "state": "SUBMITTED_UNVERIFIED",
                "note": "A submission may already exist. Inspect the original work; it will not be sent again."}
    return None


def bind_account(platform, options, receipt, checkpoint):
    current = status(platform, options["account"])
    expected_id = receipt.get("user_id") or options.get("expected_user_id")
    expected_name = receipt.get("identity") or options.get("expected_identity")
    if ((expected_id and str(expected_id) != str(current["user_id"])) or
            (expected_name and str(expected_name).casefold() != current["identity"].casefold())):
        raise VpubError("account_mismatch", "API authorization belongs to a different account; nothing was submitted.")
    receipt.update(identity=current["identity"], user_id=current["user_id"])
    checkpoint(receipt)


def publish_x(plan, options, checkpoint, receipt=None):
    account = options["account"]
    receipt = receipt or {"method": "api", "platform": "x", "state": "PREPARING",
                          "cover": "NOT_APPLIED" if plan.get("cover") else "NOT_REQUESTED"}
    stop_check("x")
    if receipt.get("id"):
        return verify_x(account, receipt)
    prior = existing_submission(receipt)
    if prior:
        return prior
    bind_account("x", options, receipt, checkpoint)
    if not x_upload(plan, account, receipt, checkpoint):
        return {**receipt, "note": "X is still processing. Resume this receipt to check the same media."}
    stop_check("x")
    receipt["state"] = "POST_SUBMITTING"
    checkpoint(receipt)
    try:
        response = http.post(X_API + "/tweets", headers=x_headers(account), timeout=HTTP_TIMEOUT,
                                 json={"text": options["post_text"], "media": {"media_ids": [receipt["media_id"]]}})
    except requests.RequestException:
        return {**receipt, "state": "SUBMITTED_UNVERIFIED", "note": "The post request had no clear result. Check the account's posts; it will not be sent again."}
    if response.status_code >= 500:
        return {**receipt, "state": "SUBMITTED_UNVERIFIED", "note": "X returned a server error. Check the account's posts; it will not be sent again."}
    if response.status_code >= 400:
        checkpoint({**receipt, "state": "PLATFORM_REJECTED"})
    post_id = check(response, "x")["data"]["id"]
    receipt.update(state="SUBMITTED", id=post_id,
                   url=f"https://x.com/{receipt['identity']}/status/{post_id}")
    checkpoint(receipt)
    return verify_x(account, receipt)


def verify_x(account, receipt):
    response = http.get(f"{X_API}/tweets/{receipt['id']}", headers=x_headers(account), timeout=HTTP_TIMEOUT,
                            params={"tweet.fields": "author_id,attachments"})
    if response.status_code == 404:
        return {**receipt, "state": "SUBMITTED_UNVERIFIED", "note": "The post is not readable yet."}
    data = check(response, "x").get("data")
    if not data or data.get("id") != receipt["id"] or data.get("author_id") != receipt.get("user_id"):
        return {**receipt, "state": "SUBMITTED_UNVERIFIED", "note": "Read-back did not show this post under the authorized account."}
    if not receipt.get("media_key") or receipt["media_key"] not in data.get("attachments", {}).get("media_keys", []):
        return {**receipt, "state": "SUBMITTED_UNVERIFIED", "note": "Read-back did not confirm the expected uploaded media."}
    return {**receipt, "state": "SUBMISSION_VERIFIED", "note": "Post and attached media read back through the official API."}


# Instagram -----------------------------------------------------------------------

def ig_get(path, token, **params):
    return check(http.get(f"{GRAPH}/{path}", headers={"Authorization": "Bearer " + token},
                              params=params, timeout=HTTP_TIMEOUT), "instagram")


def instagram_login(account, client_json):
    """Facebook Login for Business: Meta allows local-file Reel uploads only on this path."""
    client = load_client(client_json, ["app_id", "app_secret"])
    code = authorize(lambda state: FB_AUTHORIZE + "?" + urlencode({
        "client_id": client["app_id"], "redirect_uri": CALLBACK, "state": state,
        "response_type": "code", "scope": IG_SCOPES}))
    secret = {"client_id": client["app_id"], "client_secret": client["app_secret"]}
    short = check(http.get(GRAPH + "/oauth/access_token", timeout=HTTP_TIMEOUT,
                               params={**secret, "redirect_uri": CALLBACK, "code": code}), "instagram")["access_token"]
    long_lived = check(http.get(GRAPH + "/oauth/access_token", timeout=HTTP_TIMEOUT,
                                    params={**secret, "grant_type": "fb_exchange_token", "fb_exchange_token": short}), "instagram")["access_token"]
    pages = ig_get("me/accounts", long_lived, fields="access_token,instagram_business_account{id,username}", limit=100).get("data", [])
    linked = [p for p in pages if p.get("instagram_business_account")]
    wanted = client.get("ig_user_id")
    if wanted:
        linked = [p for p in linked if p["instagram_business_account"]["id"] == str(wanted)]
    if len(linked) != 1:
        raise VpubError("account_required", f"Expected one Instagram professional account linked to a Facebook Page; found {len(linked)}.",
                        "Link the account to a Page, or set ig_user_id in the credentials JSON.")
    ig = linked[0]["instagram_business_account"]
    atomic_json(token_path("instagram", account), {"access_token": linked[0]["access_token"], "ig_user_id": ig["id"]})
    return instagram_status(account)


def instagram_token(account):
    path = token_path("instagram", account)
    if not path.exists():
        raise VpubError("not_logged_in", "Instagram API authorization is missing.", "vpub auth login instagram --method api --client-json PATH")
    return read_json(path)


def instagram_status(account):
    saved = instagram_token(account)
    me = ig_get(saved["ig_user_id"], saved["access_token"], fields="id,username")
    return {"logged_in": True, "account": account, "identity": me["username"], "user_id": me["id"], "method": "api"}


def ig_upload(plan, saved, receipt, checkpoint):
    """Send the bytes from the offset Meta acknowledges, so resume never restarts a container."""
    status = ig_get(receipt["container_id"], saved["access_token"], fields="status_code,video_status")
    if status.get("status_code") in {"FINISHED", "PUBLISHED", "ERROR", "EXPIRED"} or receipt.get("bytes_uploaded"):
        return
    acknowledged = status.get("video_status", {}).get("uploading_phase", {}).get("bytes_transferred")
    if acknowledged is None and receipt.get("transfer_started"):
        raise VpubError("upload_resume_unknown", "Instagram did not return a confirmed upload offset. Inspect this container; no bytes were resent.")
    offset = int(acknowledged or 0)
    size = Path(plan["video"]).stat().st_size
    if offset < 0 or offset > size:
        raise VpubError("upload_resume_unknown", "Instagram returned an invalid upload offset; no bytes were resent.")
    if offset >= size:
        return
    receipt["transfer_started"] = True
    checkpoint(receipt)
    with open(plan["video"], "rb") as stream:
        stream.seek(offset)
        check(http.post(f"{RUPLOAD}/{receipt['container_id']}", data=stream, timeout=3600,
                            headers={"Authorization": "OAuth " + saved["access_token"], "offset": str(offset), "file_size": str(size)}), "instagram")
    receipt["bytes_uploaded"] = True
    checkpoint(receipt)


def publish_instagram(plan, options, checkpoint, receipt=None):
    account = options["account"]
    saved = instagram_token(account)
    if Path(plan["video"]).stat().st_size > IG_MAX_BYTES:
        raise VpubError("video_too_large", "Instagram API Reels are limited to 300 MB; nothing was sent.")
    receipt = receipt or {"method": "api", "platform": "instagram", "state": "PREPARING",
                          "cover": "NOT_APPLIED" if plan.get("cover") or plan.get("covers", {}).get("instagram") else "NOT_REQUESTED"}
    stop_check("instagram")
    if receipt.get("id"):
        return verify_instagram(account, receipt)
    prior = existing_submission(receipt)
    if prior:
        return prior
    bind_account("instagram", options, receipt, checkpoint)
    if not receipt.get("container_id"):
        body = {"media_type": "REELS", "upload_type": "resumable", "caption": options["post_text"], "share_to_feed": True}
        if options["ai_generated"]:
            body["is_ai_generated"] = True
        created = check(http.post(f"{GRAPH}/{saved['ig_user_id']}/media", json=body, timeout=HTTP_TIMEOUT,
                                      headers={"Authorization": "Bearer " + saved["access_token"]}), "instagram")
        receipt.update(container_id=created["id"], state="UPLOADING")
        checkpoint(receipt)
    stop_check("instagram")
    ig_upload(plan, saved, receipt, checkpoint)
    for attempt in range(IG_POLL_ATTEMPTS):
        stop_check("instagram")
        code = ig_get(receipt["container_id"], saved["access_token"], fields="status_code").get("status_code")
        if code == "FINISHED":
            break
        if code in {"ERROR", "EXPIRED"}:
            return {**receipt, "state": "PLATFORM_REJECTED", "container_status": code, "note": "Instagram did not accept this video; nothing was published."}
        if code == "PUBLISHED":
            return {**receipt, "state": "SUBMITTED_UNVERIFIED", "note": "The container is already published. Check the account's Reels."}
        time.sleep(IG_POLL_SECONDS)
    else:
        receipt["state"] = "UPLOAD_PAUSED"
        return {**receipt, "note": "Instagram is still processing. Run vpub resume with this key; nothing was published."}
    stop_check("instagram")
    receipt["state"] = "PUBLISH_SUBMITTING"
    checkpoint(receipt)
    try:
        response = http.post(f"{GRAPH}/{saved['ig_user_id']}/media_publish", json={"creation_id": receipt["container_id"]},
                                 headers={"Authorization": "Bearer " + saved["access_token"]}, timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        return {**receipt, "state": "SUBMITTED_UNVERIFIED", "note": "The publish request had no clear result. Check the account's Reels; it will not be sent again."}
    if response.status_code >= 500:
        return {**receipt, "state": "SUBMITTED_UNVERIFIED", "note": "Instagram returned a server error. Check the account's Reels; it will not be sent again."}
    if response.status_code >= 400:
        checkpoint({**receipt, "state": "PLATFORM_REJECTED"})
    receipt.update(state="SUBMITTED", id=check(response, "instagram")["id"])
    checkpoint(receipt)
    return verify_instagram(account, receipt)


def verify_instagram(account, receipt):
    saved = instagram_token(account)
    item = ig_get(receipt["id"], saved["access_token"], fields="id,media_product_type,permalink,username")
    result = {**receipt, "url": item.get("permalink"), "product_type": item.get("media_product_type")}
    if item.get("id") != receipt["id"] or item.get("media_product_type") != "REELS" or str(item.get("username", "")).casefold() != str(receipt.get("identity", "")).casefold():
        return {**result, "state": "SUBMITTED_UNVERIFIED", "note": "Read-back did not show a Reel under the authorized account."}
    return {**result, "state": "SUBMISSION_VERIFIED", "note": "Reel read back through the official API."}


def status(platform, account):
    return x_status(account) if platform == "x" else instagram_status(account)


def login(platform, account, client_json):
    return x_login(account, client_json) if platform == "x" else instagram_login(account, client_json)


def publish(platform, plan, options, checkpoint, receipt=None):
    return (publish_x if platform == "x" else publish_instagram)(plan, options, checkpoint, receipt)


def verify(platform, account, receipt):
    return verify_x(account, receipt) if platform == "x" else verify_instagram(account, receipt)

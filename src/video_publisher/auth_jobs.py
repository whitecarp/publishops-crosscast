"""Detached, bounded QR login jobs. All files stay in the private runtime."""
from __future__ import annotations

import os
import subprocess
import sys
import time

from .core import VpubError, atomic_json, home, now, read_json


def job_path(platform):
    return home() / "auth-jobs" / (platform + ".json")


def status(platform):
    path = job_path(platform)
    return read_json(path) if path.exists() else None


def start(platform, account):
    if platform not in {"douyin", "instagram", "x", "youtube"}:
        raise VpubError("native_login_required", "Use the official website directly in the host's built-in browser.")
    path = job_path(platform)
    previous = status(platform)
    if previous and previous.get("state") in {"STARTING", "LOGIN_PENDING", "VERIFICATION_REQUIRED"} and previous.get("expires_epoch", 0) > time.time():
        return previous
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"action": "login", "platform": platform, "account": account, "job_path": str(path)}
    request = path.with_suffix(".request.json")
    atomic_json(request, payload)
    atomic_json(path, {"state": "STARTING", "platform": platform, "created_at": now(), "expires_epoch": time.time() + 900})
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    with open(os.devnull, "wb") as sink:
        module = "video_publisher.browser_worker"
        from .browser_broker import enabled, release
        if enabled():
            release(platform, account)
        subprocess.Popen([sys.executable, "-m", module, "--job", str(request)],
                         stdin=subprocess.DEVNULL, stdout=sink, stderr=sink, close_fds=True, **options)
    # Return promptly; status --wait can continue waiting if QR rendering is slow.
    for _ in range(20):
        result = status(platform)
        if result and result.get("state") != "STARTING":
            return result
        time.sleep(0.5)
    return status(platform)


def wait(platform, seconds=0):
    end = time.monotonic() + seconds
    while True:
        result = status(platform)
        if not result or result.get("state") not in {"STARTING", "LOGIN_PENDING", "VERIFICATION_REQUIRED"} or time.monotonic() >= end:
            return result
        time.sleep(1)

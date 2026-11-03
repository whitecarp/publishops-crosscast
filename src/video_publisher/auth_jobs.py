"""Short sign-in commands with durable ownership, within the host's process limits."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from .core import VpubError, account_name, atomic_json, brief_lock, home, now, read_json
from .process_lock import alive


PENDING = {"STARTING", "LOGIN_PENDING", "VERIFICATION_REQUIRED"}
START_SECONDS = 30
PLATFORMS = {"douyin", "instagram", "x", "youtube"}


def job_path(platform):
    if platform not in PLATFORMS:
        raise VpubError("native_login_required", "Use the official website directly in the host's built-in browser.")
    return home() / "auth-jobs" / (platform + ".json")


def _turn(path):
    return brief_lock(path.with_suffix(".lock"), "login_busy", "Another command is checking this sign-in.", 5)


def _hint(platform, account="default"):
    return ("Sign-in did not record completion. The saved profile is retained. "
            "Close any leftover CrossCast sign-in window, then run "
            f"vpub auth login {platform} --account {account} --method browser once to resume. "
            f"Check with vpub auth status {platform} --account {account} --method browser --wait 5. "
            "If the host keeps stopping it, run these commands in your own terminal; do not keep retrying in the host.")


def _interrupted(path, saved):
    saved = {**saved, "state": "LOGIN_INTERRUPTED", "interrupted_at": now(),
             "hint": _hint(saved["platform"], saved.get("account", "default"))}
    atomic_json(path, saved)
    return saved


def _invalid():
    return VpubError("invalid_login_job", "The saved sign-in job does not match its local attempt; nothing was started.")


def _folder(path, attempt):
    if not isinstance(attempt, str) or not re.fullmatch(r"[0-9a-f]{32}", attempt):
        raise _invalid()
    folder = path.parent / attempt
    if folder.resolve() != path.parent.resolve() / attempt:
        raise _invalid()
    return folder.resolve()


def _file(folder, name):
    path = folder / name
    if path.resolve() != path:
        raise _invalid()
    return path


def _require_account(path, saved, account):
    actual = saved.get("account")
    if not actual and "attempt" not in saved:
        old_request = path.with_suffix(".request.json")
        if old_request.exists():
            payload = read_json(old_request)
            actual = payload.get("account") if payload.get("platform") == path.stem else None
    if not actual and saved.get("state") in PENDING:
        raise VpubError("login_account_unknown", "This older sign-in record does not identify its account.",
                        "Finish or close its CrossCast sign-in window; do not start a second login while it is open.")
    if actual and actual != account:
        raise VpubError("login_account_mismatch", "The recorded sign-in belongs to another account alias.",
                        "Check the account used to start that sign-in. Finish or close its window before starting another.")


def _result(path, saved):
    result = _file(_folder(path, saved["attempt"]), "result.json")
    if not result.exists():
        return saved
    answer = read_json(result)
    if answer.get("account", saved["account"]) != saved["account"] or answer.get("platform", saved["platform"]) != saved["platform"]:
        raise _invalid()
    return {**answer, "account": saved["account"]}


def _status(path):
    """Called under the short ownership lock; workers write only their own result."""
    if not path.exists():
        return None
    saved = read_json(path)
    if "attempt" not in saved:  # Older jobs have no process ownership record.
        if saved.get("state") in PENDING and saved.get("expires_epoch", 0) <= time.time():
            return _interrupted(path, saved)
        return saved
    _folder(path, saved["attempt"])
    if saved.get("platform") != path.stem:
        raise _invalid()
    account_name(saved.get("account"))
    if saved.get("state") == "LOGIN_INTERRUPTED":
        return saved
    result = _result(path, saved)
    if result.get("state") not in PENDING:
        return result
    pid = saved.get("worker_pid")
    if (pid and not alive(pid)) or (not pid and time.time() >= saved["start_deadline"]):
        # A worker can save its final answer immediately before exiting.
        result = _result(path, saved)
        if result.get("state") not in PENDING:
            return result
        return _interrupted(path, saved)
    return result


def status(platform, account="default"):
    account_name(account)
    path = job_path(platform)
    with _turn(path):
        result = _status(path)
        if result:
            _require_account(path, read_json(path), account)
        return result


def start(platform, account):
    account_name(account)
    path = job_path(platform)
    with _turn(path):
        previous = _status(path)
        if previous and previous.get("state") in PENDING:
            _require_account(path, read_json(path), account)
            return previous  # An elapsed login deadline does not prove a live worker stopped.
        if path.exists():
            saved = read_json(path)
            if saved.get("worker_pid") and alive(saved["worker_pid"]):
                _require_account(path, saved, account)
                return previous
        attempt = uuid.uuid4().hex
        folder = _folder(path, attempt)
        payload = {"action": "login", "platform": platform, "account": account,
                   "job_path": str(folder / "result.json"), "attempt": attempt}
        request = folder / "request.json"
        atomic_json(request, payload)
        # A delayed worker must not adopt a replacement attempt from this inspection copy.
        atomic_json(path.with_suffix(".request.json"), payload)
        saved = {"state": "STARTING", "platform": platform, "account": account, "attempt": attempt,
                 "created_at": now(), "start_deadline": time.time() + START_SECONDS,
                 "expires_epoch": time.time() + 900,
                 "hint": "Sign-in is starting. Leave its CrossCast Chrome window open to finish. "
                         f"Check with vpub auth status {platform} --account {account} --method browser --wait 5; "
                         "do not start another login while this one is running."}
        atomic_json(path, saved)
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        try:
            with open(os.devnull, "wb") as sink:
                subprocess.Popen([sys.executable, "-m", "video_publisher.auth_jobs", "--job", str(request)],
                                 stdin=subprocess.DEVNULL, stdout=sink, stderr=sink, close_fds=True, **options)
            # A Windows venv launcher can have a different PID from its Python
            # worker. The worker records its own PID before touching the browser.
        except OSError:
            _interrupted(path, saved)
            raise VpubError("login_start_failed", "The sign-in worker could not be started.", _hint(platform, account)) from None
    # Browser startup and the person's sign-in can both exceed a host's command limit.
    return saved


def run_job(request):
    """Claim before touching the browser; a late worker cannot start a replacement attempt."""
    request = Path(request).absolute()
    folder = _folder(home() / "auth-jobs" / "unused.json", request.parent.name)
    if request != _file(folder, "request.json"):
        raise _invalid()
    payload = read_json(request)
    path = job_path(payload["platform"])
    account_name(payload.get("account"))
    if (payload.get("action") != "login" or payload.get("attempt") != folder.name or not isinstance(payload.get("job_path"), str) or
            Path(payload.get("job_path", "")).absolute() != _file(folder, "result.json")):
        raise _invalid()
    with _turn(path):
        saved = _status(path)
        if not saved or saved.get("attempt") != payload["attempt"] or saved.get("state") != "STARTING":
            return
        _require_account(path, saved, payload["account"])
        if saved.get("worker_pid") and saved["worker_pid"] != os.getpid():
            return
        saved["worker_pid"] = os.getpid()
        atomic_json(path, saved)
    try:
        # Releasing a shared browser can itself wait; keep that out of the short launcher.
        from .browser_broker import enabled, release
        if enabled():
            release(payload["platform"], payload["account"])
        from .browser_worker import main
        before = sys.argv
        try:
            sys.argv = [before[0], "--job", str(request)]
            main()
        finally:
            sys.argv = before
    except Exception:
        # No exception text: browser failures can contain private page data.
        atomic_json(payload["job_path"], {"state": "FAILED", "platform": payload["platform"],
                    "error": {"code": "login_start_failed", "message": "The sign-in worker could not finish.",
                              "hint": _hint(payload["platform"], payload["account"])}})


def wait(platform, seconds=0, account="default"):
    end = time.monotonic() + seconds
    while True:
        result = status(platform, account)
        if result and result.get("state") == "LOGIN_INTERRUPTED":
            # Show the next step instead of falling through to a new browser status call.
            raise VpubError("login_interrupted", "Sign-in was interrupted; completion is not verified.", result["hint"])
        if result and result.get("state") == "FAILED" and isinstance(result.get("error"), dict):
            error = result["error"]
            raise VpubError(error["code"], error["message"], error.get("hint", ""))
        if not result or result.get("state") not in PENDING or time.monotonic() >= end:
            return result
        time.sleep(1)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--job":
        run_job(sys.argv[2])

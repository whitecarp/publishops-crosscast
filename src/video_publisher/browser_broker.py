"""Keep one signed-in browser per account between CLI commands.

A short-lived background process owns the profile lock and answers over a private,
authenticated local pipe. It never accepts publishing or login, never replays a
command, and exits when idle, after any error, or when a stop file appears.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from multiprocessing.connection import Client, Listener
import os
from queue import Empty, Queue
import secrets
import subprocess
import sys
from threading import Thread
import time

from .core import VpubError, account_name, atomic_json, config, home, read_json, stop_check

# Publish, resume, edit, login and read_all keep their own one-shot worker.
REUSABLE = {"status", "works", "verify", "operations", "operations_batch", "engagement",
            "comment_management", "collections", "message_sources", "direct_messages",
            "discover_works", "adopt_work"}
# Read-only actions may trust a recent identity check; anything else checks again first.
CACHED_IDENTITY = {"works", "operations_batch", "message_sources", "discover_works"}
IDENTITY_SECONDS = 300
IDLE_SECONDS = 300
MAX_LIFETIME_SECONDS = 3600
CONNECT_SECONDS = 15
BUSY_SECONDS = 180  # How long a command waits for the one the session is running now.
START_SECONDS = 150  # Longer than the slowest sign-in check, so a slow start is not mistaken for a failure.
CLOSING_SECONDS = 10  # A session that just exited may still be closing Chrome and holding the profile lock.
RELEASE_SECONDS = 30


def enabled():
    return idle_seconds() > 0


def idle_seconds():
    value = os.environ.get("VPUB_BROWSER_IDLE_SECONDS", config().get("browser_idle_seconds", IDLE_SECONDS))
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return IDLE_SECONDS


def state_path(platform, account):
    return home() / "browser-brokers" / f"{platform}-{account_name(account)}.json"


def failure_path(platform, account):
    return state_path(platform, account).with_suffix(".failure")


def new_address():
    token = secrets.token_hex(16)
    if sys.platform == "win32":
        return rf"\\.\pipe\crosscast-{token}"
    return str(home() / "browser-brokers" / f"{token}.sock")


def busy_error():
    return VpubError("browser_busy", "This platform's login or browser command is already running.",
                     "Wait for the active command; do not clear its lock.")


def wait_for_lock(platform, account, seconds):
    """Return True once nothing holds the profile lock, or False if something still does after the wait."""
    from .browser_runtime import profile_lock
    deadline = time.monotonic() + seconds
    while True:
        try:
            with profile_lock(platform, account):
                return True
        except VpubError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)


def connect(platform, account, wait=None):
    """Return a connection, or None when no session is there. A session that stays busy raises browser_busy."""
    path = state_path(platform, account)
    if not path.exists():
        return None
    try:
        saved = read_json(path)
    except (OSError, ValueError):
        return None
    results = Queue()

    def attempt():
        try:
            results.put(Client(saved["address"], authkey=bytes.fromhex(saved["key"])))
        except Exception:
            results.put(None)

    # A busy session accepts nobody until its command ends, so the handshake needs a limit.
    # The thread is a daemon because a blocked handshake must not stop the CLI from exiting.
    Thread(target=attempt, daemon=True).start()
    try:
        return results.get(timeout=CONNECT_SECONDS if wait is None else wait)
    except Empty:
        raise busy_error() from None


def start(platform, account):
    flags = {"start_new_session": True}
    if sys.platform == "win32":
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return subprocess.Popen([sys.executable, "-m", "video_publisher.browser_broker", platform, account],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=dict(os.environ, PYTHONIOENCODING="utf-8"), **flags)


def call(payload, timeout):
    """Run one command in the shared session. None means nothing was sent: use a one-shot worker."""
    platform, account = payload["platform"], payload["account"]
    # A busy session still owns the profile, so wait for it; a second session could never start.
    conn = connect(platform, account, BUSY_SECONDS)
    if conn is None:
        state_path(platform, account).unlink(missing_ok=True)
        failure = failure_path(platform, account)
        failure.unlink(missing_ok=True)
        if not wait_for_lock(platform, account, CLOSING_SECONDS):
            raise busy_error()
        process = start(platform, account)
        deadline = time.monotonic() + START_SECONDS
        while conn is None and time.monotonic() < deadline:
            if process.poll() is not None:
                # Its startup error is the answer; a one-shot worker would only repeat the sign-in check.
                if failure.exists():
                    error = read_json(failure)
                    raise VpubError(error["code"], error["message"], error.get("hint", ""))
                return None
            time.sleep(0.2)
            conn = connect(platform, account, BUSY_SECONDS)
    if conn is None:
        return None
    # From here on the command may have reached the platform, so failures never fall back or retry.
    try:
        conn.send_bytes(json.dumps(payload).encode("utf-8"))
        if not conn.poll(timeout):
            raise TimeoutError
        result = json.loads(conn.recv_bytes().decode("utf-8"))
    except (OSError, EOFError, ValueError, TimeoutError):
        raise VpubError("browser_session_interrupted", "Browser session interrupted; inspect the saved receipt before retrying.") from None
    finally:
        conn.close()
    if not result.get("ok"):
        error = result["error"]
        raise VpubError(error["code"], error["message"], error.get("hint", ""))
    return result["data"]


def release(platform, account):
    """Close a shared session so a one-shot worker can take the profile lock."""
    path = state_path(platform, account)
    if not path.exists():
        return
    try:
        conn = connect(platform, account, BUSY_SECONDS)
    except VpubError:
        conn = None  # Still busy: the lock wait below decides, and the next worker reports browser_busy.
    if conn is not None:
        try:
            conn.send_bytes(b'{"control": "shutdown"}')
            conn.poll(RELEASE_SECONDS)
        except (OSError, EOFError):
            pass
        finally:
            conn.close()
    wait_for_lock(platform, account, RELEASE_SECONDS)


def accept_later(listener):
    """Wait for a client on a daemon thread, which cannot keep a finished process alive."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def finish(value, error):
        if future.done():
            if value is not None:
                value.close()
        elif error is not None:
            future.set_exception(error)
        else:
            future.set_result(value)

    def wait():
        value, error = None, None
        try:
            value = listener.accept()
        except Exception as exc:
            error = exc
        with contextlib.suppress(RuntimeError):  # the loop has already closed
            loop.call_soon_threadsafe(finish, value, error)

    Thread(target=wait, daemon=True).start()
    return future


async def serve(listener, handle, platform, *, idle=None, lifetime=MAX_LIFETIME_SECONDS, poll_seconds=5):
    """Answer one client at a time. Return after idle, lifetime, stop, shutdown or any error."""
    idle = idle_seconds() if idle is None else idle
    started = last = time.monotonic()
    pending = None
    while True:
        pending = pending or accept_later(listener)
        done, _ = await asyncio.wait({pending}, timeout=poll_seconds)
        stop_check(platform)
        moment = time.monotonic()
        if not done:
            if moment - last >= idle or moment - started >= lifetime:
                return "idle"
            continue
        task, pending = pending, None
        try:
            conn = task.result()
        except Exception:
            continue  # a failed handshake says nothing about the session itself
        try:
            if not await asyncio.to_thread(conn.poll, 5):
                continue
            payload = json.loads(conn.recv_bytes().decode("utf-8"))
            if payload.get("control") == "shutdown":
                conn.send_bytes(b'{"ok": true}')
                return "shutdown"
            failed = False
            try:
                out = {"ok": True, "data": await handle(payload)}
            except Exception as exc:
                failed = True
                out = {"ok": False, "error": {"code": exc.code if isinstance(exc, VpubError) else "browser_error",
                                              "message": exc.message if isinstance(exc, VpubError) else f"Browser command failed ({type(exc).__name__}).",
                                              "hint": exc.hint if isinstance(exc, VpubError) else "Inspect the original work and receipt before retrying; no automatic resubmission."}}
            conn.send_bytes(json.dumps(out, ensure_ascii=False).encode("utf-8"))
            if failed:
                return "error"  # the page state is unknown, so the next command starts clean
        except (OSError, EOFError, ValueError):
            return "error"
        finally:
            conn.close()
        last = time.monotonic()
        if last - started >= lifetime:
            return "idle"


async def run(platform, account):
    from patchright.async_api import async_playwright
    from . import browser_runtime as runtime
    from .browser_worker import dispatch, action_timeout
    path = state_path(platform, account)
    path.parent.mkdir(parents=True, exist_ok=True)
    stop_check(platform)
    with runtime.profile_lock(platform, account):
        async with async_playwright() as pw:
            context = await runtime.open_context(pw, platform, account)
            try:
                page, owner = await runtime.signed_in_page(context, platform)
                checked = time.monotonic()

                async def handle(payload):
                    nonlocal owner, checked, page
                    stop_check(platform)
                    if (payload.get("platform"), payload.get("account"), payload.get("action") in REUSABLE) != (platform, account, True):
                        raise VpubError("session_scope", "This session cannot run that command.")
                    if payload["action"] == "operations_batch":
                        from .operations_batch import validate
                        validate(payload)
                    action = payload["action"]
                    if action not in CACHED_IDENTITY or time.monotonic() - checked > IDENTITY_SECONDS:
                        page, owner = await runtime.signed_in_page(context, platform)
                        checked = time.monotonic()
                    else:
                        await runtime.risk_check(page)
                    runtime.check_identity(owner, payload.get("expected_identity"))
                    if action == "status":
                        return {"logged_in": True, "identity": owner, "account": account, "method": "browser"}
                    result = await asyncio.wait_for(dispatch(page, platform, owner, payload), timeout=action_timeout(action))
                    await runtime.save_session(context, platform, account)
                    return result

                key = secrets.token_bytes(32)
                address = new_address()
                listener = Listener(address, authkey=key)
                try:
                    atomic_json(path, {"address": address, "key": key.hex(), "pid": os.getpid()})
                    if sys.platform != "win32":
                        path.chmod(0o600)
                    await serve(listener, handle, platform)
                finally:
                    path.unlink(missing_ok=True)
            finally:
                try:
                    await runtime.save_session(context, platform, account)
                finally:
                    await context.close()


def main():
    platform, account = sys.argv[1], sys.argv[2]
    try:
        asyncio.run(run(platform, account))
    except VpubError as exc:
        # run() removes only its own state file; a session that won the lock keeps running.
        atomic_json(failure_path(platform, account), {"code": exc.code, "message": exc.message, "hint": exc.hint})
    except Exception:
        pass  # The client falls back to a one-shot worker, which reports the real error.


if __name__ == "__main__":
    main()

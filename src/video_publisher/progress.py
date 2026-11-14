"""Short lines on stderr while a browser command works, so a slow one does not look hung. Stdout stays the one JSON answer.

The browser runs in another process that may have no console at all: a one-shot worker whose output is captured, a shared
session that outlives this command, or a worker on a hidden desktop. All three receive the same payload, so the payload names
a small private file, they add a word to it at each stage, and the command's own process copies those words to stderr.
Only these fixed words ever leave, so a line cannot hold a viewer's words, a name, an account ID, a message or an address.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
import uuid

from .core import home

STAGES = {"waiting for the browser", "starting the browser", "checking the account", "opening the page", "reading the page",
          "uploading", "pressing", "waiting for platform confirmation", "reading back", "closing the browser",
          "waiting for a cover slot to clear"}
# Once this command has pressed something on a platform, whatever it then reads there is the read-back.
READ_BACK = {"checking the account", "opening the page", "reading the page"}
# The longest a command stays silent, so one long stage is not mistaken for a hang.
QUIET_SECONDS = 15
LOOK_SECONDS = 0.25
STARTED = time.monotonic()
_path = None
_pressed = set()
_context = threading.local()


def attach(payload):
    """In the browser's process: this command's stages go to the file its payload names."""
    global _path
    _path = payload.get("stages_path")
    import re
    from . import audit_journal
    run = payload.get("audit_run")
    audit_journal.RUN = run if isinstance(run, str) and re.fullmatch("[a-f0-9]{32}", run) else None
    _context.platform = payload.get("platform")
    _context.action = payload.get("action")


def stage(word, path=None, *, platform=None, action=None):
    """Add one fixed word for whoever is waiting. This only informs, so it can never fail a command."""
    path = path or _path
    if word not in STAGES:
        return
    from . import audit_journal as journal
    journal.append('stage', lambda _: dict(platform=journal.known(platform or getattr(_context, 'platform', None), journal.PLATFORMS),
                                         action=journal.known(action or getattr(_context, 'action', None), journal.ACTIONS), stage=word))
    if not path:
        return
    try:
        # Do not recreate a file that explicit cleanup removed after its owner exited.
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
        with os.fdopen(descriptor, "a", encoding="ascii") as file:
            file.write(word + "\n")
    except OSError:
        pass


def watch(page):
    """Every command opens its pages through these, so this one place says when. A page is watched once, however many commands use it."""
    if getattr(page, "stages_watched", False):
        return

    def announced(go):
        async def opening(*args, **kwargs):
            stage("opening the page")
            answer = await go(*args, **kwargs)
            stage("reading the page")
            return answer
        return opening

    page.stages_watched = True
    for name in ("goto", "reload"):
        if hasattr(page, name):
            setattr(page, name, announced(getattr(page, name)))


def show(platform, word):
    """Straight to stderr's own handle. A reader that never empties stderr can stall only the thread doing this, never the command, and no lock is held at exit."""
    text = json.dumps({"event": "browser_progress", "platform": platform, "stage": word, "elapsed_seconds": round(time.monotonic() - STARTED, 1)}) + "\n"
    try:
        os.write(sys.stderr.fileno(), text.encode("ascii"))
    except Exception:
        try:
            sys.stderr.write(text)
            sys.stderr.flush()
        except Exception:
            pass


def copy_lines(platform, path, done):
    offset, current, said = 0, None, time.monotonic()
    while True:
        finished = done.wait(LOOK_SECONDS)
        try:
            data = path.read_bytes()
        except OSError:
            data = b""
        end = data.rfind(b"\n") + 1
        for line in data[offset:end].splitlines() if end > offset else ():
            word = line.decode("ascii", "replace").strip()
            if word not in STAGES:
                continue
            if word == "pressing":
                _pressed.add(platform)
            elif platform in _pressed and word in READ_BACK:
                word = "reading back"
            if word != current:
                show(platform, word)
                current, said = word, time.monotonic()
        offset = max(offset, end)
        if finished:
            return
        if current and time.monotonic() - said >= QUIET_SECONDS:
            show(platform, current)
            said = time.monotonic()


@contextlib.contextmanager
def relay(platform, payload):
    """In the command's own process: give the payload a file and copy its words to stderr until the browser step ends. Whatever goes wrong here, the command still runs."""
    previous = (getattr(_context, 'platform', None), getattr(_context, 'action', None))
    _context.platform, _context.action = platform, payload.get('action')
    path, thread, done = None, None, threading.Event()
    try:
        path = home() / "browser-jobs" / (uuid.uuid4().hex + ".stages")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        payload["stages_path"] = str(path)
        thread = threading.Thread(target=copy_lines, args=(platform, path, done), daemon=True)
        thread.start()
    except Exception:
        payload.pop("stages_path", None)
    try:
        yield
    finally:
        _context.platform, _context.action = previous
        done.set()
        if thread and thread.is_alive():
            thread.join(1)
        with contextlib.suppress(Exception):
            from .runtime_files import defer
            defer(path)

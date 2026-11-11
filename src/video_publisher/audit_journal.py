"""Private-data-minimizing command history. Instrumentation is best effort."""
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
import re
import sqlite3
from threading import Lock
import time
import uuid

from .core import PLATFORMS, atomic_json, brief_lock, home

ZERO = "0" * 64
RUN = None
_losses = {}
_loss_lock = Lock()
ACTIONS = frozenset("auth login status chat_unlock publish verify resume edit works list stats comments collections operations operations_batch work_delete delete_work direct_messages comment_management engagement upload video read whoami archive favorites season search".split())
OPERATIONS = frozenset("inspect delete verify read list add remove status edit update create rename move save like unlike reply pin hide sync".split())


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def state(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z_]{1,40}", value) else "other" if value else None


def code(value):
    if isinstance(value, dict):
        value = value.get("code")
    return value if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}|[A-Z][a-zA-Z0-9]{0,62}(?:Error|Exception)", value) else "other" if value else None


def known(value, choices):
    return value if isinstance(value, str) and value in choices else "other" if value else None


def number(value):
    return value if type(value) is int and value >= 0 else None


def bool_field(value):
    return value if type(value) is bool else None


def digest(line):
    return sha256(line).hexdigest()


def record_ref(directory, key):
    if not isinstance(key, str) or not re.fullmatch("[a-f0-9]{64}", key):
        return None
    path = directory / "salt"
    if not path.exists():
        with path.open("xb") as stream:
            stream.write(os.urandom(32))
            stream.flush()
            os.fsync(stream.fileno())
    salt = path.read_bytes()
    if len(salt) != 32:
        raise ValueError("invalid audit salt")
    return hmac.new(salt, key.encode("ascii"), "sha256").hexdigest()[:16]


def append(kind, build):
    """Construct fields inside the guard so projection errors cannot affect a caller."""
    directory = None
    try:
        directory = home() / "audit"
        with brief_lock(directory / "journal.lock", "audit_busy", "Audit journal busy", 0.8):
            path = directory / "journal.jsonl"
            head_path = directory / "head.json"
            head = json.loads(head_path.read_bytes()) if head_path.exists() else [0, ZERO, 0]
            size = path.stat().st_size if path.exists() else 0
            if size != head[2]:
                # A process may have died after fsync and before replacing the head.
                with path.open("rb") as stream:
                    stream.seek(head[2])
                    for line in stream:
                        item = json.loads(line)
                        if not line.endswith(b"\n") or item["seq"] != head[0]+1 or item["prev"] != head[1]:
                            raise ValueError("audit tail damaged")
                        head = [item["seq"], digest(line), stream.tell()]
                if size != head[2]:
                    raise ValueError("audit tail missing")
            fields = build(lambda key: record_ref(directory, key))
            with _loss_lock:
                missed = _losses.get(directory, 0)
            event = dict(seq=head[0]+1, prev=head[1], at=utc(), type=kind, run=RUN,
                         dropped_before=missed, **fields)
            line = (json.dumps(event, ensure_ascii=True, separators=(",", ":"), allow_nan=False)+"\n").encode()
            with path.open("ab") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
            atomic_json(head_path, [event["seq"], digest(line), size + len(line)])
            with _loss_lock:
                _losses[directory] = _losses.get(directory, 0) - missed
    except Exception:
        with _loss_lock:
            _losses[directory] = _losses.get(directory, 0) + 1


def verify_chain(directory):
    """Verify exact bytes including LF and compare the separately retained tail."""
    head, count, size = ZERO, 0, 0
    open_runs = set()
    try:
        with (directory / "journal.jsonl").open("rb") as stream:
            for line in stream:
                event = json.loads(line)
                if not line.endswith(b"\n") or event["seq"] != count+1 or event["prev"] != head:
                    return dict(ok=False, first_bad_seq=count+1)
                if event["type"] == "command_start":
                    open_runs.add(event["run"])
                if event["type"] == "command_end":
                    open_runs.discard(event["run"])
                count += 1
                size += len(line)
                head = digest(line)
        expected = json.loads((directory / "head.json").read_bytes())
        if expected != [count, head, size]:
            return dict(ok=False, first_bad_seq=count if expected[0] == count else count+1)
    except (ValueError, KeyError, OSError):
        return dict(ok=False, first_bad_seq=count+1)
    return dict(ok=True, records=count, head=head, not_ended=sorted(open_runs))


def snapshot(key, ref):
    if not isinstance(key, str) or not re.fullmatch("[a-f0-9]{64}", key):
        return None
    root = home()
    row = None
    db = root / "publications.sqlite3"
    if db.is_file():
        with closing(sqlite3.connect(db.as_uri()+"?mode=ro", uri=True, timeout=0.05)) as con:
            row = con.execute("SELECT platform,state,data FROM publications WHERE key=?", (key,)).fetchone()
    receipt = (json.loads(row[2]).get("receipt") or {}) if row else {}
    path = root / "work-deletions" / (key + ".json")
    deleted = json.loads(path.read_bytes()) if path.is_file() and not path.is_symlink() else {}
    return dict(ref=ref(key), platform=known(row[0], PLATFORMS) if row else None,
                state=state(row[1]) if row else None,
                final_submit_attempted=bool_field(receipt.get("final_submit_attempted")),
                final_presses=number(receipt.get("final_presses")), deletion_state=state(deleted.get("state")),
                delete_attempted=bool_field(deleted.get("delete_attempted")))


def command_start(args):
    global RUN
    RUN = uuid.uuid4().hex
    def fields(ref):
        from .audit import code_stamp
        session = os.environ.get("VPUB_AUDIT_SESSION", "")
        flags = {k: bool(getattr(args, k, False)) for k in ("dry_run", "verify_only", "person_approved", "no_cover", "person_checked")}
        workers = getattr(args, "workers", None)
        flags.update(confirm_given=bool(getattr(args, "confirm", None)), cover_given=bool(getattr(args, "cover", None)),
                     workers=workers if type(workers) is int and 1 <= workers <= 2 else None)
        return dict(command=args.command, action=getattr(args, "action", None), flags=flags, code=code_stamp(),
                    session=session if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", session) else None,
                    purpose=os.environ.get("VPUB_AUDIT_PURPOSE") if os.environ.get("VPUB_AUDIT_PURPOSE") in {"organic","test","timing","fault"} else None,
                    fault_armed=None, before=snapshot(getattr(args, "key", None), ref))
    append("command_start", fields)
    return time.monotonic()


def command_end(args, start, exit_code, error, result):
    global RUN
    def fields(ref):
        platforms = {}
        parts = result.get("results", {}) if isinstance(result, dict) else {}
        for name, part in parts.items() if isinstance(parts, dict) else ():
            if name not in PLATFORMS or not isinstance(part, dict):
                continue
            receipt = part.get("receipt") or part
            platforms[name] = dict(ref=ref(part.get("key")), state=state(part.get("state")), code=code(part.get("code")),
                                   previous_state=state(part.get("previous_state")), final_presses=number(receipt.get("final_presses")),
                                   final_submit_attempted=bool_field(receipt.get("final_submit_attempted")), id_present=bool(receipt.get("id")))
        after = snapshot(getattr(args, "key", None), ref)
        if after and after['platform'] in PLATFORMS and after['platform'] not in platforms:
            platforms[after['platform']] = dict(ref=after['ref'], state=after['state'], code=code(error),
                previous_state=None, final_presses=after['final_presses'],
                final_submit_attempted=after['final_submit_attempted'], id_present=None)
        return dict(exit_code=exit_code, ok=exit_code == 0, seconds=round(time.monotonic()-start, 3),
                    error_code=code(error), platforms=platforms, after=after)
    append("command_end", fields)
    RUN = None


@contextmanager
def call(platform, route, action, operation=None):
    started = time.monotonic()
    failure = None
    try:
        yield
    except BaseException as exc:
        failure = getattr(exc, "code", type(exc).__name__)
        raise
    finally:
        append("call", lambda _: dict(platform=known(platform, PLATFORMS), route=route, action=known(action, ACTIONS),
                                      operation=known(operation, OPERATIONS), ok=failure is None, error_code=code(failure),
                                      seconds=round(time.monotonic()-started, 3)))


def edit_group(patch):
    return next((k for k in ('chapters', 'portrait', 'cover') if k in patch), 'caption')


def transition(kind, key, platform, data, *, operation=None, group=None):
    def fields(ref):
        receipt = data.get("receipt") or data
        proof = data.get("evidence") or {}
        return dict(kind=kind, ref=ref(key), platform=known(platform, PLATFORMS),
                    state="forgotten" if operation == "forget" else state(data.get("state")),
                    receipt_state=state(receipt.get("state")), phase=code(receipt.get("phase")),
                    final_presses=number(receipt.get("final_presses")), final_submit_attempted=bool_field(receipt.get("final_submit_attempted")),
                    media_selected=bool_field(receipt.get("media_selected")), still_a_draft=bool_field(receipt.get("still_a_draft")),
                    id_present=bool(receipt.get("id")), delete_attempted=bool_field(data.get("delete_attempted")),
                    proof={k: bool_field(proof.get(k)) for k in ("present","owned","page_gone","list_absent","list_complete","fresh")},
                    platform_writes=number(proof.get("platform_writes", data.get("platform_writes"))),
                    error_code=code(data.get("error") or receipt.get("error")), operation=operation, field_group=group)
    append("transition", fields)

"""Private-data-minimizing command history. Instrumentation is best effort."""
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
import re
import sqlite3
from threading import Lock, local
import time
import uuid

from .core import PLATFORMS, atomic_json, brief_lock, home

ZERO = "0" * 64
RUN = None
STARTED = time.monotonic()
_losses = {}
_loss_ids = {}
_loss_lock = Lock()
_append_diagnostic = local()
LOCK_WAIT_SECONDS = 10.0
LOCK_RETRY_SECONDS = 0.0
_lock_degraded = set()
LOSS_REASONS = frozenset({'home_unavailable', 'lock_timeout', 'journal_read_failed',
                          'projection_failed', 'journal_write_failed', 'head_write_failed'})


def last_append_result():
    """Thread-local closed diagnostics; never include exception text or paths."""
    return dict(getattr(_append_diagnostic, 'result', {}))


def _report_loss(directory, reason):
    """One independently fsynced file per give-up, with no shared journal lock.

    Worker exit cannot erase a saved loss. If the whole destination is
    unwritable, retain the process-local fallback and report persistence false.
    No exception text, path, account, command argument or content is saved.
    """
    loss_id = uuid.uuid4().hex
    persisted = False
    try:
        folder = directory / 'losses'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        value = dict(v=1, id=loss_id, run=RUN if isinstance(RUN,str) and re.fullmatch(r'[a-f0-9]{32}',RUN) else None, at=utc(), reason=reason)
        with (folder / (loss_id + '.json')).open('x', encoding='utf-8') as stream:
            json.dump(value, stream, separators=(',', ':'))
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        persisted = True
    except Exception:
        pass
    with _loss_lock:
        _losses[directory] = _losses.get(directory, 0) + 1
        _loss_ids.setdefault(directory, []).append(loss_id)
    _append_diagnostic.result = dict(written=False, reason=reason, loss_id=loss_id,
                                     loss_persisted=persisted)
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
    phase = 'home_unavailable'
    durable = False
    missed = 0
    missed_ids = []
    try:
        directory = home() / "audit"
        phase = 'lock_timeout'
        with _loss_lock:
            wait = LOCK_RETRY_SECONDS if directory in _lock_degraded else LOCK_WAIT_SECONDS
        with brief_lock(directory / "journal.lock", "audit_busy", "Audit journal busy", wait):
            phase = 'journal_read_failed'
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
            phase = 'projection_failed'
            fields = build(lambda key: record_ref(directory, key))
            with _loss_lock:
                missed = _losses.get(directory, 0)
                missed_ids = list(_loss_ids.get(directory, []))
            event = dict(seq=head[0]+1, prev=head[1], at=utc(), type=kind, run=RUN,
                         dropped_before=missed, dropped_ids=missed_ids, **fields)
            line = (json.dumps(event, ensure_ascii=True, separators=(",", ":"), allow_nan=False)+"\n").encode()
            phase = 'journal_write_failed'
            with path.open("ab") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
            durable = True
            phase = 'head_write_failed'
            atomic_json(head_path, [event["seq"], digest(line), size + len(line)])
            with _loss_lock:
                _lock_degraded.discard(directory)
                _losses[directory] = _losses.get(directory, 0) - missed
                _loss_ids[directory] = [i for i in _loss_ids.get(directory, []) if i not in missed_ids]
            missed, missed_ids = 0, []
            _append_diagnostic.result = dict(written=True, reason=None)
            return True
    except Exception:
        if phase == 'lock_timeout':
            with _loss_lock:
                _lock_degraded.add(directory)
        if durable:
            # The line is already durable. A failed head replacement is repaired
            # by the next writer; it must not be reported as a missing event.
            with _loss_lock:
                _lock_degraded.discard(directory)
                _losses[directory] = max(0, _losses.get(directory, 0) - missed)
                _loss_ids[directory] = [i for i in _loss_ids.get(directory, []) if i not in missed_ids]
            _append_diagnostic.result = dict(written=True, reason='head_repair_needed')
            return True
        _report_loss(directory, phase)
        return False


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
    global RUN, STARTED
    RUN = uuid.uuid4().hex
    STARTED = time.monotonic()
    def fields(ref):
        from .audit import code_stamp
        session = os.environ.get("VPUB_AUDIT_SESSION", "")
        # A wrapper reservation id is separate from this command's own run id.
        # Reject arbitrary environment text before it can enter the journal.
        awl_run_id = os.environ.get("AWL_RUN_ID", "")
        flags = {k: bool(getattr(args, k, False)) for k in ("dry_run", "verify_only", "person_approved", "no_cover", "person_checked")}
        flags["person_approved"] = bool(getattr(args, "person_approved", False) or getattr(args, "reviewed", False))
        workers = getattr(args, "workers", None)
        flags.update(confirm_given=bool(getattr(args, "confirm", None)), cover_given=bool(getattr(args, "cover", None)),
                     workers=workers if type(workers) is int and 1 <= workers <= 2 else None)
        return dict(command=args.command, action=getattr(args, "action", None), flags=flags, code=code_stamp(),
                    awl_run_id=awl_run_id if re.fullmatch(r"[a-f0-9]{32}", awl_run_id) else None,
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

"""Bilibili's interactive widgets through the included bili-cli: a link to another video, a follow or triple button, a vote and a rating.

A field read found Bilibili won't list them on an approved video waiting for release, so nothing is sent until a read of the video has answered. Every send reads first, and one that may have gone is never sent again by itself.
These bili-cli commands have run by hand on real public videos. This route has not run on a real account.
"""
from pathlib import Path
import re
import time

from . import adapters
from .core import VpubError, atomic_json, digest, home, now, publication_lock, read_json, receipt_turn
from .edits_bilibili import REFUSED
from .safety import check_public_text

GAP_SECONDS = 35  # bili-cli holds these 30 seconds apart, and four sent 35 apart on real videos all went through.
SHOWS_AT_LEAST = 5
QUESTION_MOST, OPTION_MOST, RATING_TITLE_MOST = 12, 6, 12
SKINS, STYLES = {"star", "heart", "lemon"}, {"follow", "triple", "both"}
X_RANGE, Y_RANGE = (118, 549), (82, 293)
FIELDS = {"link": {"target", "text"}, "follow": {"style", "x", "y", "duration"},
          "vote": {"question", "options", "allow_custom", "custom_label"}, "rating": {"title", "skin"}}
BV = re.compile(r"BV[0-9A-Za-z]{10}")
# Nothing more goes after one of these, and the rest wait for a later run.
STOPS_THE_REST = {"rate_limited", "risk_control", "captcha_required", "not_logged_in", "auth_expired", "write_spacing", "stopped"}


def bad(message):
    return VpubError("invalid_widget", message, "Fix this widget in the plan or file. Nothing was sent.")


def one_line(value, name):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "\r\n\x00"):
        raise bad(f"A widget's {name} must be one line of text.")
    return value.strip()


def number(value, name):
    if type(value) not in {int, float} or value < 0:
        raise bad(f"A widget's {name} must be a number, not below zero.")
    return value


def clean(widget, length=None):
    """One widget with its defaults filled in, within the limits bili-cli checks, so a plan fails before anything goes up."""
    if not isinstance(widget, dict):
        raise bad("Each widget must be an object.")
    kind = widget.get("type")
    if kind == "reserve":
        raise bad("A reserve widget isn't available here, because removing one notifies everyone who booked it. Use bili danmaku interact reserve yourself.")
    if kind not in FIELDS:
        raise bad("A widget's type must be link, follow, vote or rating.")
    extra = set(widget) - FIELDS[kind] - {"type", "time", "page"}
    if extra:
        raise bad(f"A {kind} widget doesn't take {sorted(extra)}.")
    moment = number(widget.get("time"), "time")
    page = widget.get("page", 1)
    if type(page) is not int or page < 1:
        raise bad("A widget's page must be a whole number from 1.")
    if length is not None and moment > length - SHOWS_AT_LEAST:
        raise bad(f"A widget shows for at least {SHOWS_AT_LEAST} seconds, so its time must be that long before the video ends.")
    result = {"type": kind, "time": moment, "page": page}
    if kind == "link":
        if not BV.fullmatch(str(widget.get("target"))):
            raise bad("A link widget's target must be a BV number.")
        result.update(target=widget["target"], text=one_line(widget.get("text"), "text"))
    elif kind == "follow":
        style, x, y = widget.get("style", "both"), widget.get("x", 300), widget.get("y", 150)
        if style not in STYLES:
            raise bad("A follow widget's style must be follow, triple or both.")
        if type(x) is not int or type(y) is not int or not X_RANGE[0] <= x <= X_RANGE[1] or not Y_RANGE[0] <= y <= Y_RANGE[1]:
            raise bad(f"A follow widget's x must be within {X_RANGE} and its y within {Y_RANGE}.")
        duration = number(widget.get("duration", SHOWS_AT_LEAST), "duration")
        if duration < SHOWS_AT_LEAST:
            raise bad(f"A follow widget shows for at least {SHOWS_AT_LEAST} seconds.")
        result.update(style=style, x=x, y=y, duration=duration)
    elif kind == "vote":
        question = one_line(widget.get("question"), "question")
        options, custom = widget.get("options"), widget.get("allow_custom", False)
        if type(custom) is not bool or not isinstance(options, list):
            raise bad("A vote widget needs a list of options, and allow_custom must be true or false.")
        answers = [one_line(option, "option") for option in options]
        label = one_line(widget.get("custom_label", "其他"), "custom_label")
        every = answers + [label] if custom else answers
        if len(question) > QUESTION_MOST or not 2 <= len(every) <= 4 or len(set(every)) != len(every) or any(len(answer) > OPTION_MOST or ";" in answer for answer in every):
            raise bad(f"A vote needs a question of up to {QUESTION_MOST} characters and 2 to 4 different options, counting a custom one, of up to {OPTION_MOST} characters with no semicolon.")
        result.update(question=question, options=answers, allow_custom=custom, custom_label=label)
    else:
        title, skin = one_line(widget.get("title"), "title"), widget.get("skin", "star")
        if len(title) > RATING_TITLE_MOST or skin not in SKINS:
            raise bad(f"A rating needs a title of up to {RATING_TITLE_MOST} characters, and its skin must be star, heart or lemon.")
        result.update(title=title, skin=skin)
    return result


def validate(widgets, length=None):
    if not isinstance(widgets, list) or not widgets:
        raise bad("Widgets must be a list with at least one in it.")
    result = [clean(widget, length) for widget in widgets]
    check_public_text(result)
    if len({(widget["page"], widget["time"]) for widget in result}) != len(result):
        raise bad("Two widgets at one moment would clash on Bilibili, so give each its own time.")
    votes = [widget["page"] for widget in result if widget["type"] == "vote"]
    if len(set(votes)) != len(votes):
        raise bad("Bilibili takes one vote on each video.")
    return result


def command(work, widget):
    args = ["danmaku", "interact", widget["type"], work]
    if widget["type"] == "link":
        args += [widget["target"], "--text", widget["text"]]
    elif widget["type"] == "follow":
        args += ["--style", widget["style"], "--x", str(widget["x"]), "--y", str(widget["y"]), "--duration", str(widget["duration"])]
    elif widget["type"] == "vote":
        args += ["--question", widget["question"]]
        for option in widget["options"]:
            args += ["--option", option]
        if widget["allow_custom"]:
            args += ["--allow-custom", "--custom-label", widget["custom_label"]]
    else:
        args += ["--title", widget["title"], "--skin", widget["skin"]]
    return args + ["--time", str(widget["time"]), "--page", str(widget["page"])]


def read(work, page=1):
    """What Bilibili lists on this part. A failed read says nothing about whether it has any, and a field run saw this fail twice with no reason given."""
    items = adapters.bili(["danmaku", "interact", "list", work, "--page", str(page)]).get("items")
    if not isinstance(items, list):
        raise VpubError("widgets_unreadable", "bili-cli's list of widgets had no items in it.")
    return items


def shows(widget, item):
    """Whether a listed widget is this one. Bilibili keeps the time to the millisecond, and a real read's own words for a rating haven't been seen, so a rating goes by its kind and time."""
    if item.get("kind") != widget["type"] or abs((item.get("time") or 0) - widget["time"]) > 0.5:
        return False
    if widget["type"] == "link":
        return (item.get("target") or {}).get("bvid") == widget["target"]
    if widget["type"] == "follow":
        return item.get("style") == widget["style"]
    if widget["type"] == "vote":
        return item.get("text") == widget["question"]
    return True


def new_rows(widgets):
    return [{"widget": widget, "state": "PENDING"} for widget in widgets]


def overall(rows):
    return "VERIFIED" if all(row["state"] == "VERIFIED" for row in rows) else "PARTIAL"


def send(work, rows, save, again=False, sent_count=None):
    """Reads, sends what isn't there, and reads back. `save` runs before each send, so an attempt that dies is on record as one that may have gone. Returns the rows and the state.
    What may have gone is only read again, unless `again` says a person has looked: a repeat would put a second one on the video."""
    rows = [dict(row) for row in rows]
    pages = sorted({row["widget"]["page"] for row in rows})
    try:
        listed = {page: read(work, page) for page in pages}
    except VpubError as exc:
        return rows, {"state": "WAITING_FOR_PUBLIC", "error": exc.code, "reason": exc.message,
                      "next": "Bilibili didn't list this video's widgets, so nothing was sent. It does this before a video is public, and a read can also just fail. Run this again once the video is public."}
    resend = {"PENDING", "NOT_APPLIED"} | ({"UNVERIFIED", "REFUSED", "CONFLICT", "SUBMITTING", "MISSING"} if again else set())
    went = False
    for row in rows:
        widget, items = row["widget"], listed[row["widget"]["page"]]
        found = next((item for item in items if shows(widget, item)), None)
        if found:
            row.update(state="VERIFIED", id=found.get("id"))
            row.pop("error", None), row.pop("reason", None)
            continue
        if row["state"] == "VERIFIED":
            row.update(state="MISSING", reason="This widget was on the video and Bilibili no longer lists it.")
            continue
        if row["state"] == "SUBMITTING":
            row["state"] = "UNVERIFIED"
        if row["state"] not in resend:
            continue
        if widget["type"] == "vote" and any(item.get("kind") == "vote" for item in items):
            row.update(state="CONFLICT", reason="This video already has a different vote, and Bilibili takes one.")
            continue
        if went:
            time.sleep(GAP_SECONDS)
        row["state"] = "SUBMITTING"
        row.pop("error", None), row.pop("reason", None)
        save(rows)
        try:
            adapters.bili(command(work, widget))
        except VpubError as exc:
            row.update(error=exc.code, reason=exc.message)
            if exc.code in STOPS_THE_REST:
                row["state"] = "PENDING" if exc.code in {"write_spacing", "stopped"} else "NOT_APPLIED"
                break
            if exc.code == "invalid_argument":
                # It answered with what's wrong, such as 79046 for a clash with another widget's time, and the same words would get the same answer.
                row["state"] = "REFUSED"
            elif exc.code in REFUSED:
                row["state"] = "NOT_APPLIED"
            else:
                row["state"] = "UNVERIFIED"
            continue
        went = True
        row["state"] = "SENT"
        if sent_count is not None:
            sent_count.append(1)
    if any(row["state"] == "SENT" for row in rows):
        try:
            listed = {page: read(work, page) for page in pages}
        except VpubError as exc:
            listed = None
            for row in rows:
                if row["state"] == "SENT":
                    row.update(state="UNVERIFIED", error=exc.code, reason="It was sent, and the read after it failed. Reading again settles it.")
        if listed:
            for row in rows:
                if row["state"] == "SENT":
                    found = next((item for item in listed[row["widget"]["page"]] if shows(row["widget"], item)), None)
                    row.update(state="VERIFIED", id=found.get("id")) if found else row.update(state="UNVERIFIED", reason="It was sent, and Bilibili doesn't list it yet.")
    state = overall(rows)
    answer = {"state": state}
    if state != "VERIFIED":
        answer["next"] = "See each widget's state. UNVERIFIED may already be on the video, so it's only read again. A REFUSED one needs changing, usually to another time."
    return rows, answer


def finish(receipt, checkpoint=None):
    """Widgets chosen in a plan, sent once the video can take them. With nowhere to record an attempt first, it only reads."""
    wanted = receipt.get("pending_widgets")
    if not wanted or receipt.get("widgets_state") == "VERIFIED" or checkpoint is None:
        return receipt
    kept = {key: value for key, value in receipt.items() if key not in {"widgets_reason", "widgets_error", "widgets_next"}}

    def save(rows):
        checkpoint({**kept, "widgets": rows, "widgets_state": "SUBMITTING"})
    path = record_path(receipt["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    # vpub verify holds no other lock, and two of them at once would both find these missing. The second reads first when its turn comes, so it finds them there.
    try:
        with receipt_turn(path):
            rows, answer = send(receipt["id"], receipt.get("widgets") or new_rows(wanted), save)
    except VpubError as exc:
        if exc.code != "attempt_busy":
            raise
        return {**kept, "widgets_next": "Another command is sending this video's widgets. Run vpub verify again in a minute."}
    result = {**kept, "widgets": rows, "widgets_state": answer["state"]}
    for name, field in (("error", "widgets_error"), ("reason", "widgets_reason"), ("next", "widgets_next")):
        if name in answer:
            result[field] = answer[name]
    return result


def seen(work, rows=None):
    """What's on a video right now, for its record after an update, and any it had that it's lost. The owner asked that this be looked at after every one. A failed read is an unknown, not none."""
    rows = rows or []
    try:
        listed = {page: read(work, page) for page in sorted({1} | {row["widget"]["page"] for row in rows})}
    except VpubError as exc:
        return {"state": "UNKNOWN", "error": exc.code}
    items = [item for found in listed.values() for item in found]
    result = {"state": "READ", "count": len(items), "items": [{name: item.get(name) for name in ("id", "kind", "time", "text")} for item in items]}
    missing = [row["widget"] for row in rows if row["state"] == "VERIFIED" and not any(shows(row["widget"], item) for item in listed[row["widget"]["page"]])]
    return {**result, "missing": missing} if missing else result


def checked_work(work):
    if not BV.fullmatch(str(work)):
        raise VpubError("invalid_id", "Use the video's BV number.")
    return work


def record_path(work):
    return home() / "widgets" / f"{work}.json"


def record_of(work):
    path = record_path(work)
    return read_json(path) if path.exists() else {"work": work, "rows": []}


def listing(work, page=1):
    return {"work": checked_work(work), "page": page, "items": read(work, page), "platform_writes": 0}


def add(work, file, dry_run=False, again=False):
    """A file's widgets on a video that's already up. Running it again sends only what isn't there."""
    checked_work(work)
    widgets = validate(read_json(Path(file)))
    if dry_run:
        try:
            listed = {page: read(work, page) for page in {widget["page"] for widget in widgets}}
        except VpubError as exc:
            return {"work": work, "state": "WAITING_FOR_PUBLIC", "error": exc.code, "reason": exc.message, "platform_writes": 0}
        missing = [widget for widget in widgets if not any(shows(widget, item) for item in listed[widget["page"]])]
        checks = [adapters.bili(command(work, widget) + ["--dry-run"]) for widget in missing]
        return {"work": work, "state": "DRY_RUN", "already_there": len(widgets) - len(missing), "would_send": checks, "platform_writes": 0}
    path = record_path(work)
    path.parent.mkdir(parents=True, exist_ok=True)
    with publication_lock(), receipt_turn(path):
        record = record_of(work)
        known = {digest(row["widget"]): row for row in record["rows"]}
        wanted = {digest(widget) for widget in widgets}
        rows = [known.get(digest(widget)) or {"widget": widget, "state": "PENDING"} for widget in widgets]
        others = [row for key, row in known.items() if key not in wanted]

        def save(now_rows):
            atomic_json(path, {**record, "rows": others + now_rows, "updated_at": now()})
        sent = []
        rows, answer = send(work, rows, save, again, sent)
        save(rows)
    return {"work": work, **answer, "widgets": rows, "platform_writes": len(sent)}


def verify(work):
    """Only reads, and says where this video's widgets stand against what was asked for."""
    checked_work(work)
    path = record_path(work)
    if not path.exists():
        return {**listing(work), "state": "NOTHING_ON_RECORD"}
    with receipt_turn(path):
        record = record_of(work)
        rows = [dict(row) for row in record["rows"]]
        try:
            listed = {page: read(work, page) for page in sorted({row["widget"]["page"] for row in rows})}
        except VpubError as exc:
            return {"work": work, "state": "WAITING_FOR_PUBLIC", "error": exc.code, "reason": exc.message, "widgets": rows, "platform_writes": 0}
        for row in rows:
            found = next((item for item in listed[row["widget"]["page"]] if shows(row["widget"], item)), None)
            if found:
                row.update(state="VERIFIED", id=found.get("id"))
                row.pop("error", None), row.pop("reason", None)
            elif row["state"] == "VERIFIED":
                row.update(state="MISSING", reason="This widget was on the video and Bilibili no longer lists it.")
            elif row["state"] == "SUBMITTING":
                row["state"] = "UNVERIFIED"
        atomic_json(path, {**record, "rows": rows, "updated_at": now()})
    return {"work": work, "state": overall(rows) if rows else "NOTHING_ON_RECORD", "widgets": rows, "platform_writes": 0}


def remove(work, widget_id, page=1, dry_run=False):
    """One widget off a video, read back. A reserve widget is left alone, since removing one notifies everyone who booked it."""
    checked_work(work)
    target = next((item for item in read(work, page) if str(item.get("id")) == str(widget_id)), None)
    if target is None:
        raise VpubError("not_found", "This video doesn't list a widget with that ID, so nothing was removed.", "Use vpub widgets list to see them.")
    if target.get("kind") == "reserve":
        raise VpubError("reserve_not_handled", "Removing a reserve widget notifies everyone who booked it, so CrossCast leaves it alone.", "Use bili danmaku interact remove yourself if you mean to.")
    if dry_run:
        return {"work": work, "state": "DRY_RUN", "would_remove": target, "platform_writes": 0}
    path = record_path(work)
    path.parent.mkdir(parents=True, exist_ok=True)
    with publication_lock(), receipt_turn(path):
        record = record_of(work)
        try:
            adapters.bili(["danmaku", "interact", "remove", work, str(widget_id), "--page", str(page)])
        except VpubError as exc:
            if exc.code in REFUSED or exc.code == "stopped":
                raise
        try:
            gone = not any(str(item.get("id")) == str(widget_id) for item in read(work, page))
        except VpubError:
            gone = False
        if gone:
            atomic_json(path, {**record, "rows": [row for row in record["rows"] if str(row.get("id")) != str(widget_id)], "updated_at": now()})
    return {"work": work, "id": str(widget_id), "state": "REMOVED" if gone else "REMOVE_UNVERIFIED", "platform_writes": 1,
            **({} if gone else {"next": "It may be gone already. Run vpub widgets list before trying again."})}

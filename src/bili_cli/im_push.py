"""The web message center's push channel for private messages (私信).

message.bilibili.com does not poll for new messages. It keeps one WebSocket to
broadcast.chat.bilibili.com and subscribes to the IM notify stream; when a
conversation changes it is told the other side's UID and only then fetches that
conversation. Every 20 s it also polls once as a fallback. `bili dm watch` does
the same.

Frames are protobuf (bilibili.broadcast.v1). We only need a handful of fields,
so they are encoded and decoded by hand rather than pulling in a protobuf
runtime:

    BroadcastFrame { FrameOption options = 1; string target_path = 2; Any body = 3; }
    FrameOption    { int64 message_id = 1; int64 sequence = 2; ... }
    Any            { string type_url = 1; bytes value = 2; }
    AuthReq        { string guid = 1; }
    TargetPath     { repeated string target_paths = 1; }
    NotifyRsp      { int64 uid = 1; int32 cmd = 2; Payload payload = 3; ... }

A NotifyRsp payload either names a conversation that changed (field 3:
{1: session_type, 2: talker uid}) or carries a small notice JSON (field 2,
e.g. {"sender_id": ..., "msg_key": 0}) that only refreshes unread badges.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

WS_URL = "wss://broadcast.chat.bilibili.com:7826/sub"
AUTH = "/bilibili.broadcast.v1.Broadcast/Auth"
SUBSCRIBE = "/bilibili.broadcast.v1.Broadcast/Subscribe"
HEARTBEAT = "/bilibili.broadcast.v1.Broadcast/Heartbeat"
WATCH_NOTIFY = "/bilibili.broadcast.message.im.Notify/WatchNotify"
TYPE = "type.googleapis.com/"


# ---------------- protobuf wire format, just enough for these frames ----------------
def _varint(n: int) -> bytes:
    out = bytearray()
    n &= (1 << 64) - 1
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field_len(field: int, payload: bytes) -> bytes:
    return _varint(field << 3 | 2) + _varint(len(payload)) + payload


def _field_int(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def parse(buf: bytes) -> dict[int, list[Any]]:
    """Decode one message into {field: [values]}; ints for varints, bytes for length-delimited."""
    out: dict[int, list[Any]] = {}
    i, n = 0, len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _read_varint(buf, i)
        elif wire == 2:
            size, i = _read_varint(buf, i)
            val, i = bytes(buf[i:i + size]), i + size
        elif wire == 1:
            val, i = int.from_bytes(buf[i:i + 8], "little"), i + 8
        elif wire == 5:
            val, i = int.from_bytes(buf[i:i + 4], "little"), i + 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        out.setdefault(field, []).append(val)
    return out


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if b < 0x80:
            return result, i
        shift += 7


def _first(msg: dict[int, list[Any]], field: int, default: Any = None) -> Any:
    vals = msg.get(field)
    return vals[0] if vals else default


# ---------------- frames ----------------
def frame(sequence: int, path: str, type_name: str, value: bytes) -> bytes:
    body = _field_len(1, (TYPE + type_name).encode()) + _field_len(2, value)
    return _field_len(1, _field_int(2, sequence)) + _field_len(2, path.encode()) + _field_len(3, body)


def auth_frame(sequence: int = 1, guid: str | None = None) -> bytes:
    return frame(sequence, AUTH, "bilibili.broadcast.v1.AuthReq", _field_len(1, (guid or str(uuid.uuid1())).encode()))


def subscribe_frame(sequence: int = 2, path: str = WATCH_NOTIFY) -> bytes:
    return frame(sequence, SUBSCRIBE, "bilibili.broadcast.v1.TargetPath", _field_len(1, path.encode()))


def heartbeat_frame(sequence: int) -> bytes:
    return frame(sequence, HEARTBEAT, "bilibili.broadcast.v1.HeartbeatReq", b"")


@dataclass
class Frame:
    path: str
    type_name: str
    value: bytes
    message_id: int | None
    status: dict[int, list[Any]] | None   # set when the server answers with an error status


def decode_frame(buf: bytes) -> Frame:
    msg = parse(buf)
    options = parse(_first(msg, 1, b""))
    body = parse(_first(msg, 3, b""))
    type_url = (_first(body, 1, b"") or b"").decode("utf-8", "replace")
    status = _first(options, 4)
    return Frame(
        path=(_first(msg, 2, b"") or b"").decode("utf-8", "replace"),
        type_name=type_url[len(TYPE):] if type_url.startswith(TYPE) else type_url,
        value=_first(body, 2, b"") or b"",
        message_id=_first(options, 1),
        status=parse(status) if isinstance(status, bytes) and status else None,
    )


@dataclass
class Notify:
    """One push from the IM notify stream."""

    kind: str                   # "session": a conversation changed; "notice": an unread badge changed
    talker: int | None = None   # the other side of the conversation (kind == "session")
    session_type: int | None = None
    notice: dict[str, Any] | None = None


def decode_notify(value: bytes) -> Notify:
    rsp = parse(value)
    payload = parse(_first(rsp, 3, b"") or b"")
    session = _first(payload, 3)
    if isinstance(session, bytes):
        s = parse(session)
        return Notify("session", talker=_first(s, 2), session_type=_first(s, 1))
    inner = _first(payload, 2)
    notice: dict[str, Any] = {}
    if isinstance(inner, bytes):
        try:
            fields = parse(inner)
            raw = _first(fields, 6)
            notice = json.loads(raw) if isinstance(raw, bytes) else {}
            notice["code"] = _first(fields, 5)
        except (ValueError, json.JSONDecodeError):
            notice = {}
    return Notify("notice", notice=notice)

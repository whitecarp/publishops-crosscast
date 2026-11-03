"""Bounded local checks. Saved files are not proof that a sign-in still works."""
from __future__ import annotations

import os
from pathlib import Path
import sys

from .core import VpubError, home, stop_check


def inspect_video(path):
    """Reject obvious renamed/non-container files without a decoder or network.

    This deliberately makes no playable-video or codec claim. In particular, a
    recognised container may be damaged later in the file or contain only audio.
    """
    path = Path(path)
    if not path.is_file():
        raise VpubError("invalid_video", "Video file was not found.", "Choose the exported video file.")
    size = path.stat().st_size
    with path.open("rb") as stream:
        head = stream.read(832)
    container = None
    if len(head) >= 16:
        length = int.from_bytes(head[:4], "big")
        minimum = 8
        if length == 1:
            length, minimum = int.from_bytes(head[8:16], "big"), 16
        if length == 0:
            length = size
        if minimum <= length <= size and head[4:8] in {b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip",
                                                     b"uuid", b"pnot", b"udta", b"junk", b"pict", b"ediw", b"prfl", b"sidx", b"styp"}:
            container = "MP4/MOV"
    if len(head) >= 16 and head[:4] == b"\x1aE\xdf\xa3":
        container = "WebM/Matroska"
    elif len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        container = "AVI"
    elif len(head) >= 13 and head[:4] == b"FLV\x01" and 9 <= int.from_bytes(head[5:9], "big") <= size:
        container = "FLV"
    elif any(len(head) >= offset + step * 3 + 4 and all(head[offset + i * step] == 0x47 for i in range(4))
             for step, offset in ((188, 0), (192, 4), (204, 0))):
        container = "MPEG-TS"
    if not container:
        raise VpubError("invalid_video", "This file does not have a supported video-container header; renaming a text or image file is not enough.",
                        "Export a real MP4, MOV, WebM or MKV video and select that file.")
    return {"state": "CONTAINER_HEADER_RECOGNIZED", "container": container,
            "playback_verified": False,
            "note": "Header check only: video tracks, decoding, duration and platform acceptance have not been checked."}


def _nonempty(path):
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _profile_present(path):
    try:
        return path.is_dir() and next(path.iterdir(), None) is not None
    except OSError:
        return False


def _bili_home():
    # Same location as the bundled bili_cli.config.home, without importing its
    # API dependency, creating folders or reading any credential values.
    override = os.environ.get("BILI_CLI_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "bili-cli"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "bili-cli"


def sign_in(platform, options):
    method, account = options["method"], options["account"]
    root = home() / "accounts" / account
    command = f"vpub auth status {platform} --account {account} --method {method}"
    saved = None
    if method == "browser":
        saved = (_nonempty(root / (platform + "-identity.json")) or
                 _nonempty(root / (platform + "-browser-session.json")) or
                 _profile_present(root / (platform + "-browser-profile")))
    elif platform == "bilibili" and method == "cli":
        saved = _nonempty(_bili_home() / "credential.json")
    elif platform == "youtube" and method == "api":
        saved = _nonempty(root / "youtube-token.json")
    if saved is False:
        command = f"vpub auth login {platform} --account {account} --method {method}"
    return {"method": method, "saved_record_present": saved,
            "state": "NO_SAVED_SIGN_IN" if saved is False else "SAVED_SIGN_IN_UNVERIFIED" if saved else "LIVE_CHECK_REQUIRED",
            "logged_in": None, "next_command": command,
            "next_command_required": saved is False,
            "note": "Sign in when missing. Otherwise a separate auth status is optional: publishing already checks the live account."}


def dry_run(plan, media=None):
    if media is None:
        media = inspect_video(plan["video"])
    accounts = {platform: sign_in(platform, options) for platform, options in plan["platforms"].items()}
    blockers = []
    for platform, account in accounts.items():
        if account["saved_record_present"] is False:
            blockers.append({"platform": platform, "code": "no_saved_sign_in", "next_command": account["next_command"]})
        try:
            stop_check(platform)
        except VpubError as error:
            blockers.append({"platform": platform, "code": error.code, "message": error.message, "hint": error.hint})
    return {"mode": "LOCAL_REHEARSAL", "ready": False if blockers else None,
            "ready_to_publish": False if blockers else None,
            "state": "LOCAL_BLOCKERS" if blockers else "LIVE_CHECK_REQUIRED",
            "sign_ins": accounts, "blockers": blockers, "media_check": media,
            "platform_requests": 0, "platform_writes": 0,
            "note": "No login, account identity, permissions or playback were verified. Saved sign-in files may be stale. Publishing checks the live account; separate auth status calls are optional."}

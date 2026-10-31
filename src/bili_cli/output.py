"""Everything that reaches stdout goes through this module.

stdout carries exactly one JSON document per invocation:
    {"ok": true, "data": ...}
    {"ok": false, "error": {"code", "message", "hint"?, "detail"?}}
Human-oriented extras (QR code art, progress) go to stderr.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, NoReturn

import typer

from .errors import BiliError


@dataclass
class Settings:
    pretty: bool | None = None  # None = indent only when stdout is a terminal
    raw: bool = False
    dry_run: bool = False
    wait: bool = False  # sleep through short local rate limits instead of failing


settings = Settings()


def _dump(obj: Any) -> str:
    pretty = settings.pretty if settings.pretty is not None else sys.stdout.isatty()
    return json.dumps(obj, ensure_ascii=False, indent=2 if pretty else None, default=str)


def _write(text: str) -> None:
    # Windows consoles may default to a legacy code page; always emit UTF-8.
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.flush()


def success(data: Any) -> None:
    _write(_dump({"ok": True, "data": data}))


def fail(err: BiliError) -> NoReturn:
    _write(_dump({"ok": False, "error": err.to_dict()}))
    raise typer.Exit(err.exit_code)


def info(message: str) -> None:
    """Human-readable message on stderr; never parsed by agents."""
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def dry_run(action: str, params: dict[str, Any]) -> None:
    success({"dry_run": True, "action": action, "params": params})

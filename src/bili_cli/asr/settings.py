"""asr.json: configured engines and the default. Holds API keys, so it is written owner-only."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .. import config


def path() -> Path:
    return config.home() / "asr.json"


def models_dir() -> Path:
    d = config.home() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load() -> dict[str, Any]:
    try:
        return json.loads(path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save(data: dict[str, Any]) -> None:
    p = path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.name == "posix":
        os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def masked(data: dict[str, Any]) -> dict[str, Any]:
    """Copy safe to print: secrets reduced to their last 4 characters."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out[k] = masked(v)
        elif isinstance(v, str) and any(s in k for s in ("key", "secret")):
            out[k] = f"***{v[-4:]}" if v else ""
        else:
            out[k] = v
    return out

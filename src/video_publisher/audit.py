"""Identify the running Python sources without recording machine-specific locations."""
from functools import lru_cache
from hashlib import sha256
from importlib import metadata
import json
from pathlib import Path
import re
import sys
from threading import Lock
from urllib.parse import urlsplit

from . import __version__


def source_digest(package_dirs):
    files = sorted((f.relative_to(root.parent).as_posix(), f)
                   for root in package_dirs for f in root.rglob("*.py") if f.is_file())
    digest = sha256()
    for name, path in files:
        content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(name.encode("utf-8") + b"\0" + sha256(content).hexdigest().encode("ascii") + b"\n")
    return digest.hexdigest()


def direct_url():
    try:
        value = json.loads(metadata.distribution("publishops-crosscast").read_text("direct_url.json") or "null")
    except (OSError, ValueError, TypeError, metadata.PackageNotFoundError):
        return None
    return value if isinstance(value, dict) else None


def provenance(info):
    info = info or {}
    vcs = info.get("vcs_info")
    if isinstance(vcs, dict):
        commit = vcs.get("commit_id")
        return "vcs", commit.lower() if isinstance(commit, str) and re.fullmatch("[0-9A-Fa-f]{6,64}", commit) else None
    if isinstance(info.get("dir_info"), dict):
        return "local", None
    if isinstance(info.get("archive_info"), dict):
        try:
            path = urlsplit(info.get("url") or "").path
            match = re.search(r"(?:^|/)([a-fA-F0-9]{40})(?:\.(?:zip|tar(?:\.gz|\.bz2|\.xz)?|tgz))?/?$", path)
        except (ValueError, TypeError):
            match = None
        return "archive", match.group(1).lower() if match else None
    return "unknown", None


_lock = Lock()


@lru_cache(maxsize=1)
def _stamp():
    import bili_cli
    digest = source_digest((Path(__file__).resolve().parent, Path(bili_cli.__file__).resolve().parent))
    kind, commit = provenance(direct_url())
    return dict(version=__version__, source_digest=digest, install=kind, commit=commit,
                python=sys.version.split()[0], os=sys.platform)


def code_stamp():
    # Parallel publication workers can reach the first lookup together.
    with _lock:
        return _stamp().copy()


def stamped(record, previous):
    """Preserve the creator. A legacy record has only an observed latest writer."""
    current = code_stamp()
    result = dict(record)
    result.pop("code_last", None)
    if previous is None:
        result["code"] = current
    else:
        result.pop("code", None)
        if "code" in previous:
            result["code"] = previous["code"]
        if previous.get("code") != current:
            result["code_last"] = current
    return result

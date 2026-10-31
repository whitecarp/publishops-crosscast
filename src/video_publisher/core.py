"""Local-only validation, approval hashes and crash-safe publication ledger."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone

PLATFORMS = ("bilibili", "youtube", "douyin", "instagram", "x")


class VpubError(Exception):
    def __init__(self, code, message, hint=""):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


def now():
    return datetime.now(timezone.utc).isoformat()


def home():
    override = os.environ.get("VPUB_HOME")
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share"))
    p = Path(override) if override else base / "WhiteCarpVideoPublisher"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p.resolve()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".vpub-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def snapshot_file(source, target, sha, link=False):
    """Give target exactly the approved bytes; a cut-off copy never looks like a changed source.

    link shares the bytes instead of copying them, for a source nothing else will change.
    """
    target = Path(target)
    if target.exists() and file_sha(target) == sha:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".vpub-", dir=target.parent)
    os.close(fd)
    try:
        if link:
            os.unlink(name)
            try:
                os.link(source, name)
            except OSError:
                shutil.copyfile(source, name)
        else:
            shutil.copyfile(source, name)
        if file_sha(name) != sha:
            raise VpubError("source_changed", "A file differs from the approved plan; create a fresh plan.")
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return target


def account_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", value):
        raise VpubError("invalid_account", "Account alias must be 1–40 letters, digits, _ or -.")
    return value


def config():
    p = home() / "config.json"
    return read_json(p) if p.exists() else {}


def platform_check(platform):
    if platform == "tiktok":
        raise VpubError("platform_suspended", "TikTok support is suspended after an unsuccessful Hong Kong phone-number login test.",
                        "See docs/TIKTOK_STATUS.md. Existing receipts are retained; no new login or upload is allowed.")
    if platform not in PLATFORMS:
        raise VpubError("invalid_platform", "Choose " + ", ".join(PLATFORMS))


def language_tag(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", value):
        raise VpubError("invalid_language", "Use a language tag such as zh-Hans, zh-Hant, en, ja, or und (unspecified).")
    return value


def x_post_text(title, description, tags, ai_generated, content_language="und"):
    # X has one post body, not separate title/description/tag fields.
    parts = [title, description, " ".join("#" + t.lstrip("#") for t in tags)]
    if ai_generated:
        parts.append({"zh-hans": "AI 生成内容 / AI-generated", "zh-hant": "AI 生成內容 / AI-generated"}.get(content_language.lower(), "AI-generated"))
    return "\n\n".join(p for p in parts if p)


def title_limit(platform, method=None):
    # X browser publishing goes through Media Studio, whose own title field stops at 70.
    if platform == "x" and (method or "browser") == "browser":
        return 70
    return {"bilibili": 80, "youtube": 100, "douyin": 30, "x": 100, "instagram": 100}[platform]


def douyin_caption(description, tags):
    return "\n\n".join(p for p in (description, " ".join("#" + t.lstrip("#") for t in tags)) if p)


def stop_check(platform=None):
    candidates = [home() / "STOP.json"]
    external = config().get("external_stop_file")
    if external:
        candidates.append(Path(external))
    for index, path in enumerate(candidates):
        # Presence stops publication; malformed stop files also stop it.
        if path.exists():
            if index == 1 and platform and platform != "bilibili":
                try:
                    signal = read_json(path)
                except (OSError, ValueError):
                    signal = None
                # Recognize only this exact, explicit scope. Unknown/malformed
                # signals and this tool's own STOP remain global and fail closed.
                if isinstance(signal, dict) and signal.get("state") == "STOP_ALL_BILIBILI_READ_WRITE":
                    continue
            raise VpubError("stopped", f"Publication stop file exists: {path}",
                            "Inspect the stop reason. Only the operator may clear it.")


def load_plan(path, selected=None):
    path = Path(path).resolve()
    m = read_json(path)
    if m.get("schema_version") != 1:
        raise VpubError("invalid_manifest", "schema_version must be 1.")
    allowed = {"schema_version", "video", "cover", "covers", "title", "description", "tags", "platforms", "content_language"}
    if set(m) - allowed:
        raise VpubError("invalid_manifest", "Unknown top-level fields: " + ", ".join(sorted(set(m) - allowed)))
    vp = (path.parent / m.get("video", "")).resolve()
    if not vp.is_file() or vp.suffix.lower() not in {".mp4", ".mov", ".webm", ".mkv"} or vp.stat().st_size == 0:
        raise VpubError("invalid_video", "Select a complete, nonempty video file.")
    cover = (path.parent / m["cover"]).resolve() if m.get("cover") else None
    if cover and (not cover.is_file() or cover.suffix.lower() not in {".jpg", ".jpeg", ".png"}):
        raise VpubError("invalid_cover", "Cover must be an existing JPG or PNG.")
    entries = m.get("platforms")
    if isinstance(entries, dict) and "tiktok" in entries:
        platform_check("tiktok")
    if not isinstance(entries, dict) or not entries or set(entries) - set(PLATFORMS):
        raise VpubError("invalid_platform", "Choose bilibili, youtube, douyin, instagram and/or x.")
    chosen = selected or list(entries)
    if not chosen or set(chosen) - set(entries) or len(chosen) != len(set(chosen)):
        raise VpubError("invalid_platform", "Selected platforms must exist in the manifest and be unique.")
    plans = {}
    for platform in chosen:
        v = entries[platform]
        if not isinstance(v, dict):
            raise VpubError("invalid_manifest", "Each platform needs an object.")
        permitted = {"account", "title", "description", "tags", "visibility", "ai_generated", "schedule", "content_language", "metadata_language", "metadata_mode", "chapters", "duration_seconds"}
        permitted |= {"tid", "expected_uid", "method"} if platform == "bilibili" else set()
        permitted |= {"category_id", "made_for_kids", "expected_channel_id", "method", "playlist_ids", "playlist_titles"} if platform == "youtube" else set()
        permitted |= {"method", "expected_identity"} if platform == "douyin" else set()
        permitted |= {"method", "expected_identity", "submission_mode"} if platform in {"x", "instagram"} else set()
        if set(v) - permitted:
            raise VpubError("invalid_manifest", f"Unsupported {platform} options: {sorted(set(v) - permitted)}")
        if v.get("account", "default") != "default":
            raise VpubError("unsupported_account", "v0.1 supports one account per platform: default.")
        if platform == "youtube" and v.get("method", "native") not in {"native", "api"}:
            raise VpubError("invalid_method", "YouTube method must be native or api. The screen-relay prototype was retired.")
        if platform == "youtube":
            from .categories import validate_youtube
            validate_youtube(v.get("category_id"))
            for field in ("playlist_ids", "playlist_titles"):
                values = v.get(field, [])
                if not isinstance(values, list) or len(values) > 20 or not all(isinstance(x, str) and x.strip() and len(x) <= 150 for x in values) or len(set(values)) != len(values):
                    raise VpubError("invalid_playlist", "Use up to 20 unique playlist IDs/titles.")
            if any(not re.fullmatch(r"PL[A-Za-z0-9_-]{8,148}", x) for x in v.get("playlist_ids", [])):
                raise VpubError("invalid_playlist", "Use custom playlist IDs copied from YouTube, not video IDs or system lists.")
            if v.get("method") == "api" and v.get("playlist_titles"):
                raise VpubError("invalid_playlist", "API mode requires exact playlist_ids; names may be ambiguous.")
        if platform == "douyin" and v.get("method", "browser") not in {"native", "browser"}:
            raise VpubError("invalid_method", "Douyin method must be native or browser.")
        if platform == "douyin" and "expected_identity" in v:
            if not isinstance(v["expected_identity"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", v["expected_identity"]):
                raise VpubError("invalid_identity", "Use the Douyin account ID observed in the creator dashboard, not the display name.")
        if platform == "bilibili" and v.get("method", "cli") not in {"native", "cli"}:
            raise VpubError("invalid_method", "Bilibili method must be native or cli.")
        if platform in {"x", "instagram"} and v.get("method", "browser") not in {"native", "browser", "api"}:
            raise VpubError("invalid_method", "X/Instagram method must be browser, api or native.")
        if platform in {"x", "instagram"} and v.get("method") == "api" and v.get("submission_mode", "publish") != "publish":
            raise VpubError("invalid_submission_mode", "The API routes have no drafts; use browser or native for draft_only.")
        if platform in {"x", "instagram"} and v.get("submission_mode", "publish") not in {"publish", "draft_only"}:
            raise VpubError("invalid_submission_mode", "Use publish or draft_only. Draft-only permits media upload/preview, never Share/Post or scheduling.")
        if platform == "x" and v.get("expected_identity") is not None and not re.fullmatch(r"[A-Za-z0-9_]{1,15}", str(v["expected_identity"])):
            raise VpubError("invalid_identity", "Use the X handle without @, as observed in the signed-in profile.")
        if platform == "x" and cover and v.get("method", "browser") == "native":
            raise VpubError("unsupported_cover", "X native alpha uses the video's default thumbnail; shared covers are not implemented.")
        title = v.get("title", m.get("title", ""))
        desc = v.get("description", m.get("description", ""))
        tags = v.get("tags", m.get("tags", []))
        limit = title_limit(platform, v.get("method"))
        if not isinstance(title, str) or not title.strip() or len(title) > limit or any(c in title for c in "\r\n\x00"):
            raise VpubError("invalid_title", f"{platform} title must contain 1–{limit} characters, one line.")
        if not isinstance(desc, str) or "\x00" in desc or len(desc) > (2000 if platform != "youtube" else 5000):
            raise VpubError("invalid_description", f"Invalid {platform} description.")
        if platform == "youtube" and len(desc.encode("utf-8")) > 5000:
            raise VpubError("invalid_description", "YouTube description exceeds 5000 UTF-8 bytes.")
        if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() and not any(c in t for c in ",\r\n\x00") for t in tags):
            raise VpubError("invalid_tags", "Tags must be a list of nonempty strings without commas/newlines.")
        if platform == "bilibili" and not 1 <= len(tags) <= 10:
            raise VpubError("invalid_tags", "Bilibili requires 1–10 tags.")
        if platform == "youtube" and sum(len(t) + 1 for t in tags) > 500:
            raise VpubError("invalid_tags", "YouTube tags exceed the 500-character budget.")
        if platform == "douyin" and len(douyin_caption(desc, tags).encode("utf-16-le")) // 2 > 1000:
            raise VpubError("invalid_description", "Douyin description plus hashtags exceeds 1000 UTF-16 units.")
        visibility = v.get("visibility")
        supported = {"bilibili": {"public", "private"}, "youtube": {"public", "private", "unlisted"}, "douyin": {"public", "private"},
                     "x": {"account"}, "instagram": {"account"}}
        if visibility not in supported[platform]:
            raise VpubError("invalid_visibility", f"Explicit {platform} visibility required: {sorted(supported[platform])}")
        # Every route supports both values: AI-generated videos get each platform's
        # AI-content declaration, ordinary videos get none. The value must be explicit.
        if type(v.get("ai_generated")) is not bool:
            raise VpubError("invalid_declaration", "Explicit ai_generated true/false required per platform.")
        if platform == "youtube" and type(v.get("made_for_kids")) is not bool:
            raise VpubError("invalid_audience", "YouTube made_for_kids must be explicitly true/false.")
        schedule = v.get("schedule")
        if schedule:
            if platform in {"douyin", "x", "instagram"} or v.get("method") == "native":
                raise VpubError("unsupported_schedule", "This route supports immediate publication only.")
            if platform == "youtube" and v.get("method", "native") != "api":
                raise VpubError("unsupported_schedule", "YouTube browser v0.1 supports immediate publication; scheduled API mode is optional.")
            try:
                when = datetime.fromisoformat(schedule.replace("Z", "+00:00"))
                delta = (when - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, AttributeError):
                raise VpubError("invalid_schedule", "Use a future ISO timestamp with an explicit timezone.")
            if delta < 60 or (platform == "bilibili" and not 7200 <= delta <= 15 * 86400):
                raise VpubError("invalid_schedule", "Schedule is too near/past or outside Bilibili's 2h–15d range.")
            if visibility != "private" and platform == "youtube":
                raise VpubError("invalid_schedule", "YouTube schedule requires visibility=private.")
        plans[platform] = {**v, "account": account_name(v.get("account", "default")),
                           "title": title, "description": desc, "tags": tags, "visibility": visibility,
                           "content_language": language_tag(v.get("content_language", m.get("content_language", "und")))}
        if "metadata_language" in v:
            plans[platform]["metadata_language"] = language_tag(v["metadata_language"])
        if v.get("metadata_mode", "target_only") not in {"target_only", "bilingual"}:
            raise VpubError("invalid_metadata_mode", "Choose target_only or bilingual.")
        if 'metadata_mode' in v:
            from .language_policy import route
            policy = route(plans[platform]['content_language'], platform)
            if v['metadata_mode'] != policy['mode'] or v.get('metadata_language', policy['language']) != policy['language']:
                raise VpubError('wrong_metadata_language', 'Metadata mode/language conflicts with the video content language. Run vpub metadata request/apply.')
        if platform == "youtube":
            plans[platform]["method"] = v.get("method", "native")
        if platform == "douyin":
            plans[platform]["method"] = v.get("method", "browser")
        if platform in {"x", "instagram"}:
            plans[platform]["method"] = v.get("method", "browser")
        if platform in {"x", "instagram"}:
            if any(not re.fullmatch(r"#?\w+", t) for t in tags):
                raise VpubError("invalid_tags", "Caption hashtags must contain only letters, digits or underscores, without spaces.")
            plans[platform]["post_text"] = x_post_text(title, desc, tags, v["ai_generated"], plans[platform].get("metadata_language", plans[platform]["content_language"]))
            if platform == "instagram" and len(plans[platform]["post_text"]) > 2200:
                raise VpubError("invalid_description", "Instagram captions are limited to 2200 characters.")
            # The actual website counter (URL/emoji/CJK weights, account plan)
            # is authoritative. Never silently truncate or assume Premium.
        if platform == "bilibili":
            plans[platform]["method"] = v.get("method", "cli")
        if "chapters" in v:
            from .chapters import validate
            validate(v["chapters"], platform, plans[platform]["method"], v.get("duration_seconds"))
    covers = {}
    supplied_covers = m.get("covers", {})
    if not isinstance(supplied_covers, dict) or set(supplied_covers) - set(PLATFORMS):
        raise VpubError("invalid_cover", "covers maps supported platforms to landscape/portrait files.")
    for platform in plans:
        variants = supplied_covers.get(platform, {})
        if not isinstance(variants, dict) or set(variants) - {"landscape", "portrait"}:
            raise VpubError("invalid_cover", "Use landscape and/or portrait cover roles.")
        if variants and platform == "x" and plans[platform]["method"] == "native":
            raise VpubError("unsupported_cover", "This route cannot set custom covers.")
        if "portrait" in variants and platform in {"youtube", "bilibili", "x"}:
            raise VpubError("unsupported_cover", "This platform uses one landscape thumbnail in this workflow.")
        for role, filename in variants.items():
            if not isinstance(filename, str):
                raise VpubError("invalid_cover", "Each cover role needs a local file path.")
            asset = (path.parent / filename).resolve()
            if not asset.is_file() or asset.suffix.lower() not in {".jpg", ".jpeg", ".png"} or not asset.stat().st_size:
                raise VpubError("invalid_cover", "Each cover must be a complete JPG or PNG.")
            covers.setdefault(platform, {})[role] = {"path": str(asset), "sha256": file_sha(asset)}
    payload = {"schema_version": 1, "video": str(vp), "video_sha256": file_sha(vp),
               "video_bytes": vp.stat().st_size, "cover": str(cover) if cover else None,
               "cover_sha256": file_sha(cover) if cover else None, "platforms": plans}
    if covers:
        payload["covers"] = covers
    return {**payload, "confirmation": digest(payload)}


class Ledger:
    def __init__(self, path=None):
        self.path = Path(path or home() / "publications.sqlite3")
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS publications (
            key TEXT PRIMARY KEY, platform TEXT, account TEXT, media_sha TEXT,
            state TEXT, data TEXT, updated_at TEXT)""")
        self.db.commit()

    @staticmethod
    def key(platform, account, sha):
        return digest([platform, account, sha])

    def get(self, key):
        r = self.db.execute("SELECT * FROM publications WHERE key=?", (key,)).fetchone()
        return {**dict(r), "data": json.loads(r["data"])} if r else None

    def rows(self, platform=None):
        where, values = ("WHERE platform=?", (platform,)) if platform else ("", ())
        return [{**dict(r), "data": json.loads(r["data"])}
                for r in self.db.execute(f"SELECT * FROM publications {where} ORDER BY updated_at DESC", values)]

    def claim(self, platform, account, sha, data):
        key = self.key(platform, account, sha)
        try:
            self.db.execute("INSERT INTO publications VALUES (?,?,?,?,?,?,?)",
                            (key, platform, account, sha, "IN_PROGRESS", json.dumps(data), now()))
            self.db.commit()
            return key
        except sqlite3.IntegrityError:
            self.db.rollback()
            return None

    def save(self, key, state, data):
        self.db.execute("UPDATE publications SET state=?,data=?,updated_at=? WHERE key=?",
                        (state, json.dumps(data, ensure_ascii=False), now(), key))
        self.db.commit()

    def forget(self, key):
        """Release a claim only when nothing can have reached the platform."""
        self.db.execute("DELETE FROM publications WHERE key=? AND state='IN_PROGRESS'", (key,))
        self.db.commit()

    def close(self):
        self.db.close()


@contextlib.contextmanager
def publication_lock():
    from .process_lock import hold
    with hold(home() / 'publisher.lock', 'busy', 'Another publication command owns this workspace.'):
        yield

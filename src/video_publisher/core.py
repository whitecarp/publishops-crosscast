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
import time
import unicodedata
import uuid
from datetime import datetime, timezone

PLATFORMS = ("bilibili", "youtube", "douyin", "instagram", "x")
HELD_BACK = "write_spacing"
# The free version ships first. When the add-on can be bought, this is the one place its messages change.
NOT_ON_SALE = "The add-on isn't on sale yet."


class VpubError(Exception):
    def __init__(self, code, message, hint=""):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


class NothingSent(VpubError):
    """A refusal from a step that sends nothing, such as bili-cli's own dry run of an upload, so the same attempt can be made again once the plan is put right."""


def never_sent(exc):
    """bili-cli held this write back to keep its own spacing, or a step that sends nothing refused it, so nothing reached the platform, whatever the receipt said just before."""
    return isinstance(exc, NothingSent) or (isinstance(exc, VpubError) and exc.code == HELD_BACK)


def part_left_undone(receipt):
    """Chapters, widgets or a collection chosen at upload that did not land, on a work that did."""
    season = receipt.get("season")
    return receipt.get("chapters_state") not in {None, "VERIFIED"} or receipt.get("widgets_state") not in {None, "VERIFIED"} or (isinstance(season, dict) and season.get("added") is False)


def presses_on_record(saved, other):
    """What one copy of a browser receipt says about a press that the other doesn't. A killed command's worker finishes by itself, so either copy can be the newer, and a review found resume could press again after an older one was saved over a newer."""
    kept = {name: True for name in ("final_submit_attempted", "media_selected") if saved.get(name) and not other.get(name)}
    if (saved.get("final_presses") or 0) > (other.get("final_presses") or 0):
        kept["final_presses"] = saved["final_presses"]
    if kept.get("media_selected"):  # Resume finds the chosen file by these, so they go with it.
        kept.update({name: saved[name] for name in ("upload_filename", "media_url") if saved.get(name) and not other.get(name)})
    return kept


def verified_over(read, result, saved):
    """A verify's answer on top of the worker's own copy of a receipt, which can be newer than what it read. What it answers stands, even if that's the same as before, and what it took away stays away.
    Only what it never had stays as that copy has it. Reviews found that replacing the copy lost the name resume finds the chosen file by, and that going by what changed let an older success outlive a new look that found nothing."""
    merged = {**{name: value for name, value in saved.items() if name not in read}, **result}
    for copy in (saved, result):
        merged.update(presses_on_record(copy, merged))
    return merged


def reply_pressing(path, request, **details):
    """A reply's last step before its click. A command killed part way can still be running in the shared background browser, with a second attempt behind it,
    and each attempt writes over the same receipt. So only the attempt the receipt holds now gets past here, and only while it is still waiting."""
    with receipt_turn(path):
        saved = read_json(path)
        mark = request.get("attempt")
        if saved.get("state") != "REPLY_READY" or not mark or (saved.get("request") or {}).get("attempt") != mark:
            raise VpubError("attempt_exists", "This attempt at the reply has ended, or another got there first, so nothing more was sent.", "Use comments verify-reply; never send again.")
        saved.update(state="REPLY_SUBMITTING", **details)
        atomic_json(path, saved)
    return saved


def error_fields(exc, name="error"):
    """For a receipt: the code, and the words saying which step failed."""
    if isinstance(exc, VpubError):
        return {name: exc.code, "error_message": exc.message}
    return {name: type(exc).__name__, "error_message": None}


def now():
    return datetime.now(timezone.utc).isoformat()


def home():
    override = os.environ.get("VPUB_HOME")
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share"))
    p = Path(override) if override else base / "WhiteCarpVideoPublisher"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p.resolve()


def when_the_file_is_free(step):
    """On Windows a file being replaced refuses to open for a moment, and one being read refuses to be replaced. A CI run saw a second attempt at a message stop on this while a press was being recorded. Neither lasts."""
    for pause in (0.02, 0.05, 0.1, 0.2, 0.5, 1, 2):
        try:
            return step()
        except PermissionError:
            if os.name != "nt":
                raise
            time.sleep(pause)
    return step()


def read_json(path):
    return json.loads(when_the_file_is_free(lambda: Path(path).read_text(encoding="utf-8-sig")))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".vpub-", dir=path.parent)
    try:
        # A viewer's text can hold half an emoji, which still reads back the same from JSON's escaped form.
        with os.fdopen(fd, "w", encoding="utf-8", errors="backslashreplace", newline="\n") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        when_the_file_is_free(lambda: os.replace(name, path))
    finally:
        if os.path.exists(name):
            from .runtime_files import defer
            defer(Path(name))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8", "surrogatepass")).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


# Douyin's cover window stayed busy for minutes on most tries in a headless Chrome, and went through at once on the one try in a real window. That isn't proof, since the network may have changed as well, but a real window out of sight costs little.
DOUYIN_WINDOW_ACTIONS = {"publish", "resume", "edit"}


def real_window(platform, action):
    """Whether this command needs a real Chrome window rather than a headless one. Only Windows has a hidden desktop to keep one out of sight, so elsewhere Douyin stays headless."""
    return platform == "youtube" or (platform == "douyin" and action in DOUYIN_WINDOW_ACTIONS and os.name == "nt")


def wrong_file(path, sha, message):
    """The error for a file that just read back with the wrong hash. A field run's film once read wrong by 10 bytes with the file unchanged, and "changed" sent everyone looking at the wrong thing. A second read tells them apart. It never lets anything through."""
    if file_sha(path) == sha:
        return VpubError("read_unstable", "This file read back two different ways with one read after the other, so nothing went ahead. The file itself looks right, which points at this computer's memory or disk.",
                         "Run the command again. If this keeps happening, test this computer's memory before trusting an upload from it.")
    return VpubError("source_changed", message)


def snapshot_file(source, target, sha, link=False):
    """Give target exactly the approved bytes; a cut-off copy never looks like a changed source.

    link shares the bytes instead of copying them, for a source nothing else will change.
    """
    target = Path(target)
    if target.exists() and file_sha(target) == sha:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        if link:
            candidate = target.parent / ('.vpub-' + uuid.uuid4().hex)
            try:
                os.link(source, candidate)
                name = candidate
            except OSError:
                pass  # An existing candidate is not ours; never overwrite it.
        if name is None:
            fd, name = tempfile.mkstemp(prefix=".vpub-", dir=target.parent)
            os.close(fd)
            shutil.copyfile(source, name)
        if file_sha(name) != sha:
            raise wrong_file(name, sha, "A file differs from the approved plan; create a fresh plan.")
        os.replace(name, target)
    finally:
        if name is not None and os.path.exists(name):
            from .runtime_files import defer
            defer(Path(name))
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


X_AI_LINE = re.compile(r'(?:\A|\s)((?:AI 生成[内內]容\s*/\s*)?AI[- ]generated)\s*\Z', re.I)


def x_post_text(title, description, tags, ai_generated, content_language="und", *, existing_text=None):
    # X has one post body, not separate title/description/tag fields.
    previous_ai = X_AI_LINE.search(existing_text or '')
    proposed_ai = X_AI_LINE.search(description) if existing_text is not None else None
    if proposed_ai:
        description = description[:proposed_ai.start()].rstrip()
    if tags is None:
        # Omitted tags in an edit keep the separate trailing hashtag block this
        # builder writes. Title/description are complete replacements, including
        # any inline hashtags. An explicit [] removes the separate block.
        before = (existing_text[:previous_ai.start()] if previous_ai else existing_text) or ''
        block = re.search(r'(?:\A|\n)[ \t]*(#\w+(?:\s+#\w+)*)\s*\Z', before.rstrip())
        tags = block[1].split() if block else []
    parts = [title, description, " ".join("#" + t.lstrip("#") for t in tags)]
    if previous_ai:
        parts.append(previous_ai[1])
    elif proposed_ai:
        parts.append(proposed_ai[1])
    elif ai_generated:
        parts.append({"zh-hans": "AI 生成内容 / AI-generated", "zh-hant": "AI 生成內容 / AI-generated"}.get(content_language.lower(), "AI-generated"))
    return "\n\n".join(p for p in parts if p)


X_POST_LIMIT = 280
# X counts most Latin-script text, punctuation and spaces as 1 and everything else as 2, Chinese,
# Japanese, Korean and emoji included, and a link as 23 whatever its length.
X_LINK = re.compile(r"(?:https?://)?(?:[\w-]+\.)+([^\W\d_]{2,})(?::\d+)?(?:/\S*[^\s.,!?;:)\]}'\"])?")


def x_weighted_length(text):
    """Offline preflight estimate using the twitter-text port, not a home-grown URL/emoji parser.

    Its bundled tables can lag the site. Unrecognized short domain candidates
    reserve at least 23, while unrecognized emoji retain their code-point weights.
    This can overestimate; X's visible composer remains authoritative.
    """
    import warnings
    try:
        # Lazy: other platforms and CLI startup do not load the parser's tables.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API.*", category=UserWarning)
            from twitter_text import parse_tweet, extract_urls_with_indices
            from twitter_text.has_invalid_characters import has_invalid_characters
    except ImportError as exc:
        raise VpubError("missing_dependency", "X text preflight needs the installed text parser.", "Reinstall CrossCast with its dependencies; nothing was uploaded.") from exc
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    if has_invalid_characters(text):
        raise VpubError("invalid_description", "X text contains an unsupported Unicode character; nothing was uploaded.")
    try:
        total = parse_tweet(text).weightedLength
    except UnicodeError as exc:
        raise VpubError("invalid_description", "X text contains an invalid Unicode sequence; nothing was uploaded.") from exc
    urls = [entry['indices'] for entry in extract_urls_with_indices(text)]
    for link in X_LINK.finditer(text):
        # This fallback may only add an allowance, never shorten text into a URL.
        if not any(start < link.end() and end > link.start() for start, end in urls):
            total += max(0, 23 - parse_tweet(link.group(0)).weightedLength)
    return total


def title_limit(platform, method=None):
    # X browser publishing goes through Media Studio, whose own title field stops at 70.
    if platform == "x" and (method or "browser") == "browser":
        return 70
    return {"bilibili": 80, "youtube": 100, "douyin": 30, "x": 100, "instagram": 100}[platform]


def douyin_caption(description, tags):
    return "\n\n".join(p for p in (description, " ".join("#" + t.lstrip("#") for t in tags)) if p)


# bili-cli's own limit on the people one description mentions (DESC_MENTIONS_MAX); a test holds the two together.
MENTIONS_MOST = 10
# Said to whoever writes the words. bili-cli puts a mention whose @nickname it can't find at the start of the description, which would change the approved words.
MENTION_HOW = ("Bilibili's description must hold each person in mention_uids as @ and their current Bilibili nickname, followed by a space, where the mention goes; "
               "bili user info UID gives the nickname. Without that, bili-cli would put the mention at the start, so CrossCast stops before anything is uploaded.")


def bilibili_topic(value):
    """The topic a Bilibili upload joins, by the exact name Bilibili gives it. An activity topic also enters its contest, and no topic can be added after the upload."""
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 100 or any(c in value for c in "\r\n\x00"):
        raise VpubError("invalid_topic", "Write the Bilibili topic as its exact name, on one line.",
                        "Copy it from the topic or contest page. A topic can't be added after the upload, so get it right first.")
    return value


def mention_uids(value):
    """The people a Bilibili description mentions for real, by UID: a nickname can change, a UID doesn't."""
    if (not isinstance(value, list) or not 1 <= len(value) <= MENTIONS_MOST
            or any(type(uid) is not int or uid <= 0 for uid in value) or len(set(value)) != len(value)):
        raise VpubError("invalid_mention", f"Use 1 to {MENTIONS_MOST} different Bilibili UIDs, the number in each person's space link.")
    return value


def mentions_written(description, uids):
    """Only a check that there are enough @names. Whether each is that person's current nickname, only Bilibili can say, so the upload asks bili-cli's own dry run first."""
    if len(set(re.findall(r"@\S+", description))) < len(uids):
        raise VpubError("invalid_mention", "Bilibili's description has fewer @nicknames than mention_uids has people.", MENTION_HOW)


def how_to_clear_a_stop():
    """A field run cleared a stop from an ordinary window and it stayed: an app installed from the Store can have its own private AppData, so the same command looked in another folder, found no stop and said so."""
    return f"vpub stop --clear, with VPUB_HOME set to {home()} if you run it in another window"

def how_to_pass_a_check(platform):
    """What a person does about a platform's check. From a field run: YouTube asked only as an upload began, so the sign-in window, which closes as
    soon as it sees the account, never showed it, and the person passed it by starting an upload in the CrossCast window."""
    if platform == "youtube":
        # Said to whoever reads it, usually an assistant: the check, any upload to let it show and deleting that upload are the person's, and the
        # stop is cleared only once the person says the check is done.
        return ("Run vpub auth login youtube --keep-open, which leaves the CrossCast window open for the person for up to 25 minutes. The person passes "
                "YouTube's check there. YouTube can ask only once something starts, so if nothing shows, the person can start an upload of any short "
                "clip in that window, pass the check, delete that clip in Studio and close the window. Once the person says the check is done, run "
                f"{how_to_clear_a_stop()}; inspect any existing draft before retrying.")
    return f"Run auth login for this platform and complete the official verification, then {how_to_clear_a_stop()}; inspect any existing draft before retrying."


def stop_check(platform=None, signing_in=False):
    candidates = [home() / "STOP.json"]
    external = config().get("external_stop_file")
    if external:
        candidates.append(Path(external))
    for index, path in enumerate(candidates):
        # Presence stops publication; malformed stop files also stop it.
        if path.exists():
            try:
                signal = read_json(path)
            except (OSError, ValueError):
                signal = None
            if index == 1 and platform and platform != "bilibili":
                # Recognize only this exact, explicit scope. Unknown/malformed
                # signals and this tool's own STOP remain global and fail closed.
                if isinstance(signal, dict) and signal.get("state") == "STOP_ALL_BILIBILI_READ_WRITE":
                    continue
            # A platform's check is passed in its own sign-in window, so that window is the one thing its warning still allows.
            if index == 0 and signing_in and isinstance(signal, dict) and signal.get("source") == "safety" and signal.get("platform") == platform:
                continue
            if index == 0 and isinstance(signal, dict) and signal.get("source") == "safety":
                # From a review: YouTube's check showed only as an upload began, and its plain sign-in window closed before then.
                passing = how_to_pass_a_check("youtube") if signal.get("platform") == "youtube" and signal.get("code") == "human_verification_required" else \
                    f"{signal.get('platform')}'s own sign-in window still opens with vpub auth login. vpub status says more. Only a person clears the stop: {how_to_clear_a_stop()}."
                raise VpubError("stopped", f"CrossCast is stopped because {signal.get('platform')} objected ({signal.get('code')}), and a platform's objection stops every platform. Stop file: {path}",
                                passing)
            raise VpubError("stopped", f"Publication stop file exists: {path}",
                            "Inspect the stop reason. Only the operator may clear it" + (f": {how_to_clear_a_stop()}." if index == 0 else "."))


def manifest_file(manifest, value, code, message):
    """A path written in a manifest, relative to it. Anything else is the manifest's error, not a crash."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise VpubError(code, message)
    return (Path(manifest).parent / value).resolve()


def shared_portrait(covers):
    """The portrait Douyin and Instagram share, if a manifest names one."""
    for platform in ("douyin", "instagram"):
        entry = covers.get(platform) if isinstance(covers, dict) else None
        value = entry.get("portrait") if isinstance(entry, dict) else None
        if value:
            if not isinstance(value, str) or "\x00" in value:
                raise VpubError("invalid_cover", "The shared portrait must be a local file path.")
            return value
    return None


DRAFT_KEEPS = "A draft keeps the title, description, tags and cover. Choose its schedule, chapters, collection, widgets, topic, 4:3 cover or mentions when it is published."


def load_plan(path, selected=None):
    path = Path(path).resolve()
    m = read_json(path)
    if not isinstance(m, dict):
        raise VpubError("invalid_manifest", "A manifest must be a JSON object.")
    if m.get("schema_version") != 1:
        raise VpubError("invalid_manifest", "schema_version must be 1.")
    allowed = {"schema_version", "video", "cover", "covers", "title", "description", "tags", "platforms", "content_language", "review_before_publish"}
    if set(m) - allowed:
        raise VpubError("invalid_manifest", "Unknown top-level fields: " + ", ".join(sorted(set(m) - allowed)))
    vp = manifest_file(path, m.get("video"), "invalid_video", "Select a complete, nonempty video file.")
    if not vp.is_file() or vp.suffix.lower() not in {".mp4", ".mov", ".webm", ".mkv"} or vp.stat().st_size == 0:
        raise VpubError("invalid_video", "Select a complete, nonempty video file.")
    cover = manifest_file(path, m["cover"], "invalid_cover", "Cover must be an existing JPG or PNG.") if m.get("cover") else None
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
        permitted |= {"tid", "expected_uid", "method", "season_id", "submission_mode", "widgets", "topic", "mention_uids"} if platform == "bilibili" else set()
        permitted |= {"category_id", "made_for_kids", "expected_channel_id", "method", "playlist_ids", "playlist_titles", "create_missing_playlists", "submission_mode"} if platform == "youtube" else set()
        permitted |= {"method", "expected_identity", "submission_mode", "collection_title"} if platform == "douyin" else set()
        permitted |= {"method", "expected_identity", "submission_mode"} if platform in {"x", "instagram"} else set()
        permitted |= {"existing_media_url"} if platform == "x" else set()
        if set(v) - permitted:
            raise VpubError("invalid_manifest", f"Unsupported {platform} options: {sorted(set(v) - permitted)}")
        for field in ("method", "visibility", "metadata_mode", "submission_mode"):
            if field in v and not isinstance(v[field], str):
                raise VpubError("invalid_" + field, f"{platform} {field} must be text.")
        if v.get("account", "default") != "default":
            raise VpubError("unsupported_account", "v0.1 supports one account per platform: default.")
        if platform == "youtube" and v.get("method", "browser") not in {"native", "browser", "api"}:
            raise VpubError("invalid_method", "YouTube method must be browser, api or native. The screen-relay prototype was retired.")
        if platform == "youtube":
            from .categories import STUDIO_CATEGORIES, validate_youtube
            validate_youtube(v.get("category_id"))
            if v.get("method", "browser") == "browser":
                if v["category_id"] not in STUDIO_CATEGORIES:
                    raise VpubError("invalid_category", "YouTube Studio's list does not offer this category ID.")
                if v.get("playlist_ids"):
                    raise VpubError("unsupported_playlist", "Studio's playlist picker shows titles, not IDs.", "Use playlist_titles with the exact titles.")
            for field in ("playlist_ids", "playlist_titles"):
                values = v.get(field, [])
                if not isinstance(values, list) or len(values) > 20 or not all(isinstance(x, str) and x.strip() and len(x) <= 150 for x in values) or len(set(values)) != len(values):
                    raise VpubError("invalid_playlist", "Use up to 20 unique playlist IDs/titles.")
            if any(not re.fullmatch(r"PL[A-Za-z0-9_-]{8,148}", x) for x in v.get("playlist_ids", [])):
                raise VpubError("invalid_playlist", "Use custom playlist IDs copied from YouTube, not video IDs or system lists.")
            if v.get("method") == "api" and v.get("playlist_titles"):
                raise VpubError("invalid_playlist", "API mode requires exact playlist_ids; names may be ambiguous.")
            if "create_missing_playlists" in v and (type(v["create_missing_playlists"]) is not bool or v.get("method", "browser") != "browser" or not v.get("playlist_titles")):
                raise VpubError("invalid_playlist", "create_missing_playlists is true or false, on the browser route, with playlist_titles.")
        if platform == "bilibili" and "season_id" in v and (type(v["season_id"]) is not int or v["season_id"] <= 0):
            raise VpubError("invalid_collection", "Use the exact collection ID from vpub collection list bilibili.")
        if platform == "bilibili" and "topic" in v:
            bilibili_topic(v["topic"])
        if platform == "bilibili" and "mention_uids" in v:
            mention_uids(v["mention_uids"])
        if platform == "douyin" and v.get("method", "browser") not in {"native", "browser"}:
            raise VpubError("invalid_method", "Douyin method must be native or browser.")
        if platform == "douyin" and "expected_identity" in v:
            if not isinstance(v["expected_identity"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", v["expected_identity"]):
                raise VpubError("invalid_identity", "Use the Douyin account ID observed in the creator dashboard, not the display name.")
        if platform == "bilibili" and v.get("method", "cli") not in {"native", "cli"}:
            raise VpubError("invalid_method", "Bilibili method must be native or cli.")
        if platform == "bilibili" and v.get("method", "cli") != "cli" and ("topic" in v or "mention_uids" in v):
            raise VpubError("invalid_method", "A Bilibili topic or mentions go through the CLI route.")
        if platform in {"x", "instagram"} and v.get("method", "browser") not in {"native", "browser", "api"}:
            raise VpubError("invalid_method", "X/Instagram method must be browser, api or native.")
        if platform in {"x", "instagram"} and v.get("method") == "api" and v.get("submission_mode", "publish") != "publish":
            raise VpubError("invalid_submission_mode", "The API routes have no drafts; use browser or native for draft_only.")
        if platform == "youtube" and v.get("method", "browser") != "browser" and v.get("submission_mode", "publish") != "publish":
            raise VpubError("invalid_submission_mode", "YouTube draft_only needs the browser route.")
        if v.get("submission_mode", "publish") not in {"publish", "draft_only"}:
            raise VpubError("invalid_submission_mode", "Use publish or draft_only. Draft-only permits media upload/preview, never Share/Post or scheduling.")
        if platform in {"bilibili", "douyin"} and v.get("submission_mode") == "draft_only":
            if v.get("method") == "native":
                raise VpubError("invalid_submission_mode", f"{platform} draft_only needs CrossCast's own route.")
            if v.get("schedule") or v.get("chapters") or v.get("season_id") or v.get("collection_title") or v.get("widgets") or v.get("topic") or v.get("mention_uids"):
                raise VpubError("invalid_submission_mode", DRAFT_KEEPS)
        if platform == "douyin" and "collection_title" in v:
            value = v["collection_title"]
            if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 20 or v.get("method", "browser") != "browser":
                raise VpubError("invalid_collection", "Use the exact title of one of the account's Douyin collections, on the browser route.")
        if platform == "x" and v.get("expected_identity") is not None and not re.fullmatch(r"[A-Za-z0-9_]{1,15}", str(v["expected_identity"])):
            raise VpubError("invalid_identity", "Use the X handle without @, as observed in the signed-in profile.")
        if platform == "x" and "existing_media_url" in v and (v.get("method", "browser") != "browser" or not re.fullmatch(r"https://studio\.x\.com/library/\d+_\d+/edit", str(v["existing_media_url"]))):
            raise VpubError("invalid_media_url", "Use the address of the clip's own page in Media Studio, on the browser route.")
        if platform == "x" and cover and v.get("method", "browser") == "native":
            raise VpubError("unsupported_cover", "X native alpha uses the video's default thumbnail; shared covers are not implemented.")
        title = v.get("title", m.get("title", ""))
        desc = v.get("description", m.get("description", ""))
        tags = v.get("tags", m.get("tags", []))
        limit = title_limit(platform, v.get("method"))
        if not isinstance(title, str) or not title.strip() or len(title) > limit or any(c in title for c in "\r\n\x00"):
            raise VpubError("invalid_title", f"{platform} title must contain 1–{limit} characters, one line.",
                            f"Write platforms.{platform}.title, or set the root title and omit that platform's title override. "
                            "For generated copy, run vpub metadata request, then vpub metadata apply.")
        if platform == "youtube" and ("<" in title or ">" in title):
            raise VpubError("invalid_title", "YouTube titles cannot contain < or >.")
        if not isinstance(desc, str) or "\x00" in desc or len(desc) > (2000 if platform != "youtube" else 5000):
            raise VpubError("invalid_description", f"Invalid {platform} description.")
        if platform == "bilibili" and "mention_uids" in v:
            mentions_written(desc, v["mention_uids"])
        if platform == "youtube" and len(desc.encode("utf-8")) > 5000:
            raise VpubError("invalid_description", "YouTube description exceeds 5000 UTF-8 bytes.")
        if platform == "youtube" and ("<" in desc or ">" in desc):
            raise VpubError("invalid_description", "YouTube descriptions cannot contain < or >.")
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
            if platform == "instagram" or v.get("method") == "native" or (platform == "x" and v.get("method") == "api"):
                raise VpubError("unsupported_schedule", "This route supports immediate publication only.")
            try:
                when = datetime.fromisoformat(schedule.replace("Z", "+00:00"))
                delta = (when - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, AttributeError):
                raise VpubError("invalid_schedule", "Use a future ISO timestamp with an explicit timezone.")
            if (platform in {"youtube", "douyin", "x"} and v.get("method", "browser") == "browser"
                    and (when.second or when.microsecond or when.utcoffset().total_seconds() % 60)):
                raise VpubError("invalid_schedule", "This browser scheduler accepts whole minutes only, including the timestamp's timezone offset; seconds and fractional seconds must be zero.",
                                "Choose an exact minute and approve the new plan. Nothing was uploaded.")
            if delta < 60 or (platform == "bilibili" and not 7200 <= delta <= 15 * 86400):
                raise VpubError("invalid_schedule", "Schedule is too near/past or outside Bilibili's 2h–15d range.")
            if visibility != "private" and platform == "youtube":
                raise VpubError("invalid_schedule", "YouTube schedule requires visibility=private.")
        plans[platform] = {**v, "account": account_name(v.get("account", "default")),
                           "title": title, "description": desc, "tags": tags, "visibility": visibility,
                           "content_language": language_tag(v.get("content_language", m.get("content_language", "und")))}
        from .safety import PLAN_TEXT, check_public_text
        check_public_text(*(plans[platform].get(name) for name in PLAN_TEXT))
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
            plans[platform]["method"] = v.get("method", "browser")
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
            # Caught here, before any upload: a field run uploaded a film whose post X then wouldn't let out.
            if platform == "x" and (count := x_weighted_length(plans[platform]["post_text"])) > X_POST_LIMIT:
                raise VpubError("x_text_too_long", f"CrossCast's preflight estimate comes to {count}, over X's {X_POST_LIMIT}. The title, description, tags and AI line all count. "
                                "Chinese, Japanese and Korean characters and recognized emoji count 2; recognized URLs count 23. Unrecognized emoji or domain names can be overestimated; X's composer remains authoritative.",
                                "Shorten X's title or description and run it again. Keep required credits: if they don't fit, ask the user. Nothing was uploaded.")
            # X's own counter still has the last word: the route stops before Post if it disagrees. Never silently truncate or assume Premium.
        if platform == "bilibili":
            plans[platform]["method"] = v.get("method", "cli")
        if "widgets" in v:
            from .widgets_bilibili import validate as validate_widgets
            if plans[platform]["method"] != "cli":
                raise VpubError("invalid_widget", "Widgets go through the CLI route.")
            plans[platform]["widgets"] = validate_widgets(v["widgets"], v.get("duration_seconds"))
        if "chapters" in v:
            from .chapters import validate, youtube_description
            validate(v["chapters"], platform, plans[platform]["method"], v.get("duration_seconds"))
            if platform == "youtube" and plans[platform]["method"] == "browser":
                plans[platform]["description"] = youtube_description(desc, v["chapters"], v["duration_seconds"])
                if len(plans[platform]["description"].encode("utf-8")) > 5000:
                    raise VpubError("invalid_description", "YouTube description plus chapters exceeds 5000 UTF-8 bytes.")
    covers = {}
    supplied_covers = m.get("covers", {})
    if not isinstance(supplied_covers, dict) or set(supplied_covers) - set(PLATFORMS):
        raise VpubError("invalid_cover", "covers maps supported platforms to landscape/portrait files.")
    for platform in plans:
        variants = supplied_covers.get(platform, {})
        # cover43 is Bilibili's 4:3 cover for its phone feed, uploaded as it is beside the landscape one.
        if not isinstance(variants, dict) or set(variants) - {"landscape", "portrait", *(["cover43"] if platform == "bilibili" else [])}:
            raise VpubError("invalid_cover", "Use landscape and/or portrait cover roles, and cover43 only for Bilibili's 4:3 cover.")
        if variants and platform == "x" and plans[platform]["method"] == "native":
            raise VpubError("unsupported_cover", "This route cannot set custom covers.")
        if "portrait" in variants and platform in {"youtube", "bilibili", "x"}:
            raise VpubError("unsupported_cover", "This platform uses one landscape thumbnail in this workflow.")
        if "cover43" in variants and plans[platform]["method"] != "cli":
            raise VpubError("unsupported_cover", "Bilibili's 4:3 cover goes through the CLI route.")
        if "cover43" in variants and plans[platform].get("submission_mode") == "draft_only":
            raise VpubError("invalid_submission_mode", DRAFT_KEEPS)
        for role, filename in variants.items():
            asset = manifest_file(path, filename, "invalid_cover", "Each cover role needs a local file path.")
            if not asset.is_file() or asset.suffix.lower() not in {".jpg", ".jpeg", ".png"} or not asset.stat().st_size:
                if role == "cover43":
                    raise VpubError("invalid_cover", f"Bilibili's 4:3 cover must be a complete JPG or PNG, and there isn't one at {asset}.",
                                    "Put the file back, or take cover43 out of the plan, then run it again. Nothing was uploaded.")
                raise VpubError("invalid_cover", "Each cover must be a complete JPG or PNG.")
            if role == "cover43":
                from .covers import check_cover43
                check_cover43(asset)
            covers.setdefault(platform, {})[role] = {"path": str(asset), "sha256": file_sha(asset)}
    payload = {"schema_version": 1, "video": str(vp), "video_sha256": file_sha(vp),
               "video_bytes": vp.stat().st_size, "cover": str(cover) if cover else None,
               "cover_sha256": file_sha(cover) if cover else None, "platforms": plans}
    if covers:
        payload["covers"] = covers
    from .publication_review import manifest_choice
    if manifest_choice(path, m):
        payload['review_before_publish'] = True
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

    def save_unless_changed(self, key, state, data, seen):
        """Saves only if the record is still as it was when read, and says whether it did."""
        saved = self.db.execute("UPDATE publications SET state=?,data=?,updated_at=? WHERE key=? AND updated_at=?",
                                (state, json.dumps(data, ensure_ascii=False), now(), key, seen))
        self.db.commit()
        return saved.rowcount == 1

    def forget_preview(self, key):
        """Release a record that only reached a draft or preview."""
        self.db.execute("DELETE FROM publications WHERE key=? AND state IN ('DRAFT_SAVED','PREVIEW_READY')", (key,))
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


def observation_lock(seconds=30):
    """Saving what a read found touches only local files for a moment, so it never waits for a publish or a change on another platform."""
    return brief_lock(home() / 'observations.lock', 'busy', 'Another command is saving what it read.', seconds)


def receipt_turn(path, seconds=10):
    """Read a receipt and move it on as one step. Replacing the file is atomic, but what was read before it can be out of date:
    a state someone else wrote in between would be lost, and both sides would carry on as if they had got there first."""
    return brief_lock(Path(str(path) + '.turn'), 'attempt_busy', 'Another step is moving this attempt on, so nothing was sent.', seconds)


@contextlib.contextmanager
def brief_lock(path, code, message, seconds):
    """A lock held for a moment over local files, so the other side waits for it instead of giving up."""
    from .process_lock import hold
    deadline = time.monotonic() + seconds
    while True:
        held = hold(path, code, message)
        try:
            held.__enter__()
            break
        except VpubError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)
    try:
        yield
    finally:
        held.__exit__(None, None, None)

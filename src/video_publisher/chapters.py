"""Validate chapter plans for supported routes, and write them the way each platform takes them."""
import math
import re
from .core import VpubError

ROUTES = {("douyin", "browser"), ("bilibili", "cli"), ("youtube", "browser")}
CHAPTER_LINE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?\s+\S.*")
FIRST_LINE = re.compile(r"0{0,2}:?0{1,2}:00\s+\S.*")


def validate(values, platform, method, duration):
    if not isinstance(values, list) or not values or len(values) > 100:
        raise VpubError("invalid_chapters", "Use a nonempty chapter list (local limit: 100).")
    if platform not in {"bilibili", "douyin", "youtube"} or not (method == 'native' or (platform, method) in ROUTES):
        raise VpubError("unsupported_chapters", "Use Douyin or YouTube browser execution, the Bilibili CLI, or a Bilibili/Douyin/YouTube native handoff; other routes are unimplemented.")
    if type(duration) not in {int, float} or not math.isfinite(duration) or duration <= 0:
        raise VpubError("invalid_chapters", "Provide duration_seconds measured from the final video.")
    previous = -1
    for index, item in enumerate(values):
        if not isinstance(item, dict) or set(item) != {"start_seconds", "title"}:
            raise VpubError("invalid_chapters", "Each chapter contains start_seconds and title only.")
        start, title = item["start_seconds"], item["title"]
        if type(start) is not int or start <= previous or start >= duration or (index == 0 and start != 0):
            raise VpubError("invalid_chapters", "Starts must be whole seconds, begin at zero and increase within the video duration.")
        if not isinstance(title, str) or not title.strip() or len(title) > 120 or any(c in title for c in '\r\n\x00'):
            raise VpubError("invalid_chapters", "Use short single-line titles; the official UI may impose tighter limits.")
        if platform == "douyin" and len(title.encode("utf-16-le")) // 2 > 12:
            raise VpubError("invalid_chapters", "Douyin chapter titles allow at most 12 UTF-16 units, as counted by its web input. Shorten the title without changing its start.")
        previous = start
    return values


def stamp(seconds):
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def chapter_lines(values):
    return [f"{stamp(item['start_seconds'])} {item['title'].strip()}" for item in values]


def youtube_rules(values, duration):
    """YouTube makes chapters from timestamps in the description: three or more, each at least 10 seconds."""
    ends = [item["start_seconds"] for item in values[1:]] + [duration]
    if len(values) < 3 or any(end - item["start_seconds"] < 10 for item, end in zip(values, ends)):
        raise VpubError("invalid_chapters", "YouTube needs at least three chapters, each at least 10 seconds long.")
    if any("<" in item["title"] or ">" in item["title"] for item in values):
        raise VpubError("invalid_chapters", "YouTube descriptions cannot contain < or >.")


def without_chapters(description):
    """The description without its chapter list: the run of timestamped lines that starts at zero. Other timestamps stay."""
    rows = description.split("\n")
    for start, row in enumerate(rows):
        if FIRST_LINE.fullmatch(row.strip()):
            end = start
            while end < len(rows) and CHAPTER_LINE.fullmatch(rows[end].strip()):
                end += 1
            before, after = "\n".join(rows[:start]).rstrip(), "\n".join(rows[end:]).strip()
            return before + "\n\n" + after if before and after else before or after
    return description


def youtube_description(description, values, duration):
    youtube_rules(values, duration)
    kept = without_chapters(description).rstrip()
    listed = "\n".join(chapter_lines(values))
    return kept + "\n\n" + listed if kept else listed

"""Validate chapter plans for supported browser routes."""
import math
from .core import VpubError


def validate(values, platform, method, duration):
    if not isinstance(values, list) or not values or len(values) > 100:
        raise VpubError("invalid_chapters", "Use a nonempty chapter list (local limit: 100).")
    if platform not in {"bilibili", "douyin", "youtube"} or not (method == 'native' or (platform=='douyin' and method=='browser')):
        raise VpubError("unsupported_chapters", "Use Douyin browser execution or Bilibili/Douyin/YouTube native handoff; other routes are unimplemented.")
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

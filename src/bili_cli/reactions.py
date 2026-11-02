"""Line danmaku up with the picture they react to.

A danmaku's time is the playback position when it was sent. Viewers type after they see
something, so a reaction lands a couple of seconds after the shot that caused it, and it
then scrolls across the screen for several more seconds. A screenshot with the danmaku
burnt in therefore mixes reactions to the last few shots with whatever is on screen now.

Instead, each danmaku is moved back by LAG seconds (its estimated "moment") and credited to
the frame whose time window holds that moment. Peaks of the moved density are the moments
viewers reacted to most.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable

LAG = 2.0  # seconds between seeing a shot and sending a reaction to it
FRAME_REACH = 8.0  # a frame collects reactions to moments at most this far from it
TOP_PER_FRAME = 8  # in the JSON; the contact sheet draws up to CAPTION_POOL
CAPTION_POOL = 40
SKIP_MODES = {7, 8, 9}  # advanced, code and BAS danmaku are effects, not reactions

_PUNCT = re.compile(r"[\W_]+", re.UNICODE)
_REPEAT = re.compile(r"(.)\1{2,}")


def load(danmakus: Iterable[Any]) -> list[tuple[float, str]]:
    """(time, text) pairs from bilibili_api Danmaku objects, effects and blanks dropped, sorted by time."""
    out = []
    for d in danmakus:
        if getattr(d, "mode", 1) in SKIP_MODES:
            continue
        text = (getattr(d, "text", "") or "").strip()
        if text:
            out.append((float(getattr(d, "dm_time", 0.0) or 0.0), text))
    return sorted(out)


def moment(t: float, lag: float = LAG) -> float:
    return max(0.0, t - lag)


def _key(text: str) -> str:
    """Group spelling variants: case, spaces, punctuation, and 哈哈哈哈 vs 哈哈哈."""
    return _REPEAT.sub(r"\1\1", _PUNCT.sub("", text.lower()))


def top_texts(texts: list[str], k: int = TOP_PER_FRAME) -> list[dict[str, Any]]:
    """Most repeated reactions first, then the more telling one-offs. Noise like '。' or '1' is dropped."""
    groups: dict[str, list[str]] = {}
    for t in texts:
        key = _key(t)
        if len(key) >= 2 or (key and texts.count(t) >= 3):
            groups.setdefault(key, []).append(t)
    ranked = sorted(
        groups.values(),
        key=lambda g: (-len(g), -min(len(_key(g[0])), 12)),  # count, then prefer phrases over single words
    )
    return [{"text": Counter(g).most_common(1)[0][0], "n": len(g)} for g in ranked[:k]]


def assign(
    dms: list[tuple[float, str]], times: list[float], duration: float, lag: float = LAG, k: int = TOP_PER_FRAME
) -> list[dict[str, Any]]:
    """Reactions credited to each frame: moments closer to it than to its neighbours, within FRAME_REACH seconds."""
    if not times:
        return []
    order = sorted(range(len(times)), key=lambda i: times[i])
    bounds = {}
    for pos, i in enumerate(order):
        t = times[i]
        lo = (times[order[pos - 1]] + t) / 2 if pos else 0.0
        hi = (t + times[order[pos + 1]]) / 2 if pos + 1 < len(order) else duration
        bounds[i] = (max(lo, t - FRAME_REACH), min(hi, t + FRAME_REACH))
    buckets: list[list[str]] = [[] for _ in times]
    for t, text in dms:
        m = moment(t, lag)
        for i, (lo, hi) in bounds.items():
            if lo <= m < hi or (m == hi == duration):
                buckets[i].append(text)
                break
    return [{"count": len(b), "top": top_texts(b, k)} for b in buckets]


def density(dms: list[tuple[float, str]], duration: float, bins: int, lag: float = LAG) -> list[int]:
    """Reactions per time bin, by moment."""
    bins = max(1, bins)
    out = [0] * bins
    if duration <= 0:
        return out
    for t, _ in dms:
        out[min(bins - 1, int(moment(t, lag) / duration * bins))] += 1
    return out


def peaks(dms: list[tuple[float, str]], duration: float, n: int = 5, lag: float = LAG) -> list[dict[str, Any]]:
    """The moments viewers reacted to most: local maxima of a 3-second rolling count, at least min_gap apart."""
    if not dms or duration <= 0 or n <= 0:
        return []
    secs = max(1, int(duration) + 1)
    per_sec = density(dms, secs, secs, lag)
    rolled = [sum(per_sec[max(0, i - 1): i + 2]) for i in range(secs)]
    mean = sum(rolled) / secs
    floor = max(3.0, mean * 1.2)
    min_gap = max(4.0, duration / 30)
    candidates = sorted(
        (i for i in range(secs) if rolled[i] >= floor and rolled[i] == max(rolled[max(0, i - 2): i + 3])),
        key=lambda i: -rolled[i],
    )
    chosen: list[int] = []
    for i in candidates:
        if all(abs(i - c) >= min_gap for c in chosen):
            chosen.append(i)
        if len(chosen) == n:
            break
    out = []
    for i in sorted(chosen):
        near = [text for t, text in dms if abs(moment(t, lag) - (i + 0.5)) <= 1.5]
        out.append({"time": float(min(i + 0.5, max(duration - 0.5, 0.0))), "count": rolled[i], "top": top_texts(near, 3)})
    return out


NOTE = (
    f"弹幕按“发送时间 − {LAG:g} 秒”归到画面上（观众看到画面后才开始打字）；"
    "屏幕上同时滚动的弹幕往往在说前几秒的镜头，所以不要按截图上叠着的弹幕去理解当前画面。"
)

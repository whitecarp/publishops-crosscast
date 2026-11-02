"""Turn a video into still images an agent can look at.

Two sources:
- preview: Bilibili's own seek-bar thumbnails (480x270 sprite sheets plus a
  timestamp index). No video download and no ffmpeg; the nearest thumbnail
  is used, typically a few seconds from the requested time.
- hd: ffmpeg seeks inside the DASH video stream (<= 720p, H.264) and grabs the
  exact frame. Only the bytes around each timestamp are fetched, ~3 s a frame.
"""

from __future__ import annotations

import asyncio
import bisect
import io
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .errors import BiliError
from .runtime import WEB_HEADERS, web_client

MAX_PREVIEW_FRAMES = 100
MAX_HD_FRAMES = 30
HD_MAX_HEIGHT = 720
HD_CONCURRENCY = 1  # the CDN throttles parallel range requests: 6 frames took 18 s sequentially, 67 s with 3 at once
HD_FRAME_TIMEOUT = 60
SHEET_COLUMNS = 3
SHEET_TILE_WIDTH = 480


def plan_times(
    duration: float,
    count: int | None = None,
    every: float | None = None,
    at: list[float] | None = None,
    chapter_starts: list[float] | None = None,
) -> list[float]:
    """Pick sample times (seconds) within [0, duration)."""
    if duration <= 0:
        raise BiliError("invalid_argument", "无法获取视频时长。")
    if at:
        bad = [t for t in at if t >= duration]
        if bad:
            raise BiliError("invalid_argument", f"时间点超出视频时长 {int(duration)} 秒：{bad}")
        times = [float(t) for t in at]
    elif every:
        n = int(duration // every) + 1
        # skip the very first frame, which is often black
        times = [min(max(i * every, 1.0), duration - 0.5) for i in range(n)]
    elif chapter_starts is not None:
        if not chapter_starts:
            raise BiliError("not_found", "该视频没有分段章节。", hint="改用 --count 或 --every 采样。")
        times = [min(t + 3.0, duration - 0.5) for t in chapter_starts]
    else:
        n = count or 9
        times = [duration * (i + 0.5) / n for i in range(n)]
    return sorted({round(t, 2) for t in times})


def fmt_time(t: float) -> str:
    t = round(t, 2)
    frac = round(t % 1, 2)
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    sec = f"{s:02d}" + (f"{frac:.2f}".lstrip("0").rstrip("0") if frac else "")
    return f"{h}:{m:02d}:{sec}" if h else f"{m}:{sec}"


def frame_name(t: float) -> str:
    """frame_000090.jpg, or frame_000090_500.jpg for 90.5 s so sub-second frames don't overwrite each other."""
    ms = round(t * 1000) % 1000
    return f"frame_{int(t):06d}" + (f"_{ms:03d}" if ms else "") + ".jpg"


async def grab_preview(snapshot: dict[str, Any], times: list[float], out_dir: Path) -> list[dict[str, Any]]:
    from PIL import Image

    index: list[int] = snapshot.get("index") or []
    images: list[str] = snapshot.get("image") or []
    cols, rows = snapshot.get("img_x_len") or 10, snapshot.get("img_y_len") or 10
    w, h = snapshot.get("img_x_size"), snapshot.get("img_y_size")
    if not index or not images or not w or not h:
        raise BiliError("not_found", "该视频没有预览缩略图。", hint="改用 --quality hd（需要 ffmpeg）。")
    # The index starts with a placeholder 0 that has no thumbnail ([0, 0, 6, 12, ...]): thumbnail k is index[k + 1].
    # Reading it as index[k] labelled every frame one interval early (checked against exact ffmpeg frames).
    if len(index) > 1 and index[0] == 0 and index[1] == 0:
        index = index[1:]
    per_sheet = cols * rows
    count = min(len(index), len(images) * per_sheet)
    sheets: dict[int, Image.Image] = {}
    results = []
    async with web_client() as client:
        for t in times:
            k = max(0, min(bisect.bisect_right(index, t) - 1, count - 1))
            if k + 1 < count and abs(index[k + 1] - t) < abs(t - index[k]):
                k += 1  # the nearest thumbnail, not just the last one before t
            sheet_no, pos = divmod(k, per_sheet)
            if sheet_no not in sheets:
                url = images[sheet_no]
                resp = await client.get("https:" + url if url.startswith("//") else url)
                resp.raise_for_status()
                sheets[sheet_no] = Image.open(io.BytesIO(resp.content)).convert("RGB")
            x, y = (pos % cols) * w, (pos // cols) * h
            path = out_dir / frame_name(t)
            sheets[sheet_no].crop((x, y, x + w, y + h)).save(path, quality=90)
            results.append({"time": t, "actual_time": index[k], "path": str(path)})
    return results


def dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preview frames requested a few seconds apart can snap to the same thumbnail; keep one of each."""
    seen, out = set(), []
    for it in items:
        if it["actual_time"] not in seen:
            seen.add(it["actual_time"])
            out.append(it)
    return out


def pick_stream(playurl: dict[str, Any]) -> dict[str, Any]:
    streams = (playurl.get("dash") or {}).get("video") or []
    h264 = [s for s in streams if str(s.get("codecs", "")).startswith("avc1")]
    pool = h264 or streams
    if not pool:
        raise BiliError("not_found", "没有可用的视频流。", hint="改用 --quality preview。")
    fitting = [s for s in pool if (s.get("height") or 0) <= HD_MAX_HEIGHT]
    return max(fitting, key=lambda s: s.get("height") or 0) if fitting else min(pool, key=lambda s: s.get("height") or 0)


async def grab_hd(playurl: dict[str, Any], times: list[float], out_dir: Path) -> list[dict[str, Any]]:
    if not shutil.which("ffmpeg"):
        raise BiliError("invalid_argument", "--quality hd 需要 ffmpeg。", hint="安装 ffmpeg，或改用 --quality preview。")
    stream = pick_stream(playurl)
    urls = [stream.get("baseUrl") or stream.get("base_url")] + list(stream.get("backupUrl") or stream.get("backup_url") or [])
    headers = "".join(f"{k}: {v}\r\n" for k, v in WEB_HEADERS.items())
    sem = asyncio.Semaphore(HD_CONCURRENCY)

    async def one(t: float) -> dict[str, Any]:
        path = out_dir / frame_name(t)
        err = b""
        async with sem:
            # The video stream often ends a fraction of a second before the stated duration, so a time near
            # the end yields no frame; one step back finds the last picture instead of failing the whole call.
            for seek in (t, max(0.0, t - 1.0)):
                for url in filter(None, urls):
                    # A thread rather than asyncio subprocesses: the selector loop used on Windows cannot spawn them.
                    # -nostdin + DEVNULL: otherwise concurrent ffmpegs wait on the console for key presses.
                    try:
                        proc = await asyncio.to_thread(
                            subprocess.run,
                            ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-headers", headers,
                             "-ss", f"{seek:.2f}", "-i", url, "-frames:v", "1", "-q:v", "3", str(path)],
                            stdin=subprocess.DEVNULL,
                            capture_output=True,
                            timeout=HD_FRAME_TIMEOUT,
                        )
                    except subprocess.TimeoutExpired:
                        err = f"ffmpeg 超过 {HD_FRAME_TIMEOUT} 秒未完成".encode()
                        continue
                    err = proc.stderr
                    if proc.returncode == 0 and path.exists() and path.stat().st_size > 0:
                        return {"time": t, "actual_time": seek, "path": str(path)}
            raise BiliError("network_error", f"截取 {fmt_time(t)} 的画面失败：{err.decode(errors='ignore')[-200:]}")

    results = await asyncio.gather(*(one(t) for t in times))
    return list(results)


CJK_FONTS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)
CAPTION_LINES = 7  # lines of reactions under each frame, packed side by side
CAPTION_SIZE = 18
FLOW_SIZE = 16
ITEM_MAX_CHARS = 22
DENSITY_HEIGHT = 64


def _font(size: int, cjk: bool = False):
    """A CJK-capable font when asked for one (danmaku text); None if this machine has none."""
    import os

    from PIL import ImageFont

    if cjk:
        for p in filter(None, (os.environ.get("BILI_CLI_FONT"), *CJK_FONTS)):
            if Path(p).exists():
                try:
                    return ImageFont.truetype(p, size)
                except OSError:
                    continue
        return None
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _printable(text: str) -> str:
    """Drop emoji and other astral-plane characters that CJK fonts draw as boxes."""
    return "".join(c for c in text if ord(c) <= 0xFFFF and not 0x2600 <= ord(c) <= 0x27BF).strip()


def _fit(draw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _flow(draw, x: int, y: int, items: list[dict[str, Any]], font, width: int, lines: int) -> None:
    """Reactions packed like a paragraph: short ones share a line, the most repeated come first."""
    line_h = FLOW_SIZE + 5
    sep = "  ·  "
    sep_w = draw.textlength(sep, font=font)
    cx, row = x, 0
    for item in items:
        text = _printable(item["text"])
        if not text:
            continue
        if len(text) > ITEM_MAX_CHARS:
            text = text[: ITEM_MAX_CHARS - 1] + "…"
        token = text + (f" ×{item['n']}" if item["n"] > 1 else "")
        w = draw.textlength(token, font=font)
        if cx > x and cx + w > x + width:
            row, cx = row + 1, x
        if row >= lines:
            return
        if w > width:
            token = _fit(draw, token, font, width)
            w = draw.textlength(token, font=font)
        draw.text((cx, y + row * line_h), token, fill="#FFFFFF" if item["n"] > 1 else "#C8C8C8", font=font)
        cx += w
        if cx + sep_w < x + width:
            draw.text((cx, y + row * line_h), sep, fill="#666666", font=font)
            cx += sep_w


def contact_sheet(
    frames: list[dict[str, Any]],
    path: Path,
    density: list[int] | None = None,
    duration: float | None = None,
    captions: list[list[dict[str, Any]]] | None = None,
) -> Path:
    """All frames on one image with timestamps, so an agent can take them in with a single look.

    Frames carrying a "danmaku" entry get a caption strip under the picture with the reactions
    credited to them (never drawn over the picture); `captions` can hand in a longer list per frame
    than the JSON carries. With `density`, a strip at the bottom shows when viewers reacted, with
    each frame's position marked.
    """
    from PIL import Image, ImageDraw

    tiles = []
    for f in frames:
        img = Image.open(f["path"]).convert("RGB")
        ratio = SHEET_TILE_WIDTH / img.width
        tiles.append(img.resize((SHEET_TILE_WIDTH, int(img.height * ratio))))
    tile_h = max(t.height for t in tiles)
    captioned = any("danmaku" in f for f in frames)
    cjk = _font(CAPTION_SIZE, cjk=True) if captioned or density else None
    flow = _font(FLOW_SIZE, cjk=True) if cjk else None
    cap_h = (CAPTION_SIZE + 10) + (CAPTION_LINES * (FLOW_SIZE + 5) if cjk else 0) + 8 if captioned else 0
    cell_h = tile_h + cap_h
    cols = min(SHEET_COLUMNS, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    dens_h = DENSITY_HEIGHT + 34 if density and duration else 0
    sheet = Image.new("RGB", (cols * SHEET_TILE_WIDTH, rows * cell_h + dens_h), "black")
    draw = ImageDraw.Draw(sheet)
    font = (_font(22, cjk=True) if cjk else None) or _font(22)
    small = cjk or _font(CAPTION_SIZE)
    for i, (tile, f) in enumerate(zip(tiles, frames)):
        x, y = (i % cols) * SHEET_TILE_WIDTH, (i // cols) * cell_h
        sheet.paste(tile, (x, y))
        label = f"#{i + 1} {fmt_time(f['actual_time'])}" + (("  高能" if cjk else "  PEAK") if f.get("peak") else "")
        box = draw.textbbox((x + 6, y + 6), label, font=font)
        draw.rectangle((box[0] - 4, box[1] - 3, box[2] + 4, box[3] + 3), fill="#B3261E" if f.get("peak") else "black")
        draw.text((x + 6, y + 6), label, fill="white", font=font)
        if not captioned:
            continue
        cy = y + tile_h + 4
        dm = f.get("danmaku") or {"count": 0, "top": []}
        head = f"弹幕 {dm['count']} 条" if cjk else f"danmaku: {dm['count']}"
        draw.text((x + 8, cy), head, fill="#FFD84D", font=small)
        if not cjk:
            continue
        items = captions[i] if captions else dm["top"]
        _flow(draw, x + 8, cy + CAPTION_SIZE + 10, items, flow, SHEET_TILE_WIDTH - 16, CAPTION_LINES)
    if dens_h:
        top, width = rows * cell_h + 30, cols * SHEET_TILE_WIDTH
        caption = "弹幕密度（已按打字延迟前移）· 竖线 = 上面第几帧" if cjk else "danmaku density (shifted for typing delay); lines = frames"
        draw.text((6, rows * cell_h + 4), caption, fill="#AAAAAA", font=small)
        peak = max(density) or 1
        bar_w = width / len(density)
        for k, n in enumerate(density):
            h = int(DENSITY_HEIGHT * n / peak)
            draw.rectangle((int(k * bar_w), top + DENSITY_HEIGHT - h, max(int(k * bar_w), int((k + 1) * bar_w) - 1), top + DENSITY_HEIGHT), fill="#4FA3E0")
        for i, f in enumerate(frames):
            fx = min(int(width * min(f["actual_time"] / duration, 1.0)), width - 2)
            draw.line((fx, top, fx, top + DENSITY_HEIGHT), fill="#B3261E" if f.get("peak") else "#FFFFFF", width=2)
            draw.text((fx + 3, top + 1), str(i + 1), fill="white", font=_font(14))
    sheet.save(path, quality=88)
    return path

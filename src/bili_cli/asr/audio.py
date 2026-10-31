"""Fetch a video page's audio track as 16 kHz mono 16-bit WAV."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .. import config
from ..errors import BiliError
from ..runtime import WEB_HEADERS

SAMPLE_RATE = 16000


def require_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        raise BiliError("invalid_argument", "转写需要 ffmpeg 来提取音频。", hint="安装 ffmpeg 并确保它在 PATH 中。")


def _audio_urls(playurl: dict[str, Any]) -> list[str]:
    dash = playurl.get("dash") or {}
    tracks = dash.get("audio") or []
    if tracks:
        track = min(tracks, key=lambda a: a.get("bandwidth") or 0)  # speech needs no high bitrate
        urls = [track.get("baseUrl") or track.get("base_url")] + list(track.get("backupUrl") or track.get("backup_url") or [])
    else:  # old FLV/MP4 videos without DASH: ffmpeg extracts the audio from the muxed file
        urls = [d.get("url") for d in playurl.get("durl") or []][:1]
    urls = [u for u in urls if u]
    # mcdn.bilivideo.cn peer-CDN hosts often refuse connections; try the upos hosts first
    return sorted(urls, key=lambda u: "mcdn" in u)


async def fetch_wav(playurl: dict[str, Any], key: str) -> Path:
    """Download (cached) the audio as WAV. `key` names the cache file, e.g. BV..._p1."""
    require_ffmpeg()
    out = config.home() / "cache" / "audio" / f"{key}.wav"
    if out.exists() and out.stat().st_size > 1000:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    urls = _audio_urls(playurl)
    if not urls:
        raise BiliError("not_found", "没有可用的音频流。")
    headers = "".join(f"{k}: {v}\r\n" for k, v in WEB_HEADERS.items())
    tmp = out.with_suffix(".part.wav")
    err = b""
    for url in urls:
        proc = await asyncio.to_thread(
            subprocess.run,
            ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-headers", headers, "-i", url,
             "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(tmp)],
            stdin=subprocess.DEVNULL, capture_output=True,
        )
        err = proc.stderr
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 1000:
            tmp.replace(out)
            return out
    raise BiliError("network_error", f"下载音频失败：{err.decode(errors='ignore')[-300:]}")

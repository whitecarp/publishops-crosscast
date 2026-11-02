"""Keep single requests from hanging, and tell a slow network from a frozen process.

A command that sits for minutes can mean three different things, and each looks different here:
- the network is slow or dead: requests time out, everything else keeps ticking;
- the event loop is blocked: the heartbeat thread keeps ticking, but asyncio timers fire late;
- the whole process or machine is frozen (container paused, CPU starved, laptop asleep): the
  heartbeat thread itself skips minutes.
Errors carry what was seen (`stalled_seconds` / `loop_blocked_seconds`), so a report says which.
BILI_CLI_DEBUG=1 also logs every request's duration to stderr.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time

from .errors import BiliError

REQUEST_DEADLINE = 45.0  # curl's own 30 s timeout should fire first; this catches requests whose timer never does
STALL_REPORT = 5.0       # gaps shorter than this are ordinary scheduling noise
TICK = 1.0


def debug_enabled() -> bool:
    return os.environ.get("BILI_CLI_DEBUG", "").lower() not in ("", "0", "false", "no")


def debug(message: str) -> None:
    if debug_enabled():
        sys.stderr.write(f"[bili-cli {time.strftime('%H:%M:%S')}] {message}\n")
        sys.stderr.flush()


class _Stats:
    def __init__(self) -> None:
        self.frozen = 0.0     # longest the whole process stood still (the heartbeat thread skipped ticks)
        self.loop_late = 0.0  # longest an asyncio timer fired late
        self.lock = threading.Lock()
        self.started = False

    def take(self) -> dict[str, float]:
        with self.lock:
            seen = {"frozen": self.frozen, "loop_late": self.loop_late}
            self.frozen = self.loop_late = 0.0
        return seen


stats = _Stats()


def record_tick(last: float, now: float) -> float:
    """Note how much longer than one tick the heartbeat slept; returns that gap."""
    gap = now - last - TICK
    if gap > STALL_REPORT:
        with stats.lock:
            stats.frozen = max(stats.frozen, gap)
        debug(f"整个进程停了约 {gap:.0f} 秒（机器或容器被挂起、CPU 被占满时会这样）")
    return gap


def _heartbeat() -> None:
    last = time.monotonic()
    while True:
        time.sleep(TICK)
        now = time.monotonic()
        record_tick(last, now)
        last = now


def start_heartbeat() -> None:
    if stats.started:
        return
    stats.started = True
    threading.Thread(target=_heartbeat, name="bili-cli-heartbeat", daemon=True).start()


async def loop_probe() -> None:
    """Runs inside the command's event loop and measures how late its timers fire."""
    while True:
        t0 = time.monotonic()
        await asyncio.sleep(TICK)
        late = time.monotonic() - t0 - TICK
        if late > STALL_REPORT:
            with stats.lock:
                stats.loop_late = max(stats.loop_late, late)
            debug(f"事件循环晚了约 {late:.0f} 秒")


def enrich(err: BiliError) -> BiliError:
    """Say on a network_error whether the process was frozen or the event loop blocked meanwhile."""
    if err.code != "network_error":
        return err
    seen = stats.take()
    note = None
    if seen["frozen"] >= STALL_REPORT:
        err.detail["stalled_seconds"] = round(seen["frozen"])
        note = (f"这次运行中整个进程停了约 {round(seen['frozen'])} 秒（机器或容器被挂起、或 CPU 被占满），"
                "超时多半不是网络本身造成的，检查运行环境。")
    elif seen["loop_late"] >= STALL_REPORT:
        err.detail["loop_blocked_seconds"] = round(seen["loop_late"])
        note = (f"这次运行中 bili-cli 的事件循环被卡住约 {round(seen['loop_late'])} 秒，可能是 bili-cli 或底层库的问题，"
                "请带上 BILI_CLI_DEBUG=1 的 stderr 日志反馈。")
    if note:
        err.hint = f"{err.hint} {note}" if err.hint else note
    return err


_installed = False


def install_request_guard() -> None:
    """Give every GET through bilibili-api a hard REQUEST_DEADLINE.

    curl's own timeout normally fires at 30 s, but in the field it fired after 52, 122, 313 and 378 s.
    Only reads are cut off: cancelling a POST halfway could leave a comment or a message sent twice.
    """
    global _installed
    if _installed:
        return
    try:
        from bilibili_api.utils import network
    except Exception:  # noqa: BLE001
        return
    original = network.Api._request

    async def _request(self, raw: bool = False, byte: bool = False):
        method = str(getattr(self, "method", "") or "").upper()
        label = f"{method} {getattr(self, 'url', '')}"
        t0 = time.monotonic()
        try:
            if method != "GET":
                result = await original(self, raw=raw, byte=byte)
            else:
                task = asyncio.ensure_future(original(self, raw=raw, byte=byte))
                try:
                    done, _ = await asyncio.wait({task}, timeout=REQUEST_DEADLINE)
                except asyncio.CancelledError:
                    task.cancel()
                    raise
                if task not in done:
                    task.cancel()
                    await asyncio.wait({task}, timeout=2)
                    raise TimeoutError(f"请求 {label} 超过 {REQUEST_DEADLINE:g} 秒没有回应，已放弃。")
                result = task.result()
        except BaseException as exc:
            debug(f"{label} {time.monotonic() - t0:.1f}s 失败 {type(exc).__name__}: {str(exc)[:160]}")
            raise
        debug(f"{label} {time.monotonic() - t0:.1f}s")
        return result

    network.Api._request = _request
    _installed = True

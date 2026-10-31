"""Split a WAV into speech segments of at most MAX_SEGMENT seconds, each with its start/end time.

Every engine receives the same segments, so every engine yields subtitle-style
timestamps even when its API returns plain text.
"""

from __future__ import annotations

import io
import re
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from .audio import SAMPLE_RATE

MAX_SEGMENT = 20.0
BYTES_PER_SECOND = SAMPLE_RATE * 2  # 16-bit mono


@dataclass
class Segment:
    start: float
    end: float
    pcm: bytes  # 16 kHz mono int16 little-endian

    def wav_bytes(self) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(self.pcm)
        return buf.getvalue()

    def floats(self):
        import numpy as np

        return np.frombuffer(self.pcm, dtype=np.int16).astype(np.float32) / 32768


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes())


def _cut(pcm: bytes, spans: list[tuple[float, float]]) -> list[Segment]:
    out = []
    for start, end in spans:
        # split anything longer than MAX_SEGMENT into equal pieces
        n = max(1, int((end - start) // MAX_SEGMENT) + (1 if (end - start) % MAX_SEGMENT else 0))
        step = (end - start) / n
        for i in range(n):
            s, e = start + i * step, start + (i + 1) * step
            chunk = pcm[int(s * SAMPLE_RATE) * 2:int(e * SAMPLE_RATE) * 2]
            if len(chunk) >= BYTES_PER_SECOND // 4:  # drop blips under 0.25 s
                out.append(Segment(round(s, 2), round(e, 2), chunk))
    return out


def vad_segments(path: Path, vad_model: Path) -> list[Segment]:
    """Silero VAD via sherpa-onnx: tells speech from music better than a silence threshold."""
    import numpy as np
    import sherpa_onnx as so

    pcm = read_pcm(path)
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
    cfg = so.VadModelConfig()
    cfg.silero_vad.model = str(vad_model)
    cfg.silero_vad.min_silence_duration = 0.3
    cfg.silero_vad.max_speech_duration = MAX_SEGMENT
    cfg.sample_rate = SAMPLE_RATE
    vad = so.VoiceActivityDetector(cfg, buffer_size_in_seconds=MAX_SEGMENT * 3)
    spans = []

    def drain():
        while not vad.empty():
            start = vad.front.start / SAMPLE_RATE
            spans.append((start, start + len(vad.front.samples) / SAMPLE_RATE))
            vad.pop()

    window = cfg.silero_vad.window_size
    for i in range(0, len(samples), window):
        vad.accept_waveform(samples[i:i + window])
        drain()
    vad.flush()
    drain()
    return _cut(pcm, spans)


def silence_segments(path: Path) -> list[Segment]:
    """Fallback without sherpa-onnx: ffmpeg silencedetect, speech = everything between silences."""
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-af", "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-"],
        stdin=subprocess.DEVNULL, capture_output=True,
    )
    log = proc.stderr.decode(errors="ignore")
    pcm = read_pcm(path)
    total = len(pcm) / BYTES_PER_SECOND
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    spans, cursor = [], 0.0
    for s, e in zip(starts, ends + [total] * (len(starts) - len(ends))):
        if s - cursor > 0.25:
            spans.append((cursor, s))
        cursor = e
    if total - cursor > 0.25:
        spans.append((cursor, total))
    # merge short neighbours so cloud APIs get fewer, fuller requests
    merged: list[list[float]] = []
    for s, e in spans:
        if merged and e - merged[-1][0] <= MAX_SEGMENT and s - merged[-1][1] < 1.0:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return _cut(pcm, [(s, e) for s, e in merged])

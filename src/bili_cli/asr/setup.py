"""`bili asr setup local`: install dependencies into the current Python, fetch models, self-test.

CPU: sherpa-onnx (bundles ONNX Runtime, no PyTorch) + Qwen3-ASR-0.6B int8 (~840 MB).
GPU: PyTorch CUDA + qwen-asr + Qwen3-ASR-0.6B bf16 (~4 GB total); no CUDA toolkit needed.
Both also get the 0.6 MB silero VAD used for segmentation.
Benchmarks behind these choices (i7-11800H / RTX 3060, 3 min of speech): CPU int8 77 s, GPU bf16 10.5 s.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import httpx

from .. import output
from ..errors import BiliError
from . import settings

GITHUB_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
SHERPA_PACKAGES = {
    "qwen3-asr": "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25",
    "sensevoice": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09",
}
VAD_FILE = "silero_vad.onnx"
HF_MODEL = "Qwen/Qwen3-ASR-0.6B"
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
MIN_VRAM_MB = 4000


def detect_gpu() -> dict[str, Any] | None:
    if not shutil.which("nvidia-smi"):
        return None
    proc = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    name, mem = [x.strip() for x in proc.stdout.strip().splitlines()[0].split(",")]
    return {"name": name, "vram_mb": int(float(mem))}


def _importable(module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except Exception:  # noqa: BLE001 - a broken install counts as missing
        return False


def _pip(*args: str) -> None:
    output.info(f"pip install {' '.join(args)}")
    proc = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *args], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if proc.returncode != 0:
        raise BiliError("internal_error", "安装依赖失败。", hint="检查网络，或手动执行上面的 pip 命令。", detail={"pip": proc.stderr[-800:]})
    importlib.invalidate_caches()


def _download(url: str, dest: Path) -> None:
    output.info(f"下载 {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            for chunk in resp.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total and sys.stderr.isatty():
                    sys.stderr.write(f"\r  {done >> 20}/{total >> 20} MB")
                    sys.stderr.flush()
    if sys.stderr.isatty():
        sys.stderr.write("\n")
    tmp.replace(dest)


def _fetch_release(name: str, proxy: str) -> Path:
    target = settings.models_dir() / name
    if target.exists():
        return target
    archive = settings.models_dir() / f"{name}.tar.bz2"
    _download(proxy + GITHUB_RELEASE + f"{name}.tar.bz2", archive)
    output.info("解压中…")
    with tarfile.open(archive, "r:bz2") as tar:
        try:
            tar.extractall(settings.models_dir(), filter="data")
        except TypeError:  # Python < 3.12 without extraction filters
            tar.extractall(settings.models_dir())
    archive.unlink()
    return target


def _fetch_vad(proxy: str) -> Path:
    path = settings.models_dir() / VAD_FILE
    if not path.exists():
        _download(proxy + GITHUB_RELEASE + VAD_FILE, path)
    return path


def plan(device: str, model: str) -> dict[str, Any]:
    gpu = detect_gpu()
    if device == "auto":
        device = "cuda" if gpu and gpu["vram_mb"] >= MIN_VRAM_MB and model == "qwen3-asr" else "cpu"
    if device == "cuda" and not gpu:
        raise BiliError("invalid_argument", "没有检测到 NVIDIA 显卡。", hint="去掉 --device gpu，使用 CPU 版本。")
    if device == "cuda" and model != "qwen3-asr":
        raise BiliError("invalid_argument", "GPU 版本只支持 qwen3-asr 模型。")
    return {
        "device": device,
        "gpu": gpu,
        "model": model,
        "packages": ["torch (CUDA 12.8)", "qwen-asr", "sherpa-onnx", "numpy"] if device == "cuda" else ["sherpa-onnx", "numpy"],
        "downloads": (
            [f"{HF_MODEL} (~1.8 GB, HuggingFace)", "PyTorch CUDA (~2.5 GB)", VAD_FILE]
            if device == "cuda"
            else [f"{SHERPA_PACKAGES[model]} ({'~840 MB' if model == 'qwen3-asr' else '~160 MB'}, GitHub)", VAD_FILE]
        ),
        "expected_speed": "约 0.06 倍实时（30 分钟视频约 2 分钟，RTX 3060 实测）" if device == "cuda"
        else ("约 0.45 倍实时（30 分钟视频约 13 分钟，i7-11800H 实测）" if model == "qwen3-asr" else "约 0.04 倍实时，但准确率较低、无标点"),
    }


def setup_local(device: str, model: str, github_proxy: str, hf_mirror: bool) -> dict[str, Any]:
    p = plan(device, model)
    proxy = github_proxy.rstrip("/") + "/" if github_proxy else ""
    if not _importable("sherpa_onnx") or not _importable("numpy"):
        _pip("sherpa-onnx", "numpy")
    vad = _fetch_vad(proxy)
    conf = settings.load()

    if p["device"] == "cuda":
        if not (_importable("torch") and importlib.import_module("torch").cuda.is_available()):
            _pip("torch", "--index-url", TORCH_INDEX)
        if not _importable("qwen_asr"):
            _pip("qwen-asr")
        if hf_mirror:
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        from huggingface_hub import snapshot_download

        output.info(f"下载模型 {HF_MODEL}…")
        model_path = snapshot_download(HF_MODEL)
        local = {"model": "qwen3-asr", "device": "cuda", "model_path": model_path,
                 "batch_size": 16 if (p["gpu"] or {}).get("vram_mb", 0) >= 6000 else 8}
    else:
        model_dir = _fetch_release(SHERPA_PACKAGES[model], proxy)
        local = {"model": model, "device": "cpu", "model_dir": str(model_dir), "threads": min(4, os.cpu_count() or 4)}

    conf["local"] = local
    conf["vad_model"] = str(vad)
    settings.save(conf)
    return {**p, "self_test": self_test(local), "config": settings.masked(conf)}


def self_test(local: dict[str, Any]) -> dict[str, Any]:
    """Run the engine on a short sample so setup only reports success when recognition really works."""
    import asyncio
    import time

    from . import engines
    from .segment import Segment, read_pcm

    sample = None
    if local.get("model_dir"):
        wavs = sorted(Path(local["model_dir"]).glob("test_wavs/*.wav"), key=lambda p: ("zh" not in p.name and "raokouling" not in p.name, p.name))
        sample = wavs[0] if wavs else None
    if sample is not None:
        # sample files come at various rates (raokouling.wav is 44.1 kHz); normalise like real audio
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "sample.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(sample), "-t", "15", "-ac", "1", "-ar", "16000", str(wav)],
                           check=True, stdin=subprocess.DEVNULL, capture_output=True)
            pcm = read_pcm(wav)
        seg = Segment(0, len(pcm) / 32000, pcm)
    else:
        seg = Segment(0, 1.0, b"\x00\x00" * 16000)  # GPU path: one second of silence proves the model loads on CUDA
    engine = engines.build_local(local)
    t0 = time.time()
    text = asyncio.run(engine.transcribe([seg]))[0]
    return {"ok": True, "sample": sample.name if sample else "silence", "text": text, "seconds": round(time.time() - t0, 1)}

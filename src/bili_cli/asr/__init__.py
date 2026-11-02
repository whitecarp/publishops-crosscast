"""Speech-to-text for videos without subtitles.

audio.py    fetch a page's audio track as 16 kHz mono WAV (ffmpeg, upos CDN first)
segment.py  split speech into <= 20 s segments with timestamps (silero VAD, or ffmpeg silencedetect)
engines.py  local (sherpa-onnx CPU / PyTorch GPU) and cloud (OpenAI-compatible, Aliyun, Tencent) recognizers
setup.py    one-command local deployment: dependencies, model download, self-test
settings.py asr.json in the config dir: which engines are configured and which is the default
"""

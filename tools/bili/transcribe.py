"""Transcribe audio with faster-whisper on CPU.

Usage: python transcribe.py <AUDIO> <OUT_DIR> <DURATION_SECONDS>
"""
import os
import pathlib
import sys
import time

from faster_whisper import WhisperModel

audio, out, duration = sys.argv[1], pathlib.Path(sys.argv[2]), float(sys.argv[3])
model_name = os.environ.get("WHISPER_MODEL") or ("medium" if duration <= 40 * 60 else "small")
print(f"[asr] model={model_name} duration={duration:.0f}s cpus={os.cpu_count()}", flush=True)

try:
    from opencc import OpenCC
    to_simplified = OpenCC("t2s").convert
except Exception:
    to_simplified = lambda s: s

model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
segments, info = model.transcribe(
    audio,
    language=os.environ.get("ASR_LANG", "zh"),
    beam_size=5,
    vad_filter=True,
    condition_on_previous_text=False,
    initial_prompt="以下是普通话的句子，使用简体中文，带标点符号。",
)

t0, lines = time.time(), []
for seg in segments:
    m, s = divmod(int(seg.start), 60)
    lines.append(f"[{m:02d}:{s:02d}] {to_simplified(seg.text.strip())}")
    if len(lines) % 50 == 0:
        print(f"[asr] {seg.end:.0f}/{duration:.0f}s after {time.time() - t0:.0f}s", flush=True)

(out / "transcript.txt").write_text("\n".join(lines), encoding="utf-8")
print(f"[asr] done: {len(lines)} segments in {time.time() - t0:.0f}s", flush=True)

"""Transcribe one 16 kHz mono WAV chunk with faster-whisper on CPU.

Usage: python transcribe.py <WAV> <OUT_TXT> <OFFSET_SECONDS>
Timestamps in the output are shifted by OFFSET so chunks can be concatenated.
"""
import os
import sys
import time
import wave

import numpy as np
from faster_whisper import WhisperModel

wav, out_txt, offset = sys.argv[1], sys.argv[2], float(sys.argv[3])
# Stock Whisper hallucinations on music or silence.
HALLUCINATIONS = ("优优独播剧场", "YoYo Television", "字幕由", "请不吝点赞", "明镜与点点")

try:
    from opencc import OpenCC
    to_simplified = OpenCC("t2s").convert
except Exception:
    to_simplified = lambda s: s

# Decode the WAV ourselves so faster-whisper never touches PyAV.
with wave.open(wav) as w:
    assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
    audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
duration = len(audio) / 16000
print(f"[asr] {wav}: {duration:.0f}s offset={offset:.0f}s cpus={os.cpu_count()}", flush=True)

threads = os.cpu_count() or 4
model = None
for name in [os.environ.get("WHISPER_MODEL", "large-v3-turbo"), "medium"]:
    try:
        model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=threads)
        print(f"[asr] model={name}", flush=True)
        break
    except Exception as e:
        print(f"[asr] load {name} failed: {e!r}", flush=True)


def run(vad):
    t0 = time.time()
    segments, _ = model.transcribe(
        audio,
        language=os.environ.get("ASR_LANG", "zh"),
        beam_size=5,
        vad_filter=vad,
        vad_parameters={"threshold": 0.35} if vad else None,
        condition_on_previous_text=False,
        initial_prompt="以下是普通话的句子，使用简体中文，带标点符号。",
    )
    segs = list(segments)
    covered = sum(s.end - s.start for s in segs)
    print(f"[asr] vad={vad}: {len(segs)} segments, {covered:.0f}s covered, {time.time() - t0:.0f}s", flush=True)
    return segs, covered


segs, covered = run(True)
if covered < 0.3 * duration:
    alt = [s for s in run(False)[0] if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0]
    if sum(s.end - s.start for s in alt) > covered:
        segs = alt

lines, recent = [], []
for seg in segs:
    text = to_simplified(seg.text.strip())
    if not text or any(h in text for h in HALLUCINATIONS) or recent.count(text) >= 2:
        continue
    recent = (recent + [text])[-3:]
    m, s = divmod(int(seg.start + offset), 60)
    lines.append(f"[{m:02d}:{s:02d}] {text}")

os.makedirs(os.path.dirname(out_txt) or ".", exist_ok=True)
with open(out_txt, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print(f"[asr] wrote {len(lines)} lines", flush=True)

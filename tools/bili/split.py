"""Split audio into 16 kHz mono WAV chunks, undoing stereo phase inversion.

Usage: python split.py <AUDIO> <CHUNK_DIR> <CHUNK_SECONDS> <DIAG_TXT>
Some re-uploads invert one stereo channel to dodge copyright matching; a plain
downmix then cancels centre-panned speech. Per 10 s block we keep L+R or L-R,
whichever the inter-channel correlation says carries the speech.
"""
import pathlib
import subprocess
import sys
import wave

import numpy as np

audio, chunk_dir, chunk_s, diag = sys.argv[1], pathlib.Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
SR, BLOCK = 16000, 10

raw = subprocess.run(
    ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", audio, "-f", "s16le", "-ac", "2", "-ar", str(SR), "-"],
    capture_output=True, check=True,
).stdout
x = np.frombuffer(raw, np.int16).reshape(-1, 2).astype(np.float32)
L, R = x[:, 0], x[:, 1]
mid, side = (L + R) / 2, (L - R) / 2


def dbfs(v):
    return 20 * np.log10(np.sqrt(np.mean(v * v)) + 1e-9) - 20 * np.log10(32768)


out = np.empty_like(mid)
lines = ["time   corr    L_dB    R_dB  mid_dB side_dB  pick"]
n = BLOCK * SR
for i in range(0, len(mid), n):
    l, r = L[i:i + n], R[i:i + n]
    corr = float(np.sum(l * r) / (np.sqrt(np.sum(l * l) * np.sum(r * r)) + 1e-9))
    pick = "side" if corr < -0.2 else "mid"
    out[i:i + n] = (side if pick == "side" else mid)[i:i + n]
    m, s = divmod(i // SR, 60)
    lines.append(f"{m:02d}:{s:02d} {corr:+.2f} {dbfs(l):7.1f} {dbfs(r):7.1f} "
                 f"{dbfs(mid[i:i + n]):7.1f} {dbfs(side[i:i + n]):7.1f}  {pick}")
pathlib.Path(diag).write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines[:1] + lines[1::6]), flush=True)

chunk_dir.mkdir(parents=True, exist_ok=True)
pcm = np.clip(out, -32768, 32767).astype(np.int16)
step = chunk_s * SR
for k, i in enumerate(range(0, len(pcm), step)):
    with wave.open(str(chunk_dir / f"c{k:02d}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm[i:i + step].tobytes())

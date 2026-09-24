"""Post-processing: trim silence, loudness-normalise, encode Ogg Vorbis (mono, 48 kHz)."""
import json
import re
import subprocess
from pathlib import Path

import numpy as np

SAMPLE_RATE = 48000
BITRATE_KBPS = 112
LUFS = -16.0
TRUE_PEAK = -1.0  # spec ceiling for the encoded file
ENCODE_HEADROOM = 0.5  # Vorbis overshoots true peak by ~0.3-0.4 dB, so normalise the WAV to -1.5 dBTP
LRA = 11.0
PAD_S = 0.15
SILENCE_DB = -45.0


def trim(samples: np.ndarray, rate: int, pad_s: float = PAD_S, silence_db: float = SILENCE_DB) -> np.ndarray:
    """Cut leading and trailing silence (10 ms frames under `silence_db` RMS), leaving `pad_s` either side."""
    frame = max(1, rate // 100)
    n = len(samples) // frame
    if n == 0:
        raise ValueError("empty audio")
    rms = np.sqrt(np.mean(samples[: n * frame].reshape(n, frame).astype(np.float64) ** 2, axis=1))
    loud = np.flatnonzero(rms > 10 ** (silence_db / 20))
    if loud.size == 0:
        raise ValueError("audio is silent")
    pad = int(pad_s * rate)
    start = max(0, loud[0] * frame - pad)
    end = min(len(samples), (loud[-1] + 1) * frame + pad)
    return samples[start:end]


def _ffmpeg_raw(samples: np.ndarray, rate: int, args: list[str]) -> str:
    """Run ffmpeg on mono float32 samples from stdin; return stderr."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-y", "-f", "f32le", "-ar", str(rate), "-ac", "1", "-i", "pipe:", *args],
        input=samples.astype("<f4").tobytes(), capture_output=True, check=True)
    return proc.stderr.decode(errors="replace")


def _loudnorm_json(stderr: str) -> dict:
    return json.loads(re.findall(r"\{[^{}]*\}", stderr)[-1])


def _target() -> str:
    return f"loudnorm=I={LUFS}:TP={TRUE_PEAK - ENCODE_HEADROOM}:LRA={LRA}"


def loudnorm(samples: np.ndarray, rate: int, out_wav: Path) -> None:
    """Two-pass ffmpeg loudnorm to -16 LUFS / -1.5 dBTP (-1 after encoding); writes 48 kHz mono 16-bit WAV."""
    m = _loudnorm_json(_ffmpeg_raw(samples, rate, ["-af", _target() + ":print_format=json", "-f", "null", "-"]))
    second = (f"{_target()}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
              f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    _ffmpeg_raw(samples, rate, ["-af", second, "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le",
                              "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact", str(out_wav)])


def encode_ogg(wav: Path, out: Path) -> None:
    """Ogg Vorbis via oggenc: Homebrew ffmpeg lacks libvorbis."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["oggenc", "-Q", "-b", str(BITRATE_KBPS), "-o", str(out), str(wav)], check=True)


def postprocess(samples: np.ndarray, rate: int, out_wav: Path) -> float:
    """Trim and loudness-normalise into `out_wav`; return its duration in seconds."""
    trimmed = trim(samples, rate)
    loudnorm(trimmed, rate, out_wav)
    return len(trimmed) / rate


def measure(path: Path) -> tuple[float, float]:
    """(integrated LUFS, true peak dBTP) of an audio file."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", _target() + ":print_format=json", "-f", "null", "-"],
        capture_output=True, check=True)
    m = _loudnorm_json(proc.stderr.decode(errors="replace"))
    return float(m["input_i"]), float(m["input_tp"])

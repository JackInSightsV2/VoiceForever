"""Acoustic ear-proxies for a Candidate, shown next to its player: pitch, roughness and darkness.
(Copied from the bake-off's measures, #10.) Not a substitute for listening.

- f0: median pitch (Hz).
- hnr: harmonics-to-noise ratio (dB). Lower = rougher, raspier.
- centroid: median spectral centroid of active frames (Hz). Lower = darker.
"""
from __future__ import annotations

import numpy as np


def spectral_centroid(audio: np.ndarray, sr: int, frame: int = 2048, hop: int = 512) -> float | None:
    """Median per-frame spectral centroid over frames within 30 dB of the loudest frame."""
    x = np.asarray(audio, dtype=np.float64).reshape(-1)
    if len(x) < frame:
        return None
    idx = np.arange(0, len(x) - frame + 1, hop)
    frames = np.stack([x[i:i + frame] for i in idx]) * np.hanning(frame)
    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    energy = spec.sum(axis=1)
    if energy.max() <= 0:
        return None
    active = energy > energy.max() * 10 ** (-30 / 10)
    freqs = np.fft.rfftfreq(frame, 1 / sr)
    cents = (spec[active] * freqs).sum(axis=1) / energy[active]
    return float(np.median(cents))


def measure(audio: np.ndarray, sr: int, male: bool = True) -> dict:
    """{"f0", "hnr", "centroid"}; a value is None where it can't be measured (e.g. no voiced frames)."""
    import parselmouth
    from parselmouth.praat import call

    snd = parselmouth.Sound(np.asarray(audio, dtype=np.float64), sampling_frequency=sr)
    floor, ceil = (50, 300) if male else (75, 500)
    pitch = snd.to_pitch(pitch_floor=floor, pitch_ceiling=ceil)
    f0 = pitch.selected_array["frequency"]
    f0 = f0[f0 > 0]
    hnr = call(snd.to_harmonicity_cc(minimum_pitch=floor), "Get mean", 0, 0)
    cen = spectral_centroid(audio, sr)
    return {
        "f0": round(float(np.median(f0)), 1) if len(f0) else None,
        "hnr": round(float(hnr), 1) if hnr == hnr else None,
        "centroid": None if cen is None else round(cen),
    }

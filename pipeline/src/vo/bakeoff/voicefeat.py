"""Acoustic ear-proxies for "does this sound like an orc?": pitch, roughness and darkness.

Not a substitute for listening; they flag which clips moved in the intended direction.
- f0: median pitch (Hz). WoW orc males sit far below a typical male (~110 Hz).
- hnr: harmonics-to-noise ratio (dB). Lower = rougher, raspier, more growl.
- centroid: median spectral centroid of active frames (Hz). Lower = darker = bigger body.
  (Formant tracking was tried first and proved unreliable on shifted/rough voices.)
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
    active = energy > energy.max() * 10 ** (-30 / 10)
    freqs = np.fft.rfftfreq(frame, 1 / sr)
    cents = (spec[active] * freqs).sum(axis=1) / energy[active]
    return float(np.median(cents))


def measure(audio: np.ndarray, sr: int, male: bool = True) -> dict:
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


# --- round 5: is the roughness sustained, or does the voice "clean itself up"? -----------------------

ROUGH_DB = 7.0  # a voiced 10 ms frame with HNR below this counts as rough (clean male speech sits ~12-20 dB)


def thirds(times: np.ndarray, hnr: np.ndarray, duration: float, rough_db: float = ROUGH_DB) -> dict:
    """Pure: per-frame HNR (dB; Praat's -200 marks unvoiced) -> roughness of the first, middle and last
    third of the clip. `hnr_thirds`: mean HNR of voiced frames per third; `rough_thirds`: share of voiced
    frames below `rough_db`; `rough`: that share over the whole clip; `sustained`: the lowest third's
    share (a clip is only as orcish as its cleanest third); `drift`: last-third minus first-third mean HNR
    (positive = the voice got cleaner as it went on)."""
    t, h = np.asarray(times, dtype=np.float64), np.asarray(hnr, dtype=np.float64)
    voiced = h > -100
    edges = (0, duration / 3, 2 * duration / 3, duration + 1e-9)
    hs, rs = [], []
    for a, b in zip(edges, edges[1:]):
        v = h[voiced & (t >= a) & (t < b)]
        hs.append(round(float(v.mean()), 1) if len(v) else None)
        rs.append(round(float((v < rough_db).mean()), 3) if len(v) else None)
    allv = h[voiced]
    known = [r for r in rs if r is not None]
    return {"hnr_thirds": hs, "rough_thirds": rs,
            "rough": round(float((allv < rough_db).mean()), 3) if len(allv) else None,
            "sustained": min(known) if known else None,
            "drift": round(hs[2] - hs[0], 1) if hs[0] is not None and hs[2] is not None else None}


def roughness(audio: np.ndarray, sr: int, male: bool = True) -> dict:
    import parselmouth

    snd = parselmouth.Sound(np.asarray(audio, dtype=np.float64), sampling_frequency=sr)
    h = snd.to_harmonicity_cc(time_step=0.01, minimum_pitch=50 if male else 75)
    return thirds(h.xs(), h.values[0], snd.duration)

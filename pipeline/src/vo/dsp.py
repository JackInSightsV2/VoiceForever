"""Voice DSP for effect chains (vo.effects): make a voice bigger, rougher and more "fantasy".

Copied from the bake-off's vo.bakeoff.dsp (#10 round 5), which the pipeline does not import. A Chain is plain data;
`apply` runs it:
1. Praat "Change gender": pitch shift (semitones), formant shift (ratio < 1 = longer vocal tract = bigger
   creature), pitch-range scaling.
2. Sub-octave layer: an octave-down copy mixed in quietly.
3. Subharmonic ("period doubling"): every other glottal cycle attenuated, following the tracked pitch; real growl
   and vocal fry are largely period doubling.
4. Growl layer: an octave-down copy, roughened and hard-driven, band-limited to the throat range, mixed under.
5. Rasp: fast irregular amplitude modulation (30-70 Hz), heard as roughness.
6. Saturation in parallel (drive + wet mix), then low-shelf body and a gentle high cut.
7. Peak-safe level match back to the input RMS.
Deterministic: the same input gives the same output (unlike the bake-off's copy, Praat's random generator is
seeded for each call).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class Chain:
    pitch_st: float = 0.0        # semitones
    formant: float = 1.0         # formant shift ratio
    range_: float = 1.0          # pitch range factor (1 = unchanged, <1 = flatter)
    sub: float = 0.0             # sub-octave layer gain (0..1, relative)
    rasp: float = 0.0            # amplitude-modulation depth (0..1)
    rasp_hz: float = 45.0        # modulation rate
    drive_db: float = 0.0        # saturation drive
    wet: float = 0.0             # saturation parallel mix (0..1)
    low_shelf_db: float = 0.0    # +dB below ~160 Hz
    high_cut_hz: float = 0.0     # 0 = off
    subharm: float = 0.0         # period-doubling depth (0..1)
    growl: float = 0.0           # distorted octave-down growl layer gain (0..1, relative)

    @property
    def is_identity(self) -> bool:
        return self == Chain()

    def describe(self) -> str:
        if self.is_identity:
            return "none"
        d = {k: v for k, v in asdict(self).items() if v != getattr(Chain(), k)}
        return ", ".join(f"{k.rstrip('_')}={v:g}" for k, v in d.items())


PRAAT_SEED = 5


def _change_gender(audio: np.ndarray, sr: int, formant: float, pitch_st: float, range_: float) -> np.ndarray:
    import parselmouth
    from parselmouth.praat import call

    snd = parselmouth.Sound(audio.astype(np.float64), sampling_frequency=sr)
    pitch = snd.to_pitch(pitch_floor=60, pitch_ceiling=600)
    f0 = pitch.selected_array["frequency"]
    f0 = f0[f0 > 0]
    median = float(np.median(f0)) if len(f0) else 0.0
    new_median = median * 2 ** (pitch_st / 12) if median else 0.0
    # Praat's overlap-add re-synthesis draws random numbers; seed them so an anchor processes the same every time.
    parselmouth.praat.run(f"random_initializeWithSeedUnsafelyButPredictably ({PRAAT_SEED})")
    try:
        out = call(snd, "Change gender", 60, 600, formant, new_median, range_, 1.0)
    finally:
        parselmouth.praat.run("random_initializeSafelyAndUnpredictably ()")
    return np.asarray(out.values[0], dtype=np.float32)[: len(audio)]


def rasp_envelope(n: int, sr: int, depth: float, hz: float, seed: int = 0) -> np.ndarray:
    """Irregular AM envelope in [1-depth, 1]: a jittered sine, so it reads as grit, not tremolo."""
    if depth <= 0:
        return np.ones(n, dtype=np.float32)
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sr
    # Smoothly wandering instantaneous rate (+-30 %) and a little noise.
    wander = np.interp(t, np.linspace(0, t[-1] if n > 1 else 1, 64), rng.uniform(-0.3, 0.3, 64))
    phase = 2 * np.pi * np.cumsum(hz * (1 + wander)) / sr
    mod = 0.5 * (1 + np.sin(phase)) * 0.8 + 0.2 * rng.random(n)
    return (1 - depth * mod).astype(np.float32)


def subharmonic_envelope(n: int, sr: int, times: np.ndarray, f0: np.ndarray, depth: float) -> np.ndarray:
    """Gain envelope that attenuates every other pitch period: 1 - depth * (1 + cos(phase)) / 2 with the
    phase running at f0/2 (f0 track: `times` in s, `f0` in Hz, 0 = unvoiced). Unvoiced samples keep gain 1;
    the depth fades in and out over ~20 ms at voicing edges so there are no clicks."""
    if depth <= 0 or n == 0 or len(times) == 0:
        return np.ones(n, dtype=np.float32)
    t = np.arange(n) / sr
    voiced = (np.asarray(f0) > 0).astype(np.float64)
    f = np.asarray(f0, dtype=np.float64)
    fill = np.where(f > 0, f, np.nan)
    if np.all(np.isnan(fill)):
        return np.ones(n, dtype=np.float32)
    idx = np.arange(len(fill))
    ok = ~np.isnan(fill)
    fill = np.interp(idx, idx[ok], fill[ok])  # bridge unvoiced gaps so the phase stays continuous
    f_s = np.interp(t, times, fill)
    v_s = np.interp(t, times, voiced)
    k = max(1, int(0.02 * sr))
    v_s = np.convolve(v_s, np.ones(k) / k, mode="same")
    phase = 2 * np.pi * np.cumsum(f_s / 2) / sr
    return (1 - depth * v_s * 0.5 * (1 + np.cos(phase))).astype(np.float32)


def _pitch_track(x: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    import parselmouth

    p = parselmouth.Sound(x.astype(np.float64), sampling_frequency=sr).to_pitch(time_step=0.005, pitch_floor=50,
                                                                              pitch_ceiling=400)
    return p.xs(), p.selected_array["frequency"]


def _growl_layer(x: np.ndarray, sr: int, chain: Chain) -> np.ndarray:
    from pedalboard import Distortion, HighpassFilter, LowpassFilter, Pedalboard

    low = _change_gender(x, sr, 0.9, -12.0, 1.0)[: len(x)]
    low = low * rasp_envelope(len(low), sr, 0.7, max(chain.rasp_hz, 30.0) * 0.8, seed=1)
    g = Pedalboard([HighpassFilter(cutoff_frequency_hz=70), Distortion(drive_db=28),
                    LowpassFilter(cutoff_frequency_hz=2200)])(low[None, :].astype(np.float32), sr)[0]
    return g * (np.sqrt(np.mean(x**2)) / (np.sqrt(np.mean(g**2)) or 1))


def apply(audio: np.ndarray, sr: int, chain: Chain) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if chain.is_identity or len(audio) < sr // 10:
        return audio
    rms_in = float(np.sqrt(np.mean(audio**2))) or 1e-4
    x = audio
    if chain.formant != 1.0 or chain.pitch_st != 0.0 or chain.range_ != 1.0:
        x = _change_gender(x, sr, chain.formant, chain.pitch_st, chain.range_)
    if chain.sub > 0:
        low = _change_gender(x, sr, 1.0, -12.0, 1.0)
        x = x + chain.sub * low[: len(x)]
    if chain.subharm > 0:
        times, f0 = _pitch_track(x, sr)
        x = x * subharmonic_envelope(len(x), sr, times, f0, chain.subharm)
    if chain.growl > 0:
        x = x + chain.growl * _growl_layer(x, sr, chain)
    if chain.rasp > 0:
        x = x * rasp_envelope(len(x), sr, chain.rasp, chain.rasp_hz)
    from pedalboard import Distortion, HighShelfFilter, LowShelfFilter, LowpassFilter, Pedalboard

    if chain.wet > 0 and chain.drive_db > 0:
        dist = Pedalboard([Distortion(drive_db=chain.drive_db), LowpassFilter(cutoff_frequency_hz=6000)])
        d = dist(x[None, :], sr)[0]
        d *= (np.sqrt(np.mean(x**2)) / (np.sqrt(np.mean(d**2)) or 1))
        x = (1 - chain.wet) * x + chain.wet * d
    post = []
    if chain.low_shelf_db:
        post.append(LowShelfFilter(cutoff_frequency_hz=160, gain_db=chain.low_shelf_db))
    if chain.high_cut_hz:
        post.append(HighShelfFilter(cutoff_frequency_hz=chain.high_cut_hz, gain_db=-6))
    if post:
        x = Pedalboard(post)(x[None, :].astype(np.float32), sr)[0]
    rms_out = float(np.sqrt(np.mean(x**2))) or 1e-4
    x = x * (rms_in / rms_out)
    peak = float(np.max(np.abs(x)))
    if peak > 0.98:
        x = x * (0.98 / peak)
    return x.astype(np.float32)

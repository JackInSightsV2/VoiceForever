"""Per-race voice processing: make a human-sized voice sound bigger, rougher and more "fantasy".

A Chain is plain data (shown on the listening page). `apply` runs it:
1. Praat "Change gender": pitch shift (semitones), formant shift (ratio < 1 = longer vocal
   tract = bigger creature), pitch-range scaling.
2. Sub-octave layer: an octave-down copy mixed in quietly, the "two voices at once" growl.
3. Rasp: fast irregular amplitude modulation (30-70 Hz), heard as roughness / vocal fry.
4. Saturation in parallel (drive + wet mix), then low-shelf body and a gentle high cut.
5. Peak-safe level match back to the input RMS.

`vary` derives a bounded, deterministic per-NPC variation of a Chain from a seed (the NPC id),
so NPC Voices of one Archetype differ in size/pitch/grit but stay inside the race's range.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass, replace

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

    @property
    def is_identity(self) -> bool:
        return self == Chain()

    def describe(self) -> str:
        if self.is_identity:
            return "none"
        d = {k: v for k, v in asdict(self).items() if v != getattr(Chain(), k)}
        return ", ".join(f"{k.rstrip('_')}={v:g}" for k, v in d.items())

    def scaled(self, k: float) -> "Chain":
        """Scale every effect's strength by k (k=0 is identity, k=1 is this chain)."""
        base = Chain()
        if k == 0:
            return base
        lerp = lambda a, b: a + (b - a) * k  # noqa: E731
        return Chain(
            pitch_st=round(lerp(base.pitch_st, self.pitch_st), 3),
            formant=round(lerp(base.formant, self.formant), 4),
            range_=round(lerp(base.range_, self.range_), 3),
            sub=round(min(1.0, lerp(0, self.sub)), 3),
            rasp=round(min(0.9, lerp(0, self.rasp)), 3),
            rasp_hz=self.rasp_hz,
            drive_db=round(lerp(0, self.drive_db), 2),
            wet=round(min(1.0, lerp(0, self.wet)), 3),
            low_shelf_db=round(lerp(0, self.low_shelf_db), 2),
            high_cut_hz=self.high_cut_hz,
        )


# Per race and gender. Humans are deliberately untouched. Troll/dwarf chains are mild: their
# character is mostly accent, which DSP cannot add; the chain only adds size and grit.
RACE_CHAINS: dict[str, Chain] = {
    "orc_m": Chain(pitch_st=-3, formant=0.88, range_=0.85, sub=0.22, rasp=0.35, rasp_hz=48,
                   drive_db=14, wet=0.3, low_shelf_db=3, high_cut_hz=9000),
    "orc_f": Chain(pitch_st=-2, formant=0.93, range_=0.9, sub=0.08, rasp=0.22, rasp_hz=60,
                   drive_db=10, wet=0.2, low_shelf_db=2, high_cut_hz=10000),
    "troll_m": Chain(pitch_st=-1.5, formant=0.93, sub=0.05, rasp=0.25, rasp_hz=40,
                     drive_db=10, wet=0.2, low_shelf_db=1.5),
    "troll_f": Chain(pitch_st=-0.5, formant=0.96, rasp=0.18, rasp_hz=55, drive_db=8, wet=0.15),
    "dwarf_f": Chain(pitch_st=-1, formant=0.95, rasp=0.08, drive_db=6, wet=0.1, low_shelf_db=1.5),
    "human_m": Chain(),
    "human_f": Chain(),
}

# Bounds for per-NPC variation around an Archetype's chain.
VARY = {"pitch_st": 2.0, "formant": 0.04, "rasp": 0.12, "sub": 0.08, "wet": 0.1, "rasp_hz": 12.0}


def vary(chain: Chain, seed: int | str) -> Chain:
    """Deterministic bounded jitter of a chain (seed = NPC id)."""
    r = random.Random(f"npc-voice:{seed}")
    u = lambda: r.uniform(-1, 1)  # noqa: E731
    return replace(
        chain,
        pitch_st=round(chain.pitch_st + VARY["pitch_st"] * u(), 2),
        formant=round(chain.formant + VARY["formant"] * u(), 3),
        rasp=round(min(0.9, max(0.0, chain.rasp + VARY["rasp"] * u())), 3),
        sub=round(min(1.0, max(0.0, chain.sub + VARY["sub"] * u())), 3),
        wet=round(min(1.0, max(0.0, chain.wet + VARY["wet"] * u())), 3),
        rasp_hz=round(max(20.0, chain.rasp_hz + VARY["rasp_hz"] * u()), 1),
    )


def _change_gender(audio: np.ndarray, sr: int, formant: float, pitch_st: float, range_: float) -> np.ndarray:
    import parselmouth
    from parselmouth.praat import call

    snd = parselmouth.Sound(audio.astype(np.float64), sampling_frequency=sr)
    pitch = snd.to_pitch(pitch_floor=60, pitch_ceiling=600)
    f0 = pitch.selected_array["frequency"]
    f0 = f0[f0 > 0]
    median = float(np.median(f0)) if len(f0) else 0.0
    new_median = median * 2 ** (pitch_st / 12) if median else 0.0
    out = call(snd, "Change gender", 60, 600, formant, new_median, range_, 1.0)
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

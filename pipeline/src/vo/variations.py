"""Variation Candidates: new Candidates derived from one the reviewer likes (review action `vary-candidate`).

A request renders N (default 4) children of the source Candidate, ids "<source id>~v<k>" (k counts up per source, so
a repeated request adds N more), half by each method:

- "style": VoxCPM2 controllable cloning, the source anchor as reference plus a "(style)" prefix from a rotating set
  (and the reviewer's note), reading the Archetype's anchor line. Bake-off round 3 (#10) found continuation +
  description reads the description aloud, so it is reference + style, never continuation + style.
- "dsp": the source anchor itself, pitch +-2-4 st, formant +-6-10 %, pace +-5-10 % (Praat "Change gender",
  vo.voices.apply_shift): bigger than an NPC Voice's shift, in a direction (pitch, formant, pace signs) no earlier
  variation of the source took, where one is left.

Each is measured (pitch, HNR, ASR WER against its transcript, WavLM-SV similarity to the source); one that misreads
(WER > MAX_WER) or is near-identical to the source (similarity > MAX_SIM) is re-rendered with the next seed or shift,
up to MAX_TRIES per slot, then the slot is dropped. A variation inherits the source's continuation mode and never goes
through an anchor chain again (the source anchor already did). The rendering itself is vo.prepare's.
"""
from __future__ import annotations

import random
import re
import zlib
from dataclasses import dataclass

from vo.voices import Shift

ACTION = "vary-candidate"
SEP = "~v"
N = 4
METHODS = ("style", "dsp")  # slot j of a request uses METHODS[j % 2]: 2 + 2 by default
MAX_TRIES = 3
MAX_WER = 0.3
MAX_SIM = 0.985
# WavLM barely hears pitch/formant (ADR-0005): a strong DSP shift sounds different but still scores ~0.98.
MAX_SIM_DSP = 0.99
STYLES = (
    "older, more weathered and gravelly",
    "younger and lighter",
    "rougher, harsher",
    "calmer and lower",
    "brighter, more nasal",
    "wearier and breathier",
    "more forceful and commanding",
    "slower and more deliberate",
)
PITCH_ST = (2.0, 4.0)
FORMANT = (0.06, 0.10)
PACE = (0.05, 0.10)
# (pitch, formant, pace) sign triples, most natural first: a bigger or smaller speaker (pitch and formant together),
# then the crossed ones.
DIRECTIONS = ((-1, -1, 1), (1, 1, -1), (-1, -1, -1), (1, 1, 1), (-1, 1, 1), (1, -1, -1), (-1, 1, -1), (1, -1, 1))


def variation_id(source: str, k: int) -> str:
    return f"{source}{SEP}{k}"


def parent(cid: str) -> str | None:
    """The Candidate a variation was made from (its direct source), or None."""
    m = re.fullmatch(r"(.+)~v\d+", cid)
    return m.group(1) if m else None


def child_ks(ids, source: str) -> list[int]:
    """The k of every direct variation of `source` among `ids`."""
    pat = re.compile(re.escape(source) + r"~v(\d+)")
    return sorted(int(m.group(1)) for i in ids if (m := pat.fullmatch(i)))


def method(j: int) -> str:
    return METHODS[j % len(METHODS)]


def seed(source: str, k: int, attempt: int) -> int:
    """Deterministic per source, slot and attempt."""
    return (zlib.crc32(source.encode()) % 100_000) * 100 + (k % 30) * 3 + attempt


def style(used: list[str], k: int, note: str | None = None) -> str:
    """The style prefix for slot k: the first of STYLES (rotating from k) no earlier variation of the source used,
    else the rotation itself; the reviewer's note appended. (A retry keeps the style and changes the seed.)"""
    order = [STYLES[(k - 1 + i) % len(STYLES)] for i in range(len(STYLES))]
    used = [u for u in used if u]
    base = next((s for s in order if not any(u == s or u.startswith(s + ";") for u in used)), order[0])
    note = (note or "").strip().rstrip(".")
    return f"{base}; {note}" if note else base


def _signs(s: Shift) -> tuple[int, int, int]:
    return (1 if s.pitch_st > 0 else -1, 1 if s.formant > 1 else -1, 1 if s.pace > 1 else -1)


def shift(used: list[Shift], source: str, k: int, attempt: int) -> Shift:
    """A strong DSP shift for slot k, in the first direction no earlier shift of the source (`used`, failed
    attempts included, so a retry turns too) took; magnitudes seeded from (source, k, attempt)."""
    taken = {_signs(s) for s in used}
    d = next((d for d in DIRECTIONS if d not in taken), DIRECTIONS[(k + attempt) % len(DIRECTIONS)])
    rng = random.Random(f"vary:{source}:{k}:{attempt}")
    p, f, t = rng.uniform(*PITCH_ST), rng.uniform(*FORMANT), rng.uniform(*PACE)
    return Shift(round(d[0] * p, 2), round(1 + d[1] * f, 4), round(1 + d[2] * t, 3))


def describe_shift(s: Shift) -> str:
    pace = f"pace {abs(s.pace - 1):.0%} {'slower' if s.pace > 1 else 'faster'}"
    return f"pitch {s.pitch_st:+.1f} st, formant {s.formant - 1:+.0%}, {pace}"


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str


def gate(wer: float, sim: float | None, method: str = "style") -> Verdict:
    if wer > MAX_WER:
        return Verdict(False, f"misread (WER {wer:.0%} > {MAX_WER:.0%})")
    limit = MAX_SIM_DSP if method == "dsp" else MAX_SIM
    if sim is not None and sim > limit:
        return Verdict(False, f"near-identical to the source (similarity {sim:.3f} > {limit})")
    return Verdict(True, "ok")

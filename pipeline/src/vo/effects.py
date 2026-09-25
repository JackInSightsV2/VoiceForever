"""Per-Archetype effect chains. A chain is a function (mono float32 samples, sample rate) -> samples, registered by
name in CHAINS. An Archetype can reference one in two places (style guide, then the DB and approved_voices.json):

- `anchor_chain` (the one to use): applied ONCE to each Candidate's designed anchor. The processed clip is the
  Candidate's anchor: its samples, and every line `vo run` renders, are plain VoxCPM2 continuations of it, so the
  character the chain adds is copied into every line while the voice stays one person. The unprocessed design is
  kept next to it (`raw_path`). Bake-off round 5 (#10, orc male): chain on the anchor = same person, character kept.
- `effect_chain` (per line, off by default): applied to every rendered line after continuation. Round 5 found it
  loses character (cont-dsp), so no Archetype sets it; the hook stays for effects that must be line-level.

`orc`: round 5's ORC_CHAIN (vo.dsp), set as orc_m's anchor chain.
"""
from typing import Callable

import numpy as np

from vo import dsp

Chain = Callable[[np.ndarray, int], np.ndarray]

# Bake-off round 5's orc chain (vo.bakeoff.round5.ORC_CHAIN, copied): size stages (pitch/formant down, sub-octave),
# the two growl stages (period-doubling subharmonic, distorted growl layer) that lower HNR on every voiced frame,
# rasp, parallel saturation, EQ.
ORC_CHAIN = dsp.Chain(pitch_st=-2.5, formant=0.86, range_=0.8, sub=0.15, rasp=0.25, rasp_hz=45, drive_db=12, wet=0.25,
                      low_shelf_db=3, high_cut_hz=9000, subharm=0.5, growl=0.4)

CHAINS: dict[str, Chain] = {
    "orc": lambda samples, rate: dsp.apply(samples, rate, ORC_CHAIN),
}


def apply(name: str | None, samples: np.ndarray, rate: int) -> np.ndarray:
    if not name:
        return samples
    if name not in CHAINS:
        raise ValueError(f"unknown effect chain {name!r}; registered: {sorted(CHAINS)}")
    return np.asarray(CHAINS[name](samples, rate), dtype=np.float32)

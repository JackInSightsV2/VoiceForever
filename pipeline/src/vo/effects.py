"""Per-Archetype effect chains: post-render processing applied to every line of an Archetype (e.g. an Undead or
Great Beasts reverb), never to its anchor, so continuation copies the clean voice and the effect stays consistent.

None by default. A chain is a function (mono float32 samples, sample rate) -> samples, registered by name in CHAINS
and referenced by an Archetype's `effect_chain` (style guide, then approved_voices.json). The orc-male experiment
(#10 round 5) will supply the first chains.
"""
from typing import Callable

import numpy as np

Chain = Callable[[np.ndarray, int], np.ndarray]

CHAINS: dict[str, Chain] = {}


def apply(name: str | None, samples: np.ndarray, rate: int) -> np.ndarray:
    if not name:
        return samples
    if name not in CHAINS:
        raise ValueError(f"unknown effect chain {name!r}; registered: {sorted(CHAINS)}")
    return np.asarray(CHAINS[name](samples, rate), dtype=np.float32)

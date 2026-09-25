"""Bake-off anchors seeded as Archetype Candidates (`vo prepare --import-bakeoff <archetype>`).

Where no voice-design Candidate holds the character, the Candidates come from the bake-off (#10) instead: anchor
clips a human already liked, with their transcript, seed and description, copied into build/candidates/ and, for a
Seed with a chain, processed with that anchor chain (vo.effects) exactly as a designed Candidate would be.

orc_m (round 5): best = round-4 s6 with the orc chain on the anchor, then plain continuation ("same person +
character kept"). Also liked: the new round-5 anchors growl-s7 and tags-s6, seeded with and without the chain.
Descriptions are copied from vo.bakeoff (round 2/4's orc_m, round 5's "growl" and "tags"); the pipeline doesn't
import the bake-off.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Seed:
    key: str          # candidate id is "<archetype>/<key>"
    label: str        # shown on the Approval page
    source: str       # the bake-off clip, relative to build/
    seed: int
    description: str  # the voice-design description the clip was rendered from
    chain: str | None = None  # anchor chain (vo.effects.CHAINS) applied on import


R4_ORC_M = ("A huge, hulking male orc warrior with an extremely deep bass voice, very low pitch, thick gravel and a "
            "guttural growl in the throat, harsh vocal fry on every word. Slow, heavy, menacing and proud, biting off "
            "short forceful phrases like a battle-scarred warchief. Monstrous, not human.")
R5_GROWL = ("A monstrous male orc warlord. An extremely deep, guttural bass voice with a heavy throat growl on every "
            "single word, thick crackling gravel and harsh vocal fry from the first word to the last, never clean or "
            "smooth. Snarling, bestial and menacing, huge booming chest resonance, slow, heavy, deliberate pace. "
            "Inhuman.")
R5_TAGS = ("Male orc, middle-aged, very deep guttural growling voice, gravelly, raspy, heavy vocal fry, snarling, strong "
           "chest resonance, very low pitch, slow menacing pace, aggressive")

SEEDS: dict[str, tuple[Seed, ...]] = {
    "orc_m": (
        Seed("r4-s6-orc", "r4-s6 + orc chain (bake-off winner)", "bakeoff4/anchors/orc_m/s6.wav", 6, R4_ORC_M, "orc"),
        Seed("growl-s7-orc", "growl-s7 + orc chain (round 5)", "bakeoff5/anchors/growl-s7.wav", 7, R5_GROWL, "orc"),
        Seed("tags-s6-orc", "tags-s6 + orc chain (round 5)", "bakeoff5/anchors/tags-s6.wav", 6, R5_TAGS, "orc"),
        Seed("growl-s7", "growl-s7 raw (round 5, no chain)", "bakeoff5/anchors/growl-s7.wav", 7, R5_GROWL),
        Seed("tags-s6", "tags-s6 raw (round 5, no chain)", "bakeoff5/anchors/tags-s6.wav", 6, R5_TAGS),
    ),
}

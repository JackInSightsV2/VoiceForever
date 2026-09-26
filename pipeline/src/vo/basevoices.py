"""Base Voices (ADR-0006): an Archetype's approved Anchors, and which one each of its NPCs is built on.

An Archetype may have up to MAX approved Candidates. Each is a Base Voice: a different person of that race and gender.
Its NPCs are spread across them, and the per-NPC DSP shift (vo.voices) adds range on top, so 357 human males are 8
people times variations, not 1 person times 357.

Assignment, per Archetype, over the NPCs that have a spoken line, in NPC id order (greedy): each NPC takes the Base
Voice least used among its same-Archetype Neighbours assigned before it; ties go to the Base Voice used least so far
(balance), then rotate by NPC id. Deterministic, and each NPC's pick depends only on the NPCs before it.

Sticky: an NPC whose voice_builds row was assigned under the Archetype's current set of Base Voices (voice_builds.anchor
and .base_voices) keeps its Base Voice, so a new NPC or a changed Neighbour doesn't move voices already built. When the
set changes (an approval added or withdrawn), none is sticky: the Archetype's NPCs are re-assigned over the new set, and
those whose Base Voice changed are rebuilt (vo.voices.drop_stale).
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass

from vo import archetypes, lock, neighbours

MAX = 8  # approved Candidates (Base Voices) per Archetype


def plural(n: int) -> str:
    return f"{n} Base Voice{'' if n == 1 else 's'}"


def signature(bases: list[str]) -> str:
    """How voice_builds.base_voices records an Archetype's set of Base Voices."""
    return json.dumps(sorted(bases))


def bases_of(data: dict) -> dict[str, list[str]]:
    """{Archetype: its Base Voices' candidate ids, sorted} from lock data."""
    out = {}
    for aid, e in data["archetypes"].items():
        ids = sorted(a["candidate"] for a in lock.anchors(e))
        if ids:
            out[aid] = ids
    return out


def assign(npc_arch: dict[int, str], bases: dict[str, list[str]], adjacent: dict[int, set[int]],
           fixed: dict[int, str] | None = None) -> dict[int, str]:
    """{npc: Base Voice} for every NPC whose Archetype has Base Voices. `adjacent`: Neighbours per NPC. `fixed`: NPCs
    that keep a Base Voice (sticky), taken as they come in id order. Pure and deterministic."""
    fixed = fixed or {}
    members: dict[str, list[int]] = defaultdict(list)
    for npc, aid in npc_arch.items():
        if aid in bases:
            members[aid].append(npc)
    out: dict[int, str] = {}
    for aid, npcs in members.items():
        options = bases[aid]
        k = len(options)
        count: Counter = Counter()
        for n in sorted(npcs):
            pick = fixed.get(n)
            if pick not in options:
                near = Counter(out[m] for m in adjacent.get(n, ()) if m in out and npc_arch.get(m) == aid)
                pick = min(range(k), key=lambda i: (near[options[i]], count[options[i]], (i - n) % k))
                pick = options[pick]
            out[n] = pick
            count[pick] += 1
    return out


def adjacency(conn: sqlite3.Connection) -> dict[int, set[int]]:
    out: dict[int, set[int]] = defaultdict(set)
    for a, b in conn.execute("SELECT a, b FROM neighbours"):
        out[a].add(b)
        out[b].add(a)
    return out


def voiced_archetypes(conn: sqlite3.Connection) -> dict[int, str]:
    """{npc: Archetype} for NPCs with a spoken line and an NPC Archetype (not the Narrator)."""
    voiced = neighbours.voiced_npcs(conn)
    return {n: aid for n, aid in archetypes.npc_archetypes(conn).items()
            if n in voiced and aid != archetypes.NARRATOR}


def sticky(conn: sqlite3.Connection, bases: dict[str, list[str]], npc_arch: dict[int, str]) -> dict[int, str]:
    """NPCs whose stored Base Voice was assigned under their Archetype's current set."""
    sig = {aid: signature(b) for aid, b in bases.items()}
    out = {}
    for npc, anchor, recorded in conn.execute("SELECT npc_id, anchor, base_voices FROM voice_builds"):
        aid = npc_arch.get(npc)
        if aid in sig and recorded == sig[aid] and anchor in bases[aid]:
            out[npc] = anchor
    return out


def assignments(conn: sqlite3.Connection, data: dict) -> dict[int, str]:
    """{npc: Base Voice candidate id} for every voiced NPC of an Archetype in the lock data."""
    npc_arch = voiced_archetypes(conn)
    bases = bases_of(data)
    return assign(npc_arch, bases, adjacency(conn), sticky(conn, bases, npc_arch))


@dataclass
class Spread:
    archetype: str
    counts: dict[str, int]   # Base Voice -> NPCs
    pairs: int               # same-Archetype Neighbour pairs
    shared: int              # ... of which share a Base Voice

    def text(self) -> str:
        c = sorted(self.counts.values())
        return (f"{self.archetype}: {plural(len(self.counts))} over {sum(c)} NPCs (per voice {c[0]}-{c[-1]});"
                f" {self.shared} of {self.pairs} same-Archetype Neighbour pairs share one")


def spread(conn: sqlite3.Connection, data: dict, assigned: dict[int, str] | None = None) -> list[Spread]:
    """Distribution of the Base Voices per Archetype: NPCs per Base Voice, and Neighbour pairs sharing one."""
    assigned = assignments(conn, data) if assigned is None else assigned
    npc_arch = voiced_archetypes(conn)
    bases = bases_of(data)
    counts = {aid: Counter({b: 0 for b in bs}) for aid, bs in bases.items()}
    for npc, b in assigned.items():
        counts[npc_arch[npc]][b] += 1
    pairs, shared = Counter(), Counter()
    for a, b in conn.execute("SELECT a, b FROM neighbours"):
        aid = npc_arch.get(a)
        if aid in bases and aid == npc_arch.get(b) and a in assigned and b in assigned:
            pairs[aid] += 1
            shared[aid] += assigned[a] == assigned[b]
    return [Spread(aid, dict(sorted(counts[aid].items())), pairs[aid], shared[aid]) for aid in sorted(bases)
            if sum(counts[aid].values())]

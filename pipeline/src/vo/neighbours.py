"""Neighbours (#13): two NPCs a player is likely to hear close together. Their NPC Voices must sound clearly different.

Two voiced NPCs (lines with an NPC, not the Narrator) are Neighbours if either holds:
- spawn: some spawn of each is on the same map within `radius` yards (3D distance; default 150);
- quest: they share a quest chain: one gives a quest the other ends (from the pipeline's lines: quest_detail is the
  giver, quest_progress/quest_complete the ender), or they give/end two quests linked in VMaNGOS quest_template
  (PrevQuestId, NextQuestId, NextQuestInChain; a negative PrevQuestId counts by its absolute value). Links are
  direct (quest -> next quest), not a whole chain's transitive closure: a long chain would otherwise pair NPCs
  continents apart.
No edge weights. Stored in `neighbours` (a < b), replaced on every refresh; stored similarities are kept for pairs
that survive.
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

DEFAULT_RADIUS = 150.0
QUEST_LINK_COLUMNS = ("PrevQuestId", "NextQuestId", "NextQuestInChain")


def voiced_npcs(conn: sqlite3.Connection) -> set[int]:
    return {r[0] for r in conn.execute("SELECT DISTINCT npc_id FROM lines WHERE npc_id IS NOT NULL"
                                       " AND COALESCE(tts_text, '') != ''")}


def spawn_pairs(spawns, radius: float = DEFAULT_RADIUS) -> dict[tuple[int, int], float]:
    """{(a, b): closest distance} for NPCs with spawns within radius. spawns: iterable of (npc, map, x, y, z).
    Grid-bucketed per map (cell = radius), so only adjacent cells are compared."""
    cells: dict[tuple, list[tuple[int, float, float, float]]] = defaultdict(list)
    for npc, m, x, y, z in spawns:
        if x is None or y is None:
            continue
        z = z or 0.0
        cells[(m, math.floor(x / radius), math.floor(y / radius))].append((npc, x, y, z))
    out: dict[tuple[int, int], float] = {}
    for (m, cx, cy), pts in cells.items():
        near = [p for dx in (-1, 0, 1) for dy in (-1, 0, 1) for p in cells.get((m, cx + dx, cy + dy), ())]
        for n1, x1, y1, z1 in pts:
            for n2, x2, y2, z2 in near:
                if n1 >= n2:
                    continue
                d = math.dist((x1, y1, z1), (x2, y2, z2))
                if d <= radius and d < out.get((n1, n2), math.inf):
                    out[(n1, n2)] = d
    return out


def quest_npcs(conn: sqlite3.Connection) -> dict[int, set[int]]:
    """{quest: NPCs that give or end it}, from the pipeline's quest lines."""
    out: dict[int, set[int]] = defaultdict(set)
    for q, npc in conn.execute("SELECT DISTINCT quest_id, npc_id FROM lines WHERE quest_id IS NOT NULL AND npc_id IS NOT"
                               " NULL AND type IN ('quest_detail', 'quest_progress', 'quest_complete')"):
        out[q].add(npc)
    return out


def quest_links(world: sqlite3.Connection | None) -> set[tuple[int, int]]:
    """(quest, linked quest) pairs from VMaNGOS quest_template (latest patch row)."""
    if world is None:
        return set()
    from vo.extract import latest

    links = set()
    for r in latest(world, "quest_template", "t.entry, " + ", ".join(f"t.{c}" for c in QUEST_LINK_COLUMNS)):
        q = r[0]
        for other in r[1:]:
            if other and abs(other) != q:
                links.add((q, abs(other)))
    return links


def quest_pairs(by_quest: dict[int, set[int]], links: set[tuple[int, int]]) -> set[tuple[int, int]]:
    pairs = set()
    for npcs in by_quest.values():
        pairs |= {(a, b) for a, b in combinations(sorted(npcs), 2)}
    for q1, q2 in links:
        for a in by_quest.get(q1, ()):
            for b in by_quest.get(q2, ()):
                if a != b:
                    pairs.add((min(a, b), max(a, b)))
    return pairs


@dataclass
class Stats:
    npcs: int
    pairs: int
    spawn: int
    quest: int
    both: int
    mean: float
    median: float
    max: int
    isolated: int
    same_archetype_mean: float

    def text(self) -> str:
        return (f"{self.pairs} Neighbour pairs over {self.npcs} voiced NPCs ({self.spawn} spawn only, {self.quest} quest"
                f" only, {self.both} both): {self.mean:.1f} Neighbours per NPC on average (median {self.median:g},"
                f" max {self.max}; {self.isolated} with none); {self.same_archetype_mean:.1f} of the same Archetype.")


def refresh(conn: sqlite3.Connection, world: sqlite3.Connection | None = None,
            radius: float = DEFAULT_RADIUS) -> Stats:
    """Recompute and store the Neighbours table; returns its stats."""
    voiced = voiced_npcs(conn)
    spawns = [r for r in conn.execute("SELECT npc_id, map, x, y, z FROM spawns") if r[0] in voiced]
    near = spawn_pairs(spawns, radius)
    by_quest = {q: n & voiced for q, n in quest_npcs(conn).items()}
    quest = quest_pairs(by_quest, quest_links(world))
    sims = {(r[0], r[1]): r[2] for r in conn.execute("SELECT a, b, sim FROM neighbours")}
    rows = []
    for pair in sorted(set(near) | quest):
        reason = "spawn+quest" if pair in near and pair in quest else "spawn" if pair in near else "quest"
        d = near.get(pair)
        rows.append((*pair, reason, None if d is None else round(d, 1), sims.get(pair)))
    with conn:
        conn.execute("DELETE FROM neighbours")
        conn.executemany("INSERT INTO neighbours (a, b, reason, distance, sim) VALUES (?, ?, ?, ?, ?)", rows)
    return stats(conn, voiced)


def stats(conn: sqlite3.Connection, voiced: set[int] | None = None) -> Stats:
    from vo import archetypes

    voiced = voiced if voiced is not None else voiced_npcs(conn)
    deg: dict[int, int] = defaultdict(int)
    same: dict[int, int] = defaultdict(int)
    aids = archetypes.npc_archetypes(conn)
    reasons: dict[str, int] = defaultdict(int)
    n = 0
    for a, b, reason in conn.execute("SELECT a, b, reason FROM neighbours"):
        n += 1
        reasons[reason] += 1
        deg[a] += 1
        deg[b] += 1
        if aids.get(a) is not None and aids.get(a) == aids.get(b):
            same[a] += 1
            same[b] += 1
    counts = sorted(deg.get(v, 0) for v in voiced)
    k = len(counts)
    median = (counts[k // 2] if k % 2 else (counts[k // 2 - 1] + counts[k // 2]) / 2) if k else 0
    return Stats(k, n, reasons["spawn"], reasons["quest"], reasons["spawn+quest"], (sum(counts) / k) if k else 0.0,
                 median, max(counts, default=0), sum(1 for c in counts if c == 0),
                 (sum(same.get(v, 0) for v in voiced) / k) if k else 0.0)

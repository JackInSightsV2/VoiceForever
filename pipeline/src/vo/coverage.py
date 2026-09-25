"""Coverage: every line's zone and Voice Pack, stored so the dashboard and the morning report need no world DB.

`refresh` (vo extract --all, vo package, vo run, when the world DB is there) writes two small tables:
  zones       zone id -> name, from area_template (the top-level areas: AreaTable zone ids, as spawns.zone).
  line_packs  line -> Voice Pack (vo.packs.assign) and zone: its NPC's main spawn zone (most spawns, then lowest id),
              else, for Narrator lines or spawnless NPCs, its quest's zone (ZoneOrSort > 0), else NULL.

`lines` reads them back per line with job status and ASR WER; a line not in line_packs (Capture ingested since, or
no world DB) falls back to vo.packs.assign on the pipeline DB alone and its NPC's main zone.
"""
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass

from vo import packs

UNKNOWN_ZONE = "Unknown zone"


def npc_zones(conn: sqlite3.Connection) -> dict[int, int]:
    """NPC -> its main zone: the zone with most spawns, ties to the lowest id."""
    counts: dict[int, Counter] = defaultdict(Counter)
    for npc_id, zone in conn.execute("SELECT npc_id, zone FROM spawns WHERE zone IS NOT NULL"):
        counts[npc_id][zone] += 1
    return {n: min(c, key=lambda z: (-c[z], z)) for n, c in counts.items()}


def line_zones(conn: sqlite3.Connection, quest_zones: dict[int, int] | None = None) -> dict[int, int | None]:
    quest_zones = quest_zones or {}
    main = npc_zones(conn)
    out = {}
    for line_id, npc_id, quest_id in conn.execute("SELECT id, npc_id, quest_id FROM lines"):
        zone = main.get(npc_id) if npc_id is not None else None
        if zone is None and quest_id is not None and quest_zones.get(quest_id, 0) > 0:
            zone = quest_zones[quest_id]
        out[line_id] = zone
    return out


def zone_names(world: sqlite3.Connection) -> list[tuple[int, str, int]]:
    return [tuple(r) for r in world.execute(
        "SELECT entry, name, map_id FROM area_template WHERE zone_id = 0 AND name != '' ORDER BY entry")]


def refresh(conn: sqlite3.Connection, world: sqlite3.Connection | None = None,
            source: packs.Source | None = None) -> int:
    """Rewrite zones (when the world DB is given) and line_packs; returns the lines assigned."""
    if world is not None and source is None:
        source = packs.load_source(world)
    assigned = packs.assign(conn, source)
    zones = line_zones(conn, {q: v.zone for q, v in (source.quests if source else {}).items()})
    with conn:
        if world is not None:
            conn.execute("DELETE FROM zones")
            conn.executemany("INSERT INTO zones (id, name, map) VALUES (?, ?, ?)", zone_names(world))
        conn.execute("DELETE FROM line_packs")
        conn.executemany("INSERT INTO line_packs (line_id, pack, zone) VALUES (?, ?, ?)",
                         [(i, p, zones.get(i)) for i, p in assigned.items()])
    return len(assigned)


@dataclass
class Line:
    line_id: int
    npc_id: int | None
    zone: int | None
    pack: str
    status: str  # a job status, or 'unqueued' (no tts_text yet)
    attempts: int
    wer: float | None


def lines(conn: sqlite3.Connection) -> list[Line]:
    rows = conn.execute(
        "SELECT l.id, l.npc_id, lp.pack, lp.zone, COALESCE(j.status, 'unqueued'), COALESCE(j.attempts, 0), j.wer"
        " FROM lines l LEFT JOIN line_packs lp ON lp.line_id = l.id LEFT JOIN jobs j ON j.line_id = l.id"
        " ORDER BY l.id").fetchall()
    fallback = packs.assign(conn) if any(r[2] is None for r in rows) else {}
    main = npc_zones(conn) if fallback else {}
    return [Line(r[0], r[1], r[3] if r[2] is not None else main.get(r[1]), r[2] or fallback[r[0]], r[4], r[5], r[6])
            for r in rows]


def names(conn: sqlite3.Connection) -> dict[int, str]:
    return dict(conn.execute("SELECT id, name FROM zones").fetchall())


def zone_name(zones: dict[int, str], zone: int | None) -> str:
    if zone is None:
        return UNKNOWN_ZONE
    return zones.get(zone, f"Zone {zone}")


@dataclass
class Row:
    key: str
    total: int = 0
    done: int = 0
    failed: int = 0       # a failed take waiting for its retry
    quarantined: int = 0
    skipped: int = 0
    checked: int = 0      # lines spot-checked (rated or flagged)

    @property
    def pct(self) -> float:
        return 100.0 * self.done / self.total if self.total else 0.0


def table(conn: sqlite3.Connection, by: str) -> list[Row]:
    """Coverage rows per 'zone' (keyed by zone name) or 'pack', most lines first."""
    zones = names(conn)
    checked = {r[0] for r in conn.execute("SELECT DISTINCT line_id FROM ratings WHERE line_id IS NOT NULL")}
    out: dict[str, Row] = {}
    for line in lines(conn):
        key = zone_name(zones, line.zone) if by == "zone" else line.pack
        row = out.setdefault(key, Row(key))
        row.total += 1
        if line.status in ("done", "quarantined", "skipped"):
            setattr(row, line.status, getattr(row, line.status) + 1)
        elif line.status == "pending" and line.attempts > 0:
            row.failed += 1
        row.checked += line.line_id in checked
    return sorted(out.values(), key=lambda r: (-r.total, r.key))

"""Each NPC's own in-game voice set, and the game voice speaker (ADR-0007) it belongs to.

The client says which voice set an NPC speaks with when clicked: its display's `CreatureDisplayInfo.NPCSoundID` points
at an `NPCSounds` row (greeting, farewell, pissed, ... sound kits), whose `SoundKitEntry` rows are the clips'
FileDataIDs. The community listfile names their folder (`sound/creature/orcmalestandardnpc/...`), which is a game
voice kit (vo.gamevoice); the Archetype's game voice plan says which speaker (Candidate `<archetype>/gv-<key>`) that
kit went into. This is what Wowhead's NPC "Sounds" tab shows, read from the same Forever DB2 tables offline.

`npc_game_voice` holds, per NPC: the display and NPCSoundID used, the kit's Archetype and folder, and the speaker
Candidate (NULL when the kit gave no Candidate). vo.basevoices prefers an NPC's own speaker, or an approved variation
of it, as its Base Voice.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from vo import gamevoice

TABLES = ("NPCSounds", "SoundKitEntry")  # besides CreatureDisplayInfo (vo.display.TABLES)


@dataclass(frozen=True)
class Kit:
    archetype: str
    folder: str


def kit_of(path: str) -> Kit | None:
    """The game voice kit (Archetype, folder) a clip path belongs to: an NPC voice set, or a listed creature folder."""
    p = path.lower()
    got = gamevoice.npc_kit(p)
    if got is not None:
        return Kit(got[0], got[1])
    parts = p.split("/")
    if len(parts) == 4 and parts[:2] == ["sound", "creature"] and parts[2] in gamevoice.CREATURE_KITS:
        return Kit(gamevoice.CREATURE_KITS[parts[2]], parts[2])
    return None


def sound_kits(npc_sounds: dict[int, list[int]], entries: dict[int, list[int]], paths: dict[int, str]
               ) -> dict[int, Kit]:
    """{NPCSoundID: Kit}: the kit most of its clips are in (ties: folder name). NPCSounds rows whose clips are no voice
    kit (animal noises, ambience) are left out."""
    out = {}
    for sid, kits in npc_sounds.items():
        seen: Counter = Counter()
        for sk in kits:
            for fdid in entries.get(sk, ()):
                kit = kit_of(paths[fdid]) if fdid in paths else None
                if kit is not None:
                    seen[kit] += 1
        if seen:
            out[sid] = min(seen, key=lambda k: (-seen[k], k.folder))
    return out


def _rows(db2_dir: Path, table: str) -> Iterable[dict]:
    with open(db2_dir / f"{table}.csv", encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


@dataclass
class Tables:
    """What the mapping needs from the DB2 tables: display -> NPCSoundID -> Kit, and each voice clip's Kit."""
    display_sound: dict[int, int]
    kits: dict[int, Kit]
    fdid_kits: dict[int, Kit] = field(default_factory=dict)

    def kit(self, display_id: int) -> tuple[int, Kit] | None:
        sid = self.display_sound.get(display_id, 0)
        return (sid, self.kits[sid]) if sid in self.kits else None

    def sound_kit(self, fdids: Iterable[int]) -> Kit | None:
        """The Kit most of these clips (FileDataIDs, e.g. from a Wowhead NPC's Sounds tab) are in."""
        seen = Counter(k for f in fdids if (k := self.fdid_kits.get(f)) is not None)
        return min(seen, key=lambda k: (-seen[k], k.folder)) if seen else None


def load(db2_dir: Path, listfile: Path) -> Tables:
    display_sound = {int(r["ID"]): int(r["NPCSoundID"] or 0) for r in _rows(db2_dir, "CreatureDisplayInfo")}
    npc_sounds = {int(r["ID"]): [int(r[f"SoundID_{i}"]) for i in range(4) if int(r[f"SoundID_{i}"] or 0)]
                  for r in _rows(db2_dir, "NPCSounds")}
    wanted = {sk for ks in npc_sounds.values() for sk in ks}
    entries: dict[int, list[int]] = defaultdict(list)
    for r in _rows(db2_dir, "SoundKitEntry"):
        if int(r["SoundKitID"]) in wanted:
            entries[int(r["SoundKitID"])].append(int(r["FileDataID"]))
    fdids = {str(f) for fs in entries.values() for f in fs}
    paths = {}
    with open(listfile, encoding="utf-8", errors="replace") as f:
        for line in f:
            fdid, _, p = line.strip().partition(";")
            if fdid in fdids:
                paths[int(fdid)] = p
    fdid_kits = {f: k for f, p in paths.items() if (k := kit_of(p)) is not None}
    return Tables(display_sound, sound_kits(npc_sounds, entries, paths), fdid_kits)


def speakers(anchors_root: Path) -> dict[str, str]:
    """{kit folder: speaker Candidate id} from the game voice plans (data/gamevoice/anchors/<archetype>/plan.json)."""
    out = {}
    for plan in sorted(anchors_root.glob("*/plan.json")):
        for a in json.loads(plan.read_text()).get("anchors", []):
            for folder in a["folders"]:
                out[folder] = f"{a['archetype']}/gv-{a['key']}"
    return out


@dataclass(frozen=True)
class NpcVoice:
    npc_id: int
    display_id: int
    npc_sound_id: int
    archetype: str
    kit: str
    speaker: str | None
    source: str  # db2 (the NPC's Source Data displays) | wowhead (the display on its Wowhead page)


def pick(slots: list[tuple[int, int]], tables: Tables) -> tuple[int, int, Kit] | None:
    """(display, NPCSoundID, Kit) for an NPC's display slots [(display, probability)]: the most probable display that
    has a voice kit (first slot on ties; all equal when no probability is set)."""
    slots = [(d, p) for d, p in slots if d]
    weighted = [(d, p) for d, p in slots if p > 0] or [(d, 1) for d, _ in slots]
    best = None
    for d, p in weighted:
        got = tables.kit(d)
        if got is not None and (best is None or p > best[0]):
            best = (p, d, *got)
    return None if best is None else best[1:]


def map_npcs(npc_slots: dict[int, list[tuple[int, int]]], tables: Tables, speaker_of: dict[str, str],
             source: str = "db2") -> list[NpcVoice]:
    out = []
    for npc, slots in sorted(npc_slots.items()):
        got = pick(slots, tables)
        if got is not None:
            d, sid, kit = got
            out.append(NpcVoice(npc, d, sid, kit.archetype, kit.folder, speaker_of.get(kit.folder), source))
    return out


def world_slots(world: sqlite3.Connection, npcs: Iterable[int]) -> dict[int, list[tuple[int, int]]]:
    from vo.extract import latest
    wanted = set(npcs)
    return {t["entry"]: [(t[f"display_id{i}"], t[f"display_probability{i}"]) for i in range(1, 5)]
            for t in latest(world, "creature_template",
                            "t.entry, t.display_id1, t.display_id2, t.display_id3, t.display_id4, t.display_probability1,"
                            " t.display_probability2, t.display_probability3, t.display_probability4")
            if t["entry"] in wanted}


def wowhead_voices(conn: sqlite3.Connection, tables: Tables, speaker_of: dict[str, str]) -> list[NpcVoice]:
    """In-game voice sets of NPCs known from their Wowhead page (vo.wowhead): its display's, else the kit of the clips
    on its Sounds tab."""
    out = []
    for n, d, sounds in conn.execute("SELECT id, display_id, sounds FROM wowhead_npcs ORDER BY id"):
        got = tables.kit(d) if d else None
        if got is None:
            kit = tables.sound_kit(f for s in json.loads(sounds or "[]") for f in s.get("files", []))
            got = (0, kit) if kit is not None else None
        if got is not None:
            out.append(NpcVoice(n, d or 0, got[0], got[1].archetype, got[1].folder, speaker_of.get(got[1].folder),
                                "wowhead"))
    return out


def refresh(conn: sqlite3.Connection, world: sqlite3.Connection | None, tables: Tables, speaker_of: dict[str, str]
            ) -> Counter:
    """Rewrite npc_game_voice for every NPC in the pipeline DB. Returns counts (npcs, mapped, with a speaker)."""
    npcs = [r[0] for r in conn.execute("SELECT id FROM npcs")]
    slots = world_slots(world, npcs) if world is not None else {}
    rows = map_npcs(slots, tables, speaker_of)
    have, known = {r.npc_id for r in rows}, set(npcs)
    rows += [r for r in wowhead_voices(conn, tables, speaker_of) if r.npc_id not in have and r.npc_id in known]
    with conn:
        conn.execute("DELETE FROM npc_game_voice")
        conn.executemany("INSERT INTO npc_game_voice VALUES (?, ?, ?, ?, ?, ?, ?)",
                         [(r.npc_id, r.display_id, r.npc_sound_id, r.archetype, r.kit, r.speaker, r.source)
                          for r in rows])
    return Counter(npcs=len(npcs), mapped=len(rows), speaker=sum(r.speaker is not None for r in rows))


def preferred(conn: sqlite3.Connection) -> dict[int, list[str]]:
    """{npc: Candidate ids it prefers as Base Voice}: its own game voice speaker, then every variation of it
    (`<speaker>~v<k>`, and variations of those). vo.basevoices keeps the ones that are Base Voices of its Archetype."""
    own = dict(conn.execute("SELECT npc_id, speaker_candidate_id FROM npc_game_voice WHERE speaker_candidate_id IS NOT NULL"))
    if not own:
        return {}
    ids = [r[0] for r in conn.execute("SELECT id FROM candidates WHERE id LIKE '%~%'")]
    variations: dict[str, list[str]] = defaultdict(list)
    for cid in ids:
        variations[cid.split("~", 1)[0]].append(cid)
    return {npc: [s, *sorted(variations.get(s, []))] for npc, s in own.items()}


def summary_text(c: Counter) -> str:
    return (f"npc_game_voice: {c['mapped']} of {c['npcs']} NPCs have an in-game voice kit, {c['speaker']} of them a"
            f" game voice speaker Candidate")

"""Packaging: one Voice Pack addon per faction and level band (vo.packs), each holding its audio and its slice of
the lookup index. The Core Addon merges whichever packs are installed."""
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path

from vo import drift, gossip, packs

INTERFACE = "16001"  # Forever 1.60.1
PARTS = {"quest_detail": "detail", "quest_progress": "progress", "quest_complete": "complete"}


def build_index(rows) -> dict[int, dict[str, dict[str, dict]]]:
    """rows of (quest_id, type, player_gender, file, text_hash[, narrator]) -> {questId: {part: {gender: entry}}},
    entry {file, hash[, narrator: True]}. A line with no $G is filed under both genders, so the addon lookup is
    always by player gender. Narrator lines (no NPC) are flagged for the addon's Narrator toggle."""
    index: dict = {}
    for quest_id, type_, gender, file, hash_, *narrator in rows:
        genders = index.setdefault(quest_id, {}).setdefault(PARTS[type_], {})
        entry = {"file": file, "hash": hash_}
        if narrator and narrator[0]:
            entry["narrator"] = True
        for g in [gender] if gender else ["m", "f"]:
            genders.setdefault(g, entry)
    return index


def _lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _lua_entry(e: dict) -> str:
    narrator = ", narrator = true" if e.get("narrator") else ""
    return f"{{ file = {_lua_str(e['file'])}, hash = {_lua_str(e['hash'])}{narrator} }}"


def lua_index(pack: str, index: dict, gossip_index: dict | None = None) -> str:
    out = [f"VoiceForever.RegisterPack({_lua_str(pack)}, {{", "  quests = {"]
    for quest_id in sorted(index):
        parts = ", ".join(
            f"{part} = {{ " + ", ".join(f"{g} = {_lua_entry(e)}" for g, e in sorted(genders.items())) + " }"
            for part, genders in sorted(index[quest_id].items()))
        out.append(f"    [{quest_id}] = {{ {parts} }},")
    out.append("  },")
    if gossip_index:
        out += _lua_gossip(gossip_index)
    out += ["})", ""]
    return "\n".join(out)


def _lua_gossip(gossip_index: dict) -> list[str]:
    """gossip = { [npcId] = { { pattern, file, gender? }, ... } }, each NPC's patterns most specific first."""
    out = ["  gossip = {"]
    for npc_id in sorted(gossip_index):
        out.append(f"    [{npc_id}] = {{")
        for e in gossip_index[npc_id]:
            gender = f", gender = {_lua_str(e['gender'])}" if e.get("gender") else ""
            out.append(f"      {{ pattern = {_lua_str(e['pattern'])}, file = {_lua_str(e['file'])}{gender} }},")
        out.append("    },")
    out.append("  },")
    return out


def toc(pack: str) -> str:
    return "\n".join([
        f"## Interface: {INTERFACE}",
        f"## Title: VoiceForever - {pack.removeprefix(packs.PREFIX).replace('_', ' ')}",
        "## Notes: Voice Pack for VoiceForever",
        "## Dependencies: VoiceForever",
        "index.lua",
        "",
    ])


def report(conn: sqlite3.Connection, source: packs.Source | None = None) -> dict[str, dict]:
    """Per Voice Pack with at least one line: lines assigned, lines voiced and audio megabytes."""
    assigned = packs.assign(conn, source)
    voiced = {r[0]: r[1] for r in conn.execute("SELECT line_id, path FROM audio WHERE status = 'done'")}
    out = {p: {"lines": 0, "voiced": 0, "mb": 0.0} for p in sorted(set(assigned.values()))}
    for line_id, pack in assigned.items():
        out[pack]["lines"] += 1
        if line_id in voiced:
            out[pack]["voiced"] += 1
            path = Path(voiced[line_id])
            out[pack]["mb"] += path.stat().st_size / 1e6 if path.exists() else 0.0
    return out


def package(conn: sqlite3.Connection, packs_dir: Path, source: packs.Source | None = None,
            only: str | None = None) -> dict[str, Path]:
    """Write packs_dir/<pack>/ for every Voice Pack with voiced lines (or only the pack `only`), each rebuilt from
    scratch; building them all also removes pack folders left with no audio. Returns {pack: folder}."""
    assigned = packs.assign(conn, source)
    rows = defaultdict(lambda: ([], []))  # pack -> (Quest Text rows, Gossip rows)
    for r in conn.execute(
            "SELECT l.id, l.npc_id, l.quest_id, l.type, l.player_gender, l.raw_text, l.text_hash, l.match_pattern,"
            " a.path FROM lines l JOIN audio a ON a.line_id = l.id AND a.status = 'done' ORDER BY l.id"):
        pack = assigned[r["id"]]
        if only and pack != only:
            continue
        if r["type"] in PARTS:
            rows[pack][0].append(r)
        elif r["type"] in gossip.TYPES and r["npc_id"] is not None:
            rows[pack][1].append(r)
    stale = [only] if only else [p.name for p in packs_dir.glob(packs.PREFIX + "*") if p.is_dir()]
    for name in stale:
        shutil.rmtree(packs_dir / name, ignore_errors=True)
    return {pack: _write_pack(packs_dir / pack, pack, *rows[pack]) for pack in sorted(rows)}


def _copy(out: Path, pack: str, r, folder: str) -> str:
    """Copy one line's audio into the pack; returns its in-game path."""
    rel = Path("audio") / folder / f"{r['id']}.ogg"
    (out / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(r["path"], out / rel)
    return "\\".join(["Interface", "AddOns", pack, *rel.parts])


def _write_pack(out: Path, pack: str, quest_rows, gossip_rows) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for r in quest_rows:
        file = _copy(out, pack, r, str(r["npc_id"] or "narrator"))
        hash_ = r["text_hash"] or drift.text_hash(r["raw_text"], r["player_gender"])
        index.append((r["quest_id"], r["type"], r["player_gender"], file, hash_, r["npc_id"] is None))
    patterns = []
    for r in gossip_rows:
        pattern = r["match_pattern"] or gossip.match_pattern(r["raw_text"], r["player_gender"])
        patterns.append((r["npc_id"], r["player_gender"], pattern, _copy(out, pack, r, str(r["npc_id"]))))
    (out / "index.lua").write_text(lua_index(pack, build_index(index), gossip.build_index(patterns)))
    (out / f"{pack}.toc").write_text(toc(pack))
    return out

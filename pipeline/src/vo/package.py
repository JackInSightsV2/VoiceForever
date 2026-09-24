"""Packaging: a Voice Pack addon holding audio plus its slice of the lookup index."""
import shutil
import sqlite3
from pathlib import Path

from vo import drift, gossip

INTERFACE = "16001"  # Forever 1.60.1
DEFAULT_PACK = "VoiceForever_Alliance_1-10"
PARTS = {"quest_detail": "detail", "quest_progress": "progress", "quest_complete": "complete"}


def build_index(rows) -> dict[int, dict[str, dict[str, dict[str, str]]]]:
    """rows of (quest_id, type, player_gender, file, text_hash) -> {questId: {part: {gender: {file, hash}}}}.
    A line with no $G is filed under both genders, so the addon lookup is always by player gender."""
    index: dict = {}
    for quest_id, type_, gender, file, hash_ in rows:
        genders = index.setdefault(quest_id, {}).setdefault(PARTS[type_], {})
        for g in [gender] if gender else ["m", "f"]:
            genders.setdefault(g, {"file": file, "hash": hash_})
    return index


def _lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def lua_index(pack: str, index: dict, gossip_index: dict | None = None) -> str:
    out = [f"VoiceForever.RegisterPack({_lua_str(pack)}, {{", "  quests = {"]
    for quest_id in sorted(index):
        parts = ", ".join(
            f"{part} = {{ " + ", ".join(
                f"{g} = {{ file = {_lua_str(e['file'])}, hash = {_lua_str(e['hash'])} }}"
                for g, e in sorted(genders.items())) + " }"
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
        f"## Title: VoiceForever - {pack.removeprefix('VoiceForever_').replace('_', ' ')}",
        "## Notes: Voice Pack for VoiceForever",
        "## Dependencies: VoiceForever",
        "index.lua",
        "",
    ])


def package(conn: sqlite3.Connection, packs_dir: Path, pack: str = DEFAULT_PACK) -> Path:
    """Write packs_dir/<pack>/ with every voiced quest line; rebuilt from scratch each time."""
    out = packs_dir / pack
    shutil.rmtree(out, ignore_errors=True)
    rows = []
    for r in conn.execute(
            "SELECT l.id, l.npc_id, l.quest_id, l.type, l.player_gender, l.raw_text, l.text_hash, a.path FROM lines l"
            " JOIN audio a ON a.line_id = l.id AND a.status = 'done'"
            " WHERE l.type IN ('quest_detail', 'quest_progress', 'quest_complete') ORDER BY l.id"):
        rel = Path("audio") / str(r["npc_id"] or "narrator") / f"{r['id']}.ogg"
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(r["path"], out / rel)
        file = "\\".join(["Interface", "AddOns", pack, *rel.parts])
        hash_ = r["text_hash"] or drift.text_hash(r["raw_text"], r["player_gender"])
        rows.append((r["quest_id"], r["type"], r["player_gender"], file, hash_))
    gossip_index = gossip.build_index(_package_gossip(conn, out, pack))
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.lua").write_text(lua_index(pack, build_index(rows), gossip_index))
    (out / f"{pack}.toc").write_text(toc(pack))
    return out


def _package_gossip(conn: sqlite3.Connection, out: Path, pack: str) -> list[tuple]:
    """Copy voiced Gossip audio into the pack; rows of (npc_id, player_gender, pattern, file) for gossip.build_index."""
    rows = []
    for r in conn.execute(
            "SELECT l.id, l.npc_id, l.player_gender, l.raw_text, l.match_pattern, a.path FROM lines l"
            " JOIN audio a ON a.line_id = l.id AND a.status = 'done'"
            f" WHERE l.type IN {gossip.TYPES} AND l.npc_id IS NOT NULL ORDER BY l.id"):
        rel = Path("audio") / str(r["npc_id"]) / f"{r['id']}.ogg"
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(r["path"], out / rel)
        pattern = r["match_pattern"] or gossip.match_pattern(r["raw_text"], r["player_gender"])
        rows.append((r["npc_id"], r["player_gender"], pattern, "\\".join(["Interface", "AddOns", pack, *rel.parts])))
    return rows

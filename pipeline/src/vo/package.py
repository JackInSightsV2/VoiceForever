"""Packaging: a Voice Pack addon holding audio plus its slice of the lookup index."""
import shutil
import sqlite3
from pathlib import Path

INTERFACE = "16001"  # Forever 1.60.1
DEFAULT_PACK = "VoiceForever_Alliance_1-10"
PARTS = {"quest_detail": "detail", "quest_progress": "progress", "quest_complete": "complete"}


def build_index(rows) -> dict[int, dict[str, dict[str, str]]]:
    """rows of (quest_id, type, player_gender, file) -> {questId: {part: {gender: file}}}.
    A line with no $G is filed under both genders, so the addon lookup is always by player gender."""
    index: dict = {}
    for quest_id, type_, gender, file in rows:
        genders = index.setdefault(quest_id, {}).setdefault(PARTS[type_], {})
        for g in [gender] if gender else ["m", "f"]:
            genders.setdefault(g, file)
    return index


def _lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def lua_index(pack: str, index: dict) -> str:
    out = [f"VoiceForever.RegisterPack({_lua_str(pack)}, {{", "  quests = {"]
    for quest_id in sorted(index):
        parts = ", ".join(
            f"{part} = {{ " + ", ".join(f"{g} = {_lua_str(f)}" for g, f in sorted(genders.items())) + " }"
            for part, genders in sorted(index[quest_id].items()))
        out.append(f"    [{quest_id}] = {{ {parts} }},")
    out += ["  },", "})", ""]
    return "\n".join(out)


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
            "SELECT l.id, l.npc_id, l.quest_id, l.type, l.player_gender, a.path FROM lines l"
            " JOIN audio a ON a.line_id = l.id AND a.status = 'done'"
            " WHERE l.type IN ('quest_detail', 'quest_progress', 'quest_complete') ORDER BY l.id"):
        rel = Path("audio") / str(r["npc_id"] or "narrator") / f"{r['id']}.ogg"
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(r["path"], out / rel)
        rows.append((r["quest_id"], r["type"], r["player_gender"], "\\".join(["Interface", "AddOns", pack, *rel.parts])))
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.lua").write_text(lua_index(pack, build_index(rows)))
    (out / f"{pack}.toc").write_text(toc(pack))
    return out

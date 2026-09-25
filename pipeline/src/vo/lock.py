"""approved_voices.json: the Approval Gate's output, and `vo run`'s precondition.

Written by `vo prepare` once every Archetype has an approved anchor (removed again if one is re-opened); read-only on
disk. Maps each Archetype to its anchor (audio path, transcript, description, seed, continuation mode, effect chain)
and fixes the Narrator. `vo run` refuses to start without it, and speaks every NPC line as a VoxCPM2 continuation
of the NPC's own anchor (vo.voices), or of its Archetype anchor while it has none.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from vo import archetypes

ENV = "VO_APPROVED_VOICES"  # lets `vo run`'s spawned workers find the lock the main process checked
FILENAME = "approved_voices.json"
BACKEND = "voxcpm"


class LockError(RuntimeError):
    pass


def default_path() -> Path:
    return Path(__file__).resolve().parents[3] / "build" / FILENAME


def path() -> Path:
    return Path(os.environ.get(ENV) or default_path())


def voice_id(aid: str, entry: dict) -> str:
    """The job voice id for an Archetype's anchor, e.g. `voxcpm:orc_f@orc_f/g0s3`."""
    return f"{BACKEND}:{aid}@{entry['candidate']}"


HOW = ("Render Candidates with `vo prepare`, approve one anchor per Archetype on the dashboard's Approval page "
       "(`vo dashboard`, /approval), then run `vo prepare` again to apply the approvals and write the lock.")


def load(p: Path) -> dict:
    if not p.exists():
        raise LockError(f"{p} not found: the Approval Gate isn't complete. {HOW}")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise LockError(f"{p} is not valid JSON ({e}). {HOW}") from e
    if not data.get("locked") or not isinstance(data.get("archetypes"), dict):
        raise LockError(f"{p} is not a locked approved_voices.json. {HOW}")
    return data


def write(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".part")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.chmod(tmp, 0o444)
    if p.exists():
        os.chmod(p, 0o644)
    os.replace(tmp, p)


def remove(p: Path) -> bool:
    if not p.exists():
        return False
    os.chmod(p, 0o644)
    p.unlink()
    return True


def require(conn: sqlite3.Connection, p: Path) -> dict:
    """The lock, checked against the DB: every Archetype an NPC with lines needs is approved and its anchor exists.
    Raises LockError with what to do otherwise."""
    data = load(p)
    needed = {a.id for a in archetypes.derive(conn) if a.lines}
    missing = sorted(needed - set(data["archetypes"]))
    if missing:
        raise LockError(f"{p} has no approved anchor for: {', '.join(missing)}. {HOW}")
    gone = sorted(aid for aid, e in data["archetypes"].items() if aid in needed and not Path(e["anchor"]).exists())
    if gone:
        raise LockError(f"anchor audio missing for: {', '.join(gone)}. {HOW}")
    return data


def npc_voice_ids(conn: sqlite3.Connection, data: dict) -> dict[int, str]:
    """{npc id: voice id}: its Archetype's anchor, or the Narrator for races mapped to it. NPCs whose Archetype
    isn't in the lock are left out (they fall back to `vo run`'s default voice).

    An NPC's own anchor (#13, `vo voices`) takes precedence through `voices.voice_id`, which `vo run` prefers."""
    narrator = data.get("narrator", {}).get("voice_id")
    out = {}
    for npc, aid in archetypes.npc_archetypes(conn).items():
        if aid == archetypes.NARRATOR:
            if narrator:
                out[npc] = narrator
        elif aid in data["archetypes"]:
            out[npc] = voice_id(aid, data["archetypes"][aid])
    return out

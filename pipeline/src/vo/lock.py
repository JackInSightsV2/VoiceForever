"""approved_voices.json: the Approval Gate's output, and `vo run`'s precondition.

Written by `vo prepare` once every Archetype has at least one approved anchor (removed again if one is re-opened);
read-only on disk. Maps each Archetype to its Base Voices (ADR-0006): `anchors`, one entry per approved Candidate
(audio path, transcript, description, seed, continuation mode, effect chain, voice id), and fixes the Narrator. An
anchor's mode is the Archetype's unless its Candidate has its own (a game voice, ADR-0007). With an
anchor chain (vo.effects), an entry's `anchor` is the processed clip (`raw_anchor` the design it came from,
`anchor_chain` the chain): lines continue from the processed clip and are not processed again. `vo run` refuses to
start without it, and speaks every NPC line as a VoxCPM2 continuation of the NPC's own anchor (vo.voices), or while it
has none, of its Base Voice (vo.basevoices).

A lock written before several Base Voices has one entry per Archetype (the anchor's fields at its top level);
`anchors()` reads both.
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
    """The job voice id for one of an Archetype's anchors, e.g. `voxcpm:orc_f@orc_f/g0s3`."""
    return f"{BACKEND}:{aid}@{entry['candidate']}"


def anchors(entry: dict | None) -> list[dict]:
    """An Archetype's lock entry as its list of anchor entries (its Base Voices), in either lock format."""
    if not entry:
        return []
    if "anchors" in entry:
        return list(entry["anchors"])
    return [entry] if "candidate" in entry else []


def find(data: dict, aid: str, candidate: str) -> dict | None:
    """The anchor entry of that Archetype's Base Voice in the lock, or None."""
    return next((e for e in anchors(data["archetypes"].get(aid)) if e["candidate"] == candidate), None)


HOW = ("Render Candidates with `vo prepare`, approve at least one anchor per Archetype (up to 8, each a Base Voice) on "
       "the dashboard's Approval page (`vo dashboard`, /approval), then run `vo prepare` again to apply the approvals "
       "and write the lock.")


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
    """The lock, checked against the DB: every Archetype an NPC with lines needs has an approved anchor, and every
    anchor's audio exists. Raises LockError with what to do otherwise."""
    data = load(p)
    needed = {a.id for a in archetypes.derive(conn) if a.lines}
    missing = sorted(aid for aid in needed if not anchors(data["archetypes"].get(aid)))
    if missing:
        raise LockError(f"{p} has no approved anchor for: {', '.join(missing)}. {HOW}")
    gone = sorted({aid for aid, e in data["archetypes"].items() if aid in needed
                   for a in anchors(e) if not Path(a["anchor"]).exists()})
    if gone:
        raise LockError(f"anchor audio missing for: {', '.join(gone)}. {HOW}")
    return data


def entry(conn: sqlite3.Connection, aid: str) -> dict | None:
    """The Archetype's lock entry from the DB (vo prepare's review state): its approved anchors (Base Voices) with
    their audio on disk, in candidate id order; None if it has none."""
    a = conn.execute("SELECT label, races, mode, effect_chain FROM archetypes WHERE id = ?", (aid,)).fetchone()
    if a is None:
        return None
    rows = conn.execute("SELECT id, path, anchor_text, description, seed, anchor_chain, raw_path, mode FROM candidates"
                        " WHERE archetype = ? AND status = 'approved' ORDER BY id", (aid,)).fetchall()
    out = []
    for r in rows:
        if not r["path"] or not Path(r["path"]).exists():
            continue
        e = {"candidate": r["id"], "anchor": r["path"], "transcript": r["anchor_text"], "description": r["description"],
             "seed": r["seed"], "mode": r["mode"] or a["mode"] or "cont", "effect_chain": a["effect_chain"]}
        if r["anchor_chain"]:  # `anchor` is the processed clip; the unprocessed design is kept for reference
            e.update(anchor_chain=r["anchor_chain"], raw_anchor=r["raw_path"])
        e["voice_id"] = voice_id(aid, e)
        out.append(e)
    if not out:
        return None
    return {"label": a["label"], "races": json.loads(a["races"] or "{}"), "mode": a["mode"] or "cont",
            "effect_chain": a["effect_chain"], "anchors": out}


def envelope(entries: dict) -> dict:
    from vo import tts, voxcpm
    return {"locked": True, "model": voxcpm.REPO, "settings": voxcpm.SETTINGS,
            "narrator": {"voice_id": tts.NARRATOR_VOICE_ID}, "archetypes": entries}


def from_db(conn: sqlite3.Connection) -> dict:
    """The approved anchors as they stand in the DB, whether or not every Archetype is approved yet: the lock's
    content for the Archetypes approved so far, with `partial` set. What `vo run --partial` and `vo voices --partial`
    speak with before approved_voices.json exists (incremental approval)."""
    ids = [r[0] for r in conn.execute("SELECT DISTINCT c.archetype FROM candidates c JOIN archetypes a"
                                      " ON a.id = c.archetype WHERE a.kind != 'narrator' AND c.status = 'approved'"
                                      " ORDER BY c.archetype")]
    entries = {aid: e for aid in ids if (e := entry(conn, aid)) is not None}
    return {**envelope(entries), "partial": True}


def base_voices(data: dict) -> int:
    """How many Base Voices (approved anchors) the lock holds, over all Archetypes."""
    return sum(len(anchors(e)) for e in data["archetypes"].values())


def npc_voice_ids(conn: sqlite3.Connection, data: dict) -> dict[int, str]:
    """{npc id: voice id}: its Base Voice (one of its Archetype's anchors, as vo.basevoices assigns it), or the
    Narrator for races mapped to it. NPCs whose Archetype isn't in the lock are left out (they fall back to `vo run`'s
    default voice).

    An NPC's own anchor (#13, `vo voices`) takes precedence through `voices.voice_id`, which `vo run` prefers."""
    from vo import basevoices

    narrator = data.get("narrator", {}).get("voice_id")
    base = basevoices.assignments(conn, data)
    out = {}
    for npc, aid in archetypes.npc_archetypes(conn).items():
        if aid == archetypes.NARRATOR:
            if narrator:
                out[npc] = narrator
        elif npc in base:
            out[npc] = voice_id(aid, find(data, aid, base[npc]))
    return out

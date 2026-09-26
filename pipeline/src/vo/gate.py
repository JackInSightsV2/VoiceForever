"""The Approval Gate as `vo run` opens it: the approved voices and the Lexicon to speak with.

Full (the default): both locks, approved_voices.json and lexicon.json, must be written (vo prepare writes them once
every Archetype and every top Lexicon name is approved); `vo run` refuses to start otherwise.

Partial (`vo run --partial`, incremental approval): the Archetype anchors (Base Voices) approved so far, read from the
DB (vo prepare's review state), and the Lexicon spellings as they stand in the DB (reviewed where reviewed, drafts
otherwise). Lines of NPCs whose Archetype isn't approved yet get no job: they wait, neither failed nor quarantined.
The approved anchors are written to approved_voices.partial.json beside the lock, which `vo run`'s spawned workers
load. A later approval queues its Archetype's lines; a corrected spelling changes tts_text and requeues the lines
that say it (the jobs' tts_hash).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from vo import archetypes, lexicon, lock, prep

PARTIAL_FILENAME = "approved_voices.partial.json"


@dataclass
class Gate:
    approved: dict            # approved_voices.json's content (partial: the anchors approved so far)
    lock_path: Path           # the file `vo run`'s workers load it from (lock.ENV)
    lexicon: lexicon.Lexicon
    respelled: int            # lines whose tts_text the Lexicon changed just now
    partial: bool
    waiting: dict[str, int]   # partial: Archetype -> lines waiting for its approval


def partial_path(lock_path: Path | None = None) -> Path:
    return (lock_path or lock.path()).with_name(PARTIAL_FILENAME)


def waiting(conn: sqlite3.Connection, approved: dict) -> dict[str, int]:
    """Lines per Archetype that isn't approved yet (the Narrator is always approved)."""
    out: dict[str, int] = {}
    npcs = archetypes.npc_archetypes(conn)
    for npc_id, n in conn.execute("SELECT npc_id, COUNT(*) FROM lines WHERE npc_id IS NOT NULL"
                                  " AND COALESCE(tts_text, '') != '' GROUP BY npc_id"):
        aid = npcs.get(npc_id)
        if aid is not None and aid != archetypes.NARRATOR and aid not in approved["archetypes"]:
            out[aid] = out.get(aid, 0) + n
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def open_gate(conn: sqlite3.Connection, partial: bool = False, lock_path: Path | None = None,
              lexicon_path: Path | None = None) -> Gate:
    """Check the gate and bring tts_text up to date with the Lexicon. Raises lock.LockError (full mode) or
    archetypes.UnmappedRace."""
    lock_path = lock_path or lock.path()
    if partial:
        approved = lock.from_db(conn)
        lock_path = partial_path(lock_path)
        lock.write(lock_path, approved)
        lex = lexicon.from_db(conn)
    else:
        approved = lock.require(conn, lock_path)
        lex = lexicon.from_db(conn, lexicon.require(conn, lexicon_path or lexicon.path()))
    respelled = prep.prepare_lines(conn, lex)  # a Lexicon change requeues its lines (tts_hash)
    return Gate(approved, lock_path, lex, respelled, partial, waiting(conn, approved) if partial else {})


def text(g: Gate) -> str:
    """One line on what a partial run can voice."""
    n = f"{len(g.approved['archetypes'])} Archetypes approved ({lock.base_voices(g.approved)} Base Voices)"
    if not g.waiting:
        return f"partial run: {n}, none waiting"
    shown = ", ".join(f"{aid} ({k})" for aid, k in list(g.waiting.items())[:8])
    more = f" and {len(g.waiting) - 8} more" if len(g.waiting) > 8 else ""
    return (f"partial run: {n}; {sum(g.waiting.values())} lines wait for {len(g.waiting)}"
            f" unapproved Archetypes: {shown}{more}")

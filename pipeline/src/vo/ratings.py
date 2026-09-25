"""Spot-check ratings: the dashboard queues them as review_actions; `fold` stores them in `ratings`.

The dashboard's only writes are review_actions (Build Spec, Dashboard), so a rating is an action like any other:
  rate-line   target line id, payload {voice_id, rating: up | down}
  flag-line   target line id, payload {voice_id, note?}
  flag-voice  target NPC id,  payload {voice_id?, note?} (voice_id defaults to the NPC's current voice)
vo run (every poll) and vo voices (before it applies re-rolls) fold them into `ratings`, one row per action, then
queue a `reroll-voice` for every NPC Voice with REROLL_DOWNS or more lines whose latest rating is thumbs-down. A
voice is re-rolled once: a re-roll gives the NPC a new voice id, whose ratings start afresh. An Archetype anchor that
NPCs speak with before `vo voices` builds their own is shared, so it is never auto-re-rolled (listed in the log).
"""
import json
import sqlite3
from datetime import datetime
from typing import Callable

RATING_ACTIONS = ("rate-line", "flag-line", "flag-voice")
REROLL_ACTION = "reroll-voice"
REROLL_DOWNS = 3
MAX_NOTE = 500


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _rating(conn: sqlite3.Connection, action: sqlite3.Row) -> tuple:
    """(line_id, voice_id, npc_id, rating, note) for one action; ValueError/TypeError if it is invalid."""
    payload = json.loads(action["payload"]) if action["payload"] else {}
    if not isinstance(payload, dict):
        raise ValueError("payload isn't an object")
    note = str(payload.get("note") or "").strip()[:MAX_NOTE] or None
    voice = payload.get("voice_id")
    if voice is not None and not isinstance(voice, str):
        raise ValueError("voice_id isn't text")
    if action["action"] == "flag-voice":
        npc = int(action["target"])
        own = conn.execute("SELECT voice_id FROM voices WHERE npc_id = ?", (npc,)).fetchone()
        voice = voice or (own[0] if own else None)
        return None, voice, npc, "flag", note
    line = int(action["target"])
    row = conn.execute("SELECT npc_id FROM lines WHERE id = ?", (line,)).fetchone()
    if row is None:
        raise ValueError(f"no line {line}")
    if not voice:
        raise ValueError("voice_id missing")
    if action["action"] == "flag-line":
        return line, voice, row[0], "flag", note
    rating = payload.get("rating")
    if rating not in ("up", "down"):
        raise ValueError(f"rating {rating!r} isn't up or down")
    return line, voice, row[0], rating, note


def fold(conn: sqlite3.Connection, log: Callable[[str], None] = print, threshold: int = REROLL_DOWNS) -> int:
    """Store unconsumed rating actions in `ratings`, then queue re-rolls; returns the actions folded."""
    n = 0
    for action in conn.execute(
            f"SELECT * FROM review_actions WHERE consumed_at IS NULL AND action IN ({','.join('?' * len(RATING_ACTIONS))})"
            " ORDER BY id", RATING_ACTIONS).fetchall():
        with conn:
            try:
                conn.execute("INSERT OR IGNORE INTO ratings (action_id, line_id, voice_id, npc_id, rating, note, at)"
                             " VALUES (?, ?, ?, ?, ?, ?, ?)", (action["id"], *_rating(conn, action), action["created_at"]))
            except (ValueError, TypeError) as e:  # includes a bad JSON payload
                log(f"review action {action['id']} ({action['action']} {action['target']}): invalid, dropped: {e}")
            conn.execute("UPDATE review_actions SET consumed_at = ? WHERE id = ?", (_now(), action["id"]))
        n += 1
    queue_rerolls(conn, log, threshold)
    return n


def voice_downs(conn: sqlite3.Connection) -> dict[str, int]:
    """voice id -> lines whose latest thumbs rating in that voice is down."""
    return dict(conn.execute(
        "SELECT voice_id, COUNT(*) FROM ratings r WHERE rating = 'down' AND id = (SELECT MAX(id) FROM ratings x"
        " WHERE x.line_id = r.line_id AND x.voice_id = r.voice_id AND x.rating IN ('up', 'down'))"
        " GROUP BY voice_id").fetchall())


def queue_rerolls(conn: sqlite3.Connection, log: Callable[[str], None] = print, threshold: int = REROLL_DOWNS) -> list[int]:
    """Queue `reroll-voice` for each NPC Voice at or over the thumbs-down threshold; returns the NPCs queued."""
    queued = []
    for voice, downs in sorted(voice_downs(conn).items()):
        if downs < threshold:
            continue
        npc = conn.execute("SELECT v.npc_id FROM voices v JOIN voice_builds b ON b.npc_id = v.npc_id"
                           " WHERE v.voice_id = ?", (voice,)).fetchone()
        if npc is None:
            log(f"spot-check: {voice} has {downs} thumbs-down but isn't an NPC Voice (an Archetype anchor, or"
                f" replaced since); not re-rolled")
            continue
        npc = npc[0]
        seen = conn.execute(
            "SELECT 1 FROM review_actions WHERE action = ? AND ((consumed_at IS NULL AND target = ?)"
            " OR (json_valid(payload) AND json_extract(payload, '$.voice_id') = ?))",
            (REROLL_ACTION, str(npc), voice)).fetchone()
        if seen:
            continue
        with conn:
            conn.execute("INSERT INTO review_actions (action, target, payload) VALUES (?, ?, ?)",
                         (REROLL_ACTION, str(npc), json.dumps({"voice_id": voice,
                                                              "reason": f"spot-check: {downs} thumbs-down"})))
        log(f"spot-check: {voice} has {downs} thumbs-down; re-roll of NPC {npc} queued for vo voices")
        queued.append(npc)
    return queued

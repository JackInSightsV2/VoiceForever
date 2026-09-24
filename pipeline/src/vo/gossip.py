"""Gossip template matching: each Gossip line becomes a Lua pattern the Core Addon matches displayed text against.

Gossip has no ID (ADR-0001), so the addon takes the NPC's patterns and plays the first that matches the
displayed text after Drift normalisation (Drift.lua's VF.Normalise, drift.normalise here); when several match,
the most specific (most literal characters) wins. A pattern is the
line's text as the client renders it for one player gender, normalised, with Lua magic characters escaped
and every player-dependent token as a wildcard, anchored ^...$.
"""
import re
import sqlite3

from vo import drift, text

TYPES = ("gossip", "quest_greeting")
WILDCARD = ".-"  # no capture: Lua allows only 32 captures per pattern
# $N/$R/$C (any case, the client fills in the player's name, race, class) and $1234w (a world-state counter).
_TOKEN = re.compile(r"\$[NnRrCc]|\$\d+[A-Za-z]?")
_HOLE = "\x00"  # stands in for a token while the text is normalised; never in Source Data text
_MAGIC = re.compile(r"([\^$()%.\[\]*+\-?])")


def _rendered(raw: str, gender: str | None) -> str:
    """The client's rendering, normalised, with each token as _HOLE."""
    if text.GENDER.search(raw):
        if gender is None:
            raise ValueError("text has $G choices; pass the player gender")
        raw = text.GENDER.sub(r"\1" if gender == "m" else r"\2", raw)  # unstripped, as drift.mask does
    raw = re.sub(r"\$[Bb]", "\n", raw)
    return drift.normalise(_TOKEN.sub(_HOLE, raw))


def match_pattern(raw: str, gender: str | None = None) -> str:
    """Anchored Lua pattern matching `raw` as displayed to a player of `gender` ('m'/'f'; None if no $G)."""
    parts = [_MAGIC.sub(r"%\1", p) for p in _rendered(raw, gender).split(_HOLE)]
    return "^" + WILDCARD.join(parts) + "$"


def update_patterns(conn: sqlite3.Connection) -> int:
    """Set match_pattern on every Gossip line whose pattern is missing or out of date; returns how many changed."""
    changed = []
    for r in conn.execute(f"SELECT id, raw_text, player_gender, match_pattern FROM lines"
                          f" WHERE type IN {TYPES}"):
        pattern = match_pattern(r[1], r[2])
        if pattern != r[3]:
            changed.append((pattern, r[0]))
    conn.executemany("UPDATE lines SET match_pattern = ? WHERE id = ?", changed)
    conn.commit()
    return len(changed)


def build_index(rows) -> dict[int, list[dict[str, str]]]:
    """rows of (npc_id, player_gender, pattern, file) -> {npcId: [{pattern, file, gender?}]} in row order.
    A line with no $G has no gender and plays for both. The addon orders each NPC's patterns most specific first."""
    index: dict = {}
    for npc_id, gender, pattern, file in rows:
        entry = {"pattern": pattern, "file": file}
        if gender:
            entry["gender"] = gender
        index.setdefault(npc_id, []).append(entry)
    return index

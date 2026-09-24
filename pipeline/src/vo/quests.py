"""Quest Text extraction from Source Data into `lines`."""
import sqlite3

from vo import drift, source, text


def detail_text(q: sqlite3.Row) -> str:
    """What the quest window shows on QUEST_DETAIL: GetQuestText() then GetObjectiveText()."""
    parts = [p for p in (q["Details"], q["Objectives"]) if p]
    return "$B$B".join(parts)


def extract_detail(conn: sqlite3.Connection, world: sqlite3.Connection, quest_id: int) -> list[int]:
    """Store one quest's detail text as a line per player gender variant; return line ids."""
    q = source.quest(world, quest_id)
    if q is None:
        raise KeyError(f"quest {quest_id} not in Source Data")
    givers = source.quest_givers(world, quest_id)
    npc_id = givers[0] if givers else None  # None: object/item quest, voiced by the Narrator
    ids = []
    for gender, raw in text.gender_variants(detail_text(q)).items():
        # UNIQUE doesn't dedupe NULL npc_id/player_gender in SQLite, so match with IS.
        row = conn.execute(
            "SELECT id FROM lines WHERE type = 'quest_detail' AND quest_id = ?"
            " AND npc_id IS ? AND player_gender IS ? AND raw_text = ?",
            (quest_id, npc_id, gender, raw)).fetchone()
        if row is None:
            row = conn.execute(
                "INSERT INTO lines (npc_id, type, quest_id, player_gender, raw_text, tts_text, text_hash)"
                " VALUES (?, 'quest_detail', ?, ?, ?, ?, ?) RETURNING id",
                (npc_id, quest_id, gender, raw, text.tts_text(raw), drift.text_hash(raw, gender))).fetchone()
        ids.append(row[0])
    conn.commit()
    return ids

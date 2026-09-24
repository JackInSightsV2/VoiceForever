"""Text prep stage: (re)compute `lines.tts_text` from `raw_text` for every line."""
import sqlite3

from vo import text


def manual_edits(conn: sqlite3.Connection) -> set[int]:
    """Lines whose tts_text was hand-edited on the dashboard (a consumed edit-tts-text action)."""
    return {int(r[0]) for r in conn.execute(
        "SELECT target FROM review_actions WHERE action = 'edit-tts-text' AND consumed_at IS NOT NULL")}


def prepare_lines(conn: sqlite3.Connection, lexicon: text.Lexicon | None = None) -> int:
    """Set tts_text for every line whose spoken form is missing or out of date; return how many changed.
    A hand-edited tts_text is kept."""
    changed, edited = 0, manual_edits(conn)
    for line in conn.execute("SELECT id, raw_text, player_gender, tts_text FROM lines").fetchall():
        if line["id"] in edited:
            continue
        spoken = text.prepare(line["raw_text"], line["player_gender"], lexicon)
        if spoken != line["tts_text"]:
            conn.execute("UPDATE lines SET tts_text = ? WHERE id = ?", (spoken, line["id"]))
            changed += 1
    conn.commit()
    return changed

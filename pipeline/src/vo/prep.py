"""Text prep stage: (re)compute `lines.tts_text` from `raw_text` for every line."""
import sqlite3

from vo import text


def prepare_lines(conn: sqlite3.Connection, lexicon: text.Lexicon | None = None) -> int:
    """Set tts_text for every line whose spoken form is missing or out of date; return how many changed."""
    changed = 0
    for line in conn.execute("SELECT id, raw_text, player_gender, tts_text FROM lines").fetchall():
        spoken = text.prepare(line["raw_text"], line["player_gender"], lexicon)
        if spoken != line["tts_text"]:
            conn.execute("UPDATE lines SET tts_text = ? WHERE id = ?", (spoken, line["id"]))
            changed += 1
    conn.commit()
    return changed

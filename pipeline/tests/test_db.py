from vo import db


def test_schema_creates_core_tables(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"npcs", "spawns", "lines", "voices", "audio", "capture", "manual_overrides", "jobs", "review_actions", "runs"} <= tables


def test_migration_backfills_source_text_before_drift(tmp_path):
    import sqlite3
    old = sqlite3.connect(tmp_path / "vo.sqlite")
    old.executescript(db.SCHEMA)
    old.executemany("INSERT INTO lines (id, type, quest_id, raw_text, source) VALUES (?, 'quest_detail', ?, ?, ?)",
                    [(1, 1, "Captured.", "core"), (2, 2, "Plain.", "core"), (3, 3, "New.", "capture")])
    old.execute("INSERT INTO line_history (line_id, raw_text, reason) VALUES (1, 'Source.', 'drift')")
    old.commit()
    old.close()
    conn = db.connect(tmp_path / "vo.sqlite")
    assert [tuple(r) for r in conn.execute("SELECT id, source_text FROM lines ORDER BY id")] == [
        (1, "Source."), (2, "Plain."), (3, None)]

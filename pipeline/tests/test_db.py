from vo import db


def test_schema_creates_core_tables(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"npcs", "spawns", "lines", "voices", "audio", "capture", "manual_overrides", "jobs", "review_actions", "runs"} <= tables

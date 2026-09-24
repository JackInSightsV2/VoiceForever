from vo import db, prep


def test_prepare_lines_sets_and_refreshes_tts_text(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    conn.executemany("INSERT INTO lines (type, quest_id, player_gender, raw_text, tts_text) VALUES (?, ?, ?, ?, ?)", [
        ("quest_detail", 1, None, "Greetings, $c.$B$BBring 5 pelts.", None),
        ("quest_detail", 2, "f", "Hello $gsir:madam;.", "stale"),
        ("quest_detail", 3, "m", "Hello $gsir:madam;.", "Hello sir."),
    ])
    assert prep.prepare_lines(conn) == 2
    assert [r[0] for r in conn.execute("SELECT tts_text FROM lines ORDER BY id")] == [
        "Greetings, friend.\nBring five pelts.", "Hello madam.", "Hello sir."]
    assert prep.prepare_lines(conn) == 0


def test_prepare_lines_applies_lexicon(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    conn.execute("INSERT INTO lines (type, raw_text) VALUES ('quest_detail', 'Thrall awaits.')")
    prep.prepare_lines(conn, lexicon=lambda s: s.replace("Thrall", "Thrawl"))
    assert conn.execute("SELECT tts_text FROM lines").fetchone()[0] == "Thrawl awaits."

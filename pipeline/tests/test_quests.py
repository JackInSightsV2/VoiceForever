import sqlite3

import pytest

from vo import db, quests, source, text


@pytest.fixture
def world(tmp_path):
    """A tiny world DB with the VMaNGOS columns we read, including a quest changed in a later patch."""
    path = tmp_path / "mangos.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
      CREATE TABLE quest_template (entry INTEGER, patch INTEGER, Title TEXT, Details TEXT, Objectives TEXT,
        RequestItemsText TEXT, OfferRewardText TEXT);
      CREATE TABLE creature_questrelation (id INTEGER, quest INTEGER, patch_min INTEGER, patch_max INTEGER);
      INSERT INTO quest_template VALUES (783, 0, 'A Threat Within', 'Old text.', 'Old.', '', '');
      INSERT INTO quest_template VALUES (783, 3, 'A Threat Within',
        'Young $c, there is work.$B$BSpeak with the Marshal.', 'Speak with Marshal McBride.', '', 'Ah, good.');
      INSERT INTO quest_template VALUES (9, 0, 'Gendered', 'Hello $gsir:madam;, $N.', '', '', '');
      INSERT INTO creature_questrelation VALUES (823, 783, 0, 10);
    """)
    conn.commit()
    conn.close()
    return source.open_world(path)


def test_quest_uses_latest_patch(world):
    assert source.quest(world, 783)["Details"].startswith("Young $c")


def test_detail_text_joins_details_and_objectives(world):
    assert quests.detail_text(source.quest(world, 783)) == (
        "Young $c, there is work.$B$BSpeak with the Marshal.$B$BSpeak with Marshal McBride.")


def test_extract_detail_stores_line_once(world, tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    ids = quests.extract_detail(conn, world, 783)
    assert quests.extract_detail(conn, world, 783) == ids
    (line,) = conn.execute("SELECT * FROM lines").fetchall()
    assert (line["npc_id"], line["type"], line["quest_id"], line["player_gender"]) == (823, "quest_detail", 783, None)
    assert line["tts_text"] == ("Young adventurer, there is work.\nSpeak with the Marshal.\nSpeak with Marshal McBride.")


def test_extract_detail_splits_player_gender(world, tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    quests.extract_detail(conn, world, 9)
    rows = conn.execute("SELECT npc_id, player_gender, tts_text FROM lines ORDER BY player_gender").fetchall()
    assert [tuple(r) for r in rows] == [(None, "f", "Hello madam, friend."), (None, "m", "Hello sir, friend.")]


def test_extract_unknown_quest(world, tmp_path):
    with pytest.raises(KeyError):
        quests.extract_detail(db.connect(tmp_path / "vo.sqlite"), world, 1)


def test_gender_variants_without_token():
    assert text.gender_variants("No tokens.") == {None: "No tokens."}

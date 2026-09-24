import sqlite3

import pytest

from vo import db, extract, source, stats
from vo.display import Displays
from vo.questie import Npc, Quest, Questie

# Display fixtures: 10/11 humanoid (Extra), 20 creature model with its own gender,
# 21 creature model with no gender (family default), 30 a female human, 40 an orc.
INFO = {
    10: {"ModelID": "1", "ExtendedDisplayInfoID": "100", "Gender": "2"},
    11: {"ModelID": "1", "ExtendedDisplayInfoID": "101", "Gender": "2"},
    20: {"ModelID": "2", "ExtendedDisplayInfoID": "0", "Gender": "1"},
    21: {"ModelID": "3", "ExtendedDisplayInfoID": "0", "Gender": "2"},
    30: {"ModelID": "1", "ExtendedDisplayInfoID": "102", "Gender": "2"},
    40: {"ModelID": "1", "ExtendedDisplayInfoID": "103", "Gender": "2"},
    50: {"ModelID": "4", "ExtendedDisplayInfoID": "0", "Gender": "2"},
}
EXTRA = {100: {"DisplayRaceID": "1", "DisplaySexID": "0"}, 101: {"DisplayRaceID": "3", "DisplaySexID": "0"},
         102: {"DisplayRaceID": "1", "DisplaySexID": "1"}, 103: {"DisplayRaceID": "2", "DisplaySexID": "0"}}
MODELS = {1: 1001, 2: 1002, 3: 1003, 4: 1004}
RACES = {1: {"Name_lang": "Human", "ClientFileString": "Human"}, 2: {"Name_lang": "Orc", "ClientFileString": "Orc"},
         3: {"Name_lang": "Dwarf", "ClientFileString": "Dwarf"}}
PATHS = {1001: "character/human/male/humanmale.m2", 1002: "creature/harpy/harpy.m2",
         1003: "creature/ogremage/ogremage.m2", 1004: "character/scourge/female/scourgefemale.m2"}


@pytest.fixture
def displays():
    return Displays(INFO, EXTRA, MODELS, RACES, PATHS)


WORLD = """
CREATE TABLE creature_template (entry INTEGER, patch INTEGER, name TEXT, subname TEXT, level_min INTEGER,
  level_max INTEGER, faction INTEGER, npc_flags INTEGER, gossip_menu_id INTEGER, type INTEGER, rank INTEGER,
  display_id1 INTEGER, display_id2 INTEGER, display_id3 INTEGER, display_id4 INTEGER,
  display_probability1 INTEGER, display_probability2 INTEGER, display_probability3 INTEGER,
  display_probability4 INTEGER);
CREATE TABLE creature (guid INTEGER, id INTEGER, id2 INTEGER, id3 INTEGER, id4 INTEGER, id5 INTEGER, map INTEGER,
  position_x REAL, position_y REAL, position_z REAL, patch_min INTEGER, patch_max INTEGER);
CREATE TABLE quest_template (entry INTEGER, patch INTEGER, Title TEXT, ZoneOrSort INTEGER, Details TEXT,
  Objectives TEXT, RequestItemsText TEXT, OfferRewardText TEXT);
CREATE TABLE quest_greeting (entry INTEGER, type INTEGER, content_default TEXT);
CREATE TABLE gossip_menu (entry INTEGER, text_id INTEGER);
CREATE TABLE gossip_menu_option (menu_id INTEGER, id INTEGER, action_menu_id INTEGER);
CREATE TABLE npc_text (ID INTEGER, BroadcastTextID0 INTEGER, Probability0 REAL, BroadcastTextID1 INTEGER,
  Probability1 REAL, BroadcastTextID2 INTEGER, Probability2 REAL, BroadcastTextID3 INTEGER, Probability3 REAL,
  BroadcastTextID4 INTEGER, Probability4 REAL, BroadcastTextID5 INTEGER, Probability5 REAL,
  BroadcastTextID6 INTEGER, Probability6 REAL, BroadcastTextID7 INTEGER, Probability7 REAL);
CREATE TABLE broadcast_text (entry INTEGER, male_text TEXT, female_text TEXT);
CREATE TABLE creature_questrelation (id INTEGER, quest INTEGER, patch_min INTEGER, patch_max INTEGER);
CREATE TABLE creature_involvedrelation (id INTEGER, quest INTEGER, patch_min INTEGER, patch_max INTEGER);
CREATE TABLE gameobject_questrelation (id INTEGER, quest INTEGER, patch_min INTEGER, patch_max INTEGER);
CREATE TABLE gameobject_involvedrelation (id INTEGER, quest INTEGER, patch_min INTEGER, patch_max INTEGER);
CREATE TABLE item_template (entry INTEGER, patch INTEGER, start_quest INTEGER);
CREATE TABLE area_template (entry INTEGER, map_id INTEGER, zone_id INTEGER, name TEXT);

-- 197 Marshal: changed in patch 3 (name), gossip menu 4048, male human.
INSERT INTO creature_template VALUES (197, 0, 'Old Marshal', NULL, 20, 20, 12, 3, 4048, 7, 0, 10, 0, 0, 0, 100, 0, 0, 0);
INSERT INTO creature_template VALUES (197, 3, 'Marshal McBride', NULL, 20, 20, 12, 3, 4048, 7, 0, 10, 0, 0, 0, 100, 0, 0, 0);
-- 300 Guard: mostly male human, sometimes female: mixed gender. Gossip menu 50 with a sub-menu 51.
INSERT INTO creature_template VALUES (300, 0, 'Stormwind City Guard', NULL, 55, 55, 11, 1, 50, 7, 0, 10, 30, 0, 0, 70, 30, 0, 0);
-- 301 female NPC sharing the guard's menu text: gets the female broadcast variant.
INSERT INTO creature_template VALUES (301, 0, 'Priestess Anetta', 'Priest Trainer', 5, 5, 12, 16, 60, 7, 0, 30, 0, 0, 0, 100, 0, 0, 0);
-- 400 Harpy quest giver (creature model with its own gender); 401 ogre (family default gender).
INSERT INTO creature_template VALUES (400, 0, 'Windcaller', NULL, 10, 10, 14, 2, 0, 7, 0, 20, 0, 0, 0, 100, 0, 0, 0);
INSERT INTO creature_template VALUES (401, 0, 'Mug''thol', NULL, 10, 10, 14, 2, 0, 7, 0, 21, 0, 0, 0, 100, 0, 0, 0);
-- 500 a beast quest giver (filtered); 501 a beast with an include override; 502 grunt with no text.
INSERT INTO creature_template VALUES (500, 0, 'Talking Wolf', NULL, 10, 10, 14, 2, 0, 1, 0, 20, 0, 0, 0, 100, 0, 0, 0);
INSERT INTO creature_template VALUES (501, 0, 'Speaking Owl', NULL, 10, 10, 14, 2, 0, 1, 0, 20, 0, 0, 0, 100, 0, 0, 0);
INSERT INTO creature_template VALUES (502, 0, 'Defias Thug', NULL, 10, 10, 14, 0, 0, 7, 0, 10, 0, 0, 0, 100, 0, 0, 0);
-- 600 mixed-race displays: orc 2x as likely as dwarf.
INSERT INTO creature_template VALUES (600, 0, 'Reveler', NULL, 10, 10, 14, 1, 70, 7, 0, 11, 40, 0, 0, 1, 2, 0, 0);

INSERT INTO creature VALUES (1, 197, 0, 0, 0, 0, 0, -8902.6, -162.6, 82.0, 0, 10);
INSERT INTO creature VALUES (2, 197, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5);          -- removed before patch 10
INSERT INTO creature VALUES (3, 300, 0, 0, 0, 0, 0, 100, 100, 0, 0, 10);
INSERT INTO creature VALUES (4, 300, 0, 0, 0, 0, 0, 200, 200, 0, 0, 10);
INSERT INTO creature VALUES (5, 502, 301, 0, 0, 0, 0, 5, 5, 0, 0, 10);        -- shared spawn slot
INSERT INTO creature VALUES (6, 400, 0, 0, 0, 0, 43, 1, 2, 3, 0, 10);         -- instance map

INSERT INTO quest_template VALUES (783, 0, 'A Threat Within', 9, 'Old.', '', '', '');
INSERT INTO quest_template VALUES (783, 3, 'A Threat Within', 9, 'There is work, $c.', 'Speak with McBride.',
  'Well?', 'Ah, good, $gsir:madam;.');
INSERT INTO quest_template VALUES (800, 0, 'Wanted Poster', 12, 'Wanted: Hogger.', '', '', 'Bounty paid.');
INSERT INTO quest_template VALUES (801, 0, 'Wolf Talk', 12, 'Awoo.', '', '', '');
INSERT INTO quest_template VALUES (802, 0, 'Owl Talk', 12, 'Hoot.', '', '', '');
INSERT INTO quest_template VALUES (803, 0, 'Unobtainable', 12, 'Nobody starts me.', '', '', '');
INSERT INTO quest_template VALUES (804, 0, '<NYI> Placeholder', 12, 'Never shipped.', '', '', '');
INSERT INTO quest_template VALUES (805, 0, 'Item Letter', 12, 'A letter.', '', '', 'Thanks.');
INSERT INTO quest_template VALUES (806, 0, 'Harpy Business', 12, 'Screech.', '', '', '');
INSERT INTO quest_template VALUES (807, 0, 'Ogre Business', 12, 'Smash.', '', '', '');
INSERT INTO creature_questrelation VALUES (197, 783, 0, 10);
INSERT INTO creature_involvedrelation VALUES (197, 783, 0, 10);
INSERT INTO creature_questrelation VALUES (500, 801, 0, 10);
INSERT INTO creature_questrelation VALUES (501, 802, 0, 10);
INSERT INTO creature_questrelation VALUES (197, 804, 0, 10);
INSERT INTO creature_questrelation VALUES (400, 806, 0, 10);
INSERT INTO creature_questrelation VALUES (401, 807, 0, 10);
INSERT INTO gameobject_questrelation VALUES (68, 800, 0, 10);
INSERT INTO gameobject_involvedrelation VALUES (68, 800, 0, 10);
INSERT INTO creature_involvedrelation VALUES (197, 805, 0, 10);
INSERT INTO item_template VALUES (9000, 0, 805);

INSERT INTO quest_greeting VALUES (197, 0, 'Hello there, $c.');
INSERT INTO quest_greeting VALUES (68, 1, 'An object greeting.');
INSERT INTO gossip_menu VALUES (4048, 4938);
INSERT INTO gossip_menu VALUES (50, 1);
INSERT INTO gossip_menu VALUES (51, 2);
INSERT INTO gossip_menu VALUES (60, 1);
INSERT INTO gossip_menu VALUES (70, 3);
INSERT INTO gossip_menu_option VALUES (50, 0, 51);
INSERT INTO npc_text VALUES (4938, 7590, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
INSERT INTO npc_text VALUES (1, 11, 0.5, 12, 0.5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
INSERT INTO npc_text VALUES (2, 13, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
INSERT INTO npc_text VALUES (3, 14, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
INSERT INTO broadcast_text VALUES (7590, 'Hey, citizen! You look like a stout one.', '');
INSERT INTO broadcast_text VALUES (11, 'Move along, citizen.', 'Move along, dear.');
INSERT INTO broadcast_text VALUES (12, '', 'Only a female variant.');
INSERT INTO broadcast_text VALUES (13, 'The cathedral is north.', '');
INSERT INTO broadcast_text VALUES (14, 'Cheers!', '');
INSERT INTO area_template VALUES (1581, 43, 0, 'Wailing Caverns');
"""


@pytest.fixture
def world(tmp_path):
    path = tmp_path / "mangos.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(WORLD)
    conn.commit()
    conn.close()
    return source.open_world(path)


@pytest.fixture
def questie():
    return Questie(
        npcs={197: Npc(zone=12, spawns={12: [(48.9, 41.6)]}), 300: Npc(zone=1519, spawns={1519: [(50, 50)]})},
        quests={805: Quest(item_starts=[9000], creature_ends=[197])})


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "vo.sqlite")


def run(conn, world, questie, displays):
    return extract.extract_all(conn, world, questie, displays)


def npc(conn, npc_id):
    return conn.execute("SELECT * FROM npcs WHERE id = ?", (npc_id,)).fetchone()


def issues(conn, npc_id):
    return {r[0] for r in conn.execute("SELECT issue FROM npc_issues WHERE npc_id = ?", (npc_id,))}


def lines(conn, **where):
    sql = "SELECT type, quest_id, npc_id, player_gender, raw_text FROM lines"
    if where:
        sql += " WHERE " + " AND ".join(f"{k} IS ?" for k in where)
    return [tuple(r) for r in conn.execute(sql + " ORDER BY id", tuple(where.values()))]


# ------------------------------------------------------------------ patch selection

def test_templates_use_latest_patch(conn, world, questie, displays):
    run(conn, world, questie, displays)
    assert npc(conn, 197)["name"] == "Marshal McBride"
    assert ("quest_detail", 783, 197, None, "There is work, $c.$B$BSpeak with McBride.") in lines(conn, quest_id=783)


def test_spawns_live_at_final_patch(conn, world, questie, displays):
    run(conn, world, questie, displays)
    rows = conn.execute("SELECT map, zone, x, y, z FROM spawns WHERE npc_id = 197").fetchall()
    assert [tuple(r) for r in rows] == [(0, 12, -8902.6, -162.6, 82.0)]


def test_spawn_alternate_entry_and_instance_zone(conn, world, questie, displays):
    run(conn, world, questie, displays)
    assert conn.execute("SELECT COUNT(*) FROM spawns WHERE npc_id = 301").fetchone()[0] == 1
    assert conn.execute("SELECT zone FROM spawns WHERE npc_id = 400").fetchone()[0] == 1581


# ------------------------------------------------------------------ race and gender resolution

def test_extra_gives_race_and_sex(displays):
    d = displays.get(30)
    assert (d.race, d.gender, d.how) == ("Human", "female", "extra")


def test_creature_model_path_gives_race_and_display_gender(displays):
    d = displays.get(20)
    assert (d.race, d.gender, d.how, d.gender_default) == ("Harpy", "female", "creature", False)


def test_character_model_without_extra(displays):
    d = displays.get(50)
    assert (d.race, d.gender) == ("Undead", "female")


def test_creature_without_gender_defaults_and_flags(conn, world, questie, displays):
    run(conn, world, questie, displays)
    assert (npc(conn, 401)["race"], npc(conn, 401)["gender"]) == ("Ogre", "male")
    assert "gender_default" in issues(conn, 401)


def test_missing_display_is_unresolved(displays):
    res = extract.resolve([(999, 100)], displays)
    assert (res.race, res.gender) == (None, None)
    assert ("display_missing", "999") in res.issues


def test_most_probable_display_and_mixed_flags(conn, world, questie, displays):
    run(conn, world, questie, displays)
    guard = npc(conn, 300)
    assert (guard["race"], guard["gender"]) == ("Human", "male")
    assert issues(conn, 300) == {"mixed_gender"}
    reveler = npc(conn, 600)
    assert reveler["race"] == "Orc"
    assert "mixed_race" in issues(conn, 600)


def test_overrides_beat_every_source(conn, world, questie, displays):
    conn.executemany("INSERT INTO manual_overrides VALUES (?, ?, ?)", [
        (300, "gender", "female"), (401, "gender", "female"), (401, "race", "Ogre Mage"),
        (197, "role", "story"), (197, "is_named", "0")])
    run(conn, world, questie, displays)
    assert npc(conn, 300)["gender"] == "female" and "mixed_gender" not in issues(conn, 300)
    assert (npc(conn, 401)["race"], npc(conn, 401)["gender"]) == ("Ogre Mage", "female")
    assert "gender_default" not in issues(conn, 401)
    assert (npc(conn, 197)["role"], npc(conn, 197)["is_named"]) == ("story", 0)


# ------------------------------------------------------------------ filters

def test_beasts_and_textless_npcs_are_dropped(conn, world, questie, displays):
    summary = run(conn, world, questie, displays)
    assert npc(conn, 500) is None and npc(conn, 502) is None
    assert summary["dropped"]["beast"] == 2
    # the filtered beast's quest is still voiced, by the Narrator
    assert lines(conn, quest_id=801) == [("quest_detail", 801, None, None, "Awoo.")]


def test_include_override_keeps_a_speaking_beast(conn, world, questie, displays):
    conn.execute("INSERT INTO manual_overrides VALUES (501, 'include', '1')")
    run(conn, world, questie, displays)
    assert npc(conn, 501) is not None
    assert lines(conn, quest_id=802)[0][2] == 501


def test_unstarted_and_placeholder_quests_are_skipped(conn, world, questie, displays):
    summary = run(conn, world, questie, displays)
    assert lines(conn, quest_id=803) == [] and lines(conn, quest_id=804) == []
    assert summary["quests"] == 7


# ------------------------------------------------------------------ Quest Text and Gossip

def test_quest_parts_and_player_gender(conn, world, questie, displays):
    run(conn, world, questie, displays)
    got = lines(conn, quest_id=783)
    assert ("quest_progress", 783, 197, None, "Well?") in got
    assert ("quest_complete", 783, 197, "m", "Ah, good, $gsir:madam;.") in got
    assert ("quest_complete", 783, 197, "f", "Ah, good, $gsir:madam;.") in got
    spoken = dict(conn.execute("SELECT player_gender, tts_text FROM lines WHERE type = 'quest_complete' AND quest_id = 783"))
    assert spoken == {"m": "Ah, good, sir.", "f": "Ah, good, madam."}
    assert not conn.execute("SELECT 1 FROM lines WHERE tts_text IS NULL OR text_hash IS NULL").fetchone()


def test_object_and_item_quests_go_to_the_narrator(conn, world, questie, displays):
    run(conn, world, questie, displays)
    assert {r[2] for r in lines(conn, quest_id=800)} == {None}
    assert lines(conn, quest_id=805) == [("quest_detail", 805, None, None, "A letter."),
                                         ("quest_complete", 805, 197, None, "Thanks.")]


def test_gossip_chain_and_greeting(conn, world, questie, displays):
    run(conn, world, questie, displays)
    assert lines(conn, npc_id=197, type="gossip") == [
        ("gossip", None, 197, None, "Hey, citizen! You look like a stout one.")]
    assert lines(conn, npc_id=197, type="quest_greeting") == [
        ("quest_greeting", None, 197, None, "Hello there, $c.")]
    # male NPC: male variant, falling back to the female one; sub-menu 51 followed
    assert [r[4] for r in lines(conn, npc_id=300)] == [
        "Move along, citizen.", "Only a female variant.", "The cathedral is north."]
    # female NPC sharing text 1: the female variant
    assert [r[4] for r in lines(conn, npc_id=301)] == ["Move along, dear.", "Only a female variant."]


def test_rerun_keeps_line_ids(conn, world, questie, displays):
    run(conn, world, questie, displays)
    before = conn.execute("SELECT id, raw_text FROM lines ORDER BY id").fetchall()
    run(conn, world, questie, displays)
    assert conn.execute("SELECT id, raw_text FROM lines ORDER BY id").fetchall() == before


# ------------------------------------------------------------------ role, is_named, stats

def test_role_and_named(conn, world, questie, displays):
    run(conn, world, questie, displays)
    assert npc(conn, 301)["role"] == "trainer"
    assert npc(conn, 300)["role"] == "guard" and npc(conn, 300)["is_named"] == 0
    assert npc(conn, 197)["role"] == "quest" and npc(conn, 197)["is_named"] == 1


def test_is_named_heuristic():
    words = extract.name_words(["Stormwind City Guard", "Stormwind Guard", "Guard Thomas", "Guard Berton",
                                "Stormwind Citizen", "City Guard", "Marshal McBride"])
    assert extract.is_named("Guard Thomas", 1, 1, 0, words)
    assert not extract.is_named("Stormwind City Guard", 1, 1, 0, extract.name_words(
        ["Stormwind City Guard"] + [f"Stormwind City Guard {i}" for i in range(5)]))
    assert not extract.is_named("Marshal McBride", 2, 1, 0, words)  # spawns twice
    assert extract.is_named("Hogger", 5, 1, 3, words)                # boss


def test_stats_summary(conn, world, questie, displays):
    run(conn, world, questie, displays)
    out = stats.summary(conn)
    assert "unresolved race/gender: 0 (0.00%)" in out
    assert "gossip" in out and "Narrator lines:" in out

"""Voice Pack assignment rules (vo.packs) and multi-pack packaging."""
import sqlite3

import pytest

from conftest import lua_literal
from vo import db, package, packs
from vo.packs import Npc, Quest, band, npc_pack, quest_pack

ALLIANCE, HORDE, NEUTRAL = "Alliance", "Horde", "Neutral"
ELWYNN, DUROTAR, BARRENS, STORMWIND, STRANGLETHORN, SILITHUS = 12, 14, 17, 1519, 33, 1377


@pytest.mark.parametrize("level,name", [
    (None, "1-10"), (0, "1-10"), (-1, "1-10"), (1, "1-10"), (9, "1-10"), (10, "10-20"), (19, "10-20"),
    (20, "20-30"), (39, "30-40"), (40, "40-50"), (50, "50-60"), (60, "50-60"), (63, "50-60")])
def test_band(level, name):
    assert band(level) == name


def test_every_pack_name():
    assert len(packs.all_packs()) == 18
    assert packs.all_packs()[0] == "VoiceForever_Alliance_1-10"
    assert packs.all_packs()[-1] == "VoiceForever_Neutral_50-60"


# --- faction templates, as in the VMaNGOS 1.12 faction_template ------------------------------------------------------

def ft(faction, our, friendly, hostile, enemies=(), friends=()):
    return packs.FactionTemplate(faction, our, friendly, hostile, tuple(enemies), tuple(friends))


PLAYERS = {ALLIANCE: [ft(1, 3, 2, 12), ft(3, 3, 2, 12), ft(4, 3, 2, 12), ft(8, 3, 2, 12)],
           HORDE: [ft(2, 5, 4, 10), ft(5, 5, 4, 10), ft(6, 5, 4, 10), ft(9, 5, 4, 10)]}


@pytest.mark.parametrize("template,team", [
    (ft(72, 2, 2, 4, friends=[72]), ALLIANCE),   # 12 Stormwind
    (ft(76, 4, 4, 2), HORDE),                    # 29 Orgrimmar
    (ft(21, 0, 0, 0, friends=[21]), NEUTRAL),    # 120 Booty Bay
    (ft(31, 0, 1, 0, friends=[31]), NEUTRAL),    # 35 friendly to all
    (ft(14, 8, 0, 1), NEUTRAL),                  # 14 monster: hostile to both
    (ft(99, 0, 0, 0, enemies=[2, 5, 6, 9]), ALLIANCE),  # hostile to every Horde race by faction
    (ft(99, 0, 0, 0, enemies=[1]), NEUTRAL),     # hostile to humans only: other Alliance races can still talk
])
def test_template_team(template, team):
    assert packs.template_team(template, PLAYERS) == team


# --- NPC (Gossip) assignment -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("npc,pack", [
    (Npc(ALLIANCE, 5, (ELWYNN,)), "VoiceForever_Alliance_1-10"),
    (Npc(ALLIANCE, 55, (STORMWIND,)), "VoiceForever_Alliance_1-10"),       # a city guard: every level visits
    (Npc(HORDE, 12, (DUROTAR, BARRENS)), "VoiceForever_Horde_1-10"),        # lowest zone wins
    (Npc(NEUTRAL, 45, (STRANGLETHORN,)), "VoiceForever_Neutral_30-40"),     # Booty Bay
    (Npc(NEUTRAL, 10, (BARRENS,)), "VoiceForever_Neutral_10-20"),           # Ratchet: neutral in a Horde zone
    (Npc(ALLIANCE, 57, (SILITHUS,)), "VoiceForever_Alliance_50-60"),
    (Npc(ALLIANCE, 23, (99999,)), "VoiceForever_Alliance_20-30"),           # unknown zone: the NPC's own level
    (Npc(None, None, (ELWYNN,)), "VoiceForever_Alliance_1-10"),             # Capture NPC: its zone's team
    (Npc(None, None, (ELWYNN, DUROTAR)), "VoiceForever_Neutral_1-10"),      # zones disagree
    (Npc(None, None, ()), "VoiceForever_Neutral_1-10"),                     # nothing known
])
def test_npc_pack(npc, pack):
    assert npc_pack(npc) == pack


# --- quest assignment ------------------------------------------------------------------------------------------------

SW_NPC, ORC_NPC, BB_NPC = Npc(ALLIANCE, 5, (ELWYNN,)), Npc(HORDE, 5, (DUROTAR,)), Npc(NEUTRAL, 45, (STRANGLETHORN,))


@pytest.mark.parametrize("quest,npcs,pack", [
    (Quest(5, 1, 0, ELWYNN), [SW_NPC], "VoiceForever_Alliance_1-10"),
    (Quest(12, 8, 0, ELWYNN), [SW_NPC], "VoiceForever_Alliance_10-20"),     # QuestLevel beats the zone
    (Quest(-1, 55, 0, 0), [SW_NPC], "VoiceForever_Alliance_50-60"),         # scaling quest: MinLevel
    (Quest(0, 0, 0, STRANGLETHORN), [BB_NPC], "VoiceForever_Neutral_30-40"),  # no level: quest zone
    (Quest(0, 0, 0, 0), [ORC_NPC], "VoiceForever_Horde_1-10"),              # nothing on the quest: NPC levels
    (Quest(35, 30, 77, STRANGLETHORN), [BB_NPC], "VoiceForever_Alliance_30-40"),  # RequiredRaces beats the NPC
    (Quest(35, 30, 2 | 16, STRANGLETHORN), [BB_NPC], "VoiceForever_Horde_30-40"),
    (Quest(35, 30, 1 | 2, STRANGLETHORN), [BB_NPC], "VoiceForever_Neutral_30-40"),  # races of both: NPCs decide
    (Quest(35, 30, 0, STRANGLETHORN), [BB_NPC, SW_NPC], "VoiceForever_Alliance_30-40"),  # ender Alliance-only
    (Quest(35, 30, 0, STRANGLETHORN), [SW_NPC, ORC_NPC], "VoiceForever_Neutral_30-40"),  # NPCs conflict
    (Quest(3, 1, 0, ELWYNN), [], "VoiceForever_Alliance_1-10"),             # Narrator only: the zone's team
    (Quest(3, 1, 0, STRANGLETHORN), [], "VoiceForever_Neutral_1-10"),       # contested zone
    (Quest(10, 10, 0, -161), [], "VoiceForever_Neutral_10-20"),              # quest sort, not a zone
    (None, [], "VoiceForever_Neutral_1-10"),                                # Forever Content: nothing known
])
def test_quest_pack(quest, npcs, pack):
    assert quest_pack(quest, npcs) == pack


# --- from the databases ----------------------------------------------------------------------------------------------

def world_db() -> sqlite3.Connection:
    """A two-quest VMaNGOS world with the player templates plus Stormwind (12), Orgrimmar (29), Booty Bay (120)."""
    w = sqlite3.connect(":memory:")
    w.execute("CREATE TABLE quest_template (entry, patch, QuestLevel, MinLevel, RequiredRaces, ZoneOrSort)")
    w.executemany("INSERT INTO quest_template VALUES (?, ?, ?, ?, ?, ?)", [
        (783, 0, 2, 1, 0, ELWYNN), (783, 5, 12, 1, 0, ELWYNN),  # a later patch changed its level: latest wins
        (784, 0, 45, 40, 0, STRANGLETHORN), (785, 0, 4, 1, 0, DUROTAR)])
    w.execute("CREATE TABLE faction_template (id, build, faction_id, faction_flags, our_mask, friendly_mask,"
              " hostile_mask, enemy_faction1, enemy_faction2, enemy_faction3, enemy_faction4, friend_faction1,"
              " friend_faction2, friend_faction3, friend_faction4)")
    rows = [(i, 4222, f, 72, 3, 2, 12) for i, f in ((1, 1), (3, 3), (4, 4), (115, 8))]
    rows += [(i, 4222, f, 72, 5, 4, 10) for i, f in ((2, 2), (5, 5), (6, 6), (116, 9))]
    rows += [(12, 4222, 72, 0, 2, 2, 4), (29, 4222, 76, 0, 4, 4, 2), (120, 4222, 21, 0, 0, 0, 0),
             (120, 6005, 21, 0, 4, 4, 2)]  # a build after 1.12 is ignored
    w.executemany("INSERT INTO faction_template VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0)", rows)
    return w


def test_load_source():
    src = packs.load_source(world_db())
    assert src.quests[783] == Quest(12, 1, 0, ELWYNN)
    assert (src.teams[12], src.teams[29], src.teams[120], src.teams[1]) == (ALLIANCE, HORDE, NEUTRAL, ALLIANCE)


# (line id, npc, type, quest, player gender, raw text)
LINES = [
    (1, 823, "quest_detail", 783, None, "Kill the kobolds."),    # Alliance NPC, level 12 quest
    (2, 823, "quest_complete", 783, None, "Well done."),
    (3, 823, "gossip", None, None, "Hello, $N."),                # Elwynn: Alliance 1-10
    (4, 2663, "quest_detail", 784, None, "Bring me gold."),      # Booty Bay quest
    (5, 2663, "gossip", None, None, "Welcome to Booty Bay."),
    (6, 3143, "quest_detail", 785, None, "For the Horde."),      # Orgrimmar faction in Durotar
    (7, None, "quest_detail", 999, None, "A mysterious note."),  # Narrator, quest unknown to the world DB
    (8, 3143, "gossip", None, None, "Lok'tar."),                 # no audio yet
]
NPCS = [(823, 12, 10, ELWYNN), (2663, 120, 45, STRANGLETHORN), (3143, 29, 5, DUROTAR)]


@pytest.fixture
def pipeline(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    for npc_id, faction, level, zone in NPCS:
        conn.execute("INSERT INTO npcs (id, faction, level_min) VALUES (?, ?, ?)", (npc_id, faction, level))
        conn.execute("INSERT INTO spawns (npc_id, zone) VALUES (?, ?)", (npc_id, zone))
    for line_id, npc, type_, quest, gender, raw in LINES:
        conn.execute("INSERT INTO lines (id, npc_id, type, quest_id, player_gender, raw_text) VALUES (?, ?, ?, ?, ?, ?)",
                     (line_id, npc, type_, quest, gender, raw))
        if line_id != 8:
            ogg = tmp_path / f"src{line_id}.ogg"
            ogg.write_bytes(b"OggS" + bytes(line_id))
            conn.execute("INSERT INTO audio VALUES (?, 'kokoro:x', ?, 1.0, 'done')", (line_id, str(ogg)))
    return conn, packs.load_source(world_db())


EXPECTED = {1: "VoiceForever_Alliance_10-20", 2: "VoiceForever_Alliance_10-20", 3: "VoiceForever_Alliance_1-10",
            4: "VoiceForever_Neutral_40-50", 5: "VoiceForever_Neutral_30-40", 6: "VoiceForever_Horde_1-10",
            7: "VoiceForever_Neutral_1-10", 8: "VoiceForever_Horde_1-10"}


def test_assign_is_deterministic(pipeline):
    conn, src = pipeline
    assert packs.assign(conn, src) == EXPECTED
    assert packs.assign(conn, src) == EXPECTED


def test_package_builds_every_pack_with_audio(pipeline, tmp_path):
    conn, src = pipeline
    stale = tmp_path / "packs" / "VoiceForever_Horde_50-60"
    stale.mkdir(parents=True)
    built = package.package(conn, tmp_path / "packs", src)
    assert sorted(built) == sorted(set(EXPECTED.values()))
    assert not stale.exists()  # a pack left with no audio is removed
    for pack, folder in built.items():
        toc = (folder / f"{pack}.toc").read_text()
        assert "## Dependencies: VoiceForever" in toc and "index.lua" in toc
        index = (folder / "index.lua").read_text()
        assert index.startswith(f'VoiceForever.RegisterPack("{pack}"')
        mine = {i for i, p in EXPECTED.items() if p == pack and i != 8}
        assert {int(f.stem) for f in (folder / "audio").rglob("*.ogg")} == mine
    alliance = (built["VoiceForever_Alliance_10-20"] / "index.lua").read_text()
    assert "[783] = { complete = " in alliance and "gossip" not in alliance
    horde = (built["VoiceForever_Horde_1-10"] / "index.lua").read_text()
    assert "[785]" in horde and "[3143]" not in horde  # its gossip line has no audio yet
    narrator = (built["VoiceForever_Neutral_1-10"] / "index.lua").read_text()
    assert "narrator = true" in narrator
    assert (built["VoiceForever_Neutral_1-10"] / "audio" / "narrator" / "7.ogg").exists()


def test_package_one_pack_leaves_the_others(pipeline, tmp_path):
    conn, src = pipeline
    package.package(conn, tmp_path / "packs", src)
    built = package.package(conn, tmp_path / "packs", src, only="VoiceForever_Horde_1-10")
    assert list(built) == ["VoiceForever_Horde_1-10"]
    assert (tmp_path / "packs" / "VoiceForever_Alliance_1-10" / "index.lua").exists()


def test_report_counts_lines_per_pack(pipeline):
    conn, src = pipeline
    report = package.report(conn, src)
    assert report["VoiceForever_Horde_1-10"] == {"lines": 2, "voiced": 1, "mb": pytest.approx(10 / 1e6)}
    assert sum(r["lines"] for r in report.values()) == len(LINES)


def test_core_addon_loads_packs_together(pipeline, tmp_path, lua):
    conn, src = pipeline
    built = package.package(conn, tmp_path / "packs", src)
    loads = " ".join(f"dofile({lua_literal(str(f / 'index.lua'))})" for f in built.values())
    (packs_loaded, played) = lua(f"""
      load_addon()
      {loads}
      WOW.quest, WOW.text.quest = 783, "Kill the kobolds."
      fire("QUEST_DETAIL") fire("QUEST_FINISHED")
      WOW.quest, WOW.text.quest = 785, "For the Horde."
      fire("QUEST_DETAIL") fire("QUEST_FINISHED")
      WOW.npc.guid = "Creature-0-3767-0-12-2663-0000ABCDEF"
      WOW.text.gossip = "Welcome to Booty Bay."
      fire("GOSSIP_SHOW")
      emit(VoiceForever.packs) emit(WOW.played)
    """)
    assert sorted(packs_loaded) == sorted(built)
    assert [p.split("\\")[2] for p in played] == [
        "VoiceForever_Alliance_10-20", "VoiceForever_Horde_1-10", "VoiceForever_Neutral_30-40"]

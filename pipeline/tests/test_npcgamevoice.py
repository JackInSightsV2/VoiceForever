"""vo.npcgamevoice: NPC -> in-game voice kit -> game voice speaker, and vo.basevoices preferring it."""
import json
import sqlite3

import pytest

from vo import basevoices, cli, db, npcgamevoice as ngv

LISTFILE = """\
100;sound/creature/orcmalestandardnpc/orcmalestandardnpcgreeting01.ogg
101;sound/creature/orcmalestandardnpc/orcmalestandardnpcfarewell01.ogg
102;sound/creature/orcmaleguardnpc/orcmaleguardnpcgreeting01.ogg
103;sound/creature/orcmaleshadynpc/orcmaleshadynpcgreeting01.ogg
104;sound/creature/rat/ratsqueak.ogg
105;sound/creature/ogre/mogreaggro1.ogg
106;sound/creature/humanmalestandardnpc/humanmalestandardnpcgreeting01.ogg
"""
# CreatureDisplayInfo -> NPCSoundID; NPCSounds -> sound kits; SoundKitEntry -> FileDataIDs
DISPLAY = {10: 1, 11: 2, 12: 3, 13: 4, 14: 0, 15: 5}
NPC_SOUNDS = {1: [900, 901], 2: [902], 3: [903], 4: [904], 5: [905, 906]}
ENTRIES = [(900, 100), (901, 101), (902, 102), (903, 103), (904, 104), (905, 105), (906, 106), (906, 106)]


@pytest.fixture
def tables(tmp_path):
    d = tmp_path / "db2"
    d.mkdir()
    (d / "CreatureDisplayInfo.csv").write_text("ID,ModelID,NPCSoundID\n" + "".join(
        f"{i},1,{s}\n" for i, s in DISPLAY.items()))
    (d / "NPCSounds.csv").write_text("ID,SoundID_0,SoundID_1,SoundID_2,SoundID_3\n" + "".join(
        f"{i},{','.join(str(x) for x in (ks + [0, 0, 0, 0])[:4])}\n" for i, ks in NPC_SOUNDS.items()))
    (d / "SoundKitEntry.csv").write_text("ID,SoundKitID,FileDataID,Frequency,Volume,PlayerConditionID\n" + "".join(
        f"{n},{k},{f},1,1,0\n" for n, (k, f) in enumerate(ENTRIES)))
    lf = tmp_path / "listfile.csv"
    lf.write_text(LISTFILE)
    return ngv.load(d, lf)


@pytest.fixture
def anchors(tmp_path):
    root = tmp_path / "anchors"
    (root / "orc_m").mkdir(parents=True)
    (root / "orc_m" / "plan.json").write_text(json.dumps({"anchors": [
        {"archetype": "orc_m", "key": "standard_guard", "folders": ["orcmalestandardnpc", "orcmaleguardnpc"]},
        {"archetype": "orc_m", "key": "shady", "folders": ["orcmaleshadynpc"]}]}))
    return root


def test_kit_of_voice_sets_and_creature_folders_only():
    assert ngv.kit_of("sound/creature/orcmalestandardnpc/orcmalestandardnpcgreeting01.ogg") == ngv.Kit(
        "orc_m", "orcmalestandardnpc")
    assert ngv.kit_of("Sound/Creature/Ogre/MOgreAggro1.ogg") == ngv.Kit("ogre_m", "ogre")
    assert ngv.kit_of("sound/creature/rat/ratsqueak.ogg") is None


def test_display_to_kit(tables):
    assert tables.kit(10) == (1, ngv.Kit("orc_m", "orcmalestandardnpc"))
    assert tables.kit(13) is None  # a rat squeaks: no voice kit
    assert tables.kit(14) is None  # no NPCSoundID
    assert tables.kit(15) == (5, ngv.Kit("human_m", "humanmalestandardnpc"))  # most clips win (2 human, 1 ogre)


def test_speakers_from_plans(anchors):
    assert ngv.speakers(anchors) == {"orcmalestandardnpc": "orc_m/gv-standard_guard",
                                     "orcmaleguardnpc": "orc_m/gv-standard_guard", "orcmaleshadynpc": "orc_m/gv-shady"}


def test_pick_most_probable_display_with_a_kit(tables):
    assert ngv.pick([(14, 80), (12, 20)], tables) == (12, 3, ngv.Kit("orc_m", "orcmaleshadynpc"))
    assert ngv.pick([(10, 0), (11, 0)], tables)[0] == 10  # no probabilities: first slot
    assert ngv.pick([(10, 30), (11, 70)], tables)[0] == 11
    assert ngv.pick([(13, 100)], tables) is None


def _conn(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    conn.executemany("INSERT INTO npcs (id, name, race, gender) VALUES (?, ?, 'Orc', 'male')",
                     [(1, "Grunt"), (2, "Shady"), (3, "Rat"), (4, "New one")])
    conn.commit()
    return conn


def _world():
    w = sqlite3.connect(":memory:")
    w.row_factory = sqlite3.Row
    w.execute("CREATE TABLE creature_template (entry, patch, display_id1, display_id2, display_id3, display_id4,"
              " display_probability1, display_probability2, display_probability3, display_probability4)")
    w.executemany("INSERT INTO creature_template VALUES (?, 0, ?, 0, 0, 0, 100, 0, 0, 0)", [(1, 11), (2, 12), (3, 13)])
    return w


def test_refresh_and_preferred(tmp_path, tables, anchors):
    conn = _conn(tmp_path)
    conn.execute("INSERT INTO wowhead_npcs (id, display_id) VALUES (4, 10)")  # Wowhead-only NPC: its page's display
    conn.executemany("INSERT INTO candidates (id, archetype, status) VALUES (?, 'orc_m', ?)",
                     [("orc_m/gv-shady", "pending"), ("orc_m/gv-shady~v1", "approved"),
                      ("orc_m/gv-shady~v1~v2", "pending"), ("orc_m/g0s1", "approved")])
    c = ngv.refresh(conn, _world(), tables, ngv.speakers(anchors))
    assert (c["npcs"], c["mapped"], c["speaker"]) == (4, 3, 3)
    rows = {r["npc_id"]: dict(r) for r in conn.execute("SELECT * FROM npc_game_voice")}
    assert rows[1]["kit"] == "orcmaleguardnpc" and rows[1]["speaker_candidate_id"] == "orc_m/gv-standard_guard"
    assert rows[4]["source"] == "wowhead" and rows[4]["display_id"] == 10
    assert 3 not in rows
    pref = ngv.preferred(conn)
    assert pref[2] == ["orc_m/gv-shady", "orc_m/gv-shady~v1", "orc_m/gv-shady~v1~v2"]
    # idempotent
    assert ngv.refresh(conn, _world(), tables, ngv.speakers(anchors)) == c


def test_assign_prefers_own_game_voice_and_keeps_neighbour_awareness():
    bases = {"orc_m": ["orc_m/g0s1", "orc_m/gv-shady~v1", "orc_m/gv-shady~v2", "orc_m/gv-standard_guard"]}
    npc_arch = {n: "orc_m" for n in range(1, 7)}
    adjacent = {1: {2}, 2: {1}}
    pref = {1: ["orc_m/gv-shady", "orc_m/gv-shady~v1", "orc_m/gv-shady~v2"],
            2: ["orc_m/gv-shady", "orc_m/gv-shady~v1", "orc_m/gv-shady~v2"],
            3: ["orc_m/gv-standard_guard"],
            4: ["orc_m/gv-grim"]}  # not a Base Voice: spread as usual
    got = basevoices.assign(npc_arch, bases, adjacent, preferred=pref)
    assert {got[1], got[2]} == {"orc_m/gv-shady~v1", "orc_m/gv-shady~v2"}  # own voice, and Neighbours differ
    assert got[3] == "orc_m/gv-standard_guard"
    # A sticky Base Voice that isn't the NPC's own game voice is re-assigned; one that is stays.
    assert basevoices.assign(npc_arch, bases, {}, {3: "orc_m/g0s1"}, pref)[3] == "orc_m/gv-standard_guard"
    assert basevoices.assign(npc_arch, bases, {}, {1: "orc_m/gv-shady~v2"}, pref)[1] == "orc_m/gv-shady~v2"
    # Without preferences, unchanged behaviour.
    assert basevoices.assign(npc_arch, bases, adjacent) == basevoices.assign(npc_arch, bases, adjacent, preferred={})


def test_cli_npc_game_voices(tmp_path, tables, anchors, monkeypatch, capsys):
    conn = _conn(tmp_path)
    conn.close()
    monkeypatch.setattr(ngv, "load", lambda d, lf: tables)
    wpath = tmp_path / "world.sqlite"
    w = sqlite3.connect(wpath)
    w.executescript("\n".join(_world().iterdump()))
    w.close()
    cli.main(["--db", str(tmp_path / "vo.sqlite"), "--world", str(wpath), "npc-game-voices",
              "--anchors", str(anchors)])
    out = capsys.readouterr().out
    assert "2 of 4 NPCs have an in-game voice kit" in out and "2 of them a game voice speaker" in out

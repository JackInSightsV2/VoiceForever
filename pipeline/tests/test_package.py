import pytest

from conftest import CORE, lua_literal
from vo import db, drift, install, package

PACK = "VoiceForever_Neutral_1-10"  # NPCs with no faction template or zone


def test_build_index_files_genderless_line_under_both_genders():
    index = package.build_index([
        (783, "quest_detail", None, "a.ogg", "h0"),
        (9, "quest_detail", "m", "m.ogg", "hm"),
        (9, "quest_detail", "f", "f.ogg", "hf"),
    ])
    a = {"file": "a.ogg", "hash": "h0"}
    assert index == {783: {"detail": {"m": a, "f": a}},
                     9: {"detail": {"m": {"file": "m.ogg", "hash": "hm"}, "f": {"file": "f.ogg", "hash": "hf"}}}}


def test_lua_index():
    entry = {"file": "Interface\\AddOns\\P\\a.ogg", "hash": "0badf00d"}
    assert package.lua_index("P", {783: {"detail": {"m": entry}}}) == (
        'VoiceForever.RegisterPack("P", {\n'
        "  quests = {\n"
        '    [783] = { detail = { m = { file = "Interface\\\\AddOns\\\\P\\\\a.ogg", hash = "0badf00d" } } },\n'
        "  },\n"
        "})\n")


@pytest.fixture
def built_pack(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    ogg = tmp_path / "src.ogg"
    ogg.write_bytes(b"OggS")
    conn.execute("INSERT INTO lines (id, npc_id, type, quest_id, raw_text) VALUES (1, 823, 'quest_detail', 783, 'x')")
    conn.execute("INSERT INTO audio VALUES (1, 'kokoro:am_michael', ?, 1.0, 'done')", (str(ogg),))
    return package.package(conn, tmp_path / "packs")[PACK]


def test_package_writes_addon(built_pack):
    assert (built_pack / "audio" / "823" / "1.ogg").read_bytes() == b"OggS"
    assert "## Dependencies: VoiceForever" in (built_pack / f"{PACK}.toc").read_text()
    index = (built_pack / "index.lua").read_text()
    assert '"Interface\\\\AddOns\\\\VoiceForever_Neutral_1-10\\\\audio\\\\823\\\\1.ogg"' in index
    assert f'hash = "{drift.text_hash("x")}"' in index


def test_core_addon_plays_pack_audio_on_quest_detail(built_pack, lua):
    (played, stopped, captured) = lua(f"""
      load_addon()
      dofile({lua_literal(str(built_pack / "index.lua"))})
      WOW.quest, WOW.text.quest = 783, "x"
      fire("QUEST_DETAIL")
      fire("QUEST_FINISHED")
      WOW.quest = 1
      fire("QUEST_DETAIL")
      emit(WOW.played) emit(WOW.stopped) emit(#VoiceForeverDB.capture)
    """)
    assert played == ["Interface\\AddOns\\VoiceForever_Neutral_1-10\\audio\\823\\1.ogg|Dialog"]
    assert stopped == [42]
    assert captured == 1  # quest 1 is a miss; quest 783 matched its hash


def test_install_symlinks_core_and_packs(built_pack, tmp_path):
    addons = tmp_path / "AddOns"
    addons.mkdir()
    links = install.install(addons, CORE, built_pack.parent)
    assert [l.name for l in links] == ["VoiceForever", PACK]
    assert (addons / PACK).resolve() == built_pack.resolve()
    install.install(addons, CORE, built_pack.parent)  # re-run replaces links


def test_install_refuses_real_folder(built_pack, tmp_path):
    addons = tmp_path / "AddOns"
    (addons / "VoiceForever").mkdir(parents=True)
    with pytest.raises(FileExistsError):
        install.install(addons, CORE, built_pack.parent)


# --- every quest part, both genders, and the Narrator -----------------------------------------------------------------

PROGRESS = "Have you slain them yet, $gsir:madam;?"
# (line id, npc id, type, quest id, player gender, raw text); npc NULL = Narrator (object or item quest)
PART_LINES = [
    (1, 823, "quest_detail", 783, None, "Kill the kobolds, $N."),
    (2, 823, "quest_progress", 783, "m", PROGRESS),
    (3, 823, "quest_progress", 783, "f", PROGRESS),
    (4, 823, "quest_complete", 783, None, "Well done, $C!"),
    (5, None, "quest_detail", 176, None, "Wanted: Hogger."),
    (6, None, "quest_complete", 176, None, "The poster has been claimed."),
]


@pytest.fixture
def parts_pack(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    for line_id, npc, type_, quest, gender, raw in PART_LINES:
        ogg = tmp_path / f"src{line_id}.ogg"
        ogg.write_bytes(b"OggS")
        conn.execute("INSERT INTO lines (id, npc_id, type, quest_id, player_gender, raw_text) VALUES (?, ?, ?, ?, ?, ?)",
                     (line_id, npc, type_, quest, gender, raw))
        conn.execute("INSERT INTO audio VALUES (?, 'kokoro:x', ?, 1.0, 'done')", (line_id, str(ogg)))
    return package.package(conn, tmp_path / "packs")[PACK]


def _file(npc, line_id):
    return f"Interface\\AddOns\\{PACK}\\audio\\{npc}\\{line_id}.ogg"


def test_package_indexes_every_part_and_narrator_lines(parts_pack):
    index = (parts_pack / "index.lua").read_text()
    q783 = next(l for l in index.splitlines() if l.strip().startswith("[783]"))
    assert all(part in q783 for part in ("detail =", "progress =", "complete ="))
    for gender, line_id in (("m", 2), ("f", 3)):
        h = drift.text_hash(PROGRESS, gender)
        assert f'{gender} = {{ file = {package._lua_str(_file(823, line_id))}, hash = "{h}" }}' in q783
    q176 = next(l for l in index.splitlines() if l.strip().startswith("[176]"))
    assert "detail =" in q176 and "complete =" in q176 and "progress" not in q176
    assert (parts_pack / "audio" / "narrator" / "5.ogg").exists()
    assert (parts_pack / "audio" / "narrator" / "6.ogg").exists()


def _play(lua, parts_pack, setup: str):
    return lua(f"""
      load_addon()
      dofile({lua_literal(str(parts_pack / "index.lua"))})
      {setup}
      emit(WOW.played) emit(VoiceForeverDB.capture)
    """)


@pytest.mark.parametrize("sex,gender,progress_line", [(2, "m", 2), (3, "f", 3)])
def test_addon_plays_every_quest_part_for_each_gender(parts_pack, lua, sex, gender, progress_line):
    (played, captured) = _play(lua, parts_pack, f"""
      WOW.player.sex, WOW.player.class, WOW.quest = {sex}, "Warrior", 783
      WOW.text.quest, WOW.text.objective = "Kill the kobolds, Jack.", ""
      fire("QUEST_DETAIL") fire("QUEST_FINISHED")
      WOW.text.progress = {lua_literal("Have you slain them yet, " + ("sir" if gender == "m" else "madam") + "?")}
      fire("QUEST_PROGRESS") fire("QUEST_FINISHED")
      WOW.text.reward = "Well done, Warrior!"
      fire("QUEST_COMPLETE")
    """)
    assert played == [_file(823, 1) + "|Dialog", _file(823, progress_line) + "|Dialog", _file(823, 4) + "|Dialog"]
    assert captured == []  # every part matched its hash: no Drift, no miss


def test_addon_drift_on_progress_and_complete_still_plays(parts_pack, lua):
    (played, captured) = _play(lua, parts_pack, """
      WOW.quest = 783
      WOW.text.progress = "Are the kobolds dead yet?"
      fire("QUEST_PROGRESS")
      WOW.text.reward = "Splendid work."
      fire("QUEST_COMPLETE")
    """)
    assert played == [_file(823, 3) + "|Dialog", _file(823, 4) + "|Dialog"]  # stub player is female (sex 3)
    assert [(r["kind"], r["event"], r["questId"], r["expected"]) for r in captured] == [
        ("drift", "QUEST_PROGRESS", 783, drift.text_hash(PROGRESS, "f")),
        ("drift", "QUEST_COMPLETE", 783, drift.text_hash("Well done, $C!"))]


def test_addon_plays_narrator_for_game_object_quest(parts_pack, lua):
    (played, captured) = _play(lua, parts_pack, """
      WOW.npc = { guid = "GameObject-0-3767-0-12-68-0000ABCDEF", name = "Wanted Poster" }
      WOW.quest, WOW.text.quest, WOW.text.objective = 176, "Wanted: Hogger.", ""
      fire("QUEST_DETAIL") fire("QUEST_FINISHED")
      WOW.text.reward = "The poster has been claimed."
      fire("QUEST_COMPLETE")
    """)
    assert played == [_file("narrator", 5) + "|Dialog", _file("narrator", 6) + "|Dialog"]
    assert captured == []


def test_addon_plays_narrator_with_no_npc_unit(parts_pack, lua):
    """Item-started quests: no "npc" unit at all (UnitGUID("npc") is nil)."""
    (played, captured) = _play(lua, parts_pack, """
      WOW.npc = nil
      WOW.quest, WOW.text.quest, WOW.text.objective = 176, "Wanted: Hogger, a gnoll.", ""
      fire("QUEST_DETAIL")
    """)
    assert played == [_file("narrator", 5) + "|Dialog"]
    (r,) = captured  # reworded: Drift is still recorded without an NPC
    assert (r["kind"], r["questId"]) == ("drift", 176)
    assert "npcId" not in r and "guidType" not in r and "npcName" not in r

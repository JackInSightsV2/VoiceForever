import pytest

from conftest import CORE, lua_literal
from vo import db, drift, install, package

PACK = "VoiceForever_Test"


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
    return package.package(conn, tmp_path / "packs", PACK)


def test_package_writes_addon(built_pack):
    assert (built_pack / "audio" / "823" / "1.ogg").read_bytes() == b"OggS"
    assert "## Dependencies: VoiceForever" in (built_pack / f"{PACK}.toc").read_text()
    index = (built_pack / "index.lua").read_text()
    assert '"Interface\\\\AddOns\\\\VoiceForever_Test\\\\audio\\\\823\\\\1.ogg"' in index
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
    assert played == ["Interface\\AddOns\\VoiceForever_Test\\audio\\823\\1.ogg|Dialog"]
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

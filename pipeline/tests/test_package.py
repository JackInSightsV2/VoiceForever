import shutil
import subprocess
from pathlib import Path

import pytest

from vo import db, install, package

CORE = Path(__file__).resolve().parents[2] / "addon" / "VoiceForever"
PACK = "VoiceForever_Test"


def test_build_index_files_genderless_line_under_both_genders():
    index = package.build_index([
        (783, "quest_detail", None, "a.ogg"),
        (9, "quest_detail", "m", "m.ogg"),
        (9, "quest_detail", "f", "f.ogg"),
    ])
    assert index == {783: {"detail": {"m": "a.ogg", "f": "a.ogg"}}, 9: {"detail": {"m": "m.ogg", "f": "f.ogg"}}}


def test_lua_index():
    assert package.lua_index("P", {783: {"detail": {"m": "Interface\\AddOns\\P\\a.ogg"}}}) == (
        'VoiceForever.RegisterPack("P", {\n'
        "  quests = {\n"
        '    [783] = { detail = { m = "Interface\\\\AddOns\\\\P\\\\a.ogg" } },\n'
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
    assert '"Interface\\\\AddOns\\\\VoiceForever_Test\\\\audio\\\\823\\\\1.ogg"' in (built_pack / "index.lua").read_text()


HARNESS = """
local played, stopped, handler = {}, {}, nil
function CreateFrame() return { RegisterEvent = function() end, SetScript = function(_, _, f) handler = f end } end
function PlaySoundFile(path, channel) played[#played + 1] = path .. "|" .. channel; return true, 42 end
function StopSound(h) stopped[#stopped + 1] = h end
function GetQuestID() return QUEST end
function UnitSex() return SEX end
SlashCmdList = {}
dofile(CORE)
dofile(INDEX)
QUEST, SEX = 783, 3; handler(nil, "QUEST_DETAIL")
handler(nil, "QUEST_FINISHED")
QUEST = 1; handler(nil, "QUEST_DETAIL")
print(table.concat(played, ","), table.concat(stopped, ","))
"""


@pytest.mark.skipif(not shutil.which("lua"), reason="lua not installed")
def test_core_addon_plays_pack_audio_on_quest_detail(built_pack):
    script = f'CORE, INDEX = "{CORE / "VoiceForever.lua"}", "{built_pack / "index.lua"}"\n' + HARNESS
    out = subprocess.run(["lua", "-"], input=script, capture_output=True, text=True, check=True).stdout
    assert out == "Interface\\AddOns\\VoiceForever_Test\\audio\\823\\1.ogg|Dialog\t42\n"


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

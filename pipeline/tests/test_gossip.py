"""Gossip template matching: patterns (vo.gossip), packaging, and the Core Addon's match and playback."""
import pytest

import gossip_harness
from conftest import lua_literal
from vo import db, gossip, package

PACK = "VoiceForever_Neutral_1-10"  # NPCs with no faction template or zone


def test_pattern_escapes_magic_and_wildcards_tokens():
    raw = "Hey, $n!  50% off (today) [only]... a $R $c?$B$BCost: $2063w + $C-ish ^_^ $N's."
    assert gossip.match_pattern(raw) == (
        "^Hey, .-! 50%% off %(today%) %[only%]%.%.%. a .- .-%? Cost: .- %+ .-%-ish %^_%^ .-'s%.$")


def test_pattern_resolves_gender_and_normalises():
    raw = "  Well met, $gsir:madam;.\n\t|cffff0000Take care|r, $Glad : lass;!$b"
    assert gossip.match_pattern(raw, "m") == "^Well met, sir%. Take care, lad !$"
    assert gossip.match_pattern(raw, "f") == "^Well met, madam%. Take care, lass!$"
    with pytest.raises(ValueError):
        gossip.match_pattern(raw)


def test_build_index_groups_by_npc_and_keeps_gender():
    index = gossip.build_index([
        (197, None, "^.-, hello%.$", "a.ogg"),
        (197, "f", "^Hello there, madam%.$", "f.ogg"),
        (5, None, "^x$", "x.ogg"),
    ])
    assert index == {
        197: [{"pattern": "^.-, hello%.$", "file": "a.ogg"},
              {"pattern": "^Hello there, madam%.$", "file": "f.ogg", "gender": "f"}],
        5: [{"pattern": "^x$", "file": "x.ogg"}]}


def test_update_patterns_sets_gossip_lines_only(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    conn.executemany("INSERT INTO lines (id, npc_id, type, player_gender, raw_text) VALUES (?, 197, ?, ?, ?)", [
        (1, "gossip", None, "Hail, $c."), (2, "quest_greeting", "f", "Hi $gsir:madam;."), (3, "quest_detail", None, "Q")])
    assert gossip.update_patterns(conn) == 2
    assert gossip.update_patterns(conn) == 0
    assert [r[0] for r in conn.execute("SELECT match_pattern FROM lines ORDER BY id")] == [
        "^Hail, .-%.$", "^Hi madam%.$", None]


def test_lua_index_gossip_section():
    lua = package.lua_index("P", {}, {197: [{"pattern": "^Hi, .-%.$", "file": "a.ogg", "gender": "m"}]})
    assert lua == ('VoiceForever.RegisterPack("P", {\n  quests = {\n  },\n  gossip = {\n    [197] = {\n'
                   '      { pattern = "^Hi, .-%.$", file = "a.ogg", gender = "m" },\n    },\n  },\n})\n')


# --- Core Addon -------------------------------------------------------------------------------------------------

GOSSIP = "Hey, citizen!  $N, you look like a stout $gman:woman;.$B$BWe guards are spread thin."
GREETING = "Greetings, $c. I have several tasks."


@pytest.fixture
def built_pack(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    ogg = tmp_path / "src.ogg"
    ogg.write_bytes(b"OggS")
    rows = [(1, 197, "gossip", "m", GOSSIP), (2, 197, "gossip", "f", GOSSIP), (3, 197, "quest_greeting", None, GREETING),
            (4, 823, "quest_detail", None, "x")]
    conn.executemany("INSERT INTO lines (id, npc_id, type, player_gender, raw_text, quest_id) VALUES (?, ?, ?, ?, ?, 1)",
                     rows)
    gossip.update_patterns(conn)
    conn.executemany("INSERT INTO audio VALUES (?, 'kokoro:am_michael', ?, 1.0, 'done')", [(r[0], str(ogg)) for r in rows])
    return package.package(conn, tmp_path / "packs")[PACK]


def audio(line_id: int) -> str:
    return f"Interface\\AddOns\\{PACK}\\audio\\197\\{line_id}.ogg|Dialog"


def play(built_pack, lua, body: str) -> list:
    return lua(f"""
      load_addon()
      dofile({lua_literal(str(built_pack / "index.lua"))})
      WOW.npc.guid = "Creature-0-3767-0-12-197-0000ABCDEF"
      WOW.text.gossip = "Hey, citizen!  Jack, you look like a stout woman.\\n\\nWe guards are  spread thin."
      WOW.text.greeting = "Greetings, |cffffffffwarrior|r. I have several tasks."
      {body}
      emit(WOW.played) emit(WOW.stopped) emit(VoiceForeverDB.capture)
    """)


def test_pack_ships_gossip_audio(built_pack):
    assert (built_pack / "audio" / "197" / "3.ogg").read_bytes() == b"OggS"
    assert "gossip = {" in (built_pack / "index.lua").read_text()


def test_gossip_show_plays_match_for_player_gender(built_pack, lua):
    played, stopped, records = play(built_pack, lua, """
      fire("GOSSIP_SHOW")
      fire("GOSSIP_CLOSED")
      WOW.player.sex = 2
      WOW.text.gossip = WOW.text.gossip:gsub("woman", "man")
      fire("GOSSIP_SHOW")
    """)
    assert played == [audio(2), audio(1)]
    assert stopped == [42]
    assert records == []


def test_quest_greeting_plays_match(built_pack, lua):
    played, _, records = play(built_pack, lua, 'fire("QUEST_GREETING")')
    assert played == [audio(3)]
    assert records == []


def test_gossip_miss_is_captured(built_pack, lua):
    played, _, records = play(built_pack, lua, """
      WOW.text.gossip = "Something Forever added."
      fire("GOSSIP_SHOW")
      WOW.player.sex = 2  -- the female line's text, shown to a male player, is no match
      WOW.text.gossip = "Hey, citizen! Jack, you look like a stout woman. We guards are spread thin."
      fire("GOSSIP_SHOW")
      WOW.npc.guid = "GameObject-0-3767-0-12-197-0000ABCDEF"
      fire("QUEST_GREETING")
    """)
    assert played == []
    assert [(r["event"], r.get("npcId"), r["text"][:9]) for r in records] == [
        ("GOSSIP_SHOW", 197, "Something"), ("GOSSIP_SHOW", 197, "Hey, citi"), ("QUEST_GREETING", None, "Greetings")]


@pytest.mark.parametrize("locale", ["deDE", "frFR", "zhCN"])
def test_non_english_skips_gossip_but_not_quests(built_pack, lua, locale):
    played, _, records = play(built_pack, lua, f"""
      WOW.locale = "{locale}"
      fire("GOSSIP_SHOW") fire("QUEST_GREETING")
      WOW.quest, WOW.text.quest = 1, "x" fire("QUEST_DETAIL")
    """)
    assert played == [f"Interface\\AddOns\\{PACK}\\audio\\823\\4.ogg|Dialog"]
    assert records == []


def test_most_specific_pattern_wins_across_packs(lua):
    (picked, literal) = lua("""
      load_addon()
      VoiceForever.RegisterPack("A", { gossip = { [7] = { { pattern = "^Hello, .-%.$", file = "general" },
                                                          { pattern = "^.-$", file = "any" } } } })
      VoiceForever.RegisterPack("B", { gossip = { [7] = { { pattern = "^Hello, friend%.$", file = "exact" } } } })
      local M = VoiceForever.MatchGossip
      emit({ M(7, "Hello, Jack.", "m").file, M(7, "Hello,  friend.", "f").file, M(7, "Bye.", "f").file,
             M(8, "Hello, Jack.", "m") == nil })
      local L = VoiceForever.PatternLiteral
      emit({ L("^Hi, .-%.$"), L("^.-$"), L("^%%$"), L("^a%..-b$") })
    """)
    assert picked == ["general", "exact", "any", True]
    assert literal == [5, 0, 1, 3]


# --- Harness ------------------------------------------------------------------------------------------------------

def test_harness_render_is_client_like():
    raw = "$Gsir:madam;, $n the $r $C.$B$2063w left."
    assert gossip_harness.render(raw, "f", "Jaína", "Night Elf", "Druid") == "madam, Jaína the night elf Druid.\n1234 left."


def test_harness_on_tricky_lines(lua):
    lines = [(i + 1, 1, g, raw, None) for i, (g, raw) in enumerate([
        (None, "Hail, $c."), (None, "Hail, druid."),  # the second is ambiguous with the first, and more specific
        (None, "100% (really) [sure]... $N? ^_^ $b$B -- $r$c"),
        ("m", "Hi $gsir:madam;, $n."), ("f", "Hi $gsir:madam;, $n."),
        (None, "|cff00ff00$N|r's  $2063w\n coins"),
    ])]
    stats = gossip_harness.run(lines, lua)
    assert stats["renderings"] == 4 * 6 + 2 * 3
    assert stats["failures"] == []
    # "Hail, druid." (all 6 of line 2's renderings, and line 1's for a druid) matches both; the literal line wins.
    assert (stats["ambiguous_renderings"], stats["ambiguous_lines"]) == (8, 2)
    assert stats["shadowed"] == [["1", "2"], ["1", "2"]]
    assert stats["matched"] == stats["renderings"] - 2


def test_harness_every_gossip_line_in_source_data(tmp_path, lua):
    """Every Gossip line of a fresh Core Content extraction matches its own rendering; skipped without data/."""
    from vo import cli, extract, source
    if not cli.DEFAULT_WORLD.exists():
        pytest.skip("Source Data not fetched (vo fetch)")
    conn, world = db.connect(tmp_path / "vo.sqlite"), source.open_world(cli.DEFAULT_WORLD)
    extract.extract_all(conn, world, source.load_questie(cli.DATA), source.load_displays(cli.DATA, world))
    lines = gossip_harness.gossip_lines(conn)
    assert all(l[4] for l in lines)  # extraction set every match_pattern
    stats = gossip_harness.run(lines, lua)
    print({k: v for k, v in stats.items() if k not in ("failures", "shadowed")})
    assert stats["lines"] > 5000
    assert stats["failures"] == [] and stats["shadowed"] == []

"""Core Addon Drift and Capture, run in Lua against the stubbed WoW API (wowstub.lua)."""
from conftest import lua_literal
from vo import drift

SOURCE = "Young $c, there is work.$B$BSpeak with the Marshal, $N."
PACK = f"""
VoiceForever.RegisterPack("VoiceForever_Test", {{ quests = {{
  [783] = {{ detail = {{ f = {{ file = "a.ogg", hash = {lua_literal(drift.text_hash(SOURCE))} }} }} }},
}} }})
WOW.quest = 783
WOW.text.quest = "Young warrior, there is work.\\n\\nSpeak with the Marshal, Jack."
"""


def with_pack(body: str) -> str:
    return "load_addon()\n" + PACK + body


def test_matching_text_plays_without_capture(lua):
    (played, records) = lua(with_pack('fire("QUEST_DETAIL") emit(WOW.played) emit(VoiceForeverDB.capture)'))
    assert played == ["a.ogg|Dialog"]
    assert records == []


def test_drift_still_plays_and_is_captured(lua):
    changed = "Young warrior, the work has changed. Speak with the Marshal, Jack."
    (played, records) = lua(with_pack(f"""
      WOW.text.quest = {lua_literal(changed)}
      fire("QUEST_DETAIL")
      emit(WOW.played) emit(VoiceForeverDB.capture)
    """))
    assert played == ["a.ogg|Dialog"]
    (r,) = records
    assert (r["kind"], r["event"], r["questId"], r["text"]) == ("drift", "QUEST_DETAIL", 783, changed + "\n\n")
    assert r["expected"] == drift.text_hash(SOURCE)
    assert r["hash"] == drift.text_hash("Young $c, the work has changed. Speak with the Marshal, $N.")


def test_miss_captures_full_record(lua):
    (records, played) = lua("""
      load_addon()
      WOW.quest, WOW.text.quest, WOW.text.objective = 1, "Slay |cffff0000Hogger|r, Jack.", "Kill Hogger."
      fire("QUEST_DETAIL")
      emit(VoiceForeverDB.capture) emit(WOW.played)
    """)
    assert played == []
    assert records == [{
        "kind": "miss", "event": "QUEST_DETAIL", "questId": 1,
        "npcId": 823, "guidType": "Creature", "npcName": "Marshal McBride", "unitSex": 2, "creatureType": "Humanoid",
        "zone": 1429, "x": 0.4821, "y": 0.4199, "locale": "enUS",
        "text": "Slay |cffff0000Hogger|r, Jack.\n\nKill Hogger.",
        "hash": drift.fnv1a32(b"Slay Hogger, $N. Kill Hogger."), "seenAt": 1790000000,
    }]


def test_every_interaction_event_captures_misses(lua):
    (records,) = lua("""
      load_addon()
      WOW.text = { quest = "d", objective = "", progress = "p", reward = "r", greeting = "g", gossip = "s" }
      for _, e in ipairs({ "QUEST_DETAIL", "QUEST_PROGRESS", "QUEST_COMPLETE", "QUEST_GREETING", "GOSSIP_SHOW" }) do
        fire(e)
      end
      emit(VoiceForeverDB.capture)
    """)
    assert [(r["event"], r["text"], r.get("questId")) for r in records] == [
        ("QUEST_DETAIL", "d\n\n", 0), ("QUEST_PROGRESS", "p", 0), ("QUEST_COMPLETE", "r", 0),
        ("QUEST_GREETING", "g", None), ("GOSSIP_SHOW", "s", None)]


def test_object_quest_has_no_npc_id(lua):
    (records,) = lua("""
      load_addon()
      WOW.npc = { guid = "GameObject-0-3767-0-12-4567-0000ABCDEF", name = "Wanted Poster" }
      fire("QUEST_DETAIL")
      emit(VoiceForeverDB.capture)
    """)
    (r,) = records
    assert "npcId" not in r
    assert (r["guidType"], r["npcName"]) == ("GameObject", "Wanted Poster")


def test_capture_deduplicates_by_npc_event_quest_and_hash(lua):
    (records,) = lua("""
      load_addon()
      WOW.text.gossip = "Hello."
      fire("GOSSIP_SHOW") fire("GOSSIP_SHOW")
      WOW.now = WOW.now + 60; fire("GOSSIP_SHOW")
      WOW.text.gossip = "Hello, Jack."
      fire("GOSSIP_SHOW")
      WOW.npc.guid = "Creature-0-3767-0-12-824-0000ABCDEF"
      fire("GOSSIP_SHOW")
      emit(VoiceForeverDB.capture)
    """)
    assert [(r["npcId"], r["text"]) for r in records] == [(823, "Hello."), (823, "Hello, Jack."), (824, "Hello, Jack.")]


def test_capture_is_capped_and_survives_reload(lua):
    (count, first, last, dup) = lua("""
      VoiceForeverDB = { capture = {} }
      for i = 1, 5000 do
        VoiceForeverDB.capture[i] = { npcId = i, event = "GOSSIP_SHOW", hash = "00000000", text = "old" }
      end
      load_addon()
      local C = VoiceForeverDB.capture
      WOW.npc.guid = "Creature-0-1-0-1-2-0"  -- npcId 2, already saved: a duplicate
      WOW.text.gossip = "x"
      local before = VoiceForever.Capture.Record("miss", "GOSSIP_SHOW", nil, "x", "00000000")
      fire("GOSSIP_SHOW")
      emit(#C) emit(C[1].npcId) emit(C[#C].text) emit(before)
    """)
    assert (count, first, last, dup) == (5000, 2, "x", False)


def test_export_prints_saved_variables_location(lua):
    (printed,) = lua("""
      load_addon()
      fire("GOSSIP_SHOW")
      SlashCmdList.VOICEFOREVER("export")
      emit(WOW.printed)
    """)
    assert printed[0].startswith("VoiceForever: 1 Capture records (0 Drift)")
    assert printed[1].endswith("\\WTF\\Account\\<ACCOUNT>\\SavedVariables\\VoiceForever.lua")

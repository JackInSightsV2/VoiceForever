"""Core Addon settings, replay, stop conditions and partial pack installs, in Lua against wowstub.lua."""
import pytest

# Two packs: quest 783 (NPC 823, every part) in Alliance 1-10, quest 176 (Narrator) and NPC 823's gossip in
# Neutral 1-10.
ALLIANCE = """
VoiceForever.RegisterPack("VoiceForever_Alliance_1-10", { quests = {
  [783] = { detail = { f = { file = "detail.ogg" }, m = { file = "detail.ogg" } },
            progress = { f = { file = "progress.ogg" } }, complete = { f = { file = "complete.ogg" } } },
} })
"""
NEUTRAL = """
VoiceForever.RegisterPack("VoiceForever_Neutral_1-10", {
  quests = { [176] = { detail = { f = { file = "narrator.ogg", narrator = true } } } },
  gossip = { [823] = { { pattern = "^Hello%.$", file = "gossip.ogg" } } },
})
"""
SETUP = 'WOW.quest = 783; WOW.text.gossip = "Hello."; WOW.text.greeting = "Hello."'


def run(lua, body: str, packs=(ALLIANCE, NEUTRAL), before: str = "") -> list:
    return lua(before + "\nload_addon()\n" + "\n".join(packs) + "\n" + SETUP + "\n" + body)


def played(lua, body: str, **kw) -> list[str]:
    (out,) = run(lua, body + "\nemit(WOW.played)", **kw)
    return [p.removesuffix("|Dialog") for p in out]


# --- Voice Packs: any subset ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("installed,expected", [
    ((ALLIANCE, NEUTRAL), ["detail.ogg", "gossip.ogg"]),
    ((ALLIANCE,), ["detail.ogg"]),
    ((NEUTRAL,), ["gossip.ogg"]),
    ((), []),
])
def test_any_subset_of_packs(lua, installed, expected):
    (out, captured) = run(lua, """
      fire("QUEST_DETAIL") fire("QUEST_FINISHED")
      fire("GOSSIP_SHOW") fire("GOSSIP_CLOSED")
      emit(WOW.played) emit(VoiceForeverDB.capture)
    """, packs=installed)
    assert [p.removesuffix("|Dialog") for p in out] == expected
    assert len(captured) == 2 - len(expected)  # a missing pack is just a miss, recorded through Capture


def test_packs_merge_the_same_quest(lua):
    """Two packs may each carry parts of one quest; neither overwrites the other."""
    other = 'VoiceForever.RegisterPack("X", { quests = { [783] = { progress = { m = { file = "pm.ogg" } } } } })'
    (quest,) = run(lua, "emit(VoiceForever.quests[783].progress)", packs=(ALLIANCE, other))
    assert quest == {"f": {"file": "progress.ogg"}, "m": {"file": "pm.ogg"}}


# --- saved settings -----------------------------------------------------------------------------------------------

DEFAULTS = {"version": 1, "detail": True, "progress": True, "complete": True, "greeting": True, "gossip": True,
            "narrator": True, "autoPlay": True, "replayButton": True, "englishAudio": True}


def test_defaults_when_nothing_saved(lua):
    (settings,) = run(lua, "emit(VoiceForeverDB.settings)")
    assert settings == DEFAULTS


def test_migration_keeps_valid_values_and_drops_the_rest(lua):
    (settings, capture) = run(lua, "emit(VoiceForeverDB.settings) emit(#VoiceForeverDB.capture)", before="""
      VoiceForeverDB = { capture = { { npcId = 1, event = "GOSSIP_SHOW", hash = "0" } },
                         settings = { gossip = false, autoPlay = "yes", volume = 0.3, stale = 1 } }
    """)
    assert settings == {**DEFAULTS, "gossip": False}
    assert capture == 1  # Capture is untouched


def test_pre_settings_saved_variables_migrate(lua):
    (settings,) = run(lua, "emit(VoiceForeverDB.settings)", before="VoiceForeverDB = { capture = {} }")
    assert settings == DEFAULTS


# --- each setting's effect ------------------------------------------------------------------------------------------

EVERY_LINE = """
  fire("QUEST_DETAIL") fire("QUEST_PROGRESS") fire("QUEST_COMPLETE") fire("QUEST_FINISHED")
  fire("QUEST_GREETING") fire("QUEST_FINISHED")
  fire("GOSSIP_SHOW") fire("GOSSIP_CLOSED")
  WOW.quest, WOW.npc = 176, nil
  fire("QUEST_DETAIL")
"""
GREETING = """
VoiceForever.RegisterPack("G", { gossip = { [823] = { { pattern = "^Greetings%.$", file = "greeting.ogg" } } } })
"""
ALL = ["detail.ogg", "progress.ogg", "complete.ogg", "greeting.ogg", "gossip.ogg", "narrator.ogg"]


@pytest.mark.parametrize("key,missing", [
    ("detail", ["detail.ogg", "narrator.ogg"]), ("progress", ["progress.ogg"]), ("complete", ["complete.ogg"]),
    ("greeting", ["greeting.ogg"]), ("gossip", ["gossip.ogg"]), ("narrator", ["narrator.ogg"]),
    ("autoPlay", ALL),
])
def test_type_toggle_and_auto_play(lua, key, missing):
    body = f'VoiceForever.Settings.Set("{key}", false)\n' + EVERY_LINE.replace(
        'fire("QUEST_GREETING")', 'WOW.text.greeting = "Greetings." fire("QUEST_GREETING")')
    assert played(lua, body, packs=(ALLIANCE, NEUTRAL, GREETING)) == [f for f in ALL if f not in missing]


def test_everything_plays_by_default(lua):
    body = EVERY_LINE.replace('fire("QUEST_GREETING")', 'WOW.text.greeting = "Greetings." fire("QUEST_GREETING")')
    assert played(lua, body, packs=(ALLIANCE, NEUTRAL, GREETING)) == ALL


def test_settings_persist_in_saved_variables(lua):
    (settings,) = run(lua, """
      SlashCmdList.VOICEFOREVER("gossip off")
      SlashCmdList.VOICEFOREVER("autoplay")
      SlashCmdList.VOICEFOREVER("english off")
      emit(VoiceForeverDB.settings)
    """)
    assert settings == {**DEFAULTS, "gossip": False, "autoPlay": False, "englishAudio": False}


def test_saved_settings_apply_after_reload(lua):
    assert played(lua, 'fire("GOSSIP_SHOW") fire("QUEST_DETAIL")',
                  before="VoiceForeverDB = { settings = { version = 1, gossip = false } }") == ["detail.ogg"]


def test_reset_restores_defaults(lua):
    (settings,) = run(lua, """
      SlashCmdList.VOICEFOREVER("detail off") SlashCmdList.VOICEFOREVER("button off")
      SlashCmdList.VOICEFOREVER("reset")
      emit(VoiceForeverDB.settings)
    """)
    assert settings == DEFAULTS


@pytest.mark.parametrize("english,expected", [(True, ["detail.ogg"]), (False, [])])
def test_english_audio_on_non_english_client(lua, english, expected):
    body = f"""
      VoiceForever.Settings.Set("englishAudio", {str(english).lower()})
      fire("QUEST_DETAIL") fire("QUEST_FINISHED")
      fire("GOSSIP_SHOW")  -- Gossip is English clients only, whatever the setting
    """
    assert played(lua, body, before='WOW.locale = "deDE"') == expected


def test_english_audio_setting_does_not_affect_english_clients(lua):
    body = 'VoiceForever.Settings.Set("englishAudio", false) fire("QUEST_DETAIL") fire("GOSSIP_SHOW")'
    assert played(lua, body, before='WOW.locale = "enGB"') == ["detail.ogg", "gossip.ogg"]


def test_volume_is_the_dialog_channel_cvar(lua):
    (cvar1, cvar2, printed) = run(lua, """
      SlashCmdList.VOICEFOREVER("volume 30")
      emit(WOW.cvars.Sound_DialogVolume)
      Settings.registered.VoiceForever_volume:SetValue(0.75)
      emit(WOW.cvars.Sound_DialogVolume)
      SlashCmdList.VOICEFOREVER("volume")
      emit(WOW.printed)
    """)
    assert (cvar1, cvar2) == ("0.3", "0.75")
    assert printed[-1].endswith("Dialog volume 75%")


# --- Settings panel -------------------------------------------------------------------------------------------------

def test_settings_panel_registers_every_setting(lua):
    (categories, registered) = run(lua, """
      local out = {}
      for variable, s in pairs(Settings.registered) do out[variable] = { s.control, s:GetValue() } end
      emit(Settings.categories) emit(out)
    """)
    assert categories == ["VoiceForever"]
    assert registered == {"VoiceForever_volume": ["slider", 1],
                          **{f"VoiceForever_{k}": ["checkbox", True] for k in DEFAULTS if k != "version"}}


def test_settings_panel_changes_saved_settings_and_playback(lua):
    (out, settings) = run(lua, """
      Settings.registered.VoiceForever_gossip:SetValue(false)
      fire("GOSSIP_SHOW")
      emit(WOW.played) emit(VoiceForeverDB.settings)
    """)
    assert out == [] and settings["gossip"] is False


def test_slash_options_opens_the_panel(lua):
    (opened,) = run(lua, 'SlashCmdList.VOICEFOREVER("options") emit(WOW.opened)')
    assert opened == "VoiceForever"


def test_addon_works_without_settings_api(lua):
    (out, printed) = run(lua, """
      SlashCmdList.VOICEFOREVER("detail off")
      fire("QUEST_DETAIL") fire("GOSSIP_SHOW")
      SlashCmdList.VOICEFOREVER("options")  -- no panel: prints the status instead
      emit(WOW.played) emit(WOW.printed)
    """, before="Settings = nil")
    assert out == ["gossip.ogg|Dialog"]
    assert any("off: detail" in p for p in printed)


# --- replay button --------------------------------------------------------------------------------------------------

def test_replay_button_plays_the_open_windows_line(lua):
    (shown, out, hidden) = run(lua, """
      VoiceForever.Settings.Set("autoPlay", false)
      QuestFrame:Show() fire("QUEST_DETAIL")
      emit(VoiceForeverQuestReplay:IsShown())
      VoiceForeverQuestReplay:Click() VoiceForeverQuestReplay:Click()
      emit(WOW.played)
      QuestFrame:Hide()
      emit(VoiceForeverQuestReplay:IsShown())
    """)
    assert shown is True and hidden is False
    assert out == ["detail.ogg|Dialog", "detail.ogg|Dialog"]


def test_replay_plays_a_type_that_is_toggled_off(lua):
    assert played(lua, """
      VoiceForever.Settings.Set("gossip", false)
      GossipFrame:Show() fire("GOSSIP_SHOW")
      VoiceForeverGossipReplay:Click()
    """) == ["gossip.ogg"]


def test_replay_button_hidden_with_no_line_or_setting_off(lua):
    (miss, off, on) = run(lua, """
      WOW.quest = 1 fire("QUEST_DETAIL")
      emit(VoiceForeverQuestReplay:IsShown())
      WOW.quest = 783
      SlashCmdList.VOICEFOREVER("button off") fire("QUEST_DETAIL")
      emit(VoiceForeverQuestReplay:IsShown())
      SlashCmdList.VOICEFOREVER("button on")
      emit(VoiceForeverQuestReplay:IsShown())
    """)
    assert (miss, off, on) == (False, False, True)


def test_gossip_button_only_for_gossip(lua):
    (quest, gossip) = run(lua, """
      fire("QUEST_DETAIL")
      emit(VoiceForeverGossipReplay:IsShown())
      fire("GOSSIP_SHOW")
      emit(VoiceForeverGossipReplay:IsShown())
    """)
    assert (quest, gossip) == (False, True)


def test_slash_replay(lua):
    assert played(lua, 'fire("QUEST_DETAIL") fire("QUEST_FINISHED") SlashCmdList.VOICEFOREVER("replay")'
                       ' fire("GOSSIP_SHOW") SlashCmdList.VOICEFOREVER("replay")') == [
        "detail.ogg", "gossip.ogg", "gossip.ogg"]  # after the window closed there is nothing to replay


# --- stopping -------------------------------------------------------------------------------------------------------

def stopped(lua, body: str, **kw):
    (out, stops) = run(lua, body + "\nemit(WOW.played) emit(WOW.stopped)", **kw)
    return len(out), stops


def test_new_line_stops_the_last(lua):
    assert stopped(lua, 'fire("QUEST_DETAIL") fire("QUEST_PROGRESS")') == (2, [42])


def test_new_line_without_audio_still_stops_the_last(lua):
    assert stopped(lua, 'fire("GOSSIP_SHOW") WOW.quest = 1 fire("QUEST_DETAIL")') == (1, [42])


def test_line_not_auto_played_still_stops_the_last(lua):
    assert stopped(lua, 'fire("QUEST_DETAIL") VoiceForever.Settings.Set("progress", false) fire("QUEST_PROGRESS")') \
        == (1, [42])


@pytest.mark.parametrize("close", ['fire("QUEST_FINISHED")', "QuestFrame:Show() QuestFrame:Hide()"])
def test_quest_window_close_stops(lua, close):
    assert stopped(lua, f'QuestFrame:Show() fire("QUEST_DETAIL") {close}') == (1, [42])


@pytest.mark.parametrize("close", ['fire("GOSSIP_CLOSED")', "GossipFrame:Hide()"])
def test_gossip_window_close_stops(lua, close):
    assert stopped(lua, f'GossipFrame:Show() fire("GOSSIP_SHOW") {close}') == (1, [42])


def test_gossip_closing_as_a_quest_opens_keeps_the_quest_line(lua):
    """Picking a quest from a gossip window: the quest line starts, then the gossip window closes."""
    assert stopped(lua, 'GossipFrame:Show() fire("GOSSIP_SHOW") fire("QUEST_DETAIL")'
                        ' fire("GOSSIP_CLOSED") GossipFrame:Hide()') == (2, [42])


def test_walking_away_stops(lua):
    assert stopped(lua, """
      fire("QUEST_DETAIL")
      tick(0.3) tick(0.3)  -- in range
      WOW.inRange = false
      tick(0.1)            -- not checked yet
      tick(0.5)
      tick(0.5)            -- already stopped: no second StopSound
    """) == (1, [42])


def test_npc_interaction_ending_stops(lua):
    assert stopped(lua, 'fire("GOSSIP_SHOW") WOW.npc = nil tick(1)') == (1, [42])


def test_no_distance_check_in_combat(lua):
    assert stopped(lua, 'fire("QUEST_DETAIL") WOW.combat, WOW.inRange = true, false tick(1)') == (1, [])


def test_item_quest_has_no_walk_away_check(lua):
    assert stopped(lua, 'WOW.npc, WOW.quest = nil, 176 fire("QUEST_DETAIL") WOW.inRange = false tick(5)') == (1, [])


def test_slash_stop(lua):
    assert stopped(lua, 'fire("QUEST_DETAIL") SlashCmdList.VOICEFOREVER("stop")') == (1, [42])

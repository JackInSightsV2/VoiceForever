-- VoiceForever Core Addon: Voice Packs register their index; quest events look it up and play.
VoiceForever = VoiceForever or {}
local VF = VoiceForever

VF.quests = VF.quests or {} -- [questId][part][gender] = file path
VF.packs = VF.packs or {}

local PARTS = { QUEST_DETAIL = "detail" }
local GENDERS = { [2] = "m", [3] = "f" } -- UnitSex

function VF.RegisterPack(name, index)
  VF.packs[#VF.packs + 1] = name
  for questId, parts in pairs(index.quests or {}) do
    local quest = VF.quests[questId] or {}
    VF.quests[questId] = quest
    for part, genders in pairs(parts) do
      quest[part] = quest[part] or {}
      for gender, path in pairs(genders) do
        quest[part][gender] = path
      end
    end
  end
end

function VF.Lookup(questId, part, gender)
  local quest = VF.quests[questId]
  local byGender = quest and quest[part]
  return byGender and byGender[gender]
end

local handle

function VF.Stop()
  if handle then
    StopSound(handle)
    handle = nil
  end
end

function VF.Play(path)
  VF.Stop()
  local willPlay, soundHandle = PlaySoundFile(path, "Dialog")
  if willPlay then
    handle = soundHandle
  end
  return willPlay
end

local function debug(...)
  if VF.debug then
    print("|cff33ff99VoiceForever|r", ...)
  end
end

local frame = CreateFrame("Frame")
frame:RegisterEvent("QUEST_DETAIL")
frame:RegisterEvent("QUEST_FINISHED")
frame:SetScript("OnEvent", function(_, event)
  if event == "QUEST_FINISHED" then
    VF.Stop()
    return
  end
  local questId, part, gender = GetQuestID(), PARTS[event], GENDERS[UnitSex("player")]
  local path = VF.Lookup(questId, part, gender)
  if path then
    debug(event, questId, gender, path, VF.Play(path) and "playing" or "failed")
  else
    debug(event, questId, gender, "no audio")
  end
end)

SLASH_VOICEFOREVER1 = "/vf"
SlashCmdList.VOICEFOREVER = function(msg)
  if msg == "debug" then
    VF.debug = not VF.debug
    print("VoiceForever debug", VF.debug and "on" or "off")
  else
    print("VoiceForever: packs " .. (#VF.packs > 0 and table.concat(VF.packs, ", ") or "none") .. "; /vf debug")
  end
end

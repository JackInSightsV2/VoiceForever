-- VoiceForever Core Addon: Voice Packs register their index; quest events look it up and play.
-- Misses and Drift are recorded through Capture (Capture.lua); Drift hashing lives in Drift.lua.
VoiceForever = VoiceForever or {}
local VF = VoiceForever

VF.quests = VF.quests or {} -- [questId][part][gender] = { file = path, hash = Drift hash }
VF.packs = VF.packs or {}

local PARTS = { QUEST_DETAIL = "detail", QUEST_PROGRESS = "progress", QUEST_COMPLETE = "complete" }
local GENDERS = { [2] = "m", [3] = "f" } -- UnitSex

-- The displayed text per interaction, as the player sees it.
local TEXT = {
  QUEST_DETAIL = function() return (GetQuestText() or "") .. "\n\n" .. (GetObjectiveText() or "") end,
  QUEST_PROGRESS = function() return GetProgressText() end,
  QUEST_COMPLETE = function() return GetRewardText() end,
  QUEST_GREETING = function() return GetGreetingText() end,
  GOSSIP_SHOW = function() return C_GossipInfo.GetText() end,
}

function VF.RegisterPack(name, index)
  VF.packs[#VF.packs + 1] = name
  for questId, parts in pairs(index.quests or {}) do
    local quest = VF.quests[questId] or {}
    VF.quests[questId] = quest
    for part, genders in pairs(parts) do
      quest[part] = quest[part] or {}
      for gender, entry in pairs(genders) do
        quest[part][gender] = entry
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

-- Look up and play; a miss or Drift is recorded through Capture. Gossip has no lookup yet, so it is always a miss.
local function onInteraction(event)
  local part = PARTS[event]
  local questId = part and GetQuestID() or nil
  local text = TEXT[event]() or ""
  local hash = VF.PlayerDriftHash(text)
  local entry = part and VF.Lookup(questId, part, GENDERS[UnitSex("player")])
  if not entry then
    VF.Capture.Record("miss", event, questId, text, hash)
    debug(event, questId, "no audio")
    return
  end
  if entry.hash and hash ~= entry.hash then
    VF.Capture.Record("drift", event, questId, text, hash, entry.hash)
    debug(event, questId, "Drift", entry.hash, "->", hash)
  end
  debug(event, questId, entry.file, VF.Play(entry.file) and "playing" or "failed")
end

local frame = CreateFrame("Frame")
for _, event in ipairs({ "ADDON_LOADED", "QUEST_FINISHED", "QUEST_DETAIL", "QUEST_PROGRESS", "QUEST_COMPLETE",
  "QUEST_GREETING", "GOSSIP_SHOW" }) do
  frame:RegisterEvent(event)
end
frame:SetScript("OnEvent", function(_, event, arg1)
  if event == "ADDON_LOADED" then
    if arg1 == "VoiceForever" then VF.Capture.Load() end
  elseif event == "QUEST_FINISHED" then
    VF.Stop()
  else
    onInteraction(event)
  end
end)

SLASH_VOICEFOREVER1 = "/vf"
SlashCmdList.VOICEFOREVER = function(msg)
  if msg == "debug" then
    VF.debug = not VF.debug
    print("VoiceForever debug", VF.debug and "on" or "off")
  elseif msg == "export" then
    local records, drift = VF.Capture.Records(), 0
    for _, r in ipairs(records) do
      if r.kind == "drift" then drift = drift + 1 end
    end
    print(("VoiceForever: %d Capture records (%d Drift), written on logout or /reload to:"):format(#records, drift))
    print("World of Warcraft\\<game folder>\\WTF\\Account\\<ACCOUNT>\\SavedVariables\\VoiceForever.lua")
    print("Upload that file on the VoiceForever upload page.")
  else
    print("VoiceForever: packs " .. (#VF.packs > 0 and table.concat(VF.packs, ", ") or "none")
      .. "; /vf debug, /vf export")
  end
end

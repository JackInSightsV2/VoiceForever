-- VoiceForever Core Addon: Voice Packs register their index; quest events look it up and play.
-- Misses and Drift are recorded through Capture (Capture.lua); Drift hashing lives in Drift.lua.
VoiceForever = VoiceForever or {}
local VF = VoiceForever

VF.quests = VF.quests or {} -- [questId][part][gender] = { file = path, hash = Drift hash }
VF.gossip = VF.gossip or {} -- [npcId] = { { pattern = Lua pattern, file = path, gender = "m"|"f"|nil }, ... }
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
  for npcId, entries in pairs(index.gossip or {}) do
    local list = VF.gossip[npcId] or {}
    VF.gossip[npcId] = list
    for _, entry in ipairs(entries) do
      entry.literal = VF.PatternLiteral(entry.pattern)
      entry.order = #list + 1
      list[#list + 1] = entry
    end
    -- Most specific first, so the first match wins; ties keep registration order.
    table.sort(list, function(a, b)
      if a.literal ~= b.literal then return a.literal > b.literal end
      return a.order < b.order
    end)
    for i, entry in ipairs(list) do entry.order = i end
  end
end

-- Literal characters in an anchored Gossip pattern "^...$" (".-" wildcards don't count; "%x" is one).
function VF.PatternLiteral(pattern)
  local n, i, last = 0, 2, #pattern - 1
  while i <= last do
    local c = pattern:sub(i, i)
    if c == "%" then
      n, i = n + 1, i + 2
    elseif c == "." and pattern:sub(i + 1, i + 1) == "-" then
      i = i + 2
    else
      n, i = n + 1, i + 1
    end
  end
  return n
end

function VF.Lookup(questId, part, gender)
  local quest = VF.quests[questId]
  local byGender = quest and quest[part]
  return byGender and byGender[gender]
end

-- Gossip template match: the NPC's first pattern (most specific) matching the normalised displayed text.
function VF.MatchGossip(npcId, text, gender)
  local entries = npcId and VF.gossip[npcId]
  if not entries then return nil end
  text = VF.Normalise(text)
  for _, entry in ipairs(entries) do
    if (not entry.gender or entry.gender == gender) and text:find(entry.pattern) then
      return entry
    end
  end
end

-- Gossip is voiced on English clients only; other locales skip it (Quest Text still plays, by quest ID).
local function gossipLocale()
  local locale = GetLocale()
  return locale == "enUS" or locale == "enGB"
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

-- Look up and play; a miss or Drift is recorded through Capture.
-- Quest Text is looked up by quest ID, Gossip (gossip window, quest greeting) by NPC ID and template match.
local function onInteraction(event)
  local part = PARTS[event]
  if not part and not gossipLocale() then return end
  local questId = part and GetQuestID() or nil
  local text = TEXT[event]() or ""
  local hash = VF.PlayerDriftHash(text)
  local gender = GENDERS[UnitSex("player")]
  local entry
  if part then
    entry = VF.Lookup(questId, part, gender)
  else
    entry = VF.MatchGossip(VF.Capture.ParseGUID(UnitGUID("npc")), text, gender)
  end
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
for _, event in ipairs({ "ADDON_LOADED", "QUEST_FINISHED", "GOSSIP_CLOSED", "QUEST_DETAIL", "QUEST_PROGRESS",
  "QUEST_COMPLETE", "QUEST_GREETING", "GOSSIP_SHOW" }) do
  frame:RegisterEvent(event)
end
frame:SetScript("OnEvent", function(_, event, arg1)
  if event == "ADDON_LOADED" then
    if arg1 == "VoiceForever" then VF.Capture.Load() end
  elseif event == "QUEST_FINISHED" or event == "GOSSIP_CLOSED" then
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

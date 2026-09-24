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

local function englishClient()
  local locale = GetLocale()
  return locale == "enUS" or locale == "enGB"
end

-- Whether this client gets audio for the event: Gossip only on English clients; Quest Text (looked up by quest ID)
-- on every client, unless "English audio on non-English clients" is off.
function VF.Voiced(event)
  if englishClient() then return true end
  return PARTS[event] ~= nil and VF.Settings.Get("englishAudio")
end

-- Per-type toggles and auto-play; the Narrator toggle applies on top of the quest part's.
local TYPES = { QUEST_DETAIL = "detail", QUEST_PROGRESS = "progress", QUEST_COMPLETE = "complete",
  QUEST_GREETING = "greeting", GOSSIP_SHOW = "gossip" }

function VF.AutoPlays(event, entry)
  local S = VF.Settings
  if not S.Get("autoPlay") or not S.Get(TYPES[event]) then return false end
  return not entry.narrator or S.Get("narrator")
end

-- Which window a line belongs to: the quest window (Quest Text and greetings) or the gossip window.
local WINDOW = { QUEST_DETAIL = "quest", QUEST_PROGRESS = "quest", QUEST_COMPLETE = "quest", QUEST_GREETING = "quest",
  GOSSIP_SHOW = "gossip" }

-- Playback: one sound at a time. VF.current is the open window's line (what the replay button plays).
local handle, playingWindow, npcGUID
VF.current = nil -- { entry = index entry, window = "quest"|"gossip", npcGUID = GUID or nil }
VF.CHECK_INTERVAL = 0.5
VF.INTERACT_DISTANCE = 3 -- CheckInteractDistance index: ~10 yards

local ticker = CreateFrame("Frame")
local sinceCheck = 0

-- Walking away: while a line plays for an NPC, stop once the player leaves interaction distance or the NPC unit
-- changes. CheckInteractDistance is restricted in combat on the retail engine, so it is skipped there.
local function inRange()
  if UnitGUID("npc") ~= npcGUID then return false end
  if CheckInteractDistance and not (InCombatLockdown and InCombatLockdown()) then
    local ok, near = pcall(CheckInteractDistance, "npc", VF.INTERACT_DISTANCE)
    if ok then return near and true or false end
  end
  return true
end

local function onUpdate(_, elapsed)
  sinceCheck = sinceCheck + elapsed
  if sinceCheck < VF.CHECK_INTERVAL then return end
  sinceCheck = 0
  if not inRange() then VF.Stop() end
end

function VF.Stop()
  if handle then
    StopSound(handle)
    handle = nil
  end
  playingWindow, npcGUID = nil, nil
  ticker:SetScript("OnUpdate", nil)
end

function VF.Play(path, window, guid)
  VF.Stop()
  local willPlay, soundHandle = PlaySoundFile(path, "Dialog")
  if willPlay then
    handle, playingWindow, npcGUID = soundHandle, window, guid
    if guid then
      sinceCheck = 0
      ticker:SetScript("OnUpdate", onUpdate)
    end
  end
  return willPlay
end

-- Replay buttons on the quest and gossip windows, shown while the window has a voiced line.
VF.buttons = {}

function VF.UpdateButtons()
  local show = VF.Settings.Get("replayButton")
  for window, button in pairs(VF.buttons) do
    if show and VF.current and VF.current.window == window then button:Show() else button:Hide() end
  end
end

function VF.Replay()
  local c = VF.current
  return c and VF.Play(c.entry.file, c.window, c.npcGUID) or false
end

-- A window closed: forget its line and stop it if it's the one playing. Each window only stops its own line, so
-- the gossip window closing as a quest opens from it doesn't cut off the quest's audio.
local function closed(window)
  if VF.current and VF.current.window == window then VF.current = nil end
  if playingWindow == window then VF.Stop() end
  VF.UpdateButtons()
end

local function makeButton(parent, name, window)
  if not parent then return end
  local b = CreateFrame("Button", name, parent, "UIPanelButtonTemplate")
  b:SetSize(72, 22)
  b:SetPoint("TOPRIGHT", parent, "TOPRIGHT", -8, -28)
  b:SetText("Replay")
  b:SetScript("OnClick", function() VF.Replay() end)
  b:Hide()
  if parent.HookScript then parent:HookScript("OnHide", function() closed(window) end) end
  VF.buttons[window] = b
end

local function debug(...)
  if VF.debug then
    print("|cff33ff99VoiceForever|r", ...)
  end
end

-- Look up and play; a miss or Drift is recorded through Capture.
-- Quest Text is looked up by quest ID, Gossip (gossip window, quest greeting) by NPC ID and template match.
-- Any new line stops the one playing, whether or not it has audio.
local function onInteraction(event)
  local part, window = PARTS[event], WINDOW[event]
  VF.Stop()
  VF.current = nil
  VF.UpdateButtons()
  if not part and not englishClient() then return end
  local questId = part and GetQuestID() or nil
  local text = TEXT[event]() or ""
  local hash = VF.PlayerDriftHash(text)
  local gender = GENDERS[UnitSex("player")]
  local guid = UnitGUID("npc")
  local entry
  if part then
    entry = VF.Lookup(questId, part, gender)
  else
    entry = VF.MatchGossip(VF.Capture.ParseGUID(guid), text, gender)
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
  if not VF.Voiced(event) then
    debug(event, questId, "English audio off")
    return
  end
  VF.current = { entry = entry, window = window, npcGUID = guid }
  VF.UpdateButtons()
  if VF.AutoPlays(event, entry) then
    debug(event, questId, entry.file, VF.Play(entry.file, window, guid) and "playing" or "failed")
  else
    debug(event, questId, entry.file, "not auto-played")
  end
end

local frame = CreateFrame("Frame")
for _, event in ipairs({ "ADDON_LOADED", "QUEST_FINISHED", "GOSSIP_CLOSED", "QUEST_DETAIL", "QUEST_PROGRESS",
  "QUEST_COMPLETE", "QUEST_GREETING", "GOSSIP_SHOW" }) do
  frame:RegisterEvent(event)
end
frame:SetScript("OnEvent", function(_, event, arg1)
  if event == "ADDON_LOADED" then
    if arg1 == "VoiceForever" then
      VF.Capture.Load()
      VF.Settings.Load()
      VF.Settings.OnChange = function(key) if key == "replayButton" then VF.UpdateButtons() end end
      VF.Settings.Register()
      if not VF.buttons.quest then makeButton(QuestFrame, "VoiceForeverQuestReplay", "quest") end
      if not VF.buttons.gossip then makeButton(GossipFrame, "VoiceForeverGossipReplay", "gossip") end
    end
  elseif event == "QUEST_FINISHED" then
    closed("quest")
  elseif event == "GOSSIP_CLOSED" then
    closed("gossip")
  else
    onInteraction(event)
  end
end)

VF.UPLOAD_URL = "https://voiceforever.example/upload" -- placeholder until the Capture upload page is hosted

-- /vf commands mirror the Settings panel.
local function onOff(word, current)
  if word == "on" then return true elseif word == "off" then return false end
  return not current
end

local function say(...) print("|cff33ff99VoiceForever|r", ...) end

local function status()
  local S, on, off = VF.Settings, {}, {}
  for _, o in ipairs(S.OPTIONS) do
    local list = S.Get(o[1]) and on or off
    list[#list + 1] = o[1]
  end
  say("packs: " .. (#VF.packs > 0 and table.concat(VF.packs, ", ") or "none"))
  say(("volume %d%%; on: %s; off: %s"):format(math.floor(S.Volume() * 100 + 0.5), table.concat(on, ", "),
    #off > 0 and table.concat(off, ", ") or "none"))
  say("/vf replay | stop | volume <0-100> | <setting> [on|off] | options | reset | debug | export")
end

local ALIASES = { english = "englishaudio", button = "replaybutton", auto = "autoplay" }

SLASH_VOICEFOREVER1 = "/vf"
SlashCmdList.VOICEFOREVER = function(msg)
  local S = VF.Settings
  local cmd, arg = (msg or ""):lower():match("^%s*(%S*)%s*(%S*)")
  if cmd == "debug" then
    VF.debug = not VF.debug
    print("VoiceForever debug", VF.debug and "on" or "off")
  elseif cmd == "export" then
    local records, drift = VF.Capture.Records(), 0
    for _, r in ipairs(records) do
      if r.kind == "drift" then drift = drift + 1 end
    end
    print(("VoiceForever: %d Capture records (%d Drift), written on logout or /reload to:"):format(#records, drift))
    print("World of Warcraft\\<game folder>\\WTF\\Account\\<ACCOUNT>\\SavedVariables\\VoiceForever.lua")
    print("Upload that file at " .. VF.UPLOAD_URL)
  elseif cmd == "replay" then
    if not VF.Replay() then say("nothing to replay") end
  elseif cmd == "stop" then
    VF.Stop()
  elseif cmd == "volume" then
    if arg ~= "" then S.SetVolume((tonumber(arg) or 100) / 100) end
    say(("Dialog volume %d%%"):format(math.floor(S.Volume() * 100 + 0.5)))
  elseif cmd == "options" or cmd == "settings" or cmd == "config" then
    if not S.Open() then status() end
  elseif cmd == "reset" then
    S.Reset()
    say("settings reset")
  else
    cmd = ALIASES[cmd] or cmd
    for _, o in ipairs(S.OPTIONS) do
      if cmd == o[1]:lower() then
        S.Set(o[1], onOff(arg, S.Get(o[1])))
        say(o[2] .. (S.Get(o[1]) and " on" or " off"))
        return
      end
    end
    status()
  end
end

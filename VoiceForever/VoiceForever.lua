-- VoiceForever: audio registers its index (VoiceForever.RegisterPack); quest and gossip events look it up and play.
-- Misses, Drift and inferred NPCs' confirms are recorded through Capture (Capture.lua); Drift hashing lives in
-- Drift.lua.
VoiceForever = VoiceForever or {}
local VF = VoiceForever

-- [questId][part][gender] = { file = path, hash = Drift hash, t = word starts (s)?, o = first Objectives word? }
VF.quests = VF.quests or {}
VF.gossip = VF.gossip or {} -- [npcId] = { { pattern = Lua pattern, file = path, gender = "m"|"f"|nil }, ... }
VF.packs = VF.packs or {}
-- [npcId] = true: an NPC whose race or gender was guessed; interacting with it records a Capture confirm.
VF.inferred = VF.inferred or {}

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
  for npcId, on in pairs(index.inferred or {}) do
    if on then VF.inferred[npcId] = true end
  end
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
-- The entry as this NPC speaks it: an NPC the game shows as both sexes has `alt`, the clip in its other sex's voice
-- ({ sex = "m"|"f", file, t?, o? }), played when the spawn's UnitSex is that sex.
function VF.ForNpcSex(entry, npcSex)
  local alt = entry and entry.alt
  if not alt or alt.sex ~= npcSex then return entry end
  return { file = alt.file, hash = entry.hash, narrator = entry.narrator, t = alt.t, o = alt.o }
end

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

-- Greet once: an NPC's greeting/gossip isn't auto-played again within GREET_RESET_S, unless the player walked more than
-- GREET_RESET_YARDS away from where it was last heard (or moved to another map). Replay always plays.
VF.GREET_RESET_S, VF.GREET_RESET_YARDS = 60, 20
local greeted = {} -- [npc GUID] = { t = GetTime(), x, y, map }
local GREETINGS = { GOSSIP_SHOW = true, QUEST_GREETING = true }

local function playerPos()
  if not UnitPosition then return end
  local ok, y, x, _, map = pcall(UnitPosition, "player")
  if ok and y then return x, y, map end
end

function VF.GreetedRecently(event, guid)
  if not (GREETINGS[event] and guid and VF.Settings.Get("greetOnce")) then return false end
  local g, now = greeted[guid], GetTime()
  local x, y, map = playerPos()
  local recent = g and now - g.t < VF.GREET_RESET_S
  if recent and g.x and x then
    recent = map == g.map and ((x - g.x) ^ 2 + (y - g.y) ^ 2) <= VF.GREET_RESET_YARDS ^ 2
  end
  greeted[guid] = { t = recent and g.t or now, x = x, y = y, map = map }
  return recent and true or false
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

local pendingToken = 0 -- bumped by Stop/Play: a delayed start only runs if nothing happened since
local pendingWindow    -- the window whose line is waiting out the start delay

-- Stop the line playing (or waiting to start) and show its text in full.
function VF.Stop()
  pendingToken = pendingToken + 1
  pendingWindow = nil
  if handle then
    StopSound(handle)
    handle = nil
  end
  playingWindow, npcGUID = nil, nil
  ticker:SetScript("OnUpdate", nil)
  VF.Reveal.Finish()
end

-- Auto-play waits the start delay (setting "delay", default 1 s); closing the window, another line, a replay or the
-- NPC changing in the meantime cancels it. The line's text stays hidden until its voice starts (VF.Reveal.Hold).
-- `line` is { entry, window, npcGUID, event } (VF.current).
function VF.PlayDelayed(line)
  VF.Stop()
  local delay = VF.Settings.Delay()
  if delay <= 0 or not (C_Timer and C_Timer.After) then return VF.PlayLine(line) end
  local token = pendingToken
  pendingWindow = line.window
  VF.Reveal.Hold(line)
  C_Timer.After(delay, function()
    if token ~= pendingToken then return end
    pendingWindow = nil
    if VF.current ~= line or (line.npcGUID and UnitGUID("npc") ~= line.npcGUID) then
      VF.Reveal.Finish()
      return
    end
    VF.PlayLine(line)
  end)
  return true
end

-- Play a line now, its text revealed word by word with it when the entry has word timings.
function VF.PlayLine(line)
  local ok = VF.Play(line.entry.file, line.window, line.npcGUID)
  if ok then
    local t = line.entry.t
    VF.playEnds = t and #t > 0 and GetTime and GetTime() + t[#t] + 1.5 or nil
    VF.Reveal.Start(line)
  end
  return ok
end

-- Whether a line is waiting to start or (as far as we know) still playing: the game says nothing when a sound ends,
-- so a line with word timings counts as over 1.5 s after its last word starts; one without, until it is stopped.
function VF.IsPlaying()
  if pendingWindow then return true end
  if not handle then return false end
  return not VF.playEnds or not GetTime or GetTime() < VF.playEnds
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

-- Replay buttons on the quest and gossip windows (and on Immersion's and DialogueUI's frames, Compat.lua), shown while
-- the window has a voiced line. Keyed by window, or by name with button.windows = { window = true }.
VF.buttons = {}

function VF.UpdateButtons()
  local show = VF.Settings.Get("replayButton")
  for key, button in pairs(VF.buttons) do
    local w = VF.current and VF.current.window
    if show and w and (button.windows and button.windows[w] or key == w) then button:Show() else button:Hide() end
  end
end

-- Key bindings (Bindings.xml): Options > Keybindings > AddOns > VoiceForever.
BINDING_HEADER_VOICEFOREVER = "VoiceForever"
BINDING_NAME_VOICEFOREVER_REPLAY = "Replay the voice line"
BINDING_NAME_VOICEFOREVER_STOP = "Stop the voice line"

function VF.Replay()
  local c = VF.current
  return c and VF.PlayLine(c) or false
end

-- A window closed: forget its line and stop it if it's the one playing or waiting to start. Each window only stops
-- its own line, so the gossip window closing as a quest opens from it doesn't cut off the quest's audio.
local function closed(window)
  if VF.current and VF.current.window == window then VF.current = nil end
  if playingWindow == window or pendingWindow == window then VF.Stop() end
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
  VF.Capture.Confirm(event) -- an inferred NPC: who it really is (on any client, with audio or not)
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
  entry = VF.ForNpcSex(entry, GENDERS[UnitSex("npc")])
  if not entry then
    VF.Capture.Record("miss", event, questId, text, hash)
    debug(event, questId, "no audio")
    return
  end
  if entry.hash and hash ~= entry.hash and not VF.PlayerTextMatches(text, entry.hash) then
    VF.Capture.Record("drift", event, questId, text, hash, entry.hash)
    debug(event, questId, "Drift", entry.hash, "->", hash)
  end
  if not VF.Voiced(event) then
    debug(event, questId, "English audio off")
    return
  end
  VF.current = { entry = entry, window = window, npcGUID = guid, event = event }
  VF.UpdateButtons()
  if VF.GreetedRecently(event, guid) then
    debug(event, entry.file, "greeted recently: not auto-played (Replay plays it)")
  elseif VF.AutoPlays(event, entry) then
    debug(event, questId, entry.file, VF.PlayDelayed(VF.current) and "playing" or "failed")
  else
    debug(event, questId, entry.file, "not auto-played")
  end
end

local frame = CreateFrame("Frame")
for _, event in ipairs({ "ADDON_LOADED", "PLAYER_LOGIN", "QUEST_FINISHED", "GOSSIP_CLOSED", "QUEST_DETAIL",
  "QUEST_PROGRESS", "QUEST_COMPLETE", "QUEST_GREETING", "GOSSIP_SHOW" }) do
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
  elseif event == "PLAYER_LOGIN" then
    VF.Compat.Setup() -- every addon has loaded: Immersion, DialogueUI (Compat.lua)
  elseif event == "QUEST_FINISHED" then
    closed("quest")
  elseif event == "GOSSIP_CLOSED" then
    closed("gossip")
  else
    onInteraction(event)
  end
end)

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
  say("voice-over audio: " .. (#VF.packs > 0 and (#VF.packs .. " loaded") or "none installed"))
  say(("volume %d%%; delay %.1f s; on: %s; off: %s"):format(math.floor(S.Volume() * 100 + 0.5), S.Delay(),
    table.concat(on, ", "), #off > 0 and table.concat(off, ", ") or "none"))
  say("/vf replay | stop | volume <0-100> | delay <0-3> | <setting> [on|off] | options | reset | debug | export | save")
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
    VF.Export.Open() -- a copyable box of the Capture as JSON, for a GitHub issue (Export.lua)
  elseif cmd == "save" then
    -- The client writes SavedVariables only on logout or a UI reload; an addon can't flush them any other way.
    say(("%d Capture records: reloading the UI to save them"):format(#VF.Capture.Records()))
    if ReloadUI then ReloadUI() end
  elseif cmd == "replay" then
    if not VF.Replay() then say("nothing to replay") end
  elseif cmd == "stop" then
    VF.Stop()
  elseif cmd == "delay" then
    if arg ~= "" then S.Set("delay", S.ClampDelay(tonumber(arg) or S.DEFAULTS.delay)) end
    say(("voice starts %.1f s after the window opens"):format(S.Delay()))
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

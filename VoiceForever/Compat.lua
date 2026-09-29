-- Compat: VoiceForever alongside quest and gossip UI replacements.
--
-- Immersion (seblindfors/Immersion, lists Interface 16001: Forever) and DialogueUI (Peterodox/YUI-Dialogue; its tocs
-- list Classic Era 11509 and retail 120100, not Forever) hide Blizzard's quest and gossip frames but leave the game's
-- events alone, so lookup and playback run as usual: QUEST_DETAIL/PROGRESS/COMPLETE, QUEST_GREETING and GOSSIP_SHOW
-- still start a line, QUEST_FINISHED and GOSSIP_CLOSED still stop it. On top of that, with either loaded:
-- - Their frame closing stops the line (checked a frame later, so a gossip-to-quest hand-over that hides and shows
--   the frame again doesn't cut the quest's voice off).
-- - A Replay button sits on their frame (plus the key binding and /vf replay).
-- - The text reveal drives their text (Reveal.lua): Immersion's talk box, paged on the voice instead of its timer,
--   and its objectives; DialogueUI's paragraphs.
-- - No double voices. Both can read text aloud with the game's text to speech (C_VoiceChat.SpeakText): Immersion's
--   "ttsenabled" (off by default) and DialogueUI's TTS (auto-play optional). While a VoiceForever line plays or waits
--   to start, speech that starts is stopped (C_VoiceChat.StopSpeakingText). DialogueUI also gets VoiceForever as its
--   voiceover provider (DialogueUIAPI.SetVOProvider): for a line we voice, its TTS button and TTS auto-play play,
--   replay or stop our line instead of speaking.
VoiceForever = VoiceForever or {}
local VF = VoiceForever

local C = {}
VF.Compat = C

function C.Loaded(name)
  if C_AddOns and C_AddOns.IsAddOnLoaded then return C_AddOns.IsAddOnLoaded(name) and true or false end
  if IsAddOnLoaded then return IsAddOnLoaded(name) and true or false end
  return false
end

local function shown(f) return f and f.IsShown and f:IsShown() and true or false end

function C.ImmersionFrame()
  local f = C.Loaded("Immersion") and ImmersionFrame
  return f and f.TalkBox and f or nil
end

function C.DialogueUIFrame()
  return C.Loaded("DialogueUI") and DUIQuestFrame or nil
end

-- --- where the text is: groups of sources for Reveal.Schedule --------------------------------------------------------

local BLIZZARD = {
  QUEST_DETAIL = { "QuestInfoDescriptionText", "QuestInfoObjectivesText" },
  QUEST_PROGRESS = { "QuestProgressText" },
  QUEST_COMPLETE = { "QuestInfoRewardText" },
  QUEST_GREETING = { "GreetingText" },
}

-- The text each event shows (what a DialogueUI paragraph must be part of).
local SOURCE_TEXT = {
  QUEST_DETAIL = function() return GetQuestText() end,
  QUEST_PROGRESS = function() return GetProgressText() end,
  QUEST_COMPLETE = function() return GetRewardText() end,
  QUEST_GREETING = function() return GetGreetingText() end,
  GOSSIP_SHOW = function() return C_GossipInfo and C_GossipInfo.GetText() end,
}

local function plain(fs)
  if fs and fs.SetAlphaGradient and fs.GetText then return { fs = fs, chunks = { fs:GetText() or "" } } end
end

-- Whether any UI shows `event`'s text where we can reveal it.
function C.Supports(event)
  if BLIZZARD[event] then return true end
  return event == "GOSSIP_SHOW" and (C.ImmersionFrame() ~= nil or C.DialogueUIFrame() ~= nil)
end

-- Immersion's talk box: its FontString pages the text (Components/Text.lua: CreateLineData splits it, strings/timers
-- hold what is left, RemoveLine + SetToCurrentLine show the next chunk).
local function immersionText(fs)
  if not (fs and fs.CreateLineData and fs.storedText) then return nil end
  local _, chunks = fs:CreateLineData(fs.storedText)
  return {
    fs = fs, chunks = chunks, immersion = true, stored = fs.storedText,
    paged = {
      current = function()
        if not fs:HasLine() then return nil end
        return fs:GetNumTexts() - fs:GetNumRemaining() + 1
      end,
      advance = function()
        if not fs:HasFollowup() then return false end
        fs:RemoveLine()
        fs:SetToCurrentLine()
        return true
      end,
    },
  }
end

local function immersionSources(f, event)
  local talk = f.TalkBox
  local text = immersionText(talk.TextFrame and talk.TextFrame.Text)
  if not text then return {} end
  local content = talk.Elements and talk.Elements.Content
  local objectives = event == "QUEST_DETAIL" and content and shown(content.ObjectivesText)
    and plain(content.ObjectivesText)
  return objectives and { { text }, { objectives } } or { { text } }
end

-- DialogueUI's page: a pooled FontString per paragraph (fontStringPool.activeObjects); objectives are flagged
-- ttsFlag 3 (TTSFlags.QuestObjective). Paragraphs of the event's text are found by their text, top to bottom.
local function dialogueUISources(f, event)
  local pool = f.fontStringPool and f.fontStringPool.activeObjects
  if not pool then return {} end
  local source = SOURCE_TEXT[event] and SOURCE_TEXT[event]() or ""
  local body, objectives = {}, {}
  for fs in pairs(pool) do
    local text = fs.GetText and fs:GetText()
    if text and text:find("%S") and fs.SetAlphaGradient then
      if event == "QUEST_DETAIL" and fs.ttsFlag == 3 then
        objectives[#objectives + 1] = fs
      elseif not fs.ttsFlag or fs.ttsFlag == 0 or fs.ttsFlag == 1 then
        if source:find(text, 1, true) then body[#body + 1] = fs end
      end
    end
  end
  local function ordered(list)
    table.sort(list, function(a, b) return (a:GetTop() or 0) > (b:GetTop() or 0) end)
    local out = {}
    for i, fs in ipairs(list) do out[i] = plain(fs) end
    return out
  end
  if event == "QUEST_DETAIL" then return { ordered(body), ordered(objectives) } end
  return { ordered(body) }
end

-- The groups of sources the line's text is in now, and which UI shows it.
function C.Sources(line)
  local event = line.event
  local imm = C.ImmersionFrame()
  if shown(imm) then return immersionSources(imm, event), "immersion" end
  local dui = C.DialogueUIFrame()
  if shown(dui) then return dialogueUISources(dui, event), "dialogueui" end
  local groups = {}
  for _, name in ipairs(BLIZZARD[event] or {}) do
    local src = plain(_G[name])
    if src then groups[#groups + 1] = { src } end
  end
  return groups, "blizzard"
end

-- Whether the text shown changed since the sources were read (the UI filled it in after our event handler ran).
function C.Changed(sources)
  for _, src in ipairs(sources) do
    if src.immersion then
      if src.fs.storedText ~= src.stored then return true end
    elseif (src.fs:GetText() or "") ~= src.chunks[1] then
      return true
    end
  end
  return false
end

-- Classic Era's own quest text fade, completed at once so it doesn't fight ours (absent on Forever: a no-op).
local function quietBlizzard(event)
  local panel = QuestFrameDetailPanel
  if event == "QUEST_DETAIL" and panel and panel.fading and panel.fadingProgress then
    panel.fadingProgress = VF.Reveal.FULL
    local onUpdate = panel.GetScript and panel:GetScript("OnUpdate")
    if onUpdate then onUpdate(panel, 0) end -- shows the objectives and rewards and enables Accept
  end
  for _, name in ipairs({ "QuestProgressScrollChildFrame", "QuestRewardScrollChildFrame",
    "QuestGreetingScrollChildFrame" }) do
    local f = _G[name]
    if f and f.GetAlpha and f:GetAlpha() < 1 then
      if UIFrameFadeRemoveFrame then UIFrameFadeRemoveFrame(f) end
      f:SetAlpha(1)
    end
  end
end

-- Every frame while a reveal runs: keep the UI's own text playback from fighting it.
function C.Hold(line, sources)
  quietBlizzard(line.event)
  for _, src in ipairs(sources) do
    if src.immersion and src.fs.PauseTimer then src.fs:PauseTimer() end
  end
end

-- The reveal let go of these sources; `finished`: for good, so Immersion's own timer carries on.
function C.Release(sources, finished)
  if not finished then return end
  for _, src in ipairs(sources) do
    if src.immersion and src.fs.ResumeTimer then src.fs:ResumeTimer() end
  end
end

-- Whether Immersion or DialogueUI is loaded: they replace Blizzard's quest and gossip windows.
function C.Replaces() return C.ImmersionFrame() ~= nil or C.DialogueUIFrame() ~= nil end

-- Whether Immersion's or DialogueUI's frame is showing the conversation (they hide Blizzard's windows to take over).
function C.TookOver()
  local imm, dui = C.ImmersionFrame(), C.DialogueUIFrame()
  return (imm and imm:IsShown() or dui and dui:IsShown()) and true or false
end

-- --- set-up once every addon has loaded ------------------------------------------------------------------------------

local function stopWhenClosed(f)
  if not (f and f.HookScript) then return end
  f:HookScript("OnHide", function()
    local function check() if not f:IsShown() then VF.Stop() end end
    if C_Timer and C_Timer.After then C_Timer.After(0, check) else check() end
  end)
end

local function replayButton(parent, name, close)
  if not parent or _G[name] then return end
  local b = CreateFrame("Button", name, parent, "UIPanelButtonTemplate")
  b:SetSize(72, 22)
  if close then
    b:SetPoint("RIGHT", close, "LEFT", -4, 0) -- in line with their close button, just left of it
  else
    b:SetPoint("TOPRIGHT", parent, "TOPRIGHT", -44, -8)
  end
  b:SetText("Replay")
  b:SetScript("OnClick", function() VF.Replay() end)
  b:Hide()
  b.windows = { quest = true, gossip = true }
  VF.buttons[name] = b
end

-- DialogueUI's voiceover provider interface (Code/SupportedAddOns/VoiceoverPublic.lua).
function C.DialogueUIProvider()
  local PARTS = { detail = "detail", progress = "progress", completion = "complete" }
  local GENDERS = { [2] = "m", [3] = "f" }
  return {
    name = "VoiceForever",
    doesFileExist = function(kind, id, arg1, arg2)
      local gender = GENDERS[UnitSex("player")]
      if kind == "quest" then return VF.Lookup(id, PARTS[arg1], gender) ~= nil end
      if kind == "gossip" then return VF.MatchGossip(id, arg2 or "", gender) ~= nil end
      return false
    end,
    playFile = function()
      if not VF.IsPlaying() then VF.Replay() end -- its auto-play while ours already runs: nothing
    end,
    stopPlaying = function() VF.Stop() end,
    isPlaying = function() return VF.IsPlaying() end,
    getAutoPlayDelay = function() return VF.Settings.Delay() end,
  }
end

function C.Setup()
  if C.done then return end
  C.done = true
  local imm = C.ImmersionFrame()
  if imm then
    stopWhenClosed(imm)
    local tb = imm.TalkBox
    local close = tb and ((tb.MainFrame and tb.MainFrame.CloseButton) or tb.CloseButton)
    replayButton(tb, "VoiceForeverImmersionReplay", close)
  end
  local dui = C.DialogueUIFrame()
  if dui then
    stopWhenClosed(dui)
    replayButton(dui, "VoiceForeverDialogueUIReplay")
    if DialogueUIAPI and DialogueUIAPI.SetVOProvider then DialogueUIAPI.SetVOProvider(C.DialogueUIProvider()) end
  end
  -- Their text to speech never talks over our line.
  if (imm or dui) and C_VoiceChat and C_VoiceChat.SpeakText and C_VoiceChat.StopSpeakingText and hooksecurefunc then
    hooksecurefunc(C_VoiceChat, "SpeakText", function()
      if VF.IsPlaying() then C_VoiceChat.StopSpeakingText() end
    end)
  end
  VF.UpdateButtons()
end

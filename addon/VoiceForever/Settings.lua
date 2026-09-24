-- Settings: saved in VoiceForeverDB.settings, shown in the game's Settings panel (Options > AddOns > VoiceForever)
-- and mirrored by /vf slash commands.
--
-- Volume is the game's own Dialog channel volume (CVar Sound_DialogVolume): every VoiceForever line plays on the
-- Dialog channel, and PlaySoundFile has no per-sound volume, so the slider here is the same one as
-- Options > Audio > Dialog. It is saved by the game (Config.wtf), not in VoiceForeverDB.
VoiceForever = VoiceForever or {}
local VF = VoiceForever

local S = {}
VF.Settings = S
S.VERSION = 1
S.VOLUME_CVAR = "Sound_DialogVolume"

-- key -> default. Per-type toggles and auto-play gate automatic playback only; the replay button always plays.
S.DEFAULTS = {
  detail = true, progress = true, complete = true, greeting = true, gossip = true, narrator = true,
  autoPlay = true, replayButton = true, englishAudio = true,
}

-- Panel order, labels and tooltips; the key is also the /vf command.
S.OPTIONS = {
  { "autoPlay", "Auto-play", "Play a line as soon as its window opens. Off: use the replay button or /vf replay." },
  { "detail", "Quest details", "Voice the quest text when a quest is offered." },
  { "progress", "Quest progress", "Voice the quest giver while a quest is in progress." },
  { "complete", "Quest completion", "Voice the quest giver when you hand in a quest." },
  { "greeting", "Quest greetings", "Voice NPCs that offer several quests." },
  { "gossip", "Gossip", "Voice NPC gossip windows." },
  { "narrator", "Narrator", "Voice quests from objects and items (wanted posters, notes) with the Narrator." },
  { "replayButton", "Replay button", "Show a Replay button on the quest and gossip windows." },
  { "englishAudio", "English audio on non-English clients",
    "Play the English Quest Text audio on non-English game clients. Gossip is voiced on English clients only." },
}

-- Saved settings are replaced by the SavedVariables after the addon's files run: call on ADDON_LOADED.
-- Migration: no settings (before settings existed) or an older version keep every known value of the right type,
-- default the rest and drop unknown keys.
function S.Load()
  VoiceForeverDB = VoiceForeverDB or {}
  local saved = type(VoiceForeverDB.settings) == "table" and VoiceForeverDB.settings or {}
  local s = { version = S.VERSION }
  for key, default in pairs(S.DEFAULTS) do
    if type(saved[key]) == type(default) then s[key] = saved[key] else s[key] = default end
  end
  VoiceForeverDB.settings = s
  return s
end

function S.Get(key)
  local s = VoiceForeverDB and VoiceForeverDB.settings or S.Load()
  return s[key]
end

function S.Set(key, value)
  assert(S.DEFAULTS[key] ~= nil, "unknown setting " .. tostring(key))
  S.Get(key)
  VoiceForeverDB.settings[key] = value
  if S.OnChange then S.OnChange(key, value) end
end

function S.Reset()
  for key, default in pairs(S.DEFAULTS) do S.Set(key, default) end
end

-- Dialog volume, 0..1.
function S.Volume()
  return tonumber(GetCVar and GetCVar(S.VOLUME_CVAR)) or 1
end

function S.SetVolume(v)
  v = math.max(0, math.min(1, tonumber(v) or 1))
  if SetCVar then SetCVar(S.VOLUME_CVAR, v) end
  return v
end

-- The Settings panel (retail 11.x API). Guarded: if Forever lacks or changes it, /vf still works.
function S.Register()
  if S.category or not (Settings and Settings.RegisterVerticalLayoutCategory and Settings.RegisterProxySetting) then
    return S.category
  end
  local ok, err = pcall(function()
    local category = Settings.RegisterVerticalLayoutCategory("VoiceForever")
    local boolean = Settings.VarType and Settings.VarType.Boolean or "boolean"
    local number = Settings.VarType and Settings.VarType.Number or "number"
    local volume = Settings.RegisterProxySetting(category, "VoiceForever_volume", number, "Dialog volume", 1,
      S.Volume, S.SetVolume)
    local options = Settings.CreateSliderOptions(0, 1, 0.05)
    if options.SetLabelFormatter and MinimalSliderWithSteppersMixin then
      options:SetLabelFormatter(MinimalSliderWithSteppersMixin.Label.Right, function(v)
        return ("%d%%"):format(math.floor(v * 100 + 0.5))
      end)
    end
    Settings.CreateSlider(category, volume, options,
      "The game's Dialog volume (also in Options > Audio): VoiceForever plays on the Dialog channel.")
    for _, o in ipairs(S.OPTIONS) do
      local key = o[1]
      local setting = Settings.RegisterProxySetting(category, "VoiceForever_" .. key, boolean, o[2], S.DEFAULTS[key],
        function() return S.Get(key) end, function(v) S.Set(key, v) end)
      Settings.CreateCheckbox(category, setting, o[3])
    end
    Settings.RegisterAddOnCategory(category)
    S.category = category
  end)
  if not ok and VF.debug then print("|cff33ff99VoiceForever|r settings panel unavailable:", err) end
  return S.category
end

function S.Open()
  local category = S.Register()
  if category and Settings.OpenToCategory then
    Settings.OpenToCategory(category:GetID())
    return true
  end
  return false
end

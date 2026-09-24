-- Minimal stand-in for the WoW client API, enough to run the Core Addon under plain Lua.
-- Tests set fields on WOW, call load_addon(), then fire(event, ...).
WOW = {
  quest = 0,
  player = { name = "Jack", race = "Human", class = "Warrior", sex = 3 },
  npc = { guid = "Creature-0-3767-0-12-823-0000ABCDEF", name = "Marshal McBride", sex = 2, type = "Humanoid" },
  text = { quest = "", objective = "", progress = "", reward = "", greeting = "", gossip = "" },
  map = 1429, pos = { 0.48213, 0.41987 },
  locale = "enUS", now = 1790000000,
  inRange = true, combat = false,
  cvars = { Sound_DialogVolume = "1" },
  played = {}, stopped = {}, printed = {},
}

-- Frames: scripts by name, HookScript chains, Show/Hide fire OnShow/OnHide. Every frame is kept in FRAMES.
FRAMES = {}
local Frame = {}
Frame.__index = Frame
function Frame:RegisterEvent(e) self.events[e] = true end
function Frame:SetScript(name, f) self.scripts[name] = f end
function Frame:GetScript(name) return self.scripts[name] end
function Frame:HookScript(name, f)
  local prev = self.scripts[name]
  self.scripts[name] = function(...) if prev then prev(...) end f(...) end
end
function Frame:Show() local was = self.shown; self.shown = true; if not was and self.scripts.OnShow then self.scripts.OnShow(self) end end
function Frame:Hide() local was = self.shown; self.shown = false; if was and self.scripts.OnHide then self.scripts.OnHide(self) end end
function Frame:IsShown() return self.shown end
function Frame:SetText(t) self.text = t end
function Frame:SetSize() end
function Frame:SetPoint() end
function Frame:SetEnabled(on) self.enabled = on end
function Frame:Click() self.scripts.OnClick(self, "LeftButton") end

function CreateFrame(kind, name, parent, template)
  local f = setmetatable({ kind = kind, name = name, parent = parent, template = template, events = {}, scripts = {},
    shown = kind ~= "Button" or nil }, Frame)
  FRAMES[#FRAMES + 1] = f
  if name then _G[name] = f end
  return f
end
-- Blizzard's quest and gossip windows, closed.
QuestFrame = CreateFrame("Frame", "QuestFrame"); QuestFrame.shown = false
GossipFrame = CreateFrame("Frame", "GossipFrame"); GossipFrame.shown = false

function fire(event, ...)
  local handled = false
  for _, f in ipairs(FRAMES) do
    if f.events[event] then f.scripts.OnEvent(f, event, ...); handled = true end
  end
  assert(handled, "event not registered: " .. event)
end

-- Run every OnUpdate script as if `elapsed` seconds passed.
function tick(elapsed)
  for _, f in ipairs(FRAMES) do
    if f.scripts.OnUpdate then f.scripts.OnUpdate(f, elapsed) end
  end
end

function load_addon()
  for _, f in ipairs(ADDON_FILES) do dofile(f) end
  fire("ADDON_LOADED", "VoiceForever")
end

function PlaySoundFile(path, channel)
  WOW.played[#WOW.played + 1] = path .. "|" .. channel
  WOW.handle = (WOW.handle or 41) + 1
  return true, WOW.handle
end
function GetCVar(name) return WOW.cvars[name] end
function SetCVar(name, value) WOW.cvars[name] = tostring(value) end
function CheckInteractDistance(unit, index) assert(not WOW.combat, "CheckInteractDistance in combat") return WOW.inRange end
function InCombatLockdown() return WOW.combat end
function UnitExists(u) return u == "player" or (u == "npc" and WOW.npc ~= nil) end

-- The modern Settings API (retail 11.x signatures), recording what the addon registers.
Settings = {
  VarType = { Boolean = "boolean", Number = "number" },
  registered = {}, categories = {},
  RegisterVerticalLayoutCategory = function(name)
    local c = { name = name }
    function c:GetID() return self.name end
    return c, {}
  end,
  RegisterProxySetting = function(category, variable, varType, name, default, get, set)
    local s = { category = category.name, variable = variable, varType = varType, name = name, default = default }
    function s:GetValue() return get() end
    function s:SetValue(v) set(v) end
    Settings.registered[variable] = s
    return s
  end,
  CreateCheckbox = function(_, setting, tooltip) setting.control, setting.tooltip = "checkbox", tooltip end,
  CreateSliderOptions = function(min, max, step)
    return { min = min, max = max, step = step, SetLabelFormatter = function(self, _, f) self.format = f end }
  end,
  CreateSlider = function(_, setting, options, tooltip) setting.control, setting.options = "slider", options end,
  RegisterAddOnCategory = function(category) Settings.categories[#Settings.categories + 1] = category.name end,
  OpenToCategory = function(id) WOW.opened = id end,
}
MinimalSliderWithSteppersMixin = { Label = { Right = 3 } }
function StopSound(h) WOW.stopped[#WOW.stopped + 1] = h end
function GetQuestID() return WOW.quest end
function GetQuestText() return WOW.text.quest end
function GetObjectiveText() return WOW.text.objective end
function GetProgressText() return WOW.text.progress end
function GetRewardText() return WOW.text.reward end
function GetGreetingText() return WOW.text.greeting end
C_GossipInfo = { GetText = function() return WOW.text.gossip end }

local function unit(u) return u == "player" and WOW.player or u == "npc" and WOW.npc or nil end
function UnitSex(u) local x = unit(u); return x and x.sex end
function UnitName(u) local x = unit(u); return x and x.name end
function UnitRace(u) local x = unit(u); if x and x.race then return x.race, x.race:gsub(" ", ""), 1 end end
function UnitClass(u) local x = unit(u); if x and x.class then return x.class, x.class:upper(), 1 end end
function UnitGUID(u) local x = unit(u); return x and x.guid end
function UnitCreatureType(u) local x = unit(u); return x and x.type end
function GetLocale() return WOW.locale end
function time() return WOW.now end
C_Map = {
  GetBestMapForUnit = function() return WOW.map end,
  GetPlayerMapPosition = function()
    if WOW.pos then return { GetXY = function() return WOW.pos[1], WOW.pos[2] end } end
  end,
}
SlashCmdList = {}
function print(...)
  local parts = {}
  for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
  WOW.printed[#WOW.printed + 1] = table.concat(parts, " ")
end

local rawprint = _G.io.write
local function encode(v)
  local t = type(v)
  if t == "nil" then return "null" end
  if t == "boolean" then return tostring(v) end
  if t == "number" then return string.format("%.14g", v) end
  if t == "string" then
    return '"' .. v:gsub('[%c"\\]', function(c) return string.format("\\u%04x", c:byte()) end) .. '"'
  end
  local out = {}
  if #v > 0 or next(v) == nil then
    for i = 1, #v do out[i] = encode(v[i]) end
    return "[" .. table.concat(out, ",") .. "]"
  end
  for k, x in pairs(v) do out[#out + 1] = encode(tostring(k)) .. ":" .. encode(x) end
  return "{" .. table.concat(out, ",") .. "}"
end

-- Write one value as a JSON line to stdout for the Python side.
function emit(v) rawprint(encode(v), "\n") end

-- Minimal stand-in for the WoW client API, enough to run the Core Addon under plain Lua.
-- Tests set fields on WOW, call load_addon(), then fire(event, ...).
WOW = {
  quest = 0,
  player = { name = "Jack", race = "Human", class = "Warrior", sex = 3 },
  npc = { guid = "Creature-0-3767-0-12-823-0000ABCDEF", name = "Marshal McBride", sex = 2, type = "Humanoid" },
  text = { quest = "", objective = "", progress = "", reward = "", greeting = "", gossip = "" },
  map = 1429, pos = { 0.48213, 0.41987 },
  locale = "enUS", now = 1790000000,
  played = {}, stopped = {}, printed = {},
}

local frame
function CreateFrame()
  frame = { events = {} }
  function frame:RegisterEvent(e) self.events[e] = true end
  function frame:SetScript(_, f) self.handler = f end
  return frame
end

function fire(event, ...)
  assert(frame.events[event], "event not registered: " .. event)
  frame.handler(frame, event, ...)
end

function load_addon()
  for _, f in ipairs(ADDON_FILES) do dofile(f) end
  fire("ADDON_LOADED", "VoiceForever")
end

function PlaySoundFile(path, channel) WOW.played[#WOW.played + 1] = path .. "|" .. channel; return true, 42 end
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

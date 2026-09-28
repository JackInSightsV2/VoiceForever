-- Capture: lookup misses and Drift recorded to SavedVariables (VoiceForeverDB.capture), and a `confirm` record for
-- each NPC the audio index marks inferred (VF.inferred: its race or gender was guessed, not read from its display),
-- once per NPC per session. Every NPC record carries the NPC's display ID, so its race and gender can be resolved
-- exactly and its voice corrected when the guess was wrong.
VoiceForever = VoiceForever or {}
local VF = VoiceForever

local Capture = {}
VF.Capture = Capture
Capture.LIMIT = 20000

local keys -- de-duplication set, rebuilt from the saved records

function Capture.Key(r)
  if r.kind == "confirm" then
    return table.concat({ tostring(r.npcId), "confirm", tostring(r.displayId) }, ":")
  end
  return table.concat({ tostring(r.npcId), tostring(r.event), tostring(r.questId), tostring(r.hash) }, ":")
end

function Capture.Records()
  VoiceForeverDB = VoiceForeverDB or {}
  VoiceForeverDB.capture = VoiceForeverDB.capture or {}
  local records = VoiceForeverDB.capture
  if not keys then
    keys = {}
    while #records > Capture.LIMIT do table.remove(records, 1) end
    for _, r in ipairs(records) do keys[Capture.Key(r)] = true end
  end
  return records
end

-- SavedVariables replace VoiceForeverDB after the addon's files run; call on ADDON_LOADED.
function Capture.Load()
  keys = nil
  return Capture.Records()
end

-- NPC ID is the 6th field of a Creature (or Vehicle) GUID; objects and others give nil plus their GUID type.
function Capture.ParseGUID(guid)
  if not guid then return nil, nil end
  local fields = {}
  for f in guid:gmatch("[^-]+") do fields[#fields + 1] = f end
  local kind = fields[1]
  if kind == "Creature" or kind == "Vehicle" then
    return tonumber(fields[6]), kind
  end
  return nil, kind
end

local function round(v)
  return v and math.floor(v * 10000 + 0.5) / 10000
end

local function position()
  local mapId = C_Map and C_Map.GetBestMapForUnit("player")
  local pos = mapId and C_Map.GetPlayerMapPosition(mapId, "player")
  if pos then
    local x, y = pos:GetXY()
    return mapId, round(x), round(y)
  end
  return mapId
end

-- A unit's display ID and model file (FileDataID), read through a hidden PlayerModel: SetUnit, then GetDisplayInfo
-- and GetModelFileID (the retail engine's model API). Every call is guarded: nil when the client lacks one or the unit
-- has none.
local model -- the PlayerModel frame; false once it couldn't be made
function Capture.Display(unit)
  if model == nil then
    local ok, f = pcall(CreateFrame, "PlayerModel")
    model = ok and f or false
    if model then
      -- Invisible rather than hidden: a hidden model frame may not load its unit's model.
      pcall(model.SetSize, model, 1, 1)
      pcall(model.SetAlpha, model, 0)
    end
  end
  if not model or not pcall(model.SetUnit, model, unit) then return nil, nil end
  local okD, displayId = pcall(model.GetDisplayInfo, model)
  local okF, fileId = pcall(model.GetModelFileID, model)
  displayId = okD and type(displayId) == "number" and displayId > 0 and displayId or nil
  fileId = okF and type(fileId) == "number" and fileId > 0 and fileId or nil
  return displayId, fileId
end

-- A record of the NPC the player is interacting with (the "npc" unit): who and where it is.
local function npcRecord(kind, event)
  local npcId, guidType = Capture.ParseGUID(UnitGUID("npc"))
  local zone, x, y = position()
  local displayId, modelFileId
  if npcId then displayId, modelFileId = Capture.Display("npc") end
  return {
    kind = kind, event = event,
    npcId = npcId, guidType = guidType,
    npcName = UnitName("npc"), unitSex = UnitSex("npc"), creatureType = UnitCreatureType("npc"),
    displayId = displayId, modelFileId = modelFileId,
    zone = zone, x = x, y = y,
    locale = GetLocale(), seenAt = time(),
  }
end

-- Store a record unless one with its key is saved; past LIMIT the oldest go. Returns true if it was added.
local function add(r)
  local records = Capture.Records()
  local key = Capture.Key(r)
  if keys[key] then return false end
  while #records >= Capture.LIMIT do
    keys[Capture.Key(table.remove(records, 1))] = nil
  end
  records[#records + 1] = r
  keys[key] = true
  return true
end

-- kind is "miss" or "drift"; expected is the audio entry's hash for Drift. Returns true if a new record was added.
function Capture.Record(kind, event, questId, text, hash, expected)
  local r = npcRecord(kind, event)
  r.questId, r.text, r.hash, r.expected = questId, text, hash, expected
  if not add(r) then return false end
  if not Capture.hinted then
    -- The client writes SavedVariables only on logout or /reload: a crash or a force-quit loses the session's records.
    Capture.hinted = true
    print("|cff33ff99VoiceForever|r recorded a line it has no voice for. Saved on logout or /reload"
      .. " (/vf save reloads now).")
  end
  return true
end

-- An interaction (any quest or gossip event) with an NPC the audio index marks inferred (VF.inferred): record who it really
-- is, once per NPC per session, whether or not its line has audio. Its key (NPC and display) also skips a display
-- already saved from an earlier session. Returns true if a new record was added.
local confirmed = {} -- [npcId] = true: confirmed this session
function Capture.Confirm(event)
  local npcId = Capture.ParseGUID(UnitGUID("npc"))
  if not npcId or not (VF.inferred and VF.inferred[npcId]) or confirmed[npcId] then return false end
  confirmed[npcId] = true
  return add(npcRecord("confirm", event))
end

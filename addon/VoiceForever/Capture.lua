-- Capture: lookup misses and Drift recorded to SavedVariables (VoiceForeverDB.capture) for the pipeline.
VoiceForever = VoiceForever or {}
local VF = VoiceForever

local Capture = {}
VF.Capture = Capture
Capture.LIMIT = 5000

local keys -- de-duplication set, rebuilt from the saved records

function Capture.Key(r)
  return table.concat({ tostring(r.npcId), r.event, tostring(r.questId), r.hash }, ":")
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

-- kind is "miss" or "drift"; expected is the pack's hash for Drift. Returns true if a new record was added.
function Capture.Record(kind, event, questId, text, hash, expected)
  local records = Capture.Records()
  local npcId, guidType = Capture.ParseGUID(UnitGUID("npc"))
  local zone, x, y = position()
  local r = {
    kind = kind, event = event, questId = questId,
    npcId = npcId, guidType = guidType,
    npcName = UnitName("npc"), unitSex = UnitSex("npc"), creatureType = UnitCreatureType("npc"),
    zone = zone, x = x, y = y,
    locale = GetLocale(), text = text, hash = hash, expected = expected, seenAt = time(),
  }
  local key = Capture.Key(r)
  if keys[key] then return false end
  while #records >= Capture.LIMIT do
    keys[Capture.Key(table.remove(records, 1))] = nil
  end
  records[#records + 1] = r
  keys[key] = true
  return true
end

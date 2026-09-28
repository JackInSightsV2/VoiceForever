-- Drift hash: FNV-1a 32-bit over normalised, player-masked Quest Text.
-- Must stay byte-for-byte identical to the maintainer's tools' hash (shared test vectors check both).
-- Pure Lua 5.1 arithmetic, no bit library: every intermediate stays below 2^53, so doubles are exact.
VoiceForever = VoiceForever or {}
local VF = VoiceForever

local byte, floor, format, concat = string.byte, math.floor, string.format, table.concat
local TWO32 = 4294967296

local function xor8(a, b)
  local r, p = 0, 1
  for _ = 1, 8 do
    local x, y = a % 2, b % 2
    if x ~= y then r = r + p end
    a, b, p = (a - x) / 2, (b - y) / 2, p * 2
  end
  return r
end

function VF.Fnv1a32(s)
  local h = 2166136261
  for i = 1, #s do
    local low = h % 256
    h = h - low + xor8(low, byte(s, i))
    -- h * 16777619 mod 2^32, with 16777619 = 2^24 + 403 and (h * 2^24) mod 2^32 = (h mod 2^8) * 2^24
    h = (h * 403 + (h % 256) * 16777216) % TWO32
  end
  return format("%04x%04x", floor(h / 65536), h % 65536)
end

-- Strip colour codes, collapse whitespace, trim.
function VF.Normalise(text)
  text = (text or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")
  text = text:gsub("%s+", " "):gsub("^ ", ""):gsub(" $", "")
  return text
end

-- Bytes >= 128 count as word characters so UTF-8 names aren't matched inside longer words.
local function isWordByte(b)
  return b ~= nil and (b >= 128 or (b >= 48 and b <= 57) or (b >= 65 and b <= 90) or (b >= 97 and b <= 122))
end

-- Replace whole-word, ASCII-case-insensitive occurrences of `word` with `placeholder`.
local function maskWord(text, word, placeholder)
  word = VF.Normalise(word)
  if word == "" then return text end
  local lower, target, out, pos = text:lower(), word:lower(), {}, 1
  while true do
    local s, e = lower:find(target, pos, true)
    if not s then break end
    if not isWordByte(byte(text, s - 1)) and not isWordByte(byte(text, e + 1)) then
      out[#out + 1] = text:sub(pos, s - 1)
      out[#out + 1] = placeholder
      pos = e + 1
    else
      out[#out + 1] = text:sub(pos, s)
      pos = s + 1
    end
  end
  out[#out + 1] = text:sub(pos)
  return concat(out)
end

-- Displayed text as it was hashed: normalised, player name/race/class as $N/$R/$C.
function VF.Mask(text, name, race, class)
  text = VF.Normalise(text)
  text = maskWord(text, name, "$N")
  text = maskWord(text, race, "$R")
  return maskWord(text, class, "$C")
end

function VF.DriftHash(text, name, race, class)
  return VF.Fnv1a32(VF.Mask(text, name, race, class))
end

-- Hash of text shown to the current player.
function VF.PlayerDriftHash(text)
  return VF.DriftHash(text, UnitName("player"), UnitRace("player"), UnitClass("player"))
end

-- Whether text shown to the current player is the text an audio entry hashed. That text has $N/$R/$C only where the
-- source has a token: a literal word that happens to be the player's race, class or name ("the human lands" to a
-- Human) stays a word there, so every combination of masking the three is tried (8 hashes at most).
function VF.PlayerTextMatches(text, expected)
  local words = { UnitName("player"), UnitRace("player"), UnitClass("player") }
  for m = 0, 7 do
    local w = {}
    for i = 1, 3 do
      w[i] = (math.floor(m / 2 ^ (i - 1)) % 2 == 0) and words[i] or nil
    end
    if VF.DriftHash(text, w[1], w[2], w[3]) == expected then return true end
  end
  return false
end

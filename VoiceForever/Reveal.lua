-- Reveal: the quest text fades in word by word in step with its voice.
--
-- An audio entry with word timings has `t`, each spoken word's start in seconds from the clip's start, and on a quest
-- detail `o`, the first word the Narrator speaks (where the objectives start). The displayed text differs from the
-- spoken text (the player's name, race and class, Lexicon respellings, numbers read out), so words are matched by
-- position: displayed word i (0-based) of n_displayed is spoken word round(i * n_spoken / n_displayed). Each word
-- starts fading in when its spoken word starts, via FontString:SetAlphaGradient(start, length): characters before
-- `start` are shown, the next `length` fade out, the rest are hidden. An OnUpdate moves `start` from one word's first
-- character to the next's between their start times.
--
-- Before the voice starts (the start delay) the text is hidden: it appears with the voice, as Classic's own quest
-- text fade started from an empty page. Stopping, a new line, walking away or the window closing shows the text in
-- full. A line with no audio or no timings, or with the setting off, is left alone.
--
-- Where the text is shown (VF.Compat.Sources, chosen afresh while the line plays):
-- - Blizzard's quest frame. Forever's is the retail one (Blizzard_UIPanels_Game/Mainline in Gethe/wow-ui-source,
--   branch forever): QuestInfoDescriptionText and QuestInfoObjectivesText (detail), QuestProgressText,
--   QuestInfoRewardText, GreetingText. It has no quest text fade of its own; Classic Era's does
--   (QuestFrameDetailPanel.fading, UIFrameFadeIn on the scroll children) and is completed at once while a reveal
--   runs. Blizzard's gossip text is a ScrollBox element with no named FontString: not revealed.
-- - Immersion's talk box, which shows its text a chunk (paragraph or sentence) at a time on its own timer: while a
--   reveal runs its timer is paused and the voice moves it to the chunk being spoken; a chunk the player skipped
--   ahead to is shown in full. Its objectives (Elements.Content.ObjectivesText) reveal from `o`.
-- - DialogueUI's page: one FontString per paragraph, all shown at once.
VoiceForever = VoiceForever or {}
local VF = VoiceForever

local R = {}
VF.Reveal = R
R.GRADIENT = 10  -- characters over which the leading edge fades in
R.TAIL = 0.4     -- seconds the last word takes to fade in fully
R.FULL = 100000  -- a gradient start past any text: all of it shown

-- Word starts of displayed text as 0-based character offsets, and its length in characters. A UTF-8 character counts
-- once; colour codes (|cAARRGGBB, |r) count as nothing; a word is a run of non-space holding a letter or digit.
function R.Words(text)
  local starts, n, i, len = {}, 0, 1, #text
  local inWord, wordStart, hasAlnum = false, 0, false
  while i <= len do
    local c = text:sub(i, i)
    local nextc = text:sub(i + 1, i + 1)
    if c == "|" and (nextc == "c" or nextc == "C") then
      i = i + 10
    elseif c == "|" and (nextc == "r" or nextc == "R") then
      i = i + 2
    else
      local b = c:byte()
      if c:match("%s") then
        if inWord and hasAlnum then starts[#starts + 1] = wordStart end
        inWord = false
        n = n + 1
      else
        if not inWord then inWord, wordStart, hasAlnum = true, n, false end
        if c:match("%w") or b >= 0xC0 then hasAlnum = true end
        if b < 0x80 or b >= 0xC0 then n = n + 1 end -- a continuation byte belongs to the character before
      end
      i = i + 1
    end
  end
  if inWord and hasAlnum then starts[#starts + 1] = wordStart end
  return starts, n
end

-- The reveal schedule. `groups` (from VF.Compat.Sources) is { description sources } or, on a detail with `o`,
-- { description sources, objectives sources }; each source is { fs = FontString, chunks = { text, ... } } (one chunk
-- unless the UI pages its text, like Immersion). Every chunk gets points { time, character } its gradient start moves
-- through; a group's words share one run of the spoken words: 1..o-1 and o..#t, or all of them.
function R.Schedule(line, groups)
  local t = line.entry.t
  local o = line.entry.o and math.max(1, math.min(#t + 1, line.entry.o))
  local ranges = { { 1, #t } }
  if o and #groups == 2 then
    ranges = { { 1, o - 1 }, { o, #t } }
  elseif #groups > 1 then -- no split: one voice speaks every group, one run of words
    local all = {}
    for _, group in ipairs(groups) do
      for _, src in ipairs(group) do all[#all + 1] = src end
    end
    groups = { all }
  end
  local sources = {}
  for gi, group in ipairs(groups) do
    local lo, hi = ranges[gi][1], ranges[gi][2]
    local words = {}
    for _, src in ipairs(group) do
      src.parts = {}
      sources[#sources + 1] = src
      for ci, text in ipairs(src.chunks) do
        local starts, total = R.Words(text)
        local part = { text = text, total = total, points = {} }
        src.parts[ci] = part
        for _, pos in ipairs(starts) do words[#words + 1] = { part = part, pos = pos } end
      end
    end
    local d, n = #words, hi - lo + 1
    local finish = n > 0 and t[hi] + R.TAIL or (lo > #t and (t[#t] or 0) + R.TAIL or 0)
    for i, w in ipairs(words) do
      w.time = n > 0 and t[math.min(hi, lo + math.floor((i - 1) * n / d + 0.5))] or finish
      w.part.points[#w.part.points + 1] = { w.time, w.pos }
      w.part.start = w.part.start or w.time
    end
    for i, w in ipairs(words) do -- each part ends where the group's next word starts, or at the group's end
      local nxt = words[i + 1]
      if not nxt or nxt.part ~= w.part then
        w.part.points[#w.part.points + 1] = { math.max(w.time, nxt and nxt.time or finish), w.part.total }
      end
    end
    for _, src in ipairs(group) do
      for _, part in ipairs(src.parts) do part.start = part.start or 0 end
    end
  end
  return sources
end

-- Where the gradient starts at `now` (seconds into the clip), and whether the text is fully shown.
function R.Position(points, now)
  if #points == 0 then return R.FULL, true end
  if now < points[1][1] then return 0, false end
  for k = #points, 1, -1 do
    local p = points[k]
    if now >= p[1] then
      local nxt = points[k + 1]
      if not nxt then return R.FULL, true end
      return p[2] + (nxt[2] - p[2]) * (now - p[1]) / (nxt[1] - p[1]), false
    end
  end
end

-- A block whose voice hasn't reached it is fully hidden (alpha 0): at gradient position 0 the soft edge would still
-- show its first few letters faintly (the objectives before the Narrator starts, any text during the start delay).
local function show(fs, pos)
  if pos <= 0 then
    fs:SetAlpha(0)
  else
    fs:SetAlpha(1)
    fs:SetAlphaGradient(pos, R.GRADIENT)
  end
end

local state -- { line, start = GetTime() when the voice started (nil while held), key, sources }
local frame = CreateFrame("Frame")

-- Show one source at `now`; returns whether it is fully shown. A paged source (Immersion) is moved to the chunk being
-- spoken, never back: a chunk the player skipped ahead to shows in full.
local function apply(src, now)
  local part = src.parts[1]
  if src.paged then
    local target = 1
    for ci, p in ipairs(src.parts) do
      if now >= p.start and #p.points > 0 then target = ci end
    end
    local cur = src.paged.current()
    while cur and cur < target and src.paged.advance() do cur = cur + 1 end
    if not cur then return true end
    part = src.parts[cur]
    if cur > target then
      show(src.fs, R.FULL)
      return false
    end
  end
  local pos, full = R.Position(part and part.points or {}, now)
  show(src.fs, pos)
  if src.paged and full then return src.paged.current() == #src.parts end
  return full
end

local function update()
  if not state then return end
  local groups, key = VF.Compat.Sources(state.line)
  if key ~= state.key or not state.sources or VF.Compat.Changed(state.sources) then
    if state.sources then VF.Compat.Release(state.sources, false) end
    state.key, state.sources = key, R.Schedule(state.line, groups)
  end
  VF.Compat.Hold(state.line, state.sources)
  local now = state.start and (GetTime() - state.start) or -1
  local done = state.start ~= nil
  for _, src in ipairs(state.sources) do
    done = apply(src, now) and done
  end
  if done then R.Finish() end
end

local function applies(line)
  local t = line and line.entry and line.entry.t
  return VF.Settings.Get("reveal") and t and #t > 0 and VF.Compat.Supports(line.event)
end

local function begin(line, start)
  R.Finish()
  if not applies(line) then return false end
  state = { line = line, start = start }
  frame:SetScript("OnUpdate", update)
  update()
  return true
end

-- The start delay: hide the line's text until its voice starts.
function R.Hold(line) return begin(line, nil) end

-- The voice started: reveal the text in step with it.
function R.Start(line) return begin(line, GetTime()) end

-- Show the text in full and stop revealing; the UI's own text playback (Immersion's timer) carries on.
function R.Finish()
  if not state then return end
  local sources = state.sources
  state = nil
  frame:SetScript("OnUpdate", nil)
  if sources then
    for _, src in ipairs(sources) do show(src.fs, R.FULL) end
    VF.Compat.Release(sources, true)
  end
end

function R.Active() return state ~= nil end

-- Export: the Capture as JSON in a copyable text box (/vf export), for pasting into a GitHub issue.
-- Each page is one self-contained JSON object: {"vf":"capture","v":1,addon,build,locale,page,pages,records:[...]}.
-- The maintainer's tools read it from the pasted GitHub issue.
VoiceForever = VoiceForever or {}
local VF = VoiceForever

local Export = {}
VF.Export = Export
Export.ISSUE_URL = "https://github.com/JackInSightsV2/VoiceForever/issues/new?template=capture.yml"
-- GitHub caps an issue body or comment at 65,536 characters; a page leaves room for the form's other fields.
Export.PAGE_BYTES = 60000
Export.FORMAT, Export.VERSION = "capture", 1

-- --- JSON ---------------------------------------------------------------------------------------------------------

local ESCAPES = { ['"'] = '\\"', ["\\"] = "\\\\", ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t" }

-- Control bytes as \u00XX; a backtick too, so a pasted page can't end the Markdown code block GitHub wraps it in.
-- Other bytes (UTF-8 text) pass through unchanged.
local function quote(s)
  return '"' .. s:gsub('[%c"\\`]', function(c) return ESCAPES[c] or ("\\u%04x"):format(c:byte()) end) .. '"'
end

local function isArray(t)
  local n = 0
  for _ in pairs(t) do n = n + 1 end
  return n == #t
end

-- JSON for nil, booleans, numbers, strings and tables of them: a table with keys 1..n (or none) is an array, any other
-- an object with its keys (as strings) sorted. NaN and infinities, which JSON can't hold, become null.
function Export.Encode(v)
  local t = type(v)
  if t == "string" then return quote(v) end
  if t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    if v == math.floor(v) and v > -2 ^ 53 and v < 2 ^ 53 then return ("%.0f"):format(v) end
    return ("%.14g"):format(v)
  end
  if t == "boolean" then return tostring(v) end
  if t ~= "table" then return "null" end
  local out = {}
  if isArray(v) then
    for i = 1, #v do out[i] = Export.Encode(v[i]) end
    return "[" .. table.concat(out, ",") .. "]"
  end
  local keys = {}
  for k in pairs(v) do keys[#keys + 1] = tostring(k) end
  table.sort(keys)
  for i, k in ipairs(keys) do
    local x = v[k]
    if x == nil then x = v[tonumber(k)] end
    out[i] = quote(k) .. ":" .. Export.Encode(x)
  end
  return "{" .. table.concat(out, ",") .. "}"
end

-- --- Pages --------------------------------------------------------------------------------------------------------

local function addonVersion()
  local get = (C_AddOns and C_AddOns.GetAddOnMetadata) or GetAddOnMetadata
  return get and get("VoiceForever", "Version") or nil
end

local function clientBuild()
  if not GetBuildInfo then return nil end
  local version, build = GetBuildInfo()
  if version and build then return version .. "." .. build end
  return version
end

-- The Capture as a list of JSON pages, each at most maxBytes (default PAGE_BYTES) unless one record alone is larger.
function Export.Pages(records, maxBytes)
  records = records or VF.Capture.Records()
  maxBytes = maxBytes or Export.PAGE_BYTES
  local header = ('{"vf":%s,"v":%d,"addon":%s,"build":%s,"locale":%s'):format(quote(Export.FORMAT), Export.VERSION,
    Export.Encode(addonVersion()), Export.Encode(clientBuild()), Export.Encode(GetLocale and GetLocale()))
  local room = maxBytes - #header - 64 -- the page, pages and count fields, and the closing brackets
  local groups, current, size = {}, {}, 0
  for _, r in ipairs(records) do
    local json = Export.Encode(r)
    if #current > 0 and size + #json + 1 > room then
      groups[#groups + 1], current, size = current, {}, 0
    end
    current[#current + 1] = json
    size = size + #json + 1
  end
  if #current > 0 or #groups == 0 then groups[#groups + 1] = current end
  local pages = {}
  for i, g in ipairs(groups) do
    pages[i] = ('%s,"page":%d,"pages":%d,"count":%d,"records":[%s]}'):format(header, i, #groups, #g,
      table.concat(g, ","))
  end
  return pages
end

-- --- The copy box -------------------------------------------------------------------------------------------------

local function instruction(page, pages)
  if pages == 1 then
    return "Copy (Ctrl/Cmd+C) and paste it into a new GitHub issue: " .. Export.ISSUE_URL
  end
  return ("Page %d of %d. Copy (Ctrl/Cmd+C) and paste page 1 into a new GitHub issue: %s. Paste each other page as"
    .. " a comment on that issue."):format(page, pages, Export.ISSUE_URL)
end

local function build()
  local f = CreateFrame("Frame", "VoiceForeverExportFrame", UIParent, "BasicFrameTemplateWithInset")
  f:SetSize(640, 460)
  f:SetPoint("CENTER")
  f:SetFrameStrata("DIALOG")
  f:SetMovable(true)
  f:EnableMouse(true)
  f:RegisterForDrag("LeftButton")
  f:SetScript("OnDragStart", f.StartMoving)
  f:SetScript("OnDragStop", f.StopMovingOrSizing)
  if f.TitleText then f.TitleText:SetText("VoiceForever: export captured dialogue") end

  f.info = f:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
  f.info:SetPoint("TOPLEFT", 14, -32)
  f.info:SetPoint("TOPRIGHT", -14, -32)
  f.info:SetJustifyH("LEFT")

  local scroll = CreateFrame("ScrollFrame", "VoiceForeverExportScroll", f, "UIPanelScrollFrameTemplate")
  scroll:SetPoint("TOPLEFT", 14, -84)
  scroll:SetPoint("BOTTOMRIGHT", -34, 44)
  local edit = CreateFrame("EditBox", "VoiceForeverExportEditBox", scroll)
  edit:SetMultiLine(true)
  edit:SetAutoFocus(false)
  edit:SetMaxLetters(0)
  edit:SetFontObject(ChatFontNormal)
  edit:SetWidth(580)
  edit:SetScript("OnEscapePressed", function() f:Hide() end)
  -- Read-only: typing puts the page back, selected, so a stray key can't spoil the copy.
  edit:SetScript("OnTextChanged", function(self, userInput)
    if userInput then
      self:SetText(f.pages[f.page])
      self:HighlightText()
    end
  end)
  edit:SetScript("OnMouseUp", function(self) self:HighlightText() end)
  scroll:SetScrollChild(edit)
  f.edit = edit

  f.prev = CreateFrame("Button", nil, f, "UIPanelButtonTemplate")
  f.prev:SetSize(110, 22)
  f.prev:SetPoint("BOTTOMLEFT", 12, 12)
  f.prev:SetText("< Previous")
  f.prev:SetScript("OnClick", function() Export.Show(f.page - 1) end)
  f.next = CreateFrame("Button", nil, f, "UIPanelButtonTemplate")
  f.next:SetSize(110, 22)
  f.next:SetPoint("BOTTOMRIGHT", -12, 12)
  f.next:SetText("Next >")
  f.next:SetScript("OnClick", function() Export.Show(f.page + 1) end)
  f.pageText = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  f.pageText:SetPoint("BOTTOM", 0, 17)
  return f
end

-- Show one page of the export (pages from Export.Pages() when first opened), selected and focused.
function Export.Show(page, pages)
  local f = Export.frame or build()
  Export.frame = f
  if pages then f.pages = pages end
  f.page = math.max(1, math.min(page or 1, #f.pages))
  local text = f.pages[f.page]
  f.info:SetText(instruction(f.page, #f.pages))
  f.pageText:SetText(("Page %d of %d (%d KB)"):format(f.page, #f.pages, math.ceil(#text / 1024)))
  f.prev:SetEnabled(f.page > 1)
  f.next:SetEnabled(f.page < #f.pages)
  f:Show()
  f.edit:SetText(text)
  f.edit:SetFocus()
  f.edit:HighlightText()
  return f
end

-- /vf export: open the copy box on page 1, or say there is nothing to export.
function Export.Open()
  local records = VF.Capture.Records()
  if #records == 0 then
    print("|cff33ff99VoiceForever|r has no captured dialogue to export yet: talk to NPCs whose lines have no voice.")
    return nil
  end
  local pages = Export.Pages(records)
  print(("|cff33ff99VoiceForever|r exporting %d captured lines (%d page%s)."):format(#records, #pages,
    #pages == 1 and "" or "s"))
  return Export.Show(1, pages)
end

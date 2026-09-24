"""vo ingest: Capture SavedVariables -> capture table, Capture lines, Drift updates and Capture NPCs."""
import sqlite3
from pathlib import Path

import pytest

from conftest import lua_literal
from vo import cli, db, drift, ingest, run, savedvars, text

FIXTURE = Path(__file__).parent / "fixtures" / "VoiceForever.lua"
SOURCE = "Young $c, there is work.$B$BSpeak with the Marshal, $N."
PROGRESS = "Have you spoken with the Marshal, $N?"

# Serialise a Lua value the way the client writes SavedVariables: tab-indented, ["key"] = value, "-- [n]" comments.
SERIALISE = r"""
local function q(s)
  return '"' .. (s:gsub('[\\"\n\r]', { ["\\"] = "\\\\", ['"'] = '\\"', ["\n"] = "\\n", ["\r"] = "\\r" })) .. '"'
end
local function ser(v, indent, out)
  if type(v) ~= "table" then out[#out + 1] = type(v) == "string" and q(v) or tostring(v) return end
  out[#out + 1] = "{\n"
  local keys = {}
  for k in pairs(v) do
    if type(k) ~= "number" or k < 1 or k > #v then keys[#keys + 1] = k end
  end
  table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
  for _, k in ipairs(keys) do
    out[#out + 1] = indent .. "\t[" .. (type(k) == "string" and q(k) or tostring(k)) .. "] = "
    ser(v[k], indent .. "\t", out)
    out[#out + 1] = ",\n"
  end
  for i = 1, #v do
    out[#out + 1] = indent .. "\t"
    ser(v[i], indent .. "\t", out)
    out[#out + 1] = ", -- [" .. i .. "]\n"
  end
  out[#out + 1] = indent .. "}"
end
function savedvariables(name, v)
  local out = { name, " = " }
  ser(v, "", out)
  out[#out + 1] = "\n"
  return table.concat(out)
end
"""

# A play session: the player (Jack, Human Warrior, female) meets Core Content and Forever Content.
SESSION = f"""
load_addon()
VoiceForever.RegisterPack("VoiceForever_Test", {{ quests = {{
  [783] = {{ detail = {{ f = {{ file = "a.ogg", hash = {lua_literal(drift.text_hash(SOURCE))} }} }} }},
}} }})
-- Core quest 783: detail reworded in Forever (Drift); progress not voiced yet (a miss on known text).
WOW.quest = 783
WOW.text.quest = "Young warrior, the work has changed.\\n\\nSpeak with the Marshal, Jack."
fire("QUEST_DETAIL")
WOW.text.progress = "Have you spoken with the Marshal, Jack?"
fire("QUEST_PROGRESS")
-- Forever Content: a new NPC and quest.
WOW.npc = {{ guid = "Creature-0-3767-0-12-190001-0000ABCDEF", name = "Aeris Windcaller", sex = 3, type = "Humanoid" }}
WOW.quest = 90001
WOW.text.quest, WOW.text.objective = "Welcome to the skies, Jack. A |cffffd100Warrior|r like you is needed.", "Speak with Aeris."
fire("QUEST_DETAIL")
WOW.text.progress, WOW.text.reward = "Back so soon, Jack?", "The winds thank you, warrior."
fire("QUEST_PROGRESS")
fire("QUEST_COMPLETE")
WOW.text.gossip, WOW.text.greeting = "The winds favour you, Warrior.", "Many tasks await, Jack."
fire("GOSSIP_SHOW")
fire("QUEST_GREETING")
-- A quest from an object: the Narrator's.
WOW.npc = {{ guid = "GameObject-0-3767-0-12-4567-0000ABCDEF", name = "Wanted Poster" }}
WOW.quest, WOW.text.quest, WOW.text.objective = 90002, "Wanted: the harpy queen.", "Slay her."
fire("QUEST_DETAIL")
-- A German client: stored, never voiced.
WOW.locale, WOW.quest, WOW.text.quest = "deDE", 90003, "Willkommen, Jack."
fire("QUEST_DETAIL")
"""


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    conn.execute("INSERT INTO npcs (id, name, race, gender) VALUES (823, 'Marshal McBride', 'human', 'male')")
    for kind, raw in (("quest_detail", SOURCE), ("quest_progress", PROGRESS)):
        conn.execute("INSERT INTO lines (npc_id, type, quest_id, raw_text, tts_text, text_hash) VALUES (823, ?, 783, ?, ?, ?)",
                     (kind, raw, text.prepare(raw), drift.text_hash(raw)))
    conn.commit()
    return conn


@pytest.fixture
def session_file(lua, tmp_path):
    """A SavedVariables file written from the Core Addon's own Capture records."""
    def write(extra: str = "", name: str = "VoiceForever.lua") -> Path:
        (sv,) = lua(SERIALISE + SESSION + extra + 'emit(savedvariables("VoiceForeverDB", VoiceForeverDB))')
        path = tmp_path / name
        path.write_text(sv, encoding="utf-8")
        return path
    return write


def lines(conn, source="capture"):
    return {(r["type"], r["quest_id"], r["npc_id"]): r for r in conn.execute("SELECT * FROM lines WHERE source = ?", (source,))}


def test_session_file_imports(conn, session_file):
    path = session_file()
    upload_id, c = ingest.ingest_file(conn, path)
    assert (c["read"], c["new"], c["duplicate"], c["invalid"]) == (9, 9, 0, 0)
    assert (c["new_lines"], c["drift_updates"], c["new_npcs"], c["known"], c["other_locale"]) == (6, 1, 1, 1, 1)
    assert conn.execute("SELECT COUNT(*), COUNT(DISTINCT upload_id), MIN(upload_id) FROM capture").fetchone()[:] == (
        9, 1, upload_id)
    r = conn.execute("SELECT * FROM capture WHERE kind = 'drift'").fetchone()
    assert (r["npc_id"], r["event"], r["quest_id"], r["expected_hash"], r["locale"], r["guid_type"]) == (
        823, "QUEST_DETAIL", 783, drift.text_hash(SOURCE), "enUS", "Creature")
    assert r["seen_at"].startswith("2026-")
    assert "9 records read, 9 new, 0 duplicate; 6 new lines, 1 drift updates, 1 new NPCs" in ingest.summary_text(
        path, upload_id, c)


def test_misses_become_capture_lines_with_player_words_retokenised(conn, session_file):
    ingest.ingest_file(conn, session_file())
    got = {k: r["raw_text"] for k, r in lines(conn).items()}
    assert got == {
        ("quest_detail", 90001, 190001): "Welcome to the skies, $N. A Warrior like you is needed.$B$BSpeak with Aeris."
        .replace("Warrior", "$C"),
        ("quest_progress", 90001, 190001): "Back so soon, $N?",
        ("quest_complete", 90001, 190001): "The winds thank you, $C.",
        ("gossip", None, 190001): "The winds favour you, $C.",
        ("quest_greeting", None, 190001): "Many tasks await, $N.",
        ("quest_detail", 90002, None): "Wanted: the harpy queen.$B$BSlay her.",
    }
    for r in lines(conn).values():
        assert r["player_gender"] is None
        assert r["tts_text"] == text.prepare(r["raw_text"])
        assert r["text_hash"] == drift.text_hash(r["raw_text"])  # what the addon will compute for any player
    assert lines(conn)[("quest_progress", 90001, 190001)]["tts_text"] == "Back so soon, friend?"
    assert conn.execute("SELECT COUNT(*) FROM line_issues").fetchone()[0] == 0


def test_unknown_npc_becomes_flagged_capture_npc(conn, session_file):
    ingest.ingest_file(conn, session_file())
    npc = conn.execute("SELECT * FROM npcs WHERE id = 190001").fetchone()
    assert (npc["name"], npc["gender"], npc["race"], npc["source"]) == ("Aeris Windcaller", "female", None, "capture")
    issues = {r[0]: r[1] for r in conn.execute("SELECT issue, detail FROM npc_issues WHERE npc_id = 190001")}
    assert issues == {"capture_only": "UnitSex 3, creature type Humanoid", "race_unresolved": "Humanoid"}
    assert conn.execute("SELECT source FROM npcs WHERE id = 823").fetchone()[0] == "core"  # known NPC untouched
    assert conn.execute("SELECT COUNT(*) FROM npcs").fetchone()[0] == 2  # the object isn't an NPC


def test_drift_updates_core_line_keeps_history_and_requeues(conn, session_file):
    run.sync_jobs(conn, "kokoro:am_michael")
    conn.execute("UPDATE jobs SET status = 'done'")
    conn.commit()
    ingest.ingest_file(conn, session_file())
    line = conn.execute("SELECT * FROM lines WHERE type = 'quest_detail' AND quest_id = 783").fetchone()
    new = "Young $C, the work has changed.$B$BSpeak with the Marshal, $N."
    assert (line["raw_text"], line["source"]) == (new, "core")
    assert line["tts_text"] == text.prepare(new)
    assert line["text_hash"] == drift.text_hash(new)
    old = conn.execute("SELECT * FROM line_history WHERE line_id = ?", (line["id"],)).fetchone()
    assert (old["raw_text"], old["text_hash"], old["reason"]) == (SOURCE, drift.text_hash(SOURCE), "drift")
    assert old["tts_text"] == text.prepare(SOURCE)
    run.sync_jobs(conn, "kokoro:am_michael")
    status = dict(conn.execute("SELECT line_id, status FROM jobs WHERE line_id IN (SELECT id FROM lines"
                               " WHERE quest_id = 783)").fetchall())
    progress = conn.execute("SELECT id FROM lines WHERE type = 'quest_progress' AND quest_id = 783").fetchone()[0]
    assert status == {line["id"]: "pending", progress: "done"}  # only the drifted line regenerates


def test_reimport_creates_no_duplicates(conn, session_file):
    path = session_file()
    ingest.ingest_file(conn, path)
    before = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("capture", "lines", "npcs", "npc_issues", "line_history")]
    _, c = ingest.ingest_file(conn, path)
    assert (c["read"], c["new"], c["duplicate"], c["new_lines"], c["drift_updates"], c["new_npcs"]) == (9, 0, 9, 0, 0, 0)
    after = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in ("capture", "lines", "npcs", "npc_issues", "line_history")]
    assert after == before


def test_overlapping_upload_adds_only_new_records(conn, session_file):
    ingest.ingest_file(conn, session_file())
    more = session_file('WOW.locale, WOW.quest, WOW.text.quest, WOW.text.objective = "enUS", 90004, "Onward, Jack.", ""\n'
                        'fire("QUEST_DETAIL")\n', name="other.lua")
    _, c = ingest.ingest_file(conn, more)
    assert (c["read"], c["new"], c["duplicate"], c["new_lines"]) == (10, 1, 9, 1)
    assert conn.execute("SELECT raw_text FROM lines WHERE quest_id = 90004").fetchone()[0] == "Onward, $N."
    assert conn.execute("SELECT COUNT(DISTINCT upload_id) FROM capture").fetchone()[0] == 2


def test_miss_with_new_wording_of_core_quest_is_drift(conn, session_file):
    path = session_file('WOW.locale, WOW.quest, WOW.text.progress = "enUS", 783, "Did you find him, Jack?"\n'
                        'fire("QUEST_PROGRESS")\n')
    _, c = ingest.ingest_file(conn, path)
    assert c["drift_updates"] == 2
    assert conn.execute("SELECT raw_text FROM lines WHERE type = 'quest_progress' AND quest_id = 783").fetchone()[0] == (
        "Did you find him, $N?")
    assert conn.execute("SELECT reason FROM line_history WHERE raw_text = ?", (PROGRESS,)).fetchone()[0] == "drift (miss)"


def test_text_that_cannot_be_retokenised_is_kept_and_flagged(conn, tmp_path):
    path = tmp_path / "sv.lua"
    path.write_text('VoiceForeverDB = { ["capture"] = { { ["kind"] = "miss", ["event"] = "GOSSIP_SHOW",'
                    ' ["npcId"] = 823, ["guidType"] = "Creature", ["locale"] = "enUS",'
                    ' ["text"] = "Hello, Jack.", ["hash"] = "00000000" } } }')
    _, c = ingest.ingest_file(conn, path)
    assert c["new_lines"] == 1
    (line,) = lines(conn).values()
    assert (line["raw_text"], line["text_hash"]) == ("Hello, Jack.", "00000000")
    assert conn.execute("SELECT issue FROM line_issues WHERE line_id = ?", (line["id"],)).fetchone()[0] == "untokenised"


def test_invalid_records_are_skipped(conn, tmp_path):
    path = tmp_path / "sv.lua"
    path.write_text('VoiceForeverDB = { capture = { "junk", { kind = "miss", event = "GOSSIP_SHOW", text = "x",'
                    ' hash = "not a hash" }, { kind = "drift", event = "QUEST_DETAIL", questId = 783, text = "x",'
                    ' hash = "12345678" } } }')
    _, c = ingest.ingest_file(conn, path)
    assert (c["read"], c["invalid"], c["new"]) == (3, 3, 0)


def test_committed_fixture_imports(conn):
    _, c = ingest.ingest_file(conn, FIXTURE)
    assert (c["read"], c["new"], c["invalid"], c["new_lines"], c["drift_updates"], c["new_npcs"]) == (9, 9, 0, 6, 1, 1)


@pytest.mark.parametrize("body, error", [
    ('VoiceForeverDB = os.execute("touch /tmp/pwned")', "only data is allowed"),
    ("VoiceForeverDB = { capture = { { kind = 'miss' }", "expected"),
    ('VoiceForeverDB = { capture = { "unterminated } }', "unterminated string"),
    ("VoiceForeverDB = { capture = 1 + 2 }", "expected ',' or '}'"),
    ("print('hi')", "expected '='"),
    ("OtherAddonDB = {}", "no VoiceForeverDB.capture"),
    ("VoiceForeverDB = ", "unexpected end of file"),
    ("VoiceForeverDB = { capture = 5 }", "isn't a table"),
    ("VoiceForeverDB = " + "{" * 40 + "}" * 40, "nested deeper"),
])
def test_malformed_files_are_rejected_without_writing(conn, tmp_path, body, error):
    path = tmp_path / "bad.lua"
    path.write_text(body)
    with pytest.raises(savedvars.Malformed, match=error):
        ingest.ingest_file(conn, path)
    assert conn.execute("SELECT COUNT(*) FROM capture").fetchone()[0] == 0


def test_oversized_file_is_rejected(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(savedvars, "MAX_BYTES", 100)
    path = tmp_path / "big.lua"
    path.write_text("VoiceForeverDB = { capture = {} }" + " " * 100)
    with pytest.raises(savedvars.Malformed, match="over the 100-byte limit"):
        ingest.ingest_file(conn, path)


def test_cli_ingests_and_rejects(tmp_path, session_file, capsys):
    good, bad = session_file(), tmp_path / "bad.lua"
    bad.write_text("VoiceForeverDB = loadstring('x')()")
    with pytest.raises(SystemExit) as e:
        cli.main(["--db", str(tmp_path / "vo.sqlite"), "ingest", str(good), str(bad)])
    assert e.value.code == 1
    out, err = capsys.readouterr()
    assert "9 records read, 9 new, 0 duplicate" in out
    assert "bad.lua: rejected" in err
    assert sqlite3.connect(tmp_path / "vo.sqlite").execute("SELECT COUNT(*) FROM capture").fetchone()[0] == 9


def test_parser_reads_lua_data():
    got = savedvars.loads(b'-- header\nA = {\n\t["s"] = "a\\"b\\\\c\\n\\065\\xe2\\x9c\\x93\\u{263A}",\n'
                          b'\t["n"] = -1.5e2, ["h"] = 0x10, ["t"] = true, ["f"] = false, ["z"] = nil,\n'
                          b'\t[3] = "three", key = [[long\nstring]],\n'
                          b'\t{ 1, 2, }, -- [1]\n} --[[ block\ncomment ]]\nB = { "x", "y"; "z" }\nC = 7\n')
    assert got == {"A": {"s": 'a"b\\c\nA✓☺', "n": -150.0, "h": 16, "t": True, "f": False,
                         3: "three", "key": "long\nstring", 1: [1, 2]},
                   "B": ["x", "y", "z"], "C": 7}


def test_mask_matches_addon(lua):
    """The Python port of Drift.lua's masking gives the addon's hash, UTF-8 and case quirks included."""
    cases = [("Hail, jack! Jackson, a Night Elf Warrior, meets Jack.", "Jack", "Night Elf", "Warrior"),
             ("Ça va, Zoë? Zoëlle and zoë.", "Zoë", "Human", "Mage")]
    got = lua("load_addon()\n" + "".join(
        f"emit(VoiceForever.DriftHash({lua_literal(t)}, {lua_literal(n)}, {lua_literal(r)}, {lua_literal(c)}))\n"
        for t, n, r, c in cases))
    for (t, n, r, c), want in zip(cases, got):
        assert drift.fnv1a32(ingest.mask(drift.normalise(t).encode(), n.encode(), r.encode(), c.encode())) == want
        assert ingest.player_words(t, want) is not None

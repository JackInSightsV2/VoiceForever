"""vo.wowhead: Forever quests from Wowhead pages -> source='wowhead' lines and NPCs; the polite, cached fetcher.

The fixture pages under fixtures/wowhead/ are SYNTHETIC, modelled on Wowhead's page markup: no live page could be
fetched (robots.txt disallows Anthropic's agents; CloudFront answered 403). Validate against real saved pages."""
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from vo import cli, db, drift, ingest, npcgamevoice, packs, text, wowhead as wh
from vo.display import Display

FIXTURES = Path(__file__).parent / "fixtures" / "wowhead"
DETAIL = ("You there, $N! A young $R with the look of a $C about you.$B$BThe storm hawks have scattered their"
          " feathers across the vale. Gather them for me, $Gbrother:sister;, and bring them to Old Talon.$B$B"
          "Bring 8 Storm Feathers to Old Talon at the Aerie.")
ROBOTS = b"User-agent: ClaudeBot\nUser-agent: anthropic-ai\nDisallow: /\n\nUser-agent: Mediapartners-Google\n" \
         b"Disallow: /forever/list\n"


def page(kind, id_):
    return (FIXTURES / kind / f"{id_}.html").read_text()


# --- parsing ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("html, raw", [
    ("Hello, &lt;name&gt;.", "Hello, $N."),
    ("A &lt;Race&gt; &lt;class&gt;!", "A $R $C!"),
    ("Thanks, &lt;lad/lass&gt;.<br /><br />Go.", "Thanks, $Glad:lass;.$B$BGo."),
    ("  <b>Bold</b>&nbsp;text&#39;s <a href='x'>link</a>\n", "Bold text's link"),
    ("One<br>Two<br/>", "One$BTwo"),
    ("&lt;Insert name here&gt; stays", "<Insert name here> stays"),
])
def test_raw_text(html, raw):
    assert wh.raw_text(html) == raw


def test_parse_quest_page():
    q = wh.parse_quest(page("quest", 99201), 99201)
    assert (q.title, q.level, q.min_level, q.zone, q.side) == ("Winds Over the Vale", 12, 10, 9901, "Both")
    assert q.start == wh.Ref("npc", 190001, "Aeris Windcaller") and q.end == wh.Ref("npc", 190002, "Old Talon")
    assert q.detail == DETAIL
    assert q.progress == "Have you brought the feathers, $N?"
    assert q.completion == "Ah, the winds favour you.$B$BTake this, and fly well."
    assert q.issues == []


def test_parse_quest_started_by_an_object_without_a_gatherer_record():
    q = wh.parse_quest(page("quest", 99202), 99202)
    assert q.start == wh.Ref("object", 500100, "Weathered Note") and q.end.id == 190001
    assert (q.level, q.zone, q.progress) == (14, 9901, "")
    assert q.completion == "Where did you find this? Thank you."


def test_parse_quest_of_an_unrelated_page_flags_it():
    q = wh.parse_quest("<html><body><p>Nothing here</p></body></html>", 1)
    assert not any(q.parts().values()) and {"no_title", "no_detail", "no_start"} <= set(q.issues)


def test_parse_npc_page():
    n = wh.parse_npc(page("npc", 190001), 190001)
    assert (n.name, n.subname, n.display_id, n.level_min, n.level_max) == (
        "Aeris Windcaller", "Skyborne Emissary", 20, 30, 30)
    assert n.zones == [9901, 9902]
    assert n.sounds == [{"id": 900, "name": "FemaleSkyborneGreetings", "files": [100]},
                        {"id": 901, "name": "FemaleSkyborneFarewells", "files": [101]}]
    n = wh.parse_npc(page("npc", 190002), 190002)
    assert (n.name, n.subname, n.display_id, n.zones) == ("Old Talon", "Aerie Keeper", None, [])
    assert n.sounds[0]["files"] == [103]


# --- targets and fetching --------------------------------------------------------------------------------------------

def _world(path=":memory:"):
    w = sqlite3.connect(path)
    w.execute("CREATE TABLE quest_template (entry INTEGER, patch INTEGER)")
    w.executemany("INSERT INTO quest_template VALUES (?, ?)", [(783, 0), (783, 5), (7, 0)])
    w.commit()
    return w


def test_targets_are_client_quests_the_source_data_lacks(tmp_path):
    v2 = tmp_path / "QuestV2.csv"
    v2.write_text("ID,UniqueBitFlag,UiQuestDetailsThemeID\n7,1,0\n783,2,0\n99201,3,0\n99202,4,0\n")
    assert wh.targets(_world(), v2) == [99201, 99202]


class FakeWeb:
    def __init__(self, pages):
        self.pages, self.requests = pages, []

    def __call__(self, u):
        self.requests.append(u)
        if u == wh.ROBOTS:
            return 200, ROBOTS
        return self.pages.get(u, (404, b"not found"))


def test_fetch_is_polite_and_cached(tmp_path):
    web = FakeWeb({wh.url("quest", 1): (200, b"<h1>One</h1>"), wh.url("quest", 3): (200, b"<h1>Three</h1>")})
    slept = []
    c = wh.fetch(tmp_path, "quest", [1, 2, 3], get=web, sleep=slept.append, interval=2.0, log=lambda s: None)
    assert (c["fetched"], c["missing"]) == (2, 1)
    assert slept == [2.0, 2.0]  # between requests, not before the first
    assert (tmp_path / "quest" / "1.html").read_bytes() == b"<h1>One</h1>" and (tmp_path / "quest" / "2.missing").exists()
    web.requests.clear()
    c = wh.fetch(tmp_path, "quest", [1, 2, 3], get=web, sleep=slept.append)
    assert web.requests == [] and c["cached"] == 3  # robots.txt and pages: never fetched twice


@pytest.mark.parametrize("status, body", [(403, b"Request blocked."), (429, b""), (503, b""),
                                          (200, b"<title>Just a moment...</title>")])
def test_fetch_stops_at_the_first_block(tmp_path, status, body):
    web = FakeWeb({wh.url("npc", 5): (status, body), wh.url("npc", 6): (200, b"ok")})
    with pytest.raises(wh.Blocked):
        wh.fetch(tmp_path, "npc", [5, 6], get=web, sleep=lambda s: None)
    assert wh.url("npc", 6) not in web.requests and not (tmp_path / "npc" / "5.html").exists()


def test_fetch_honours_robots_txt(tmp_path):
    (tmp_path / "robots.txt").write_text(f"User-agent: {wh.USER_AGENT.split()[0]}\nDisallow: /forever/\n")
    web = FakeWeb({})
    with pytest.raises(wh.Blocked, match="robots.txt"):
        wh.fetch(tmp_path, "quest", [1], get=web, sleep=lambda s: None)
    assert web.requests == []


def test_wowheads_robots_txt_allows_the_pipeline_but_not_anthropic_agents():
    import urllib.robotparser
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(ROBOTS.decode().splitlines())
    assert rp.can_fetch(wh.USER_AGENT, wh.url("quest", 1))
    assert not rp.can_fetch("ClaudeBot", wh.url("quest", 1))


# --- ingest ----------------------------------------------------------------------------------------------------------

class FakeDisplays:
    def get(self, display_id):
        return Display("Human", "female", "character/human/female/humanfemale.m2", "extra") if display_id == 20 \
            else Display(None, None, None, "missing")


VOICES = wh.Voices(FakeDisplays(), npcgamevoice.Tables({}, {}, {103: npcgamevoice.Kit("orc_m", "orcmaleshadynpc")}))


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "wowhead"
    shutil.copytree(FIXTURES, r)
    return r


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    conn.execute("INSERT INTO npcs (id, name, race, gender) VALUES (197, 'Marshal McBride', 'Human', 'male')")
    conn.commit()
    return conn


def _lines(conn):
    return {(r["type"], r["quest_id"], r["player_gender"]): dict(r) for r in conn.execute(
        "SELECT * FROM lines WHERE source = 'wowhead'")}


def test_ingest_adds_wowhead_lines_and_npcs(conn, root):
    c = wh.ingest(conn, root, core_quests={783}, voices=VOICES)
    assert (c["quest_pages"], c["parsed"], c["core_quests"], c["new_lines"], c["new_npcs"]) == (3, 3, 1, 6, 2)
    lines = _lines(conn)
    assert set(lines) == {("quest_detail", 99201, "m"), ("quest_detail", 99201, "f"), ("quest_progress", 99201, None),
                          ("quest_complete", 99201, None), ("quest_detail", 99202, None),
                          ("quest_complete", 99202, None)}
    d = lines[("quest_detail", 99201, "f")]
    assert d["raw_text"] == DETAIL and d["npc_id"] == 190001 and d["text_hash"] == drift.text_hash(DETAIL, "f")
    assert d["tts_text"] == text.prepare(DETAIL, "f") and "sister" in d["tts_text"]
    assert lines[("quest_progress", 99201, None)]["npc_id"] == 190002
    assert lines[("quest_detail", 99202, None)]["npc_id"] is None  # an object starts it: the Narrator
    assert lines[("quest_complete", 99202, None)]["npc_id"] == 190001
    npcs = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM npcs WHERE source = 'wowhead'")}
    assert (npcs[190001]["race"], npcs[190001]["gender"], npcs[190001]["subname"]) == ("Human", "female",
                                                                                      "Skyborne Emissary")
    assert (npcs[190002]["race"], npcs[190002]["gender"]) == ("Orc", "male")  # from its voice kit
    issues = {(r[0], r[1]) for r in conn.execute("SELECT npc_id, issue FROM npc_issues")}
    assert {(190001, "wowhead_only"), (190002, "wowhead_only"), (190002, "race_from_voice_kit")} <= issues
    assert (190002, "race_unresolved") not in issues
    spawns = sorted(tuple(r) for r in conn.execute("SELECT npc_id, zone FROM spawns"))
    assert spawns == [(190001, 9901), (190001, 9902), (190002, 9901)]
    assert conn.execute("SELECT count(*) FROM wowhead_quests").fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM lines WHERE quest_id = 783").fetchone()[0] == 0


def test_npc_without_a_saved_page_is_flagged_unresolved(conn, root):
    shutil.rmtree(root / "npc")
    wh.ingest(conn, root, core_quests={783}, voices=VOICES)
    assert conn.execute("SELECT name, race, gender FROM npcs WHERE id = 190002").fetchone()[:] == (
        "Old Talon", None, None)
    issues = {r[0] for r in conn.execute("SELECT issue FROM npc_issues WHERE npc_id = 190002")}
    assert {"wowhead_only", "race_unresolved", "gender_unresolved"} <= issues
    assert wh.needed_npcs(conn, root) == [190001, 190002]


def test_ingest_is_idempotent_and_updates_changed_text(conn, root):
    wh.ingest(conn, root, core_quests={783}, voices=VOICES)
    before = _lines(conn)
    c = wh.ingest(conn, root, core_quests={783}, voices=VOICES)
    assert (c["new_lines"], c["updated_lines"], c["unchanged_lines"], c["new_npcs"]) == (0, 0, 6, 0)
    assert _lines(conn) == before
    assert conn.execute("SELECT count(*) FROM npcs WHERE source = 'wowhead'").fetchone()[0] == 2
    p = root / "quest" / "99201.html"
    p.write_text(p.read_text().replace("Have you brought the feathers", "Where are the feathers"))
    c = wh.ingest(conn, root, core_quests={783}, voices=VOICES)
    assert c["updated_lines"] == 1
    line = _lines(conn)[("quest_progress", 99201, None)]
    assert line["id"] == before[("quest_progress", 99201, None)]["id"]
    assert line["raw_text"] == "Where are the feathers, $N?"
    assert conn.execute("SELECT reason FROM line_history WHERE line_id = ?", (line["id"],)).fetchone()[0] == "wowhead"


def test_in_game_text_wins_over_wowhead(conn, root):
    raw = "Captured progress, $N."
    conn.execute("INSERT INTO lines (npc_id, type, quest_id, raw_text, tts_text, text_hash, source)"
                 " VALUES (190002, 'quest_progress', 99201, ?, ?, ?, 'capture')", (raw, text.prepare(raw), drift.text_hash(raw)))
    c = wh.ingest(conn, root, core_quests={783}, voices=VOICES)
    assert c["known_parts"] == 1 and ("quest_progress", 99201, None) not in _lines(conn)


def test_capture_drift_updates_a_wowhead_line(conn, root):
    wh.ingest(conn, root, core_quests={783}, voices=VOICES)
    shown = "Did you bring the feathers, Jack?"
    r = dict(type="quest_progress", quest_id=99201, npc_id=190002, text=shown,
             hash=drift.text_hash("Did you bring the feathers, $N?"))
    counts = Counter()
    ingest._miss(conn, r, 1, counts)
    assert counts["drift_updates"] == 1 and counts["new_lines"] == 0
    assert _lines(conn)[("quest_progress", 99201, None)]["raw_text"] == "Did you bring the feathers, $N?"


def test_voice_packs_use_wowhead_quest_levels(conn, root):
    wh.ingest(conn, root, core_quests={783}, voices=VOICES)
    got = packs.assign(conn)
    line = _lines(conn)[("quest_progress", 99201, None)]
    assert got[line["id"]].endswith("_10-20")


def test_cli_wowhead(tmp_path, root, capsys, monkeypatch):
    w = tmp_path / "world.sqlite"
    _world(str(w)).close()
    db2 = tmp_path / "db2"
    db2.mkdir()
    (db2 / "QuestV2.csv").write_text("ID\n783\n99201\n99203\n")
    base = ["--db", str(tmp_path / "vo.sqlite"), "--world", str(w), "wowhead"]
    cli.main(base + ["targets", "--root", str(root), "--db2", str(db2)])
    out = capsys.readouterr()
    assert out.out == wh.url("quest", 99203) + "\n" and "2 quest targets, 1 cached, 1 to fetch" in out.err
    cli.main(base + ["ingest", "--root", str(root), "--db2", str(db2)])
    assert "3 quest pages, 3 parsed (1 Core Content quests skipped); 6 new lines" in capsys.readouterr().out
    monkeypatch.setattr(wh, "http_get", FakeWeb({wh.url("quest", 99203): (403, b"Request blocked.")}))
    with pytest.raises(SystemExit, match="blocked: .*quest=99203: HTTP 403"):
        cli.main(base + ["fetch", "--root", str(root), "--db2", str(db2)])

"""Coverage tables (vo.coverage), spot-check ratings and auto re-rolls (vo.ratings), and the morning report
(vo.report), including its generation at the end of every vo run."""
import json
import re
import sqlite3
from pathlib import Path

import pytest

from test_packs import ELWYNN, STRANGLETHORN, world_db
from test_run import VOICE, Fake
from vo import coverage, db, ratings, report, run

NPC_VOICE = "voxcpm:human_m@human_m/g0s1#823-r0a0k1"


@pytest.fixture
def world():
    w = world_db()
    w.execute("CREATE TABLE area_template (entry, map_id, zone_id, name)")
    w.executemany("INSERT INTO area_template VALUES (?, ?, ?, ?)", [
        (ELWYNN, 0, 0, "Elwynn Forest"), (STRANGLETHORN, 0, 0, "Stranglethorn Vale"),
        (87, 0, ELWYNN, "Goldshire")])  # a subzone: not a zone
    return w


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    conn.execute("INSERT INTO npcs (id, name, faction, level_min, is_named) VALUES (823, 'Deputy Willem', 12, 10, 1)")
    conn.execute("INSERT INTO npcs (id, name, faction, level_min, is_named) VALUES (2663, 'Narkk', 120, 45, 0)")
    conn.executemany("INSERT INTO spawns (npc_id, zone) VALUES (?, ?)",
                     [(823, ELWYNN), (823, ELWYNN), (823, STRANGLETHORN), (2663, STRANGLETHORN)])
    rows = [(1, 823, "quest_detail", 783), (2, 823, "quest_complete", 783), (3, 823, "gossip", None),
            (4, 2663, "quest_detail", 784), (5, 2663, "gossip", None), (6, None, "quest_detail", 784)]
    for i, npc, type_, quest in rows:
        conn.execute("INSERT INTO lines (id, npc_id, type, quest_id, raw_text, tts_text) VALUES (?, ?, ?, ?, ?, ?)",
                     (i, npc, type_, quest, f"line {i}", f"line {i}"))
    conn.execute("INSERT INTO voices (npc_id, voice_id, archetype) VALUES (823, ?, 'human_m')", (NPC_VOICE,))
    conn.execute("INSERT INTO voice_builds (npc_id, roll, status) VALUES (823, 0, 'ok')")
    conn.commit()
    return conn


def go(conn, tmp_path, fake=None, **kw):
    return run.run(conn, tmp_path / "audio", voice_id=VOICE, workers=1, processor=fake or Fake(),
                   log=kw.pop("log", lambda _: None), **kw)


# --- coverage ------------------------------------------------------------------------------------------------------

def test_refresh_stores_zone_names_and_each_lines_pack_and_zone(conn, world):
    assert coverage.refresh(conn, world) == 6
    assert dict(conn.execute("SELECT id, name FROM zones")) == {ELWYNN: "Elwynn Forest", STRANGLETHORN: "Stranglethorn Vale"}
    got = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT line_id, pack, zone FROM line_packs")}
    assert got[1] == ("VoiceForever_Alliance_10-20", ELWYNN)  # 823's main zone: most spawns
    assert got[3] == ("VoiceForever_Alliance_1-10", ELWYNN)
    assert got[5][1] == STRANGLETHORN
    assert got[6] == ("VoiceForever_Neutral_40-50", STRANGLETHORN)  # Narrator: its quest's zone


def test_coverage_table_per_zone_and_pack(conn, world, tmp_path):
    coverage.refresh(conn, world)
    go(conn, tmp_path, Fake(fail=lambda j: j.line_id == 4))
    conn.execute("INSERT INTO ratings (line_id, voice_id, rating) VALUES (1, ?, 'up')", (NPC_VOICE,))
    conn.commit()
    zones = {r.key: r for r in coverage.table(conn, "zone")}
    assert (zones["Elwynn Forest"].total, zones["Elwynn Forest"].done, zones["Elwynn Forest"].checked) == (3, 3, 1)
    stv = zones["Stranglethorn Vale"]
    assert (stv.total, stv.done, stv.quarantined, round(stv.pct)) == (3, 2, 1, 67)
    assert {r.key for r in coverage.table(conn, "pack")} >= {"VoiceForever_Alliance_10-20", "VoiceForever_Alliance_1-10"}


def test_coverage_without_line_packs_falls_back_to_the_pipeline_db(conn):
    rows = {r.key: r.total for r in coverage.table(conn, "zone")}
    assert rows == {"Zone 12": 3, "Zone 33": 2, coverage.UNKNOWN_ZONE: 1}  # no zone names, no quest zones


# --- ratings -------------------------------------------------------------------------------------------------------

def act(conn, action, target, payload=None):
    conn.execute("INSERT INTO review_actions (action, target, payload) VALUES (?, ?, ?)",
                 (action, str(target), payload and json.dumps(payload)))
    conn.commit()


def rerolls(conn):
    return [(r[0], json.loads(r[1] or "{}").get("voice_id")) for r in conn.execute(
        "SELECT target, payload FROM review_actions WHERE action = 'reroll-voice' ORDER BY id")]


def test_fold_stores_ratings_per_line_and_voice(conn):
    act(conn, "rate-line", 1, {"voice_id": NPC_VOICE, "rating": "up"})
    act(conn, "flag-line", 2, {"voice_id": NPC_VOICE, "note": "mispronounced"})
    act(conn, "flag-voice", 823, {"note": "too young"})
    act(conn, "rate-line", 99, {"voice_id": NPC_VOICE, "rating": "down"})  # no such line
    act(conn, "rate-line", 3, {"voice_id": NPC_VOICE, "rating": "meh"})
    logs = []
    assert ratings.fold(conn, logs.append) == 5
    got = [tuple(r) for r in conn.execute("SELECT line_id, voice_id, npc_id, rating, note FROM ratings ORDER BY id")]
    assert got == [(1, NPC_VOICE, 823, "up", None), (2, NPC_VOICE, 823, "flag", "mispronounced"),
                   (None, NPC_VOICE, 823, "flag", "too young")]
    assert sum("invalid, dropped" in l for l in logs) == 2
    assert conn.execute("SELECT COUNT(*) FROM review_actions WHERE consumed_at IS NULL").fetchone()[0] == 0


def test_three_thumbs_down_on_a_voice_queue_one_reroll(conn):
    for line in (1, 2):
        act(conn, "rate-line", line, {"voice_id": NPC_VOICE, "rating": "down"})
    act(conn, "rate-line", 1, {"voice_id": NPC_VOICE, "rating": "down"})  # the same line again counts once
    ratings.fold(conn, lambda _: None)
    assert rerolls(conn) == []
    act(conn, "rate-line", 3, {"voice_id": NPC_VOICE, "rating": "down"})
    ratings.fold(conn, lambda _: None)
    assert rerolls(conn) == [("823", NPC_VOICE)]
    act(conn, "rate-line", 4, {"voice_id": NPC_VOICE, "rating": "down"})  # (a line of another NPC, same voice)
    ratings.fold(conn, lambda _: None)
    assert rerolls(conn) == [("823", NPC_VOICE)]  # once per voice, even after vo voices consumed it


def test_a_thumbs_up_after_a_down_takes_the_line_back(conn):
    for line in (1, 2, 3):
        act(conn, "rate-line", line, {"voice_id": NPC_VOICE, "rating": "down"})
    act(conn, "rate-line", 3, {"voice_id": NPC_VOICE, "rating": "up"})
    ratings.fold(conn, lambda _: None)
    assert ratings.voice_downs(conn) == {NPC_VOICE: 2}
    assert rerolls(conn) == []


def test_an_archetype_anchor_is_never_auto_rerolled(conn):
    shared = "voxcpm:human_m@human_m/g0s1"
    for line in (1, 2, 3):
        act(conn, "rate-line", line, {"voice_id": shared, "rating": "down"})
    logs = []
    ratings.fold(conn, logs.append)
    assert rerolls(conn) == []
    assert any("isn't an NPC Voice" in l for l in logs)


def test_vo_run_folds_ratings_and_vo_voices_applies_the_reroll(conn, tmp_path):
    for line in (1, 2, 3):
        act(conn, "rate-line", line, {"voice_id": NPC_VOICE, "rating": "down"})
    go(conn, tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0] == 3
    assert rerolls(conn) == [("823", NPC_VOICE)]
    from vo import voices
    assert voices.consume_rerolls(conn, lambda _: None) == 1
    assert tuple(conn.execute("SELECT roll, stale FROM voice_builds WHERE npc_id = 823").fetchone()) == (1, 1)


# --- morning report ------------------------------------------------------------------------------------------------

def test_every_run_writes_the_morning_report(conn, world, tmp_path):
    coverage.refresh(conn, world)
    reports = tmp_path / "reports"
    go(conn, tmp_path, Fake(fail=lambda j: j.line_id == 5), report_dir=reports)
    page = (reports / "1.html").read_text()
    assert (reports / "latest.html").read_text() == page
    assert "Morning report" in page and "Run #1 finished" in page
    assert "Elwynn Forest" in page and "VoiceForever_Alliance_10-20" in page
    assert "Quarantined lines (1)" in page and "ASR WER 0.90" in page
    srcs = re.findall(r'<audio controls preload=none src="([^"]+)"', page)
    assert srcs and all(not s.startswith("/") for s in srcs)
    assert all((reports / s).resolve().exists() for s in srcs)  # relative: plays opened from disk
    go(conn, tmp_path, report_dir=reports)
    assert (reports / "2.html").exists() and "Run #2" in (reports / "latest.html").read_text()


def test_samples_are_per_zone_named_first_one_per_npc(conn, world, tmp_path):
    coverage.refresh(conn, world)
    go(conn, tmp_path)
    got = dict(report.samples(conn, 1, tmp_path / "reports", coverage.names(conn), per_zone=3))
    assert [c["npc_id"] for c in got["Elwynn Forest"]] == [823]
    stv = got["Stranglethorn Vale"]
    assert {c["npc_id"] for c in stv} == {2663, None} and len(stv) == 2
    assert got == dict(report.samples(conn, 1, tmp_path / "reports", coverage.names(conn), per_zone=3))  # seeded


def test_report_lists_leftovers_drift_and_capture_since_the_previous_run(conn, world, tmp_path):
    coverage.refresh(conn, world)
    go(conn, tmp_path)
    conn.execute("UPDATE runs SET ended_at = '2026-01-01T00:00:00'")  # the previous run ended long ago
    conn.execute("UPDATE voice_builds SET status = 'leftover', issue = 'floor', detail = 'cosine 0.97 > 0.95'")
    conn.execute("INSERT INTO capture (kind, npc_name, event, text, ingested_at) VALUES"
                 " ('miss', 'Skyborne Scout', 'GOSSIP_SHOW', 'Welcome, traveller.', '2026-06-01T10:00:00')")
    conn.execute("INSERT INTO capture (kind, ingested_at) VALUES ('drift', '2025-06-01T10:00:00')")  # before: left out
    conn.execute("INSERT INTO line_history (line_id, raw_text, reason) VALUES (1, 'old words', 'drift')")
    conn.execute("UPDATE lines SET raw_text = 'new words' WHERE id = 1")
    conn.commit()
    go(conn, tmp_path, report_dir=tmp_path / "reports")
    page = (tmp_path / "reports" / "2.html").read_text()
    assert "Neighbour-separation leftovers (1)" in page and "cosine 0.97 &gt; 0.95" in page
    assert "Skyborne Scout" in page and "1 Capture records" in page and "(1 new dialogue, 0 Drift)" in page
    assert "Drift updates" in page and "new words" in page


def test_a_failing_report_never_fails_the_run(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(report, "generate", lambda *a: (_ for _ in ()).throw(RuntimeError("disk full")))
    logs = []
    summary = go(conn, tmp_path, report_dir=tmp_path / "reports", log=logs.append)
    assert summary["jobs"] == {"done": 6}
    assert any("morning report failed: RuntimeError: disk full" in l for l in logs)

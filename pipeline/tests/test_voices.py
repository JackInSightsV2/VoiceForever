"""NPC Voices and Neighbours (#13): Neighbour computation, variation, the ceiling/floor constraint with re-rolls,
`vo voices` builds (deterministic, resumable, re-roll actions, stale anchors) and `vo run` using per-NPC anchors."""
import sqlite3
import zlib
from pathlib import Path

import numpy as np
import pytest

from vo import db, lock, neighbours, prepare, run, speaker, tts, voices, voxcpm


# --- Neighbours ------------------------------------------------------------------------------------------------------

def test_spawn_pairs_within_radius_across_cells_and_maps():
    spawns = [(1, 0, 0.0, 0.0, 0.0), (2, 0, 149.0, 0.0, 0.0),  # 149 yd apart, adjacent grid cells
              (3, 0, 0.0, 151.0, 0.0),                           # 151 yd from 1
              (4, 1, 0.0, 0.0, 0.0),                             # same coordinates, other map
              (5, 0, -10.0, -10.0, 0.0), (5, 0, 5000.0, 0.0, 0.0),  # several spawns: the closest counts
              (6, 0, 0.0, 0.0, 200.0)]                           # 3D: 200 yd straight up
    pairs = neighbours.spawn_pairs(spawns, 150)
    assert set(pairs) == {(1, 2), (1, 5)}
    assert pairs[(1, 2)] == pytest.approx(149)
    assert pairs[(1, 5)] == pytest.approx(np.hypot(10, 10))
    assert (1, 3) not in pairs and (1, 4) not in pairs and (1, 6) not in pairs


def _world(tmp_path, quests):
    """A VMaNGOS-shaped quest_template: (entry, patch, PrevQuestId, NextQuestId, NextQuestInChain)."""
    w = sqlite3.connect(tmp_path / "world.sqlite")
    w.execute("CREATE TABLE quest_template (entry INTEGER, patch INTEGER, PrevQuestId INTEGER, NextQuestId INTEGER,"
              " NextQuestInChain INTEGER)")
    w.executemany("INSERT INTO quest_template VALUES (?, ?, ?, ?, ?)", quests)
    w.commit()
    return w


@pytest.fixture
def town(tmp_path):
    """NPCs 1-6 with lines. 1 and 2 stand together; 3 gives quest 100 that 4 ends; 5 gives 200, which follows 100 in
    the world DB (5 -> Neighbour of 3 and 4); 6 gives 300 whose chain link only exists in an older patch."""
    conn = db.connect(tmp_path / "vo.sqlite")
    conn.executemany("INSERT INTO npcs (id, name, race, gender, is_named, level_max) VALUES (?, ?, ?, ?, ?, ?)",
                     [(i, f"NPC {i}", "Orc", "female", 0, 10) for i in range(1, 7)] + [(7, "Mute", "Orc", "female", 0, 1)])
    conn.executemany("INSERT INTO spawns (npc_id, map, x, y, z) VALUES (?, ?, ?, ?, ?)",
                     [(1, 1, 0, 0, 0), (2, 1, 30, 40, 0), (3, 1, 1000, 0, 0), (4, 1, 2000, 0, 0),
                      (5, 1, 3000, 0, 0), (6, 1, 4000, 0, 0), (7, 1, 10, 0, 0)])
    lines = [(1, 1, "gossip", None), (2, 2, "gossip", None), (3, 3, "quest_detail", 100), (4, 4, "quest_complete", 100),
             (5, 4, "quest_progress", 100), (6, 5, "quest_detail", 200), (7, 6, "quest_detail", 300)]
    conn.executemany("INSERT INTO lines (id, npc_id, type, quest_id, raw_text, tts_text) VALUES (?, ?, ?, ?, ?, ?)",
                     [(i, n, t, q, f"Line {i} of the test.", f"Line {i} of the test.") for i, n, t, q in lines])
    conn.commit()
    world = _world(tmp_path, [(100, 0, 0, 200, 0), (200, 0, -100, 0, 0), (300, 0, 0, 100, 0), (300, 5, 0, 0, 0)])
    return conn, world


def test_neighbours_from_spawns_and_quest_chains(town):
    conn, world = town
    s = neighbours.refresh(conn, world, 150)
    got = {(a, b): (r, d) for a, b, r, d in conn.execute("SELECT a, b, reason, distance FROM neighbours")}
    assert got == {(1, 2): ("spawn", 50.0), (3, 4): ("quest", None), (3, 5): ("quest", None), (4, 5): ("quest", None)}
    # NPC 7 has no lines (not voiced); NPC 6's link to 100 is gone at the latest patch.
    assert (s.npcs, s.pairs, s.spawn, s.quest, s.isolated, s.max) == (6, 4, 1, 3, 1, 2)
    assert s.mean == pytest.approx(8 / 6) and "Neighbours per NPC" in s.text()
    # Without the world DB only the giver/ender pair of quest 100 is a quest link.
    s = neighbours.refresh(conn, None, 150)
    assert {(a, b) for a, b in conn.execute("SELECT a, b FROM neighbours")} == {(1, 2), (3, 4)}


def test_neighbour_refresh_keeps_similarities(town):
    conn, world = town
    neighbours.refresh(conn, world)
    conn.execute("UPDATE neighbours SET sim = 0.5 WHERE a = 1 AND b = 2")
    neighbours.refresh(conn, world)
    assert conn.execute("SELECT sim FROM neighbours WHERE a = 1 AND b = 2").fetchone()[0] == 0.5


# --- variation -------------------------------------------------------------------------------------------------------

def npc(i=1, named=False, name="Grunta", subname=None, role="quest", level=20, pinned=None):
    return voices.Npc(i, name, subname, role, 1, level, named, "orc_f", "female", pinned)


def test_variation_is_deterministic_and_bounded():
    a, b = voices.variation(npc(1), 0, 0), voices.variation(npc(1), 0, 0)
    assert a == b and a != voices.variation(npc(1), 0, 1) and a != voices.variation(npc(2), 0, 0)
    assert a.count(",") + a.count(" and ") <= 3  # 2-3 traits for an unnamed NPC
    assert voices.variation(npc(1, role="guard"), 0, 0).startswith("Speaks like a watchful, dutiful guard.")
    for i in range(50):
        s = voices.shift(npc(i), 0, 0, 0)
        assert 0.8 <= abs(s.pitch_st) <= 2.0 and abs(s.formant - 1) <= 0.035 and abs(s.pace - 1) <= 0.06 + 1e-9
        n = voices.shift(npc(i, named=True), 0, 0, 0)
        assert abs(n.pitch_st) <= 3.0 and abs(n.formant - 1) <= 0.05
    assert voices.shift(npc(3), 1, 2, 3) == voices.shift(npc(3), 1, 2, 3) != voices.shift(npc(3), 2, 2, 3)


def test_named_hints_and_pinned_prompt():
    assert voices.name_hints("King Magni Bronzebeard", None) == ["regal and measured"]
    assert voices.name_hints("Thrall", "Warchief") == ["commanding and forceful"]
    assert voices.name_hints("Stormwind Guard", None) == []
    v = voices.variation(npc(1, named=True, name="Thrall", subname="Warchief"), 0, 0)
    assert v.startswith("This one is commanding and forceful.")
    assert "commanding" not in voices.variation(npc(1, named=False, name="Thrall", subname="Warchief"), 0, 0)
    assert voices.variation(npc(1, pinned=" A booming, weary old voice. "), 0, 3) == "A booming, weary old voice."
    assert voices.age_index(5) == 0 and voices.age_index(60) == 3


# --- the constraint --------------------------------------------------------------------------------------------------

def unit(*xs):
    return speaker.normalise(np.array(xs + (0.0,) * (4 - len(xs)), dtype=np.float32))


ARCH = voices.ArchRef("orc_f", "orc_f/g0s0", "a.wav", "You there.", "Orc.", "ultimate", False,
                      embedding=unit(1, 0), f0=150.0, hnr=10.0)
CFG = voices.Config(ceiling=0.8, floor=0.9, max_rerolls=2, f0_band_st=3, hnr_band_db=5, max_wer=0.25)


def cand(k, emb, f0=150.0, hnr=10.0, wer=None, attempt=0):
    return voices.Cand(voices.tag_for(9, 0, attempt, k), voices.seed_for(9, 0, attempt, k), "p", {"strategy": "dsp"},
                       embedding=speaker.normalise(emb), f0=f0, hnr=hnr, wer=wer)


def test_gate_ceiling_pitch_hnr_wer():
    c = voices.score(cand(0, unit(0.5, 1)), ARCH, {}, CFG)
    assert c.reasons and c.reasons[0].startswith("ceiling")
    assert voices.score(cand(0, unit(1, 0.1), f0=252), ARCH, {}, CFG).reasons[0].startswith("pitch")
    assert voices.score(cand(0, unit(1, 0.1), hnr=17), ARCH, {}, CFG).reasons[0].startswith("hnr")
    assert voices.score(cand(0, unit(1, 0.1), wer=0.5), ARCH, {}, CFG).reasons[0].startswith("wer")
    assert voices.score(cand(0, unit(1, 0.1), f0=170, hnr=13, wer=0.1), ARCH, {}, CFG).reasons == []


def test_pick_closest_to_archetype_that_clears_the_floor():
    neighbour = {5: unit(1, 0.3)}
    cands = [cand(0, unit(1, 0.3)),        # the Neighbour's voice
             cand(1, unit(1, 0, 0.45)),    # arch 0.912, Neighbour 0.873: clears, the pick
             cand(2, unit(1, 0, 0.3)),     # arch 0.958 but Neighbour 0.917: over the floor
             cand(3, unit(1, -0.6))]       # arch 0.857, Neighbour 0.673: clears, but further from the Archetype
    ch = voices.resolve(lambda a: cands if a == 0 else pytest.fail("no re-roll needed"), ARCH, neighbour, CFG)
    assert ch.ok and ch.attempt == 0
    clear = [c for c in cands if not c.reasons and c.nmax <= CFG.floor]
    assert ch.cand is cands[1] and clear == [cands[1], cands[3]]
    assert ch.cand.neighbour == 5 and ch.cand.nmax <= 0.9


def test_floor_violation_rerolls_then_reports_a_leftover():
    neighbour = {5: unit(1, 0.05)}
    calls = []

    def attempts(a):
        calls.append(a)
        return [cand(k, unit(1, 0.02 * k), attempt=a) for k in range(3)]  # all ~ the Neighbour
    ch = voices.resolve(attempts, ARCH, neighbour, CFG)
    assert calls == [0, 1, 2] and not ch.ok and ch.issue == "floor" and "Neighbour 5" in ch.detail
    assert ch.tried == 9

    # A re-roll that clears is kept, at its attempt.
    def later(a):
        return [cand(0, unit(1, 0.05), attempt=a)] if a < 2 else [cand(0, unit(1, 0, 0.6), attempt=a)]
    ch = voices.resolve(later, ARCH, neighbour, CFG)
    assert ch.ok and ch.attempt == 2

    # Nothing passes the gate: the leftover names the gate failure.
    ch = voices.resolve(lambda a: [cand(0, unit(0, 1))], ARCH, {}, CFG)
    assert not ch.ok and ch.issue == "ceiling"


# --- builds ----------------------------------------------------------------------------------------------------------

def _tone(f0=150.0, sr=16000, s=1.0):
    t = np.arange(int(sr * s)) / sr
    return (0.3 * np.sin(2 * np.pi * f0 * t) + 0.1 * np.sin(4 * np.pi * f0 * t)).astype(np.float32), sr


class FakeEmbed:
    """Embeddings near a fixed direction, perturbed by a hash of the audio: deterministic, similar but not equal."""

    def __init__(self, spread=0.3):
        self.spread, self.n = spread, 0

    def __call__(self, samples, rate):
        self.n += 1
        rng = np.random.default_rng(zlib.crc32(np.round(np.asarray(samples), 4).tobytes()))
        return speaker.normalise(np.eye(16)[0] + self.spread * rng.standard_normal(16))


def fake_measure(samples, rate, male):
    return {"f0": 150.0, "hnr": 10.0, "centroid": 500}


class FakeEngine:
    def __init__(self):
        self.design_calls, self.continue_calls = [], []

    def design(self, text, description, seed):
        self.design_calls.append((text, description, seed))
        return _tone(120 + seed % 97)

    def continue_(self, text, anchor, anchor_text, seed, mode="cont"):
        self.continue_calls.append((text, str(anchor), anchor_text, seed, mode))
        return _tone()


@pytest.fixture
def built(town, tmp_path):
    """The town with an approved orc_f anchor and its lock data."""
    conn, world = town
    neighbours.refresh(conn, world)
    anchor = tmp_path / "orc_f.wav"
    prepare.write_wav(anchor, *_tone())
    data = {"locked": True, "archetypes": {"orc_f": {
        "candidate": "orc_f/g0s3", "anchor": str(anchor), "transcript": "You there, go.", "mode": "ultimate",
        "description": "An orc woman.", "effect_chain": None}}}
    return conn, data, tmp_path / "voices"


def _build(conn, data, out, **kw):
    kw.setdefault("cfg", voices.Config(ceiling=-1.0, floor=0.97, max_rerolls=1, candidates=2))
    tools = voices.Tools(engine=FakeEngine(), embed=FakeEmbed(), measure=fake_measure, heard=lambda a, r: "You there, go.")
    return voices.build(conn, data, out, tools=tools, log=lambda m: None, **kw), tools


def _voices(conn):
    return {r[0]: (r[1], r[2]) for r in conn.execute("SELECT npc_id, voice_id, embedding FROM voices")}


def test_build_is_deterministic_resumable_and_stores_voices(built, town, tmp_path):
    conn, data, out = built
    s, _ = _build(conn, data, out, limit=4)
    assert s.built == 4
    s, _ = _build(conn, data, out)
    assert s.built == 2  # resumes with the rest
    s, _ = _build(conn, data, out)
    assert s.built == 0
    first = _voices(conn)
    assert set(first) == {1, 2, 3, 4, 5, 6}
    for npc_id, (vid, emb) in first.items():
        assert vid.startswith(f"voxcpm:orc_f@orc_f/g0s3#{npc_id}-r0a")
        assert len(speaker.from_blob(emb)) == 16
    row = conn.execute("SELECT * FROM voices WHERE npc_id = 1").fetchone()
    assert row["archetype"] == "orc_f" and row["prompt"].startswith("dsp: pitch") and Path(row["ref_clip"]).exists()
    b = conn.execute("SELECT * FROM voice_builds WHERE npc_id = 2").fetchone()
    assert b["status"] == "ok" and b["neighbour"] == 1 and b["neighbour_sim"] <= 0.97
    assert conn.execute("SELECT sim FROM neighbours WHERE a = 1 AND b = 2").fetchone()[0] == pytest.approx(
        speaker.cosine(speaker.from_blob(first[1][1]), speaker.from_blob(first[2][1])), abs=1e-4)

    # A fresh DB builds exactly the same voices.
    conn2 = db.connect(tmp_path / "again.sqlite")
    for table in ("npcs", "spawns", "lines", "neighbours"):
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        if rows:
            conn2.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(rows[0]))})", [tuple(r) for r in rows])
    conn2.execute("UPDATE neighbours SET sim = NULL")
    conn2.commit()
    _build(conn2, data, tmp_path / "voices2")
    assert _voices(conn2) == first


def test_build_filters_npc_and_archetype(built):
    conn, data, out = built
    s, _ = _build(conn, data, out, only_npcs={3, 5})
    assert s.built == 2 and set(_voices(conn)) == {3, 5}
    s, _ = _build(conn, data, out, only_archetypes={"troll_m"})
    assert s.built == 0


def test_leftovers_are_listed_not_blocking(built):
    conn, data, out = built
    s, _ = _build(conn, data, out, cfg=voices.Config(ceiling=-1.0, floor=-1.0, max_rerolls=1, candidates=2))
    assert s.built == 6 and s.leftovers == 3  # floor -1: any voiced Neighbour violates it
    left = {r["npc_id"]: r for r in voices.leftovers(conn)}
    assert set(left) == {2, 4, 5} and left[2]["issue"] == "floor" and "Neighbour 1" in left[2]["detail"]
    assert 1 not in left  # built first: no voiced Neighbour yet
    assert set(_voices(conn)) == {1, 2, 3, 4, 5, 6}  # every NPC still gets a voice


def test_reroll_action_rebuilds_with_a_new_roll(built):
    conn, data, out = built
    _build(conn, data, out)
    before = _voices(conn)
    old_clip = conn.execute("SELECT ref_clip FROM voices WHERE npc_id = 2").fetchone()[0]
    conn.execute("INSERT INTO review_actions (action, target) VALUES ('reroll-voice', '2')")
    conn.execute("INSERT INTO review_actions (action, target) VALUES ('reroll-voice', '99')")
    conn.commit()
    assert run.consume_actions(conn, log=lambda m: None) == 0  # left for vo voices
    s, _ = _build(conn, data, out)
    assert s.rerolls == 2 and s.built == 1
    after = _voices(conn)
    assert after[2][0].split("#")[1].startswith("2-r1a") and after[2] != before[2]
    assert {k: v for k, v in after.items() if k != 2} == {k: v for k, v in before.items() if k != 2}
    assert not Path(old_clip).exists()
    assert conn.execute("SELECT COUNT(*) FROM review_actions WHERE consumed_at IS NULL").fetchone()[0] == 0


def test_new_archetype_anchor_makes_npc_voices_stale(built):
    conn, data, out = built
    _build(conn, data, out)
    data2 = {**data, "archetypes": {"orc_f": {**data["archetypes"]["orc_f"], "candidate": "orc_f/g1s0"}}}
    assert voices.drop_stale(conn, data2) == 6 and _voices(conn) == {}
    s, _ = _build(conn, data2, out)
    assert s.built == 6 and all(v.startswith("voxcpm:orc_f@orc_f/g1s0#") for v, _ in _voices(conn).values())


def test_design_strategy_and_pinned_prompt(built):
    conn, data, out = built
    conn.execute("INSERT INTO manual_overrides (npc_id, field, value) VALUES (1, 'voice_prompt', 'Very old and slow.')")
    conn.commit()
    s, tools = _build(conn, data, out, only_npcs={1, 2})
    calls = tools.engine.design_calls
    # NPC 1 is pinned: design with the pinned text, even under the dsp strategy; NPC 2 uses dsp (no model call).
    assert {c[1] for c in calls} == {"An orc woman. Very old and slow."} and len(calls) == 2
    assert all(c[0] == "You there, go." for c in calls)
    s, tools = _build(conn, data, out, only_npcs={3},
                      cfg=voices.Config(strategy="design", ceiling=-1.0, floor=1.0, candidates=3))
    calls = tools.engine.design_calls
    assert [c[2] for c in calls] == [30000, 30001, 30002] and calls[0][1].startswith("An orc woman. ")
    b = conn.execute("SELECT strategy, wer FROM voice_builds WHERE npc_id = 3").fetchone()
    assert b["strategy"] == "design" and b["wer"] == 0


# --- vo run uses per-NPC anchors -------------------------------------------------------------------------------------

def test_run_uses_npc_anchor_and_falls_back_to_archetype(built):
    conn, data, out = built
    _build(conn, data, out, only_npcs={1})
    arch_voice = lock.voice_id("orc_f", data["archetypes"]["orc_f"])
    run.sync_jobs(conn, "kokoro:am_michael", "kokoro:bm_lewis", lock.npc_voice_ids(conn, data))
    jobs = dict(conn.execute("SELECT line_id, voice_id FROM jobs").fetchall())
    own = conn.execute("SELECT voice_id, ref_clip FROM voices WHERE npc_id = 1").fetchone()
    assert jobs[1] == own["voice_id"] and jobs[2] == arch_voice

    eng = FakeEngine()
    backend = voxcpm.Backend(eng, data, voices_dir=out)
    name, voice = tts.split_voice_id(own["voice_id"])
    backend.render("Strength and honour.", voice, 0)
    (text, anchor, transcript, seed, mode), = eng.continue_calls
    assert Path(anchor).resolve() == Path(own["ref_clip"]) and (text, transcript, seed, mode) == (
        "Strength and honour.", "You there, go.", 0, "ultimate")
    backend.render("Go.", tts.split_voice_id(arch_voice)[1], 0)
    assert eng.continue_calls[1][1] == data["archetypes"]["orc_f"]["anchor"]
    with pytest.raises(ValueError, match="run `vo voices`"):
        backend.render("Go.", "orc_f@orc_f/g0s3#99-r0a0k0", 0)

    # The Archetype anchor is replaced: the NPC voice is dropped and its lines requeue on the Archetype's new anchor.
    data2 = {**data, "archetypes": {"orc_f": {**data["archetypes"]["orc_f"], "candidate": "orc_f/g1s0"}}}
    voices.drop_stale(conn, data2)
    run.sync_jobs(conn, "kokoro:am_michael", "kokoro:bm_lewis", lock.npc_voice_ids(conn, data2))
    assert dict(conn.execute("SELECT line_id, voice_id FROM jobs").fetchall())[1] == "voxcpm:orc_f@orc_f/g1s0"


def test_apply_shift_moves_pitch():
    from vo import voicefeat
    a, sr = _tone(150, s=1.5)
    up = voices.apply_shift(a, sr, voices.Shift(2.0, 1.0, 1.1))
    f0 = voicefeat.measure(up, sr, False)["f0"]
    assert 12 * np.log2(f0 / 150) == pytest.approx(2.0, abs=0.3)
    assert len(up) == pytest.approx(len(a) * 1.1, rel=0.03)

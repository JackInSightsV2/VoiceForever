"""Approval Gate (#11): Archetype mapping, vo prepare, review actions, the approved_voices.json lock."""
import json
from pathlib import Path

import numpy as np
import pytest

from vo import archetypes, cli, db, lock, prepare, run, tts, voxcpm

# Every (race, gender) label in the Core Content pipeline DB (SELECT race, gender, count(*) FROM npcs), Sept 2026.
RACE_LABELS = [
    ("Ancient", "male", 7), ("Banshee", "female", 6), ("Blood Elf", "female", 7), ("Blood Elf", "male", 13),
    ("Bogbeast", "male", 1), ("Centaur", "female", 1), ("Centaur", "male", 2), ("Chicken", "male", 3),
    ("Cupid", "male", 1), ("Demon", "female", 1), ("Demon", "male", 5), ("Dragon", "male", 8),
    ("Dreadlord", "male", 2), ("Dryad", "female", 8), ("Dwarf", "female", 41), ("Dwarf", "male", 280),
    ("Elemental", "male", 5), ("Fleshgolem", "male", 3), ("Forceofnature", "male", 1), ("Furbolg", "male", 8),
    ("Ghost", "female", 1), ("Ghost", "male", 1), ("Giant", "male", 2), ("Gilnean", "male", 1), ("Gnoll", "male", 2),
    ("Gnome", "female", 27), ("Gnome", "male", 77), ("Goblin", "female", 23), ("Goblin", "male", 107),
    ("Golem", "male", 4), ("Gorilla", "male", 1), ("High Order Skyborne", "female", 1), ("Human", "female", 129),
    ("Human", "male", 357), ("Keeper", "male", 5), ("Lostone", "male", 2), ("Naga", "female", 1),
    ("Night Elf", "female", 143), ("Night Elf", "male", 137), ("Nightmare", "male", 1), ("Ogre", "male", 22),
    ("Orc", "female", 48), ("Orc", "male", 210), ("Questobjects", "male", 2), ("Quilboar", "male", 1),
    ("Salamander", "male", 1), ("Satyr", "male", 2), ("Skeleton", "male", 1), ("Snowman", "male", 1),
    ("Stonekeeper", "male", 2), ("Tauren", "female", 62), ("Tauren", "male", 163), ("Troglodyte", "male", 1),
    ("Troll", "female", 28), ("Troll", "male", 77), ("Undead", "female", 61), ("Undead", "male", 147),
    ("Wisp", "male", 1), ("Wolf", "male", 1),
]


@pytest.fixture
def world(tmp_path):
    """A DB with one NPC (and one line) per race label."""
    conn = db.connect(tmp_path / "vo.sqlite")
    for i, (race, gender, _) in enumerate(RACE_LABELS, 1):
        conn.execute("INSERT INTO npcs (id, name, race, gender) VALUES (?, ?, ?, ?)", (i, f"NPC {i}", race, gender))
        conn.execute("INSERT INTO lines (id, npc_id, type, raw_text, tts_text) VALUES (?, ?, 'gossip', ?, ?)",
                     (i, i, f"Line {i}", f"Well met, friend. This is line number {i}, spoken for the test."))
    conn.commit()
    return conn


def test_every_race_label_maps():
    for race, gender, _ in RACE_LABELS:
        aid = archetypes.archetype_id(race, gender)
        assert aid == archetypes.NARRATOR or aid in archetypes.STYLE, (race, gender, aid)


def test_archetype_list(world):
    got = {a.id: a for a in archetypes.derive(world)}
    races = {f"{r}_{g[0]}" for r in ("human", "dwarf", "nightelf", "orc", "tauren", "undead", "goblin", "troll",
                                     "gnome", "bloodelf") for g in ("m", "f")} | {"ogre_m"}
    families = {"great_beasts_m", "great_beasts_f", "ancients_m", "spirits_m", "spirits_f", "wild_folk_m",
                "wild_folk_f", "fey_f", "naga_f", "undead_constructs_m", "oddities_m"}
    assert set(got) == races | families
    assert len(got) == 32
    assert got["great_beasts_m"].races == {"Demon male": 1, "Dragon male": 1, "Dreadlord male": 1, "Giant male": 1}
    assert got["human_m"].races == {"Gilnean male": 1, "Human male": 1}
    assert "High Order Skyborne female" in got["spirits_f"].races
    assert got["fey_f"].kind == "family" and got["orc_f"].kind == "race" and got["orc_f"].label == "Orc, female"
    # Questobjects speak with the Narrator, not an Archetype.
    npc = next(i for i, (r, *_) in enumerate(RACE_LABELS, 1) if r == "Questobjects")
    assert archetypes.npc_archetypes(world)[npc] == archetypes.NARRATOR
    # Every derived Archetype has its own style guide entry and a valid mode.
    for aid in got:
        assert aid in archetypes.STYLE
        assert archetypes.STYLE[aid].mode in voxcpm.MODES


def test_unmapped_race_is_named(world):
    world.execute("INSERT INTO npcs (id, race, gender) VALUES (999, 'Murloc', 'male')")
    with pytest.raises(archetypes.UnmappedRace, match="Murloc"):
        archetypes.derive(world)


def test_families_gendered_only_where_npcs_are():
    assert archetypes.archetype_id("Dragon", "male") == "great_beasts_m"
    assert archetypes.archetype_id("Demon", "female") == "great_beasts_f"
    assert archetypes.style("fey_m").description.startswith("The man of this kind")  # a later Capture gender


def test_bakeoff_winners_are_kept():
    from vo.bakeoff import round2, round3
    for vid in ("human_m", "human_f", "orc_f", "troll_m", "troll_f"):
        assert archetypes.STYLE[vid].description == round2.voice(vid).prompt
    assert archetypes.STYLE["dwarf_f"].description == round3.DWARF_F_V2


# --- vo prepare with a fake engine -----------------------------------------------------------------------------------

class FakeEngine:
    def __init__(self):
        self.design_calls, self.continue_calls = [], []

    @staticmethod
    def _tone(seed: int) -> tuple[np.ndarray, int]:
        sr = 16000
        t = np.arange(int(0.8 * sr)) / sr
        rng = np.random.default_rng(seed)
        a = 0.3 * np.sin(2 * np.pi * (140 + seed % 50) * t) + 0.01 * rng.standard_normal(len(t))
        return a.astype(np.float32), sr

    def design(self, text, description, seed):
        self.design_calls.append((text, description, seed))
        return self._tone(seed)

    def continue_(self, text, anchor, anchor_text, seed, mode="cont"):
        self.continue_calls.append((text, str(anchor), anchor_text, seed, mode))
        return self._tone(seed)


class FakeASR:
    def __init__(self):
        self.n = 0

    def transcribe(self, wav):
        self.n += 1
        return "hello there"


@pytest.fixture
def small(tmp_path):
    """Two Archetypes (orc_f, troll_m), a Questobjects NPC and a Narrator line."""
    conn = db.connect(tmp_path / "vo.sqlite")
    conn.executemany("INSERT INTO npcs (id, name, race, gender) VALUES (?, ?, ?, ?)",
                     [(1, "Grunta", "Orc", "female"), (2, "Zjin", "Troll", "male"), (3, "Crystal", "Questobjects", "male")])
    rows = [(10, 1, "gossip", "Strength and honour. The Horde needs every blade it can find, so sharpen yours."),
            (11, 1, "quest_detail", "Go to the ravine and bring back ten quilboar tusks. We will make trophies of them."),
            (12, 1, "quest_complete", "Good work. The warriors will sing of this for a night or two, at least."),
            (20, 2, "gossip", "Ah, mon, you be lookin' for somethin'? De loa be watchin' you, so choose careful."),
            (21, 2, "quest_progress", "You got dem raptor feathers yet? Da ritual can't wait forever, mon."),
            (30, 3, "quest_detail", "A cracked crystal pulses faintly with necrotic energy. Bring it to the camp."),
            (40, None, "quest_detail", "Wanted: the bandit leader Hogger, for crimes against Elwynn Forest.")]
    conn.executemany("INSERT INTO lines (id, npc_id, type, raw_text, tts_text) VALUES (?, ?, ?, ?, ?)",
                     [(i, n, t, x, x) for i, n, t, x in rows])
    conn.commit()
    return conn


def _prepare(conn, tmp_path, engine, **kw):
    narrated = []

    def narrator(text):
        narrated.append(text)
        return FakeEngine._tone(7)

    kw.setdefault("candidates", 2)
    kw.setdefault("samples", 2)
    s = prepare.prepare(conn, tmp_path / "candidates", engine=engine, asr_backend=FakeASR(), narrator=narrator,
                        lock_path=tmp_path / "approved_voices.json", log=lambda m: None, **kw)
    return s, narrated


def test_prepare_renders_and_is_idempotent(small, tmp_path):
    eng = FakeEngine()
    s, narrated = _prepare(small, tmp_path, eng)
    assert (s.archetypes, s.approved, s.lock) == (2, 0, "open")
    # The Narrator's sample, then the Lexicon's one top name (Elwynn, in a Narrator line: no NPC Archetype).
    assert narrated == ["Wanted: the bandit leader Hogger, for crimes against Elwynn Forest."] * 2
    assert s.names == 1 and s.name_samples == 1
    # 2 Candidates per Archetype, seeded 0 and 1, reading the Archetype's anchor line with its description.
    assert sorted((d[2], d[1] == archetypes.STYLE["orc_f"].description) for d in eng.design_calls) == [
        (0, False), (0, True), (1, False), (1, True)]
    assert all(d[0] in (archetypes.A_ORC, archetypes.A_TROLL) for d in eng.design_calls)
    # 2 samples per Candidate, as continuation from its anchor, in the Archetype's mode.
    assert len(eng.continue_calls) == 8
    orc = [c for c in eng.continue_calls if "orc_f" in c[1]]
    assert {c[4] for c in orc} == {"ultimate"} and {c[3] for c in orc} == {1, 2}
    assert {c[0] for c in orc} <= {r[0] for r in small.execute("SELECT tts_text FROM lines WHERE npc_id = 1")}
    cands = small.execute("SELECT * FROM candidates WHERE archetype = 'orc_f' ORDER BY id").fetchall()
    assert [c["id"] for c in cands] == ["orc_f/g0s0", "orc_f/g0s1"]
    assert all(Path(c["path"]).exists() and c["f0"] and c["wer"] is not None for c in cands)
    assert small.execute("SELECT COUNT(*) FROM candidate_samples").fetchone()[0] == 8

    eng2 = FakeEngine()
    s2, narrated2 = _prepare(small, tmp_path, eng2)
    assert (eng2.design_calls, eng2.continue_calls, narrated2) == ([], [], [])
    assert (s2.candidates, s2.samples) == (0, 0)

    # A deleted file is rendered again (resumable), and --archetype limits rendering.
    Path(cands[1]["path"]).unlink()
    eng3 = FakeEngine()
    _prepare(small, tmp_path, eng3, only=["troll_m"])
    assert eng3.design_calls == []
    _prepare(small, tmp_path, eng3, only=["orc_f"])
    assert [d[2] for d in eng3.design_calls] == [1]
    with pytest.raises(ValueError, match="unknown Archetype"):
        _prepare(small, tmp_path, eng3, only=["murloc_m"])


def _act(conn, action, target, payload=None):
    conn.execute("INSERT INTO review_actions (action, target, payload) VALUES (?, ?, ?)",
                 (action, target, json.dumps(payload) if payload else None))
    conn.commit()


def test_review_actions_approve_reject_regenerate(small, tmp_path):
    _prepare(small, tmp_path, FakeEngine())
    _act(small, "approve-candidate", "orc_f/g0s1")
    _act(small, "reject-candidate", "troll_m/g0s0")
    _act(small, "approve-candidate", "nope/g0s0")  # invalid: dropped
    _act(small, "retry-line", "10")  # vo run's: left queued
    eng = FakeEngine()
    s, _ = _prepare(small, tmp_path, eng)
    assert s.actions == 3 and s.approved == 1
    assert small.execute("SELECT approved FROM archetypes WHERE id = 'orc_f'").fetchone()[0] == "orc_f/g0s1"
    status = dict(small.execute("SELECT id, status FROM candidates").fetchall())
    assert status["orc_f/g0s1"] == "approved" and status["troll_m/g0s0"] == "rejected"
    assert small.execute("SELECT COUNT(*) FROM review_actions WHERE consumed_at IS NULL").fetchone()[0] == 1
    assert eng.design_calls == []  # nothing new to render

    # Regenerate with a note: appended to the description, a fresh generation with new seeds, approval withdrawn.
    _act(small, "regenerate-archetype", "orc_f", {"note": "deeper, less theatrical"})
    eng = FakeEngine()
    _prepare(small, tmp_path, eng)
    a = small.execute("SELECT * FROM archetypes WHERE id = 'orc_f'").fetchone()
    assert a["generation"] == 1 and a["approved"] is None
    assert a["description"] == archetypes.STYLE["orc_f"].description + " deeper, less theatrical."
    assert json.loads(a["notes"]) == ["deeper, less theatrical"]
    assert sorted(d[2] for d in eng.design_calls) == [1000, 1001]
    assert {d[1] for d in eng.design_calls} == {a["description"]}
    status = dict(small.execute("SELECT id, status FROM candidates").fetchall())
    assert status["orc_f/g0s0"] == status["orc_f/g0s1"] == "superseded"
    assert status["orc_f/g1s0"] == "pending"

    # Every Candidate of a generation rejected: the next prepare renders a new generation.
    _act(small, "reject-candidate", "troll_m/g0s1")
    eng = FakeEngine()
    _prepare(small, tmp_path, eng)
    assert sorted(d[2] for d in eng.design_calls) == [1000, 1001]
    assert small.execute("SELECT generation FROM archetypes WHERE id = 'troll_m'").fetchone()[0] == 1


def test_vo_run_leaves_approval_actions_for_prepare(small):
    _act(small, "approve-candidate", "orc_f/g0s0")
    logged = []
    assert run.consume_actions(small, logged.append) == 0
    assert logged == []
    assert set(run.PREPARE_ACTIONS) == set(prepare.ACTIONS)


def test_lock_written_once_all_approved_and_gates_run(small, tmp_path, monkeypatch, capsys):
    path = tmp_path / "approved_voices.json"
    _prepare(small, tmp_path, FakeEngine())
    _act(small, "approve-candidate", "orc_f/g0s0")
    s, _ = _prepare(small, tmp_path, FakeEngine())
    assert s.lock == "open" and not path.exists()
    with pytest.raises(lock.LockError, match="not found"):
        lock.require(small, path)

    _act(small, "approve-candidate", "troll_m/g0s1")
    s, _ = _prepare(small, tmp_path, FakeEngine())
    assert s.lock == "written"
    data = lock.require(small, path)
    assert set(data["archetypes"]) == {"orc_f", "troll_m"}
    t = data["archetypes"]["troll_m"]
    assert (t["candidate"], t["seed"], t["mode"], t["transcript"]) == ("troll_m/g0s1", 1, "cont", archetypes.A_TROLL)
    assert t["description"] == archetypes.STYLE["troll_m"].description and Path(t["anchor"]).exists()
    assert t["voice_id"] == "voxcpm:troll_m@troll_m/g0s1"
    assert data["narrator"] == {"voice_id": "kokoro:bm_lewis"}
    assert not path.stat().st_mode & 0o222  # read-only
    stamp = data["locked_at"]
    s, _ = _prepare(small, tmp_path, FakeEngine())
    assert s.lock == "unchanged" and lock.load(path)["locked_at"] == stamp

    # NPCs speak with their Archetype's anchor; Questobjects with the Narrator; a per-NPC voice wins (#13 hook).
    voices = lock.npc_voice_ids(small, data)
    assert voices == {1: "voxcpm:orc_f@orc_f/g0s0", 2: "voxcpm:troll_m@troll_m/g0s1", 3: "kokoro:bm_lewis"}
    small.execute("INSERT INTO voices (npc_id, voice_id) VALUES (2, 'voxcpm:npc-2@x')")
    run.sync_jobs(small, "kokoro:am_michael", "kokoro:bm_lewis", voices)
    jobs = dict(small.execute("SELECT line_id, voice_id FROM jobs").fetchall())
    assert jobs[10] == "voxcpm:orc_f@orc_f/g0s0" and jobs[20] == "voxcpm:npc-2@x"
    assert jobs[30] == jobs[40] == "kokoro:bm_lewis"

    # vo run starts only with the lock.
    monkeypatch.setenv(lock.ENV, str(tmp_path / "missing.json"))
    with pytest.raises(SystemExit) as e:
        cli.main(["--db", str(tmp_path / "vo.sqlite"), "run", "--no-caffeinate"])
    assert "vo run refuses to start" in str(e.value) and "Approval" in str(e.value)

    # Re-opening an Archetype removes the lock.
    _act(small, "regenerate-archetype", "troll_m", {"note": "raspier"})
    s, _ = _prepare(small, tmp_path, FakeEngine())
    assert s.lock == "open" and not path.exists()


def test_lock_requires_every_needed_archetype(small, tmp_path):
    path = tmp_path / "approved_voices.json"
    anchor = tmp_path / "a.wav"
    anchor.write_bytes(b"RIFF")
    lock.write(path, {"locked": True, "archetypes": {"orc_f": {"candidate": "orc_f/g0s0", "anchor": str(anchor)}}})
    with pytest.raises(lock.LockError, match="troll_m"):
        lock.require(small, path)
    lock.write(path, {"locked": False, "archetypes": {}})
    with pytest.raises(lock.LockError, match="not a locked"):
        lock.load(path)


def test_voxcpm_backend_continues_from_the_locked_anchor(tmp_path):
    eng = FakeEngine()
    data = {"locked": True, "archetypes": {"orc_f": {"candidate": "orc_f/g0s3", "anchor": "/x/a.wav",
                                                      "transcript": "You there.", "mode": "ultimate",
                                                      "effect_chain": None}}}
    b = voxcpm.Backend(eng, data)
    audio, sr = b.render("Go now.", "orc_f@orc_f/g0s3", 2, tts.delivery("quest_complete"))
    assert sr == 16000 and len(audio)
    assert eng.continue_calls == [("Go now.", "/x/a.wav", "You there.", 2, "ultimate")]
    with pytest.raises(ValueError, match="not an approved anchor"):
        b.render("Go now.", "orc_f@orc_f/g0s1", 2)
    assert tts.split_voice_id("voxcpm:orc_f@orc_f/g0s3") == ("voxcpm", "orc_f@orc_f/g0s3")


def test_continuation_kwargs_and_chunks():
    assert voxcpm.continuation_kwargs("a.wav", "Hi.", "cont") == {"prompt_audio": "a.wav", "prompt_text": "Hi."}
    assert voxcpm.continuation_kwargs("a.wav", "Hi.", "ultimate")["ref_audio"] == "a.wav"
    with pytest.raises(ValueError):
        voxcpm.continuation_kwargs("a.wav", "Hi.", "design")
    assert voxcpm.chunk("One. Two! Three?", 9) == ["One. Two!", "Three?"]


def test_dialect_wer():
    assert prepare.dialect_wer("Ah, mon, de spirits told me you be comin'.", "Ah man, the spirits told me you be coming.") == 0
    assert prepare.describe("Base.", ["deeper", "less theatrical."]) == "Base. deeper. less theatrical."

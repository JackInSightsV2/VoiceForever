"""Game voice Candidates (ADR-0007): listfile filtering, speaker grouping, anchor assembly, the cached Library, and
`vo prepare --import-gamevoice`."""
import json
from pathlib import Path

import numpy as np
import pytest

from vo import basevoices, db, gamevoice as gv, lock, prepare

from test_approval import FakeASR, FakeEngine, _act, _prepare

LISTFILE = """\
557842;sound/creature/orcmalestandardnpc/orcmalestandardnpcpissed01.ogg
557846;sound/creature/orcmalestandardnpc/orcmalestandardnpcgreeting01.ogg
557847;sound/creature/orcmalestandardnpc/orcmalestandardnpcgreeting02.ogg
557848;sound/creature/orcmalestandardnpc/orcmalestandardnpcvendor01.ogg
557850;sound/creature/orcmalestandardnpc/orcmalestandardnpcfarewell01.ogg
557851;sound/creature/orcmalestandardnpc/orcmalestandardnpcfarewell01.ogg.meta
557799;sound/creature/orcmaleguardnpc/orcmaleguardnpcfarewell02.ogg
557802;sound/creature/orcmaleguardnpc/orcmaleguardnpcgreeting01.ogg
552133;sound/creature/humanfemalestandardnpc/humanfemalestandardnpcgreeting01.ogg
1;sound/creature/npcbloodelfmalenoble/npcbloodelfmalenoblegreeting01.ogg
2;sound/creature/npcdeathknightmalelow01/npcdeathknightmalelow01greeting01.ogg
3;sound/creature/goblinguardm/vo_goblinguardm_greeting01.ogg
4;sound/creature/goblinmaleguardnpc/goblinmaleguardnpcgreeting01.ogg
5;sound/creature/gilneanvenf/vo_gilneanvenf_vendor01.ogg
6;sound/creature/ogre/mogreaggro1.ogg
7;sound/creature/murloc/mmurlocaggro.ogg
8;sound/character/orc/orcvocalmale/orcmalehello01.ogg
9;sound/creature/orcmalestandardnpc/orcmalestandardnpcattack01.ogg
"""


@pytest.fixture
def listfile(tmp_path):
    p = tmp_path / "listfile.csv"
    p.write_text(LISTFILE)
    return p


def test_kits_from_the_listfile(listfile):
    kits = gv.find_kits(gv.read_listfile(listfile))
    assert sorted(kits) == ["bloodelf_m", "goblin_m", "human_f", "ogre_m", "orc_m"]
    orc = {k.key: k for k in kits["orc_m"]}
    assert list(orc) == ["guard", "standard"]
    # NPC voice sets keep their spoken kinds only (no .meta, no attack grunt), in path order.
    assert [(c.fdid, c.kind) for c in orc["standard"].clips] == [
        (557850, "farewell"), (557846, "greeting"), (557847, "greeting"), (557842, "pissed"), (557848, "vendor")]
    assert orc["standard"].source == "npc" and orc["standard"].folder == "orcmalestandardnpc"
    assert [k.key for k in kits["bloodelf_m"]] == ["noble"]
    assert [k.key for k in kits["human_f"]] == ["gilnean-vendor", "standard"]  # Gilnean is Human
    # Two goblin guard sets: keyed by folder so their Candidate ids differ.
    assert sorted(k.key for k in kits["goblin_m"]) == ["goblinguardm", "goblinmaleguardnpc"]
    # A listed creature folder keeps every clip (the transcript check sorts speech from grunts).
    (ogre,) = kits["ogre_m"]
    assert (ogre.source, ogre.key, [c.kind for c in ogre.clips]) == ("creature", "ogre", ["other"])
    # Death knight voice sets and unlisted creatures aren't Archetypes.
    assert gv.npc_kit("sound/creature/npcdeathknightmalelow01/x_greeting01.ogg") is None
    assert all(c.fdid not in (2, 7, 8) for ks in kits.values() for k in ks for c in k.clips)


def test_spoken():
    assert gv.spoken("Lok'tar, friend.", 1.0)
    assert gv.spoken("What do you want?", 1.2)
    assert not gv.spoken("", 1.0)
    assert not gv.spoken("Argh! Ugh...", 1.0)  # a grunt, written as interjections
    assert not gv.spoken("Thank you.", 1.0)  # a stock Whisper hallucination
    assert not gv.spoken("one two three four five six seven eight nine ten", 1.0)  # too many words for 1 s
    assert gv.clean_text("  well met  ") == "well met." and gv.clean_text("Hail!") == "Hail!"


def _unit(*v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_keep_clips_drops_the_odd_one_out():
    embs = [_unit(1, 0.05, 0), _unit(1, 0, 0.05), _unit(1, 0.02, 0.02), _unit(0, 1, 0)]
    assert gv.keep_clips(embs) == [True, True, True, False]
    assert gv.keep_clips(embs[2:]) == [True, True]  # too few to judge


def test_group_merges_one_person_complete_linkage():
    embs = {"a": _unit(1, 0, 0), "b": _unit(1, 0.1, 0), "c": _unit(0, 1, 0), "d": _unit(0.93, 0.37, 0)}
    # a~b (0.995), a~d (0.93) but b~d below threshold -> complete linkage keeps d apart when same=0.96
    assert gv.group(["a", "b", "c", "d"], embs, same=0.96) == [["a", "b"], ["c"], ["d"]]
    assert gv.group(["a", "b", "c", "d"], embs, same=0.90) == [["a", "b", "d"], ["c"]]
    assert gv.group(["a", "c"], embs, same=0.99) == [["a"], ["c"]]
    # Per-kit floors (each kit's split-half similarity): a pair merges when it's as alike as the looser kit is with
    # itself. a~b 0.995 passes 0.99; a~d 0.93 fails d's 0.95 but passes 0.92.
    assert gv.group(["a", "b", "c", "d"], embs, {"a": 0.99, "b": 0.99, "d": 0.95}) == [["a", "b"], ["c"], ["d"]]
    assert gv.group(["a", "d"], embs, {"a": 0.99, "d": 0.92}) == [["a", "d"]]
    assert gv.group(["a", "d"], embs, {"a": 0.99}) == [["a"], ["d"]]  # d unmeasured: SAME_SPEAKER


def test_split_half():
    same = [_unit(1, 0.01 * i, 0) for i in range(6)]
    assert gv.split_half(same) > 0.999 and gv.split_half(same[:3]) is None
    drift = [_unit(1, 0, 0), _unit(1, 0.2, 0), _unit(1, 0, 0.2), _unit(1, 0.2, 0.2)]
    assert gv.split_half(drift) == pytest.approx(gv._cos(gv.centroid(drift[0::2]), gv.centroid(drift[1::2])))


def test_rank_prefers_npc_sets_and_the_standard_kit():
    k = lambda key, src, folder: gv.Kit("orc_m", folder, key, src)
    kits = [k("ogre", "creature", "ogre"), k("guard", "npc", "g"), k("shady", "npc", "s"), k("standard", "npc", "x")]
    got = gv.rank(kits, {"g": 5.0, "s": 9.0, "x": 1.0, "ogre": 30.0})
    assert [x.key for x in got] == ["standard", "shady", "guard", "ogre"]


def _part(fdid, kind, seconds, text="Well met.", level=0.1):
    t = np.arange(int(seconds * gv.RATE)) / gv.RATE
    return gv.Part(fdid, kind, (level * np.sin(2 * np.pi * 150 * t)).astype(np.float32), text)


def test_pick_and_assemble_an_anchor():
    parts = [_part(1, "pissed", 2.0, "Stop that!"), _part(2, "greeting", 2.0, "Hail"), _part(3, "vendor", 3.0),
             _part(4, "greeting", 2.0, "Lok'tar"), _part(5, "farewell", 2.0, "Go"), _part(6, "farewell", 9.0)]
    chosen = gv.pick(parts, target_s=8.0, max_s=10.0, gap_s=0.3)
    # Greetings first, then farewells, skipping one that would pass max_s, until target_s; played greeting-last.
    assert [p.fdid for p in chosen] == [3, 5, 4, 2]
    samples, text = gv.assemble(chosen, gap_s=0.3)
    assert len(samples) == int(9.0 * gv.RATE) + 3 * int(0.3 * gv.RATE)
    assert text == "Well met. Go. Lok'tar. Hail."
    # Every clip at one loudness, and the whole under the peak ceiling.
    loud = gv.assemble([_part(1, "greeting", 1.0, level=0.9), _part(2, "greeting", 1.0, level=0.01)])[0]
    a, b = loud[: gv.RATE], loud[-gv.RATE:]
    assert abs(np.sqrt(np.mean(a ** 2)) - np.sqrt(np.mean(b ** 2))) < 1e-3
    assert np.max(np.abs(loud)) <= gv.PEAK + 1e-6
    with pytest.raises(ValueError):
        gv.assemble([])


class FakeLib:
    """The pieces a Library calls out to: a fetch that writes a tone as Ogg (FileDataIDs above 900 aren't in the
    build), a transcriber, an embedder (per speaker: kits named *guard* and *standard* are one person)."""

    def __init__(self):
        self.fetched, self.heard, self.embedded = [], [], 0

    def fetch(self, fdid, dest):
        from pedalboard.io import AudioFile
        self.fetched.append(fdid)
        if fdid > 900:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        t = np.arange(int(1.6 * 22050)) / 22050
        with AudioFile(str(dest), "w", 22050, 1) as f:
            f.write((0.2 * np.sin(2 * np.pi * (100 + fdid) * t)).astype(np.float32)[None])
        return True

    def hear(self, wav):
        fdid = int(Path(wav).stem)
        self.heard.append(fdid)
        return "Argh!" if fdid % 10 == 9 else f"Line {fdid} of mine."

    def embed(self, samples, rate):
        self.embedded += 1
        return _unit(1, 0, 0)


def _kits(aid="orc_m"):
    return {aid: [gv.Kit(aid, "orcmaleguardnpc", "guard", "npc", [gv.Clip(i, f"g/{i}", "greeting") for i in (11, 12, 13, 19)]),
                  gv.Kit(aid, "orcmaleshadynpc", "shady", "npc", [gv.Clip(i, f"s/{i}", "farewell") for i in (21, 22, 23, 24, 925)]),
                  gv.Kit(aid, "ogre", "ogre", "creature", [gv.Clip(931, "o/931", "other")])]}


def _library(root, fake, kits=None):
    return gv.Library(root, root / "none.csv", heard=fake.hear, embed=fake.embed, fetch=fake.fetch,
                      kits=kits or _kits(), log=lambda m: None)


def test_library_builds_anchors_and_caches_them(tmp_path, monkeypatch):
    fake = FakeLib()
    monkeypatch.setattr(gv, "MIN_ANCHOR_S", 2.0)
    anchors = _library(tmp_path, fake).anchors("orc_m")
    # One speaker: both kits embed alike (merged); the grunt (x9) and missing clips (>900) are dropped.
    (a,) = anchors
    assert (a.key, a.candidate, a.folders) == ("shady_guard", "orc_m/gv-shady_guard", ["orcmaleshadynpc", "orcmaleguardnpc"])
    assert a.label == "game voice: shady, guard"  # the kit with more speech first
    assert a.fdids == [24, 23, 22, 21, 13, 12, 11]  # greetings picked first, played last
    assert a.transcript.startswith("Line 24 of mine. Line 23") and a.transcript.endswith("Line 11 of mine.")
    assert Path(a.path).exists()
    assert 5.0 <= a.duration_s <= gv.MAX_S
    clips = json.loads((tmp_path / "clips.json").read_text())
    assert clips["925"]["missing"] is True and clips["19"]["spoken"] is False and clips["11"]["text"] == "Line 11 of mine."
    plan = json.loads((tmp_path / "anchors/orc_m/plan.json").read_text())
    assert plan["kits"] == {"orcmaleguardnpc": 4, "orcmaleshadynpc": 5, "ogre": 1} and len(plan["anchors"]) == 1
    # A second Library reads the plan: nothing fetched, heard or embedded again.
    again = FakeLib()
    assert _library(tmp_path, again).anchors("orc_m") == anchors
    assert (again.fetched, again.heard, again.embedded) == ([], [], 0)
    # An Archetype without kits gets none.
    assert _library(tmp_path, FakeLib()).anchors("human_f") == []


def test_library_keeps_distinct_speakers_apart_and_caps_them(tmp_path, monkeypatch):
    fake = FakeLib()
    monkeypatch.setattr(gv, "MIN_ANCHOR_S", 2.0)

    def embed(samples, rate):  # one person per kit: FakeLib's tone is 100 + fdid Hz, and kit i has fdids 10i+j
        hz = np.argmax(np.abs(np.fft.rfft(samples))) * rate / len(samples)
        return np.eye(16, dtype=np.float32)[int(round(hz - 100)) // 10]

    fake.embed = embed
    kits = {"orc_m": [gv.Kit("orc_m", f"k{i}", f"k{i}", "npc", [gv.Clip(10 * i + j, f"k{i}/{j}", "greeting")
                                                                 for j in range(1, 5)]) for i in range(1, 11)]}
    anchors = _library(tmp_path, fake, kits).anchors("orc_m")
    assert len(anchors) == gv.MAX_SPEAKERS == basevoices.MAX
    assert len({a.candidate for a in anchors}) == gv.MAX_SPEAKERS and all("_" not in a.key for a in anchors)


# --- vo prepare --import-gamevoice -----------------------------------------------------------------------------------

class StubLibrary:
    def __init__(self, anchors):
        self._anchors, self.calls = anchors, 0

    def anchors(self, aid):
        self.calls += 1
        return [a for a in self._anchors if a.archetype == aid]


@pytest.fixture
def orc(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    conn.execute("INSERT INTO npcs (id, name, race, gender) VALUES (1, 'Grok', 'Orc', 'male')")
    rows = [(10, "gossip", "Lok'tar. The Horde stands strong, and so must you, if you want to live out here."),
            (11, "quest_detail", "The quilboar raid our caravans. Take your axe and teach them fear, then return.")]
    conn.executemany("INSERT INTO lines (id, npc_id, type, raw_text, tts_text) VALUES (?, 1, ?, ?, ?)",
                     [(i, t, x, x) for i, t, x in rows])
    conn.commit()
    anchors = []
    for i, key in enumerate(("standard", "guard_shady")):
        wav = tmp_path / "gamevoice" / f"{key}.wav"
        prepare.write_wav(wav, *FakeEngine._tone(40 + i))
        anchors.append(gv.Anchor("orc_m", key, f"game voice: {key}", f"WoW NPC voice set {key}", str(wav),
                                 f"Transcript of {key}.", 0.8, [i], [key]))
    return conn, StubLibrary(anchors)


def test_import_gamevoice_adds_candidates_and_is_idempotent(orc, tmp_path, monkeypatch):
    from vo import effects
    conn, lib = orc
    chained = []
    monkeypatch.setitem(effects.CHAINS, "orc", lambda s, r: chained.append(1) or s)
    eng = FakeEngine()
    s, _ = _prepare(conn, tmp_path, eng, only=["orc_m"], import_gamevoice=True, gamevoice_library=lib,
                    gamevoice_mode="ultimate")
    cands = {c["id"]: c for c in conn.execute("SELECT * FROM candidates WHERE id LIKE 'orc_m/gv-%' ORDER BY id")}
    assert list(cands) == ["orc_m/gv-guard_shady", "orc_m/gv-standard"]
    c = cands["orc_m/gv-standard"]
    assert (c["label"], c["anchor_text"], c["mode"], c["anchor_chain"], c["status"], c["seed"]) == (
        "game voice: standard", "Transcript of standard.", "ultimate", None, "pending", 0)
    assert Path(c["path"]) == (tmp_path / "candidates/orc_m/gamevoice/standard.wav").resolve()
    assert chained == []  # orc_m's anchor chain is for designed voices, not the game's
    assert c["asr"] == "hello there" and c["wer"] == 1.0  # the anchor is heard against its own transcript
    # Samples continue from the game voice anchor with its transcript and its own mode (orc_m's is cont).
    gv_calls = [x for x in eng.continue_calls if "/gamevoice/" in x[1]]
    assert len(gv_calls) == 2 * 2 and {x[4] for x in gv_calls} == {"ultimate"}
    assert {x[2] for x in gv_calls} == {"Transcript of standard.", "Transcript of guard_shady."}
    assert s.candidates >= 2

    # Approved, it's a Base Voice whose lock entry carries its own transcript and mode.
    _act(conn, "approve-candidate", "orc_m/gv-standard")
    eng = FakeEngine()
    s, _ = _prepare(conn, tmp_path, eng, only=["orc_m"], import_gamevoice=True, gamevoice_library=lib)
    assert (s.candidates, s.samples) == (0, 0) and not [x for x in eng.continue_calls if "/gamevoice/" in x[1]]
    e = lock.find(prepare.lock_data(conn, ["orc_m"]), "orc_m", "orc_m/gv-standard")
    assert (e["transcript"], e["mode"], e["voice_id"]) == ("Transcript of standard.", "ultimate",
                                                          "voxcpm:orc_m@orc_m/gv-standard")

    # A regenerate supersedes the pending one; a re-import brings it back into the new generation, unrendered.
    _act(conn, "regenerate-archetype", "orc_m", {"note": "more growl"})
    _prepare(conn, tmp_path, FakeEngine(), only=["orc_m"])
    assert conn.execute("SELECT status FROM candidates WHERE id = 'orc_m/gv-guard_shady'").fetchone()[0] == "superseded"
    eng = FakeEngine()
    s, _ = _prepare(conn, tmp_path, eng, only=["orc_m"], import_gamevoice=True, gamevoice_library=lib)
    row = conn.execute("SELECT status, generation FROM candidates WHERE id = 'orc_m/gv-guard_shady'").fetchone()
    assert tuple(row) == ("pending", 1) and s.candidates == 0
    assert conn.execute("SELECT status FROM candidates WHERE id = 'orc_m/gv-standard'").fetchone()[0] == "approved"


def test_the_base_voice_cap_counts_game_voices(orc, tmp_path, monkeypatch):
    conn, lib = orc
    monkeypatch.setattr(basevoices, "MAX", 1)
    _prepare(conn, tmp_path, FakeEngine(), only=["orc_m"], import_gamevoice=True, gamevoice_library=lib)
    ids = [r[0] for r in conn.execute("SELECT id FROM candidates WHERE archetype = 'orc_m' ORDER BY id")]
    assert len(ids) == 2
    for cid in ids:
        _act(conn, "approve-candidate", cid)
    logs = []
    prepare.consume_actions(conn, logs.append)
    assert prepare.base_voices(conn, "orc_m") == ids[:1] and "at most 1" in logs[-1]


def test_import_gamevoice_needs_a_library(orc, tmp_path):
    conn, _ = orc
    with pytest.raises(ValueError, match="gamevoice_library"):
        prepare.prepare(conn, tmp_path / "c", engine=FakeEngine(), asr_backend=FakeASR(), narrator=FakeEngine._tone,
                        only=["orc_m"], import_gamevoice=True, lock_path=tmp_path / "l.json", log=lambda m: None)


def test_a_failed_download_builds_nothing_and_caches_nothing(tmp_path, monkeypatch):
    fake = FakeLib()
    monkeypatch.setattr(gv, "MIN_ANCHOR_S", 2.0)
    ok = fake.fetch

    def flaky(fdid, dest):
        if fdid == 12:
            raise TimeoutError("timed out")
        return ok(fdid, dest)

    fake.fetch = flaky
    with pytest.raises(gv.Unavailable, match="1 game voice clip download"):
        _library(tmp_path, fake).anchors("orc_m")
    assert not (tmp_path / "anchors/orc_m/plan.json").exists()
    assert "missing" not in json.loads((tmp_path / "clips.json").read_text())["12"]  # tried again next time
    fake.fetch = ok
    assert len(_library(tmp_path, fake).anchors("orc_m")) == 1


def test_an_unavailable_archetype_is_skipped_by_prepare(orc, tmp_path):
    conn, _ = orc

    class Down:
        def anchors(self, aid):
            raise gv.Unavailable(f"{aid}: 3 game voice clip download(s) failed; import again later")

    logs = []
    prepare.prepare(conn, tmp_path / "c", engine=FakeEngine(), asr_backend=FakeASR(), narrator=lambda t: FakeEngine._tone(1),
                    only=["orc_m"], import_gamevoice=True, gamevoice_library=Down(), lock_path=tmp_path / "l.json",
                    log=logs.append)
    assert any("download(s) failed" in m for m in logs)
    assert conn.execute("SELECT COUNT(*) FROM candidates WHERE id LIKE '%/gv-%'").fetchone()[0] == 0

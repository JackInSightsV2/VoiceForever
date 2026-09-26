"""Variation Candidates (vo.variations, review action vary-candidate), retiring unapproved Candidates, the game voice
baseline and `vo prepare --vary-gamevoices`, with fake engines."""
import json
from pathlib import Path

import numpy as np
import pytest

from vo import archetypes, db, effects, gamevoice as gv, prepare, variations
from vo.voices import Shift

from test_approval import FakeEngine, _act
from test_gamevoice import StubLibrary

ORC_LINE = archetypes.STYLE["orc_m"].anchor_text


class EchoASR:
    """Hears `text`, except the first `garbage` clips, which it misreads."""

    def __init__(self, text: str, garbage: int = 0):
        self.text, self.garbage = text, garbage

    def transcribe(self, wav):
        if self.garbage > 0:
            self.garbage -= 1
            return "something else entirely"
        return self.text


class EchoEngine(FakeEngine):
    """Its first `copies` clones return the reference itself (a near-identical variation)."""

    def __init__(self, copies: int = 0):
        super().__init__()
        self.copies = copies

    def clone(self, text, reference, style, seed):
        self.clone_calls.append((text, str(reference), style, seed))
        if self.copies > 0:
            self.copies -= 1
            return prepare.read_wav(Path(reference))
        return self._tone(seed + 3)


class Embed:
    """A stand-in speaker embedding: the same clip gives the same vector (similarity 1), any other clip an unrelated
    one (similarity near 0)."""

    def __call__(self, x, sr):
        q = np.round(np.asarray(x, dtype=np.float32) * 32767).astype(np.int32)
        rng = np.random.default_rng(abs(hash(q.tobytes())) % 2**32)
        return rng.standard_normal(256)


@pytest.fixture
def orc(tmp_path, monkeypatch):
    """orc_m (anchor chain "orc", recorded) with two game voices reading the orc anchor line, and orc_f (designed)."""
    chained = []
    monkeypatch.setitem(effects.CHAINS, "orc", lambda s, r: chained.append(1) or s)
    conn = db.connect(tmp_path / "vo.sqlite")
    conn.executemany("INSERT INTO npcs (id, name, race, gender) VALUES (?, ?, 'Orc', ?)",
                     [(1, "Grok", "male"), (2, "Grunta", "female")])
    rows = [(10, 1, "gossip", "Lok'tar. The Horde stands strong, and so must you, if you want to live out here."),
            (11, 1, "quest_detail", "The quilboar raid our caravans. Take your axe and teach them fear, then return."),
            (20, 2, "gossip", "Strength and honour. The Horde needs every blade it can find, so sharpen yours.")]
    conn.executemany("INSERT INTO lines (id, npc_id, type, raw_text, tts_text) VALUES (?, ?, ?, ?, ?)",
                     [(i, n, t, x, x) for i, n, t, x in rows])
    conn.commit()
    anchors = []
    for i, key in enumerate(("standard", "shady")):
        wav = tmp_path / "gamevoice" / f"{key}.wav"
        prepare.write_wav(wav, *FakeEngine._tone(40 + i))
        anchors.append(gv.Anchor("orc_m", key, f"game voice: {key}", f"WoW NPC voice set {key}", str(wav), ORC_LINE,
                                 0.8, [i], [key]))
    lib = StubLibrary(anchors)
    emb = Embed()
    prepare.prepare(conn, tmp_path / "candidates", engine=FakeEngine(), asr_backend=EchoASR(ORC_LINE),
                        narrator=lambda t: FakeEngine._tone(7), only=["orc_m"], import_gamevoice=True,
                        gamevoice_library=lib, candidates=2, samples=2, lock_path=tmp_path / "l.json",
                        embed=emb, log=lambda m: None)
    return conn, emb, chained


def run(conn, tmp_path, engine=None, asr=None, emb=None, **kw):
    logs = []
    engine = engine or FakeEngine()
    kw.setdefault("only", ["orc_m"])
    s = prepare.prepare(conn, tmp_path / "candidates", engine=engine, asr_backend=asr or EchoASR(ORC_LINE),
                        narrator=lambda t: FakeEngine._tone(7), candidates=2, samples=2,
                        lock_path=tmp_path / "l.json", embed=emb or Embed(), log=logs.append, **kw)
    return s, engine, logs


def variation_rows(conn, source):
    return [r for r in conn.execute("SELECT * FROM candidates WHERE id LIKE ? ORDER BY id", (f"{source}~v%",))]


def test_vary_candidate_renders_four_mixed_variations_with_samples(orc, tmp_path):
    conn, emb, chained = orc
    _act(conn, "vary-candidate", "orc_m/gv-standard", {"note": "a bit hoarse"})
    s, eng, logs = run(conn, tmp_path, emb=emb)
    rows = variation_rows(conn, "orc_m/gv-standard")
    assert [r["id"] for r in rows] == [f"orc_m/gv-standard~v{k}" for k in (1, 2, 3, 4)]
    assert s.variations == 4 and "4 variations" in prepare.summary_text(s)
    vs = [json.loads(r["variation"]) for r in rows]
    assert [v["method"] for v in vs] == ["style", "dsp", "style", "dsp"]
    assert {v["source"] for v in vs} == {"orc_m/gv-standard"}
    # Style: controllable cloning of the Archetype's anchor line from the source anchor, style + note, seeded.
    src = conn.execute("SELECT path FROM candidates WHERE id = 'orc_m/gv-standard'").fetchone()[0]
    assert [(c[0], c[1]) for c in eng.clone_calls] == [(ORC_LINE, src)] * 2
    styles = [v["style"] for v in vs if v["method"] == "style"]
    assert styles[0] != styles[1] and all(s_.endswith("; a bit hoarse") for s_ in styles)
    assert rows[0]["label"] == f"variation of game voice: standard: style {styles[0]!r}"
    assert rows[0]["anchor_text"] == ORC_LINE
    # DSP: strong shifts in different directions.
    shifts = [Shift(v["pitch_st"], v["formant"], v["pace"]) for v in vs if v["method"] == "dsp"]
    for sh in shifts:
        assert 2 <= abs(sh.pitch_st) <= 4 and 0.06 <= abs(sh.formant - 1) <= 0.10 + 1e-9
        assert 0.05 <= abs(sh.pace - 1) <= 0.10 + 1e-9
    assert variations._signs(shifts[0]) != variations._signs(shifts[1])
    assert rows[1]["label"].startswith("variation of game voice: standard: DSP pitch ")
    # Inherit the source's mode; no anchor chain (orc_m has one), measured, pending in the current generation.
    for r in rows:
        assert (r["mode"], r["anchor_chain"], r["raw_path"], r["status"], r["generation"]) == (
            "ultimate", None, None, "pending", 0)
        assert r["wer"] == 0.0 and r["f0"] and r["hnr"] is not None and Path(r["path"]).exists()
    assert chained == []
    assert all(abs(v["sim"]) < 0.5 for v in vs)
    # Their sample lines, continued from each variation in its mode.
    n = conn.execute("SELECT COUNT(*) FROM candidate_samples WHERE candidate LIKE 'orc_m/gv-standard~v%'").fetchone()[0]
    assert n == 4 * 2
    assert {c[4] for c in eng.continue_calls if "~v" in c[1]} == {"ultimate"}
    assert conn.execute("SELECT done_at IS NOT NULL FROM variation_requests").fetchone()[0] == 1

    # Idempotent: nothing more to render.
    s, eng, _ = run(conn, tmp_path, emb=emb)
    assert s.variations == 0 and eng.clone_calls == []


def test_repeated_requests_add_new_ids(orc, tmp_path):
    conn, emb, _ = orc
    _act(conn, "vary-candidate", "orc_m/gv-shady")
    _act(conn, "vary-candidate", "orc_m/gv-shady")  # queued twice before a pass
    run(conn, tmp_path, emb=emb)
    _act(conn, "vary-candidate", "orc_m/gv-shady")
    run(conn, tmp_path, emb=emb)
    ids = [r["id"] for r in variation_rows(conn, "orc_m/gv-shady")]
    assert sorted(ids, key=lambda i: int(i.rsplit("~v", 1)[1])) == [f"orc_m/gv-shady~v{k}" for k in range(1, 13)]
    # A variation of a variation is its child, not its source's.
    _act(conn, "vary-candidate", "orc_m/gv-shady~v2")
    run(conn, tmp_path, emb=emb)
    assert variations.parent("orc_m/gv-shady~v2~v1") == "orc_m/gv-shady~v2"
    assert len(variation_rows(conn, "orc_m/gv-shady~v2")) == 4
    assert len([i for i in (r["id"] for r in variation_rows(conn, "orc_m/gv-shady"))
                if variations.parent(i) == "orc_m/gv-shady"]) == 12


def test_failed_variations_are_retried_then_dropped(orc, tmp_path):
    conn, emb, _ = orc
    # Slot 1 (style): the first clone is the source itself (near-identical), the retry passes.
    # Slot 2 (dsp): misheard on every try: dropped. Slots 3, 4 pass.
    asr = EchoASR(ORC_LINE)
    eng = EchoEngine(copies=1)
    _act(conn, "vary-candidate", "orc_m/gv-standard")

    heard = []
    orig = asr.transcribe

    def transcribe(wav):
        heard.append(str(wav))
        if "variations/gv-standard~v2" in str(wav):
            return "nothing like it"
        return orig(wav)

    asr.transcribe = transcribe
    s, eng, logs = run(conn, tmp_path, engine=eng, asr=asr, emb=emb)
    ids = [r["id"] for r in variation_rows(conn, "orc_m/gv-standard")]
    assert ids == ["orc_m/gv-standard~v1", "orc_m/gv-standard~v3", "orc_m/gv-standard~v4"]
    assert s.variations == 3
    v1 = json.loads(variation_rows(conn, "orc_m/gv-standard")[0]["variation"])
    assert v1["attempt"] == 1 and abs(v1["sim"]) < 0.5
    assert any("near-identical to the source (similarity 1.000" in m for m in logs)
    assert sum("standard~v2 try" in m and "misread" in m for m in logs) == variations.MAX_TRIES
    assert any("standard~v2: no render passed in 3 tries; slot dropped" in m for m in logs)
    assert not (tmp_path / "candidates/orc_m/variations/gv-standard~v2.wav").exists()
    # The retries of the dropped DSP slot each took a new direction.
    assert conn.execute("SELECT done_at IS NOT NULL FROM variation_requests").fetchone()[0] == 1


def test_gate():
    assert variations.gate(0.0, 0.985, "dsp").ok is True and variations.gate(0.0, 0.985).ok is False
    assert variations.gate(0.31, 0.5).ok is False
    assert variations.gate(0.3, 0.97).ok is True
    assert variations.gate(0.0, 0.971).ok is False


def test_shift_directions_avoid_ones_taken():
    first = variations.shift([], "a/gv-x", 1, 0)
    second = variations.shift([first], "a/gv-x", 2, 0)
    assert variations._signs(first) != variations._signs(second)
    taken = [Shift(d[0] * 3, 1 + d[1] * 0.08, 1 + d[2] * 0.07) for d in variations.DIRECTIONS]
    assert variations.shift(taken, "a/gv-x", 9, 0)  # all taken: still a shift
    assert variations.shift([], "a/gv-x", 1, 0) == first  # deterministic


def test_style_rotation():
    s1 = variations.style([], 1)
    s2 = variations.style([s1], 2)
    assert s1 == variations.STYLES[0] and s2 == variations.STYLES[1]
    assert variations.style([f"{s1}; note"], 1, "deeper.") == f"{variations.STYLES[1]}; deeper"


def test_variation_of_a_chained_designed_candidate_skips_the_chain(orc, tmp_path):
    """A designed orc_m Candidate's anchor already went through the orc chain: its variations use that processed
    clip and are never processed again."""
    conn, emb, chained = orc
    a = conn.execute("SELECT * FROM archetypes WHERE id = 'orc_m'").fetchone()
    samples, rate = FakeEngine._tone(9)
    prepare._add_candidate(conn, a, "orc_m/g0s0", 0, a["description"], samples, rate,
                           tmp_path / "candidates/orc_m/g0s0.wav", "orc", None, prepare._Check(EchoASR(ORC_LINE), ""),
                           lambda m: None)
    assert chained == [1]
    src = conn.execute("SELECT * FROM candidates WHERE id = 'orc_m/g0s0'").fetchone()
    _act(conn, "vary-candidate", "orc_m/g0s0")
    s, eng, _ = run(conn, tmp_path, emb=emb)
    assert chained == [1] and s.variations == 4
    assert {c[1] for c in eng.clone_calls} == {src["path"]}
    rows = variation_rows(conn, "orc_m/g0s0")
    assert all(r["anchor_chain"] is None and r["mode"] is None for r in rows)
    assert rows[0]["label"].startswith("variation of g0s0: style ")
    assert not (tmp_path / "candidates/orc_m/variations/g0s0~v1_raw.wav").exists()


def test_invalid_vary_is_dropped(orc, tmp_path):
    conn, emb, _ = orc
    _act(conn, "vary-candidate", "orc_m/nope")
    s, _, logs = run(conn, tmp_path, emb=emb)
    assert any("invalid, dropped: no candidate orc_m/nope" in m for m in logs)
    assert conn.execute("SELECT COUNT(*) FROM variation_requests").fetchone()[0] == 0


# --- supersede rules --------------------------------------------------------------------------------------------------

def test_variations_are_not_superseded_by_a_regenerate(orc, tmp_path):
    conn, emb, _ = orc
    run(conn, tmp_path, emb=emb, only=["orc_f"], asr=EchoASR(archetypes.STYLE["orc_f"].anchor_text))
    designed = [r[0] for r in conn.execute("SELECT id FROM candidates WHERE archetype = 'orc_f' ORDER BY id")]
    assert designed == ["orc_f/g0s0", "orc_f/g0s1"]
    _act(conn, "vary-candidate", "orc_f/g0s0")
    run(conn, tmp_path, emb=emb, only=["orc_f"], asr=EchoASR(archetypes.STYLE["orc_f"].anchor_text))
    vids = [r["id"] for r in variation_rows(conn, "orc_f/g0s0")]
    # The DSP ones read the source's transcript (the orc_f anchor line), the style ones the same line: both pass.
    assert len(vids) == 4
    _act(conn, "approve-candidate", vids[0])
    _act(conn, "regenerate-archetype", "orc_f", {"note": "gruffer"})
    run(conn, tmp_path, emb=emb, only=["orc_f"], asr=EchoASR(archetypes.STYLE["orc_f"].anchor_text))
    st = dict(conn.execute("SELECT id, status || ':' || generation FROM candidates WHERE archetype = 'orc_f'").fetchall())
    assert st["orc_f/g0s0"] == "superseded:0" and st["orc_f/g0s1"] == "superseded:0"
    assert st[vids[0]] == "approved:0"
    assert all(st[v] == "pending:1" for v in vids[1:])
    assert st["orc_f/g1s0"] == "pending:1"
    # Unapproving a variation from an older generation makes it pending in the current one, not superseded.
    _act(conn, "unapprove-candidate", vids[0])
    prepare.consume_actions(conn, lambda m: None)
    assert tuple(conn.execute("SELECT status, generation FROM candidates WHERE id = ?", (vids[0],)).fetchone()) == (
        "pending", 1)


# --- retire, game voice baseline, --vary-gamevoices ------------------------------------------------------------------

def test_retire_unapproved_keeps_game_voices_and_approvals(orc, tmp_path):
    conn, emb, _ = orc
    run(conn, tmp_path, emb=emb, only=["orc_f"], asr=EchoASR(archetypes.STYLE["orc_f"].anchor_text))
    _act(conn, "vary-candidate", "orc_m/gv-standard")
    run(conn, tmp_path, emb=emb)
    _act(conn, "approve-candidate", "orc_f/g0s1")
    _act(conn, "approve-candidate", "orc_m/gv-standard~v2")
    _act(conn, "reject-candidate", "orc_m/gv-standard~v3")
    prepare.consume_actions(conn, lambda m: None)
    n = prepare.retire_unapproved(conn, log=lambda m: None)
    st = dict(conn.execute("SELECT id, status FROM candidates WHERE archetype != 'narrator'").fetchall())
    assert st["orc_m/gv-standard"] == st["orc_m/gv-shady"] == "pending"  # the baseline stays approvable
    assert st["orc_m/gv-standard~v2"] == st["orc_f/g0s1"] == "approved"
    assert st["orc_f/g0s0"] == st["orc_m/gv-standard~v1"] == st["orc_m/gv-standard~v3"] == "retired"
    assert n == 4
    # Retired ones aren't replaced (no fresh design for orc_f) and get no samples; the approved one keeps its own.
    conn.execute("DELETE FROM candidate_samples")
    conn.commit()
    s, eng, _ = run(conn, tmp_path, emb=emb, only=["orc_f", "orc_m"])
    assert eng.design_calls == []
    sampled = {r[0] for r in conn.execute("SELECT DISTINCT candidate FROM candidate_samples")}
    assert "orc_f/g0s0" not in sampled and "orc_f/g0s1" in sampled
    assert {"orc_m/gv-standard", "orc_m/gv-standard~v2"} <= sampled and "orc_m/gv-standard~v1" not in sampled
    # --archetype limits it.
    _act(conn, "unapprove-candidate", "orc_f/g0s1")
    prepare.consume_actions(conn, lambda m: None)
    assert prepare.retire_unapproved(conn, ["orc_m"], log=lambda m: None) == 0
    assert prepare.retire_unapproved(conn, ["orc_f"], log=lambda m: None) == 1


def test_game_voice_archetype_designs_nothing(orc, tmp_path):
    conn, emb, _ = orc
    for cid in ("orc_m/gv-standard", "orc_m/gv-shady"):
        _act(conn, "reject-candidate", cid)
    s, eng, logs = run(conn, tmp_path, emb=emb, design=True)
    assert eng.design_calls == [] and any("game voice baseline" in m for m in logs)
    assert conn.execute("SELECT generation FROM archetypes WHERE id = 'orc_m'").fetchone()[0] == 0
    # With no live game voice a regenerate has nothing to vary: dropped.
    _act(conn, "regenerate-archetype", "orc_m", {"note": "deeper"})
    s, eng, logs = run(conn, tmp_path, emb=emb)
    assert any("no live game voice Candidates to vary" in m for m in logs)


def test_regenerate_without_note_on_game_voices_mixes_methods(orc, tmp_path):
    conn, emb, _ = orc
    _act(conn, "regenerate-archetype", "orc_m")
    s, eng, _ = run(conn, tmp_path, emb=emb)
    assert s.variations == 2 * prepare.REGEN_PER_GAMEVOICE
    methods = [json.loads(r["variation"])["method"] for r in variation_rows(conn, "orc_m/gv-shady")]
    assert methods == ["style", "dsp"]


def test_vary_gamevoices_tops_up_and_is_idempotent(orc, tmp_path):
    conn, emb, _ = orc
    _act(conn, "vary-candidate", "orc_m/gv-standard")
    run(conn, tmp_path, emb=emb)
    _act(conn, "reject-candidate", "orc_m/gv-standard~v1")
    _act(conn, "approve-candidate", "orc_m/gv-standard~v2")
    prepare.consume_actions(conn, lambda m: None)
    n = prepare.queue_gamevoice_variations(conn, 4, log=lambda m: None)
    # gv-standard: 3 live -> 1 more; gv-shady: 4; the approved gv-standard~v2: 4.
    assert n == 1 + 4 + 4
    assert prepare.queue_gamevoice_variations(conn, 4, log=lambda m: None) == 0  # queued ones count
    s, eng, _ = run(conn, tmp_path, emb=emb)
    assert s.variations == 9
    assert len(variation_rows(conn, "orc_m/gv-shady")) == 4
    assert [r["id"] for r in variation_rows(conn, "orc_m/gv-standard~v2")] == [
        f"orc_m/gv-standard~v2~v{k}" for k in (1, 2, 3, 4)]
    assert "orc_m/gv-standard~v5" in {r["id"] for r in variation_rows(conn, "orc_m/gv-standard")}
    assert prepare.queue_gamevoice_variations(conn, 4, log=lambda m: None) == 0
    assert prepare.queue_gamevoice_variations(conn, 4, ["orc_f"], log=lambda m: None) == 0
    # Variations of a game voice aren't game voices themselves: they don't count as the baseline.
    assert prepare.has_gamevoice(conn, "orc_m") and not prepare.has_gamevoice(conn, "orc_f")


def test_watch_picks_up_queued_variations(orc, tmp_path):
    conn, emb, _ = orc
    prepare.queue_variations(conn, "orc_m/gv-shady", None, 2)
    conn.commit()
    assert prepare.pending_actions(conn) == 1
    s, _, _ = run(conn, tmp_path, emb=emb)
    assert s.variations == 2 and prepare.pending_actions(conn) == 0

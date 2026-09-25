import numpy as np

from vo.bakeoff import dsp, page5, round4, round5, voicefeat


def test_thirds_catches_a_voice_that_cleans_itself_up():
    t = np.arange(0, 9, 0.01)
    h = np.where(t < 3, 2.0, np.where(t < 6, 5.0, 12.0))  # rough, rough, clean
    h[::10] = -200.0  # unvoiced frames are ignored
    r = voicefeat.thirds(t, h, 9.0)
    assert r["hnr_thirds"] == [2.0, 5.0, 12.0]
    assert r["rough_thirds"] == [1.0, 1.0, 0.0]
    assert r["sustained"] == 0.0 and r["drift"] == 10.0
    assert abs(r["rough"] - 2 / 3) < 0.01
    steady = voicefeat.thirds(t, np.full_like(t, 3.0), 9.0)
    assert steady["sustained"] == 1.0 and steady["drift"] == 0.0
    empty = voicefeat.thirds(t, np.full_like(t, -200.0), 9.0)
    assert empty["sustained"] is None and empty["rough"] is None and empty["drift"] is None


def test_subharmonic_envelope_halves_the_period_only_where_voiced():
    sr, n = 16000, 16000
    times = np.arange(0, 1, 0.005)
    f0 = np.where(times < 0.5, 100.0, 0.0)
    env = dsp.subharmonic_envelope(n, sr, times, f0, 0.6)
    assert env.shape == (n,) and env.min() >= 0.4 - 1e-6 and env.max() <= 1 + 1e-6
    assert np.allclose(env[int(0.8 * sr):], 1.0)  # unvoiced: untouched
    x = np.sin(2 * np.pi * 100 * np.arange(n) / sr)[: sr // 2] * env[: sr // 2]
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(len(x), 1 / sr)
    assert spec[np.argmin(abs(freqs - 50))] > 0.1 * spec.max()  # energy at f0/2: the growl
    assert np.all(dsp.subharmonic_envelope(n, sr, times, f0, 0.0) == 1)


def test_chain_growl_stages_scale_and_default_off():
    assert dsp.Chain().subharm == 0 and dsp.Chain().growl == 0
    assert dsp.RACE_CHAINS["orc_m"].subharm == 0  # round-2 chains unchanged
    c = round5.ORC_CHAIN
    assert c.scaled(0).is_identity
    assert c.scaled(2).subharm <= 0.95 and c.scaled(2).growl <= 1.0
    assert "subharm=0.5" in c.describe() and "growl=0.4" in c.describe()


def _cand(sust, rough, drift, f0, wer=0.0):
    return {"file": "x.wav", "wer": wer, "features": {"f0": f0, "hnr": 5.0},
            "rough3": {"sustained": sust, "rough": rough, "drift": drift, "rough_thirds": [rough, rough, sust]}}


def test_char_score_rewards_sustained_roughness_and_penalises_cleanup_pitch_and_asr():
    base = round5.char_score(_cand(0.7, 0.8, 0.0, 100))
    assert base == round(0.7 + 0.4, 3)
    assert round5.char_score(_cand(0.7, 0.8, 3.0, 100)) < base  # cleaned itself up
    assert round5.char_score(_cand(0.7, 0.8, -3.0, 100)) == base  # got rougher: no bonus
    assert round5.char_score(_cand(0.7, 0.8, 0.0, 260)) == round(base - 0.3, 3)  # an octave above 130 Hz
    assert round5.char_score(_cand(0.7, 0.8, 0.0, 100, wer=0.5)) == round(base - 1, 3)
    assert round5.char_score({"rough3": {}}) is None


def test_rank_sources_and_picks():
    cands = {round5.R4_ANCHOR: _cand(0.6, 0.6, 1.4, 134), "growl-s0": _cand(0.9, 0.9, 0, 90),
             "tags-s1": _cand(0.3, 0.4, 0, 90), "beast-s2": _cand(0.8, 0.8, 0, 90), "tags-s3": {"file": "y"}}
    assert round5.rank(cands) == ["growl-s0", "beast-s2", round5.R4_ANCHOR, "tags-s1", "tags-s3"]
    assert round5.sources(cands) == ["growl-s0", "beast-s2", round5.R4_ANCHOR]
    assert round5.sources(cands, ["tags-s1", "growl-s0", "tags-s1"]) == ["tags-s1", "growl-s0"]
    assert round5.sources({"growl-s0": cands["growl-s0"]}) == ["growl-s0"]
    md = "## Bake-off round 5: orc male\n\n### Anchor picks (best first)\n\n- pick: beast-s2\n- pick: `r4-s6`\n\n- best: x"
    assert round5.parse_picks(md) == ["beast-s2", "r4-s6"]
    assert round5.check_picks(["beast-s2"], cands) == []
    assert round5.check_picks(["nope"], cands) == ["no candidate 'nope'"]
    assert round5.check_picks([], cands) == ["no anchors picked"]


def test_descriptions_subjects_and_plan():
    assert len(round5.DESCRIPTIONS) == 3 and len(round5.SEEDS) == 8
    assert all(d.text != round4.subject("orc_m").description for d in round5.DESCRIPTIONS)
    s = round5.subject("tags")
    assert s.anchor_text == round5.ANCHOR_TEXT == round4.ANCHOR_TEXT["orc"] and s.male
    assert s.description == round5.description("tags").text
    assert round5.cand_id("beast", 7) == "beast-s7"
    assert len(round5.lines()) == 10
    assert round5.plan(["a", "b"]) == [("cont", "a"), ("dsp-anchor", "a"), ("cont", "b"), ("dsp-anchor", "b"),
                                       ("cont-dsp", "a"), ("cont-vc", "a")]
    assert round5.plan([]) == []
    assert round5.tune({0.5: 0.02, 1.0: 0.04, 1.5: 0.2}, 0.0) == 1.0


def _clip(file, f0=90, hnr=4.0, wer=0.0, rough=0.8):
    return {"file": file, "audio_s": 5.0, "wall_s": 4.0, "rtf": 1.25, "wer": wer, "asr": "x",
            "features": {"f0": f0, "hnr": hnr, "centroid": 800},
            "rough3": {"rough": rough, "sustained": rough - 0.1, "drift": 0.5, "rough_thirds": [rough, rough, rough - 0.1],
                       "hnr_thirds": [4, 4, 4.5]}}


def _results():
    ids = [l.id for l in round5.lines()][:3]
    rng = np.random.default_rng(0)
    ref = {i: _clip(f"reference/{i}.wav") for i in ids}
    clips = {v: {round5.clip_key("growl-s0", i): _clip(f"audio/{v}/growl-s0/{i}.wav") for i in ids}
             for v in round5.VARIANT_IDS}
    anchors = {"growl-s0": _clip("anchors/growl-s0.wav"), round5.R4_ANCHOR: _clip("anchors/r4-s6.wav", 134, 6.5)}
    res = {"reference": ref, "anchors": anchors, "dsp_anchors": {"growl-s0": _clip("anchors/dsp/growl-s0.wav")},
           "sources": ["growl-s0"], "clips": clips, "ranking": ["growl-s0", round5.R4_ANCHOR],
           "tune": {"anchor": "growl-s0", "base_wer": 0.01, "k": 1.0, "chain": "c",
                    "sweep": {"1": {"wer": 0.02, "rough": 0.8, "chain": "c"}}, "ladder": {"1": _clip("dsp_tune/k1/x.wav")}}}
    files = [c["file"] for c in ref.values()] + [a["file"] for a in anchors.values()] + ["anchors/dsp/growl-s0.wav"]
    files += [c["file"] for cs in clips.values() for c in cs.values()]
    emb = {f: rng.normal(size=8).tolist() for f in files}
    return res, emb


def test_build_stats_rows_and_page():
    res, emb = _results()
    st = round5.build_stats(res, emb)
    assert set(st) == {round5.REFERENCE, *(f"{v}@growl-s0" for v in round5.VARIANT_IDS)}
    r = st["cont-vc@growl-s0"]
    for k in ("within", "anchor_sim", "arch_sim", "rough", "sustained", "first", "last", "drift", "wer", "rtf", "f0"):
        assert r[k] is not None, k
    assert r["rough"] == 0.8 and r["last"] == 0.7
    res["stats"] = st
    html = page5.render(res)
    for s in ("pick this anchor", "growl-s0", "r4-s6", "Play all in sequence", "Copy as Markdown", "- pick: ",
              "cons:cont-dsp@growl-s0/orc_m", "best:orc_m", "reference/", "Strength 1:"):
        assert s in html, s
    assert page5.render({}).startswith("<!doctype html>")

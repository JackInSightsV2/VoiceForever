import numpy as np
import pytest

from vo.bakeoff import page3, round2, round3


def test_generate_kwargs_map_each_variant_to_voxcpm2_modes():
    kw = lambda vid: round3.generate_kwargs(round3.variant(vid), "DESC", "a.wav", "anchor words")
    assert kw("direct") == {"instruct": "DESC"} == kw("seed")
    assert kw("cont") == {"prompt_audio": "a.wav", "prompt_text": "anchor words"}
    assert kw("cont-desc") == {"instruct": "DESC", "prompt_audio": "a.wav", "prompt_text": "anchor words"}
    assert kw("ultimate") == {"ref_audio": "a.wav", "prompt_audio": "a.wav", "prompt_text": "anchor words"}
    assert kw("ref") == {"ref_audio": "a.wav"}
    assert kw("ref-desc") == {"instruct": "DESC", "ref_audio": "a.wav"}
    with pytest.raises(ValueError):
        round3.generate_kwargs(round3.variant("cont"), "DESC", None, None)
    assert [v.id for v in round3.VARIANTS if v.fixed_seed] == ["seed"]


def test_plan_renders_b_and_c_first_then_dwarf_and_npcs_with_the_best():
    first = round3.plan(None)
    assert [v for v, _ in first[:4]] == ["cont"] * 4 and first[4][0] == "ref-desc"
    assert {s for _, s in first} == set(round3.MAIN_VOICES)
    assert len(first) == len(round3.RENDER_ORDER) * len(round3.MAIN_VOICES)
    full = round3.plan("cont")
    extra = full[len(first):]
    assert extra == [("direct", "dwarf_f2"), ("cont", "dwarf_f2")] + [("cont", f"orc_m@{n}") for n in round3.NPC_IDS]


def test_subjects_reuse_round2_lines_and_anchor_texts_per_race():
    for s in round3.SUBJECTS:
        ls = round3.lines(s)
        assert len(ls) == 10 and all(l.voice == s.voice for l in ls)
        assert s.anchor_text not in {l.text for l in ls}
    assert round3.subject("dwarf_f2").description == round3.DWARF_F_V2 != round2.voice("dwarf_f").prompt
    npcs = [s for s in round3.SUBJECTS if s.group == "npc"]
    assert len({s.anchor_seeds for s in npcs}) == 3 and len({s.description for s in npcs}) == 1


def test_npc_anchor_is_first_intelligible_seed_and_main_anchor_is_best_score():
    npc = round3.subject("orc_m@3139")
    seeds = round3.npc_seeds(3139)
    assert seeds[0] == 3139 and len(set(seeds)) == round3.NPC_TRIES
    cands = {str(seeds[0]): {"wer": 0.5}, str(seeds[1]): {"wer": 0.0}, str(seeds[2]): {"wer": 0.0}}
    assert round3.pick_anchor(cands, npc) == str(seeds[1])
    assert round3.pick_anchor({str(seeds[0]): {"wer": 0.9}}, npc) == str(seeds[0])
    main = round3.subject("orc_m")
    deep = {"features": {"f0": 76, "hnr": 5, "centroid": 800}, "wer": 0.0}
    high = {"features": {"f0": 180, "hnr": 5, "centroid": 800}, "wer": 0.0}
    assert round3.pick_anchor({"0": high, "1": deep}, main) == "1"
    assert round3.pick_anchor({}, main) is None


def _unit(*xs):
    v = np.array(xs, dtype=float)
    return v / np.linalg.norm(v)


def test_within_between_and_separation():
    a = [_unit(1, 0.1, 0), _unit(1, 0, 0.1), _unit(1, 0.05, 0.05)]
    b = [_unit(0, 1, 0.1), _unit(0.1, 1, 0)]
    w = round3.within(a)
    assert w["n"] == 3 and 0.98 < w["min"] <= w["mean"] <= 1
    assert round3.within([a[0]])["mean"] is None
    assert round3.between(a, b) < 0.2
    sep = round3.separation({"a": a, "b": b})
    assert list(sep["between"]) == ["a|b"] and sep["margin"] > 0.7
    assert round3.to_ref(a, round3.centroid(a)) > 0.99


def test_character_guard_and_best_variant_rule():
    base = {"f0": 100, "hnr": 5, "wer": 0.05}
    assert round3.keeps_character({"f0": 110, "hnr": 7, "wer": 0.08}, base)
    assert not round3.keeps_character({"f0": 130, "hnr": 5, "wer": 0.0}, base), "more than 2 semitones up"
    assert not round3.keeps_character({"f0": 100, "hnr": 9, "wer": 0.0}, base), "much smoother"
    assert not round3.keeps_character({"f0": 100, "hnr": 5, "wer": 0.2}, base), "less intelligible"
    deeper = {"f0": 70, "hnr": 5, "wer": 0.0}
    assert round3.keeps_character(deeper, base, target_f0=75), "moved toward the orc target"
    assert not round3.keeps_character(deeper, base, target_f0=200), "moved away from the target"
    good = {"f0": 100, "hnr": 5, "wer": 0.05}
    smooth = {"f0": 100, "hnr": 12, "wer": 0.0}
    table = {
        "direct": {"x": {**good, "within": 0.5}},
        "cont": {"x": {**good, "within": 0.80}},
        "ref": {"x": {**smooth, "within": 0.95}},  # most consistent, but lost the character
        "seed": {"x": {**good, "within": 0.60}},
    }
    assert round3.pick_best(table, {"x": base}) == "cont"
    assert round3.pick_best({"ref": table["ref"]}, {"x": base}) == "ref", "falls back when none keeps it"
    assert round3.pick_best({}, {}) is None


def test_f0_spread_in_semitones():
    clips = [{"features": {"f0": 100}}, {"features": {"f0": 200}}, {"features": {"f0": None}}]
    assert round3.f0_spread(clips) == pytest.approx(8.49, abs=0.01)
    assert round3.f0_spread(clips[:1]) is None


def _fake_results():
    rng = np.random.default_rng(0)
    centres = {k: rng.normal(size=16) for k in [*round3.MAIN_VOICES, "orc_m@3139", "orc_m@3143", "orc_m@3188"]}
    emb, results = {}, {"clips": {}, "anchors": {}}

    def add(vid, key, spread, voice=None):
        for i, l in enumerate(round3.lines(round3.subject(key)) if "@" in key or key in round3.MAIN_VOICES else []):
            f = f"audio/{vid}/{key}/{l.id}.wav"
            emb[f] = list(centres[key] + spread * rng.normal(size=16))
            results["clips"].setdefault(vid, {})[f"{key}/{l.id}"] = {
                "file": f, "wer": 0.0, "audio_s": 5.0, "wall_s": 4.0,
                "features": {"f0": 100 * ((1.25 if i % 2 else 0.8) if spread > 1 else 1), "hnr": 5.0}}
        if "@" in key or vid != round3.BASELINE:
            f = f"anchors/{key}.wav"
            emb[f] = list(centres[key])
            results["anchors"][key] = {"chosen": "0", "file": f, "cands": {"0": {"file": f}}}

    for v in round3.MAIN_VOICES:
        add(round3.BASELINE, v, 2.0)
        add("cont", v, 0.2)
    for key in ("orc_m@3139", "orc_m@3143", "orc_m@3188"):
        add("cont", key, 0.2)
    return results, emb


def test_build_stats_measures_consistency_and_npc_separation():
    results, emb = _fake_results()
    r2_clips = {"vox-direct": {"a/1": {"file": "x1"}, "a/2": {"file": "x2"}, "b/1": {"file": "y1"},
                               "b/2": {"file": "y2"}}}
    for f, v in {"x1": _unit(1, 0, 0), "x2": _unit(1, 0.1, 0), "y1": _unit(0, 1, 0), "y2": _unit(0, 1, 0.1)}.items():
        emb["r2:" + f] = list(v)
    st = round3.build_stats(results, r2_clips, emb)
    assert st["r2"]["vox-direct"]["within"]["a"]["mean"] > 0.99 > st["r2"]["vox-direct"]["between_mean"]
    base, cont = st["variants"][round3.BASELINE], st["variants"]["cont"]
    assert cont["within_mean"] > base["within_mean"]
    row = cont["rows"]["orc_m"]
    assert row["anchor_sim"] > 0.9 and row["keeps"] is True and row["rtf"] == 1.25
    assert base["rows"]["orc_m"]["f0_sd"] > 0 and row["f0_sd"] == 0
    assert st["best"] == "cont"
    npc = st["npc"]
    assert npc["variant"] == "cont" and set(npc["rows"]) == {"orc_m", "orc_m@3139", "orc_m@3143", "orc_m@3188"}
    assert npc["margin"] > 0 and "orc_m@3139|orc_m@3143" in npc["anchor_pairs"]


def test_page_has_sequence_players_ratings_baseline_and_tables():
    results, emb = _fake_results()
    results["stats"] = round3.build_stats(results, {}, emb)
    results["best"] = {"variant": "cont", "how": "rule"}
    html = page3.render(results)
    assert html.count('class="play"') >= 2 * len(round3.MAIN_VOICES) + 4
    assert 'name="cons:cont/orc_m"' in html and 'value="drifts"' in html and 'value="different"' in html
    assert 'name="best:orc_m"' in html and 'name="char:r2-direct/orc_m"' in html
    assert "Round-2 vox-direct (baseline)" in html and 'id="npcs"' in html and "npc:apart" in html
    assert "localStorage" in html and "Copy as Markdown" in html
    assert page3.render({}).startswith("<!doctype html>")

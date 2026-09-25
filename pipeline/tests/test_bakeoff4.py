import numpy as np

from vo.bakeoff import page4, round2, round3, round4


def test_voices_reuse_round2_descriptions_and_lines_with_round4_anchor_text():
    for vid in round4.VOICES:
        s = round4.subject(vid)
        assert s.description == round2.voice(vid).prompt
        assert s.anchor_text == round4.ANCHOR_TEXT[vid.split("_")[0]] != round3.ANCHOR_TEXT.get(s.race)
        assert len(round4.lines(vid)) == 10
        assert s.anchor_text not in {l.text for l in round4.lines(vid)}
    assert len(round4.SEEDS) == 8
    # engines3.VoxCPM2 passes s.anchor_text as the continuation transcript: it must be round 4's
    kw = round3.generate_kwargs(round3.variant("cont"), "D", "a.wav", round4.subject("orc_m").anchor_text)
    assert kw["prompt_text"] == round4.ANCHOR_TEXT["orc"]


def test_parse_picks_reads_page_export_and_json():
    md = """## Bake-off round 4: anchor picks

- orc_m: s3
- orc_f: none
* human_f: `s7`
troll_m: s1

### Notes

**orc_m**: s3 has the growl
"""
    assert round4.parse_picks(md) == {"orc_m": "s3", "orc_f": "none", "human_f": "s7"}
    assert round4.parse_picks('{"human_m": "s0", "dwarf_f": "s1", "troll_f": "NONE"}') == {"human_m": "s0",
                                                                                        "troll_f": "none"}
    assert round4.parse_picks("nothing here") == {}


def test_check_picks_and_plan():
    anchors = {"orc_m": {"cands": {"s0": {}, "s3": {}}}, "orc_f": {"cands": {"s1": {}}}}
    assert round4.check_picks({"orc_m": "s3", "orc_f": "none"}, anchors) == []
    assert "s9" in round4.check_picks({"orc_m": "s9"}, anchors)[0]
    assert round4.check_picks({"orc_f": "none"}, anchors) == ["no voice has a picked anchor"]
    assert round4.plan({"orc_m": "s3", "orc_f": "none", "human_f": "s1"}) == [
        ("cont", "orc_m", "s3"), ("cont", "human_f", "s1"), ("ultimate", "orc_m", "s3"), ("ultimate", "human_f", "s1")]
    assert round4.clip_key("orc_m", "s3", "21-greeting") == "orc_m@s3/21-greeting"


def _clip(file, f0, hnr, wer=0.0):
    return {"file": file, "audio_s": 5.0, "wall_s": 4.0, "wer": wer, "asr": "x",
            "features": {"f0": f0, "hnr": hnr, "centroid": 900}}


def _results():
    ids = [l.id for l in round4.lines("orc_m")][:3]
    ref = {f"orc_m/{i}": _clip(f"reference/orc_m/{i}.wav", 130, 4.0) for i in ids}
    cont = {round4.clip_key("orc_m", "s3", i): _clip(f"audio/cont/orc_m/s3/{i}.wav", 131, 5.0) for i in ids}
    stale = {round4.clip_key("orc_m", "s0", i): _clip(f"audio/cont/orc_m/s0/{i}.wav", 300, 20.0) for i in ids}
    anchors = {"orc_m": {"text": round4.anchor_text("orc_m"), "description": "D",
                         "cands": {"s0": _clip("anchors/orc_m/s0.wav", 180, 9.0, wer=0.4),
                                   "s3": {**_clip("anchors/orc_m/s3.wav", 128, 3.0), "score": 1.2}}}}
    return {"picks": {"orc_m": "s3", "orc_f": "none"}, "reference": ref, "anchors": anchors,
            "clips": {"cont": {**cont, **stale}}}, ids


def test_build_stats_uses_only_the_picked_anchor_clips():
    results, ids = _results()
    rng = np.random.default_rng(0)
    base = rng.normal(size=8)
    emb = {}
    for c in [*results["reference"].values(), *results["clips"]["cont"].values(),
              *results["anchors"]["orc_m"]["cands"].values()]:
        emb[c["file"]] = (base + 0.1 * rng.normal(size=8)).tolist()
    st = round4.build_stats(results, emb)
    assert set(st) == {"orc_m"}
    rows = st["orc_m"]
    assert set(rows) == {round4.REFERENCE, "cont"}
    assert rows["cont"]["n"] == 3 and rows["cont"]["f0"] == 131.0  # the s0 clips (f0 300) are not counted
    assert rows["cont"]["keeps"] is True and rows["cont"]["d_hnr"] == 1.0
    assert rows["cont"]["anchor_sim"] > 0.9 and rows["cont"]["arch_sim"] > 0.9


def test_pick_page_has_candidates_none_option_reference_and_export():
    results, ids = _results()
    html = page4.pick_page(results)
    assert html.count('name="pick-orc_m"') == 3  # two candidates + none
    assert 'value="none"' in html and "Copy as Markdown" in html
    assert "reference/orc_m/" in html and "anchors/orc_m/s3.wav" in html
    assert "ASR struggled" in html  # s0 has WER 40%
    assert "(-0.3 st)" in html  # s3 pitch 128 Hz vs the reference median 130 Hz
    for v in round4.VOICES:
        assert f'id="v-{v}"' in html


def test_result_page_shows_picked_voice_cards_and_ratings():
    results, ids = _results()
    results["stats"] = {}
    html = page4.result_page(results)
    assert "anchors/orc_m/s3.wav" in html and "audio/cont/orc_m/s3/" in html
    assert "audio/cont/orc_m/s0/" not in html
    assert 'name="cons:cont/orc_m"' in html and 'name="char:cont/orc_m"' in html
    assert "vo-bakeoff-r4-v1" in html and "vo-bakeoff-r3-v1" not in html
    assert "picked" in html and "none" in html

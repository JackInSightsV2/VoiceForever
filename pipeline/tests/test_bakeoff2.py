from collections import Counter

import numpy as np
import pytest

from vo.bakeoff import dsp, page2, round2
from vo.bakeoff.lines import KINDS
from vo.bakeoff.voicefeat import spectral_centroid


def test_round2_lines_are_ten_per_voice_covering_every_kind():
    lines = round2.lines()
    counts = Counter(l.voice for l in lines)
    assert set(counts) == set(round2.VOICE_IDS) and set(counts.values()) == {10}
    assert len({l.key for l in lines}) == len(lines)
    for v in round2.VOICE_IDS:
        assert {l.kind for l in lines if l.voice == v} == set(KINDS)
    orc_f = [l.text for l in lines if l.voice == "orc_f"]
    assert orc_f == [l.text for l in lines if l.voice == "orc_m"], "m/f of a race read the same texts"


def test_candidate_score_prefers_target_pitch_roughness_and_intelligibility():
    t = round2.Target(f0=75, hnr_max=6, centroid_max=900)
    deep_rough = {"f0": 78, "hnr": 5, "centroid": 850}
    human = {"f0": 115, "hnr": 11, "centroid": 1200}
    assert round2.candidate_score(deep_rough, 0.0, t) < round2.candidate_score(human, 0.0, t)
    garbled = round2.candidate_score(deep_rough, 0.3, t)
    assert garbled > round2.candidate_score(human, 0.0, t), "unintelligible references lose"
    cands = {"0": {"features": human, "wer": 0.0}, "1": {"features": deep_rough, "wer": 0.0},
             "2": {"features": deep_rough, "wer": 0.4}}
    assert round2.pick_candidate(cands, t) == "1"
    assert round2.pick_candidate({}, t) is None


def test_tune_strength_takes_strongest_within_budget_and_stops_at_first_failure():
    assert round2.tune_strength({0.5: 0.05, 1.0: 0.07, 1.5: 0.2}, base_wer=0.04) == 1.0
    assert round2.tune_strength({0.5: 0.2, 1.0: 0.05}, base_wer=0.0) == 0.0
    assert round2.tune_strength({1.5: 0.0, 0.5: 0.0, 1.0: 0.0}, base_wer=0.0) == 1.5


def test_dsp_approaches_skip_voices_without_a_chain():
    chains = {v: c.scaled(1.0) for v, c in dsp.RACE_CHAINS.items()}
    a = round2.approach("cb-vc")
    assert round2.applies(a, "orc_m", chains) and not round2.applies(a, "human_m", chains)
    assert round2.applies(round2.approach("cb-vox"), "human_m", chains)
    assert not round2.approach("vox-direct").consistent


def test_chain_scaling_and_description():
    c = dsp.RACE_CHAINS["orc_m"]
    assert c.scaled(0) == dsp.Chain() and c.scaled(0).is_identity
    assert c.scaled(1) == c
    half = c.scaled(0.5)
    assert half.pitch_st == pytest.approx(c.pitch_st / 2) and half.formant == pytest.approx(1 - (1 - c.formant) / 2)
    assert dsp.Chain().describe() == "none"
    assert "pitch_st=-3" in c.describe() and "range=0.85" in c.describe()


def test_vary_is_deterministic_bounded_and_distinct_per_npc():
    base = dsp.RACE_CHAINS["orc_m"]
    a, b = dsp.vary(base, 3139), dsp.vary(base, 3143)
    assert a == dsp.vary(base, 3139) and a != b
    for v in (a, b, dsp.vary(base, 1)):
        assert abs(v.pitch_st - base.pitch_st) <= dsp.VARY["pitch_st"] + 1e-9
        assert abs(v.formant - base.formant) <= dsp.VARY["formant"] + 1e-9
        assert 0 <= v.rasp <= 0.9 and 0 <= v.sub <= 1 and 0 <= v.wet <= 1


def test_rasp_envelope_stays_in_range():
    env = dsp.rasp_envelope(24000, 24000, 0.35, 45.0)
    assert env.min() >= 1 - 0.35 - 1e-6 and env.max() <= 1.0 + 1e-6
    assert np.all(dsp.rasp_envelope(100, 24000, 0.0, 45.0) == 1)


def test_identity_chain_returns_input_unchanged():
    x = np.random.default_rng(0).standard_normal(24000).astype(np.float32) * 0.1
    assert np.array_equal(dsp.apply(x, 24000, dsp.Chain()), x)


def test_spectral_centroid_of_a_tone_is_its_frequency():
    sr = 16000
    t = np.arange(sr) / sr
    assert spectral_centroid(np.sin(2 * np.pi * 1000 * t), sr) == pytest.approx(1000, rel=0.02)
    assert spectral_centroid(np.zeros(100), sr) is None


def _results():
    clip = {"file": "audio/cb-vox/orc_m/21-greeting.wav", "voice": "orc_m", "audio_s": 4.0, "wall_s": 2.0,
            "rtf": 2.0, "asr": "lok <tar>", "wer": 0.2, "features": {"f0": 80.0, "hnr": 5.5, "centroid": 880}}
    return {
        "meta": {"lines": 70, "hardware": "test", "updated": "now"},
        "candidates": {"vox": {"orc_m": {"0": {"file": "refs/vox/orc_m_s0.wav", "seconds": 9.0, "wer": 0.0,
                                               "features": {"f0": 80}, "score": 0.1},
                                         "1": {"file": "refs/vox/orc_m_s1.wav", "seconds": 9.0, "wer": 0.0,
                                               "features": {"f0": 110}, "score": 1.4}}}},
        "refs": {"vox": {"orc_m": "0"}},
        "dsp": {"orc_m": {"chain": "x", "k": 1.0, "final": "pitch_st=-3", "base_wer": 0.02,
                          "sweep": {"0.5": 0.02, "1.0": 0.03, "1.5": 0.2},
                          "tune_files": ["dsp_tune/orc_m/k1/21-greeting.wav"], "ref_file": "refs/dsp/orc_m.wav"}},
        "approaches": {"cb-vox": {"load_s": 1, "peak_mem_gb": 5.3}, "fish": {"error": "ImportError: nope"}},
        "clips": {"cb-vox": {"orc_m/21-greeting": clip}},
        "variation": {"archetype": {"ref_file": "refs/vox/orc_m.wav", "chain": "c", "dsp_ref_file": "refs/dsp/orc_m.wav"},
                      "npcs": {"3139": {"chain": "pitch_st=-2", "ref_file": "variation/3139/ref.wav",
                                        "clips": {"21-greeting": clip}}},
                      "similarity": {"archetype|3139": 0.91}},
        "benchmark": {"dwarf_m": {"label": "Dwarf bench", "clips": {"01-greeting": {"file": "benchmark/dwarf_m/01-greeting.wav",
                                                                                   "wer": 0.0}}}},
    }


def test_page2_has_players_picks_refs_dsp_variation_and_licences():
    html = page2.render(_results())
    assert 'src="audio/cb-vox/orc_m/21-greeting.wav"' in html
    assert 'name="orc_m/21-greeting" value="cb-vox"' in html
    assert 'src="refs/vox/orc_m_s1.wav"' in html and "chosen" in html
    assert "pitch_st=-3" in html and 'src="refs/dsp/orc_m.wav"' in html
    assert "NPC 3139" in html and "0.910" in html
    assert "did not run: ImportError: nope" in html
    assert "lok &lt;tar&gt;" in html
    assert 'src="benchmark/dwarf_m/01-greeting.wav"' in html
    assert "non-commercial" in html and "IndexTTS-2" in html
    assert 'data-vtally="orc_m|cb-vc"' in html and 'id="export"' in html
    for a in round2.APPROACHES:
        assert a.label.replace("'", "&#x27;") in html


def test_page2_renders_with_no_results():
    html = page2.render({})
    assert "No clips rendered yet" in html and "Research summary" in html

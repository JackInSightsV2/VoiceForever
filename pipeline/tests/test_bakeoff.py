from collections import Counter

import pytest

from vo.bakeoff import catalog, metrics, page
from vo.bakeoff.lines import KINDS, LINES, NARRATOR_LINES, by_id
from vo.bakeoff.text import chunk


def test_line_set_is_about_fifty_and_balanced():
    assert 45 <= len(LINES) <= 60
    assert len(by_id()) == len(LINES), "line ids are unique"
    voices = Counter(l.voice for l in LINES)
    assert set(voices) == {v.id for v in catalog.REF_VOICES}
    assert len(set(voices.values())) == 1, "every reference voice gets the same number of lines"
    assert {l.kind for l in LINES} == set(KINDS)
    for voice in voices:
        assert {l.kind for l in LINES if l.voice == voice} == set(KINDS), f"{voice} gets every kind"


def test_line_set_covers_lore_names_and_long_passages():
    text = " ".join(l.text for l in LINES)
    for name in ("Kel'Thuzad", "Quel'Thalas", "Ahn'Qiraj", "Thrall", "Stormwind"):
        assert name in text
    assert "$" not in text, "player tokens are replaced by a Neutral Address"
    assert sum(len(l.text) > 250 for l in LINES if l.kind == "detail") >= 8
    assert set(NARRATOR_LINES) <= set(by_id())


def test_wer_ignores_case_punctuation_and_apostrophes():
    assert metrics.wer("Kel'Thuzad walks!", "kelthuzad walks") == 0.0
    assert metrics.wer("one two three four", "one three four") == pytest.approx(0.25)
    assert metrics.wer("one two", "one two three four") == pytest.approx(1.0)


def test_summarise_uses_total_audio_over_total_wall():
    clips = {"a": {"audio_s": 10, "wall_s": 5, "rtf": 2.0, "wer": 0.0},
             "b": {"audio_s": 2, "wall_s": 4, "rtf": 0.5, "wer": 0.2}}
    s = metrics.summarise(clips)
    assert s["rtf"] == pytest.approx(12 / 9)
    assert s["rtf_median"] == pytest.approx(1.25)
    assert s["wer_over_10pct"] == 1


def test_chunk_keeps_sentences_whole():
    text = "One two. Three four five! Six? " + "x" * 50 + "."
    assert chunk(text, 20) == ["One two.", "Three four five!", "Six?", "x" * 50 + "."]
    assert chunk(text, 1000) == [text.strip()]


def _results():
    return {
        "meta": {"lines": 1, "hardware": "test", "updated": "now"},
        "refs": {"dwarf_m": {"file": "refs/dwarf_m.wav", "seconds": 9.5, "wer": 0.0}},
        "models": {"chatterbox": {"load_s": 1, "peak_mem_gb": 5.3}, "f5": {"error": "ImportError: nope"}},
        "clips": {"chatterbox": {"01-greeting": {"file": "audio/chatterbox/01-greeting.wav", "voice": "dwarf_m",
                                                  "audio_s": 4.0, "wall_s": 2.0, "rtf": 2.0,
                                                  "asr": "a <fresh> face", "wer": 0.2}}},
        "narrator": {},
    }


def test_page_has_players_speed_table_notes_and_licences():
    html = page.render(_results())
    assert 'src="audio/chatterbox/01-greeting.wav"' in html
    assert 'src="refs/dwarf_m.wav"' in html
    assert "2.00×" in html and "5.3 GB" in html
    assert "did not run: ImportError: nope" in html
    assert 'id="notes"' in html and "prefers-color-scheme:dark" in html
    assert "a &lt;fresh&gt; face" in html, "ASR text is escaped"
    for m in catalog.MODELS:
        assert m.label in html
    assert "CC-BY-NC-4.0" in html and "Llama 3.2" in html
    assert 'name="01-greeting" value="chatterbox"' in html, "rendered clips get a pick box"
    assert 'name="01-greeting" value="f5"' not in html and "not rendered" in html


def test_page_renders_with_no_results():
    html = page.render({})
    assert "No clips rendered yet" in html

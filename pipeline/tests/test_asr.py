import pytest

from vo import asr


def test_normalise_drops_case_and_punctuation_and_joins_apostrophes():
    assert asr.normalise("Kel'Thuzad's minions—beware! Stormwind-bound.") == [
        "kelthuzads", "minions", "beware", "stormwind", "bound"]


@pytest.mark.parametrize("ref, hyp, expected", [
    ("Hello there, friend.", " hello there friend", 0.0),
    ("kill ten wolves", "kill ten wolf", 1 / 3),      # substitution
    ("kill ten wolves", "kill wolves", 1 / 3),        # deletion
    ("kill ten wolves", "kill ten big wolves", 1 / 3),  # insertion
    ("kill ten wolves", "", 1.0),
    ("", "", 0.0),
    ("", "noise", 1.0),
])
def test_wer(ref, hyp, expected):
    assert asr.wer(ref, hyp) == pytest.approx(expected)

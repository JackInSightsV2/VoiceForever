"""The shared Drift test vectors (drift_vectors.json) must pass in both the pipeline and the Core Addon."""
import pytest

from conftest import VECTORS, lua_literal
from vo import drift

FNV = VECTORS["fnv1a32"]
QUEST_TEXT = VECTORS["quest_text"]


@pytest.mark.parametrize("v", FNV, ids=lambda v: repr(v["input"]))
def test_python_fnv1a32(v):
    assert drift.fnv1a32(v["input"].encode("utf-8")) == v["hash"]


@pytest.mark.parametrize("v", QUEST_TEXT, ids=lambda v: v["name"])
def test_python_quest_text(v):
    assert drift.mask(v["source"], v["gender"]) == v["masked"]
    assert drift.text_hash(v["source"], v["gender"]) == v["hash"]


def test_python_requires_gender_for_gendered_text():
    with pytest.raises(ValueError):
        drift.text_hash("Hello $gsir:madam;.")


def test_lua_vectors(lua):
    (fnv, masked, hashes) = lua(f"""
      load_addon()
      local VF, fnv, masked, hashes = VoiceForever, {{}}, {{}}, {{}}
      for i, v in ipairs({lua_literal(FNV)}) do fnv[i] = VF.Fnv1a32(v.input) end
      for i, v in ipairs({lua_literal(QUEST_TEXT)}) do
        local p = v.player
        masked[i] = VF.Mask(v.displayed, p.name, p.race, p["class"])
        hashes[i] = VF.DriftHash(v.displayed, p.name, p.race, p["class"])
      end
      emit(fnv) emit(masked) emit(hashes)
    """)
    assert fnv == [v["hash"] for v in FNV]
    assert masked == [v["masked"] for v in QUEST_TEXT]
    assert hashes == [v["hash"] for v in QUEST_TEXT]

"""Round 5 of the bake-off (#10): a guttural orc male that stays orc.

Round 4: none of 8 VoxCPM2 voice-design anchors for orc male had the character; the closest (s6) "was not
guttural and orcish; over time it cleaned itself up and just became a human male voice". VoxCPM2's growl is
unstable even within one clip. Round 5 tries, for orc male only:

1. Stronger descriptions: 3 descriptions that push the growl harder, 8 anchor candidates each, auto-ranked by a
   character proxy (roughness sustained through the whole clip, first vs last third) and shown for picking.
2. "dsp-anchor": the best raw anchors (round-4 s6 and the top new ones) through an orc DSP chain (pitch/formant
   down, period-doubling subharmonic, distorted growl layer, EQ), then VoxCPM2 continuation from the processed
   anchor. Does the grit carry into the lines?
3. "cont-dsp": continuation from the best raw anchor, then the same chain on every line (deterministic: grit
   can't wash out), strength tuned against ASR WER.
4. "cont-vc": the same continuation lines converted by Chatterbox's S3Gen (speech tokens of the line, timbre of
   the processed anchor).

This module is data and pure logic: descriptions, candidate ids, the character score and ranking, the chain,
the plan and the stats. Rendering is in run5 (models in engines5); the page in page5.
"""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass

from vo.bakeoff import dsp, round2, round3, round4

VOICE = "orc_m"
SEEDS = tuple(range(8))
ANCHOR_TEXT = round4.ANCHOR_TEXT["orc"]  # same anchor text as round 4, so s6 is comparable
R4_ANCHOR = "r4-s6"  # round 4's near-miss, copied in and treated as one more candidate
R4_SEED = 6
N_NEW = 2  # top-ranked new anchors used for step 2, next to round-4 s6


@dataclass(frozen=True)
class Description:
    id: str
    label: str
    text: str


# The model card's own examples are short attribute lists in the "(description)" prefix ("A young woman, gentle
# and sweet voice"): gender, age, tone, emotion, pace. Round 2-4's description was long prose. Here: the prose
# pushed harder, a card-style attribute list, and a performance framing ("a voice actor doing a monster").
DESCRIPTIONS: tuple[Description, ...] = (
    Description("growl", "Prose, pushed harder",
                "A monstrous male orc warlord. An extremely deep, guttural bass voice with a heavy throat growl on "
                "every single word, thick crackling gravel and harsh vocal fry from the first word to the last, never "
                "clean or smooth. Snarling, bestial and menacing, huge booming chest resonance, slow, heavy, deliberate "
                "pace. Inhuman."),
    Description("tags", "Model-card style attribute list",
                "Male orc, middle-aged, very deep guttural growling voice, gravelly, raspy, heavy vocal fry, snarling, "
                "strong chest resonance, very low pitch, slow menacing pace, aggressive"),
    Description("beast", "Performance framing",
                "A voice actor performing a savage orc monster: a strained, distorted, growling roar forced through the "
                "throat, rough and crackling on every syllable, snarls between phrases, very deep and heavy, slow and "
                "threatening. The growl never lets up."),
)
DESC_IDS = tuple(d.id for d in DESCRIPTIONS)


def description(desc_id: str) -> Description:
    return next(d for d in DESCRIPTIONS if d.id == desc_id)


def subject(desc_id: str) -> round4.Subject:
    d = description(desc_id)
    return round4.Subject(VOICE, VOICE, f"Orc, male ({d.label})", d.text, SEEDS, "main")


def cand_id(desc_id: str, seed: int) -> str:
    return f"{desc_id}-s{seed}"


def lines() -> tuple[round2.Line, ...]:
    return round4.lines(VOICE)


# --- character proxy ---------------------------------------------------------

ANCHOR_MAX_WER = round4.ANCHOR_MAX_WER
HIGH_F0 = 130.0  # Hz; above this an orc male starts to sound like a man


def char_score(c: dict) -> float | None:
    """Higher = more orc, by ear-proxy (see voicefeat.thirds). Sustained roughness (the cleanest third's share of
    rough frames) dominates, plus half the whole-clip share; minus 0.1 per dB the last third is cleaner than the
    first; minus 0.3 per octave above HIGH_F0; minus 1 when ASR could not follow it."""
    r = c.get("rough3") or {}
    if r.get("sustained") is None:
        return None
    s = r["sustained"] + 0.5 * (r.get("rough") or 0)
    s -= 0.1 * max(0.0, r.get("drift") or 0)
    f0 = (c.get("features") or {}).get("f0")
    if f0:
        s -= 0.3 * max(0.0, math.log2(f0 / HIGH_F0))
    if (c.get("wer") or 0) > ANCHOR_MAX_WER:
        s -= 1.0
    return round(s, 3)


def rank(cands: dict[str, dict]) -> list[str]:
    """Candidate ids, most orc first (ties by id); unscored candidates last."""
    scored = [(char_score(c), cid) for cid, c in cands.items()]
    return [cid for _, cid in sorted(scored, key=lambda x: (x[0] is None, -(x[0] or 0), x[1]))]


def sources(cands: dict[str, dict], picks: list[str] | None = None, n_new: int = N_NEW) -> list[str]:
    """Anchors for step 2, best first. With picks: the picks, in order. Otherwise round-4 s6 and the top
    `n_new` new candidates, ordered by character score."""
    if picks:
        return list(dict.fromkeys(picks))
    ranked = rank(cands)
    new = [c for c in ranked if c != R4_ANCHOR][:n_new]
    chosen = set(new) | ({R4_ANCHOR} if R4_ANCHOR in cands else set())
    return [c for c in ranked if c in chosen]


_PICK = re.compile(r"^\s*(?:[-*]\s*)?pick\s*[:=]\s*`?([A-Za-z0-9_-]+)`?\s*$", re.I)


def parse_picks(text: str) -> list[str]:
    """`- pick: <candidate id>` lines (the pick page's Markdown export), in order."""
    return [m.group(1) for line in text.splitlines() if (m := _PICK.match(line))]


def check_picks(picks: list[str], cands: dict[str, dict]) -> list[str]:
    errs = [f"no candidate {p!r}" for p in picks if p not in cands]
    if not picks:
        errs.append("no anchors picked")
    return errs


# --- the orc chain -----------------------------------------------------------

# Round 2's orc chain (dsp.RACE_CHAINS["orc_m"]) moved the voice down but barely roughened it (Praat's
# re-synthesis even smooths it: round-4 s6 HNR 6.5 -> 7.1 dB). This one keeps its size stages, eases the pitch
# drop (the subharmonic already reads as lower), and adds the two growl stages (dsp.subharmonic_envelope,
# dsp._growl_layer) that do lower HNR on every voiced frame.
ORC_CHAIN = dsp.Chain(pitch_st=-2.5, formant=0.86, range_=0.8, sub=0.15, rasp=0.25, rasp_hz=45, drive_db=12, wet=0.25,
                      low_shelf_db=3, high_cut_hz=9000, subharm=0.5, growl=0.4)
ANCHOR_STRENGTH = 1.0
STRENGTHS = (0.5, 0.75, 1.0, 1.25, 1.5)  # per-line DSP sweep
WER_BUDGET = 0.05  # over the unprocessed lines' mean WER


def tune(sweep: dict[float, float], base_wer: float) -> float:
    return round2.tune_strength(sweep, base_wer, WER_BUDGET)


# --- variants ----------------------------------------------------------------

@dataclass(frozen=True)
class Variant:
    id: str
    label: str
    item: int      # the ticket's numbered approach
    note: str


VARIANTS: tuple[Variant, ...] = (
    Variant("cont", "Continuation from the raw anchor", 0,
            "Control: round 3/4's continuation (anchor + transcript as the prompt), from the unprocessed anchor."),
    Variant("dsp-anchor", "Continuation from the DSP'd anchor", 2,
            "The anchor goes through the orc chain first; VoxCPM2 continues from the processed clip."),
    Variant("cont-dsp", "Continuation + orc chain on every line", 3,
            "The control's lines, each through the orc chain at the tuned strength. Deterministic grit."),
    Variant("cont-vc", "Continuation → Chatterbox VC to the DSP'd anchor", 4,
            "The control's lines, re-voiced by Chatterbox's S3Gen: speech tokens from the line, timbre from the "
            "DSP'd anchor."),
)
VARIANT_IDS = tuple(v.id for v in VARIANTS)
REFERENCE = "r2-direct"


def variant(vid: str) -> Variant:
    return next(v for v in VARIANTS if v.id == vid)


def plan(srcs: list[str]) -> list[tuple[str, str]]:
    """(variant, anchor id) to render: continuation raw and from the DSP'd anchor for every source anchor; the
    per-line post-processing variants only for the best (first) one."""
    out = [(vid, a) for a in srcs for vid in ("cont", "dsp-anchor")]
    if srcs:
        out += [("cont-dsp", srcs[0]), ("cont-vc", srcs[0])]
    return out


def clip_key(anchor: str, line_id: str) -> str:
    return f"{anchor}/{line_id}"


def variant_clips(results: dict, vid: str, anchor: str) -> dict[str, dict]:
    pre = anchor + "/"
    return {k[len(pre):]: c for k, c in results.get("clips", {}).get(vid, {}).items() if k.startswith(pre)}


# --- stats -------------------------------------------------------------------

def _median(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 3) if xs else None


def rough_row(clips: list[dict]) -> dict:
    """Median roughness numbers over a variant's lines (see voicefeat.thirds)."""
    r = [c.get("rough3") or {} for c in clips]
    return {"rough": _median([x.get("rough") for x in r]), "sustained": _median([x.get("sustained") for x in r]),
            "drift": _median([x.get("drift") for x in r]),
            "first": _median([(x.get("rough_thirds") or [None])[0] for x in r]),
            "last": _median([(x.get("rough_thirds") or [None, None, None])[2] for x in r])}


def row(clips: list[dict], embs: list) -> dict:
    return {**round3.voice_row(clips, embs), **rough_row(clips)}


def build_stats(results: dict, emb: dict) -> dict:
    """Pure: results + {file: embedding} in; the reference row and one row per rendered (variant, anchor), with
    within-voice cosine, similarity to the anchor the lines came from (the processed one for dsp-anchor and the
    VC target for cont-vc) and to the round-2 reference centroid, pitch, HNR, roughness, drift, WER, RTF."""
    ref = list(results.get("reference", {}).values())
    ref_e = round3._embs(ref, emb)
    out: dict = {}
    if ref:
        out[REFERENCE] = row(ref, ref_e)
    for vid in VARIANT_IDS:
        for a in results.get("sources", []):
            cs = list(variant_clips(results, vid, a).values())
            if not cs:
                continue
            e = round3._embs(cs, emb)
            r = row(cs, e)
            anchor = (results.get("dsp_anchors", {}).get(a) if vid in ("dsp-anchor", "cont-vc")
                      else results.get("anchors", {}).get(a))
            if anchor and anchor.get("file") in emb and e:
                r["anchor_sim"] = round3.to_ref(e, emb[anchor["file"]])
            if ref_e and e:
                r["arch_sim"] = round3.to_ref(e, round3.centroid(ref_e))
            out[f"{vid}@{a}"] = r
    return out

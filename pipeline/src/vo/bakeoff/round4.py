"""Round 4 of the bake-off (#10): human-picked anchors, then continuation from the pick.

Round 3 found anchor continuation keeps one identity per NPC, but copies the anchor faithfully: where
the auto-picked anchor (chosen by target pitch) had no character, neither had the lines. Round 4 is a
two-step prototype of the Approval Gate:

1. Render 8 VoxCPM2 voice-design candidate anchors per voice; a human picks one (or "none").
2. Render the voice's 10 lines by continuation from the picked anchor ("cont", round-3 B) and, for
   comparison, ultimate cloning ("ultimate", round-3 B2), and rate them as in round 3.

This module is data and pure logic: voices, anchor texts, candidate ids, parsing the picks, the plan.
Rendering is in run4 (reusing round-3's VoxCPM2 wrapper); the pages in page4.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from vo.bakeoff import round2, round3

VOICES = ("orc_m", "orc_f", "human_f", "human_m", "troll_f")
SEEDS = tuple(range(8))  # candidate anchors per voice
VARIANTS = ("cont", "ultimate")  # step 2: round-3 B, and B2 for comparison
NONE = "none"  # the "none of these have the character" pick
ANCHOR_MAX_WER = 0.15  # above this, the page flags the candidate: continuation would inherit misreadings

# One quest-giver line per race to continue from: neutral enough to carry any later line, with some
# attitude so the designed voice commits to a character. ~8-15 s at a natural pace.
ANCHOR_TEXT = {
    "orc": ("You there. Raiders in the eastern ravine have been stealing our wolves. Go, bring me the heads "
            "of their leaders, and the Horde will remember your name."),
    "troll": ("Ah, mon, de spirits told me you be comin'. Dem raptors in de jungle be stealin' our eggs again. "
              "You go bring dem back, and I make it worth your while."),
    "human": ("Traveller, a moment of your time. Bandits have been raiding the farms along the river road, and "
              "the guard is stretched too thin. If you could drive them off, the whole village would be in your debt."),
}


def anchor_text(voice_id: str) -> str:
    return ANCHOR_TEXT[voice_id.split("_")[0]]


@dataclass(frozen=True)
class Subject(round3.Subject):
    """A round-2 voice as a round-3 Subject (same description, lines, target), reading round 4's anchor text."""

    @property
    def anchor_text(self) -> str:
        return anchor_text(self.voice)


def subject(voice_id: str) -> Subject:
    v = round2.voice(voice_id)
    return Subject(voice_id, voice_id, v.label, v.prompt, SEEDS, "main")


def cand_id(seed: int) -> str:
    return f"s{seed}"


def lines(voice_id: str) -> tuple[round2.Line, ...]:
    return tuple(l for l in round2.lines() if l.voice == voice_id)


# --- picks -------------------------------------------------------------------

_PICK = re.compile(r"^\s*(?:[-*]\s*)?`?([a-z]+_[mf]\d*)`?\s*[:=]\s*`?([A-Za-z0-9_-]+)`?\s*$")


def parse_picks(text: str) -> dict[str, str]:
    """`voice: candidate id` lines (the step-1 page's Markdown export, with or without list bullets and
    other text around them), or a JSON object {voice: candidate id}. Voices not in round 4 are ignored;
    "none" picks are kept (they mean: no anchor had the character)."""
    s = text.strip()
    if s.startswith("{"):
        raw = {str(k): str(v) for k, v in json.loads(s).items()}
    else:
        raw = {}
        for line in s.splitlines():
            m = _PICK.match(line)
            if m:
                raw[m.group(1)] = m.group(2)
    return {v: c.lower() if c.lower() == NONE else c for v, c in raw.items() if v in VOICES}


def check_picks(picks: dict[str, str], anchors: dict[str, dict]) -> list[str]:
    """Problems with the picks against the rendered candidates (empty list = usable)."""
    errs = []
    for voice, cid in picks.items():
        if cid == NONE:
            continue
        if cid not in anchors.get(voice, {}).get("cands", {}):
            errs.append(f"{voice}: no candidate {cid!r} (have {sorted(anchors.get(voice, {}).get('cands', {}))})")
    if not any(c != NONE for c in picks.values()):
        errs.append("no voice has a picked anchor")
    return errs


def plan(picks: dict[str, str]) -> list[tuple[str, str, str]]:
    """(variant, voice, candidate id) to render: each variant for every voice with a real pick,
    continuation first so the main candidate is ready soonest."""
    return [(vid, voice, cid) for vid in VARIANTS for voice, cid in picks.items() if cid != NONE]


def clip_key(voice: str, cid: str, line_id: str) -> str:
    """Step-2 clips are keyed by the anchor they continue from, so a changed pick re-renders."""
    return f"{voice}@{cid}/{line_id}"


# --- step-2 numbers ----------------------------------------------------------

REFERENCE = "r2-direct"  # round-2 vox-direct clips of the voice: the character the human liked


def picked_clips(results: dict, vid: str, voice: str, cid: str) -> dict[str, dict]:
    """{line id: clip} for one variant continuing from one anchor."""
    pre = f"{voice}@{cid}/"
    return {k[len(pre):]: c for k, c in results.get("clips", {}).get(vid, {}).items() if k.startswith(pre)}


def reference_clips(results: dict, voice: str) -> dict[str, dict]:
    pre = voice + "/"
    return {k[len(pre):]: c for k, c in results.get("reference", {}).items() if k.startswith(pre)}


def build_stats(results: dict, emb: dict) -> dict:
    """Pure: results + {file: embedding} in; per picked voice, the round-2 reference row and one row per
    variant from the picked anchor (round-3 measures: within-voice cosine, to-anchor, to-reference
    centroid, pitch/roughness shift against the reference, and the character guard)."""
    out: dict = {}
    for voice, cid in results.get("picks", {}).items():
        if cid == NONE:
            continue
        ref = list(reference_clips(results, voice).values())
        ref_e = round3._embs(ref, emb)
        base = round3.voice_row(ref, ref_e) if ref else {}
        rows = {REFERENCE: base} if ref else {}
        anchor = results.get("anchors", {}).get(voice, {}).get("cands", {}).get(cid)
        for vid in VARIANTS:
            cs = list(picked_clips(results, vid, voice, cid).values())
            if not cs:
                continue
            e = round3._embs(cs, emb)
            r = round3.voice_row(cs, e)
            if anchor and anchor["file"] in emb and e:
                r["anchor_sim"] = round3.to_ref(e, emb[anchor["file"]])
            if ref_e and e:
                r["arch_sim"] = round3.to_ref(e, round3.centroid(ref_e))
            r["d_st"] = round3.semitones(r["f0"], base.get("f0"))
            r["d_hnr"] = (round(r["hnr"] - base["hnr"], 1)
                          if r["hnr"] is not None and base.get("hnr") is not None else None)
            r["keeps"] = round3.keeps_character(r, base) if base else None
            rows[vid] = r
        out[voice] = rows
    return out

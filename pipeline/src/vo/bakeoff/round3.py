"""Round 3 of the bake-off (#10): one consistent VoxCPM2 voice per NPC.

Round 2's winner, VoxCPM2 voice design per line ("vox-direct"), re-designs the voice from its text
description on every line, so an NPC's lines don't sound like one person. Round 3 tries VoxCPM2's own
features to lock one identity per NPC while keeping vox-direct's character, and measures within-voice
speaker-embedding consistency, WER, RTF and ear-proxies.

This module is data and pure logic: subjects, variants, anchor texts, consistency statistics, the
best-variant rule. Rendering is in engines3/run3; the page in page3.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

import numpy as np

from vo.bakeoff import round2

# --- what VoxCPM2 is asked to do -------------------------------------------

@dataclass(frozen=True)
class Variant:
    id: str
    label: str
    letter: str       # the ticket's option letter
    anchor: bool      # needs the NPC's anchor clip
    desc: bool        # passes the voice description as "(description)" before the text
    ref: bool         # anchor as reference_wav (timbre prefix)
    prompt: bool      # anchor + its transcript as the continuation prompt
    fixed_seed: bool  # same diffusion seed for every line of the NPC
    note: str


VARIANTS: tuple[Variant, ...] = (
    Variant("direct", "Design per line (round-2 vox-direct)", "–", False, True, False, False, False,
            "Baseline: description only, a different seed per line. Round 2's winner on character."),
    Variant("seed", "Design per line, fixed seed per NPC", "A", False, True, False, False, True,
            "Description only; the NPC's seed is reset before every line."),
    Variant("cont", "Anchor continuation", "B", True, False, False, True, False,
            "Continuation mode: the NPC's designed anchor clip and its transcript are the prompt; each line "
            "continues from it (prompt_audio + prompt_text). No description."),
    Variant("cont-desc", "Anchor continuation + description", "D", True, True, False, True, False,
            "As B, with the description in parentheses before the line text (after the anchor transcript). "
            "Broken: mid-sequence, VoxCPM2 reads the description aloud instead of treating it as an instruction "
            "(WER 190-350%), so its high consistency is meaningless."),
    Variant("ultimate", "Anchor 'ultimate cloning' (ref + continuation)", "B2", True, False, True, True, False,
            "The model card's 'ultimate cloning': the anchor as both reference_wav and continuation prompt."),
    Variant("ref", "Anchor as reference (plain cloning)", "C0", True, False, True, False, False,
            "Reference cloning from the anchor, no transcript and no description. Control for C."),
    Variant("ref-desc", "Anchor as reference + description (controllable cloning)", "C", True, True, True, False,
            False, "The model card's 'controllable cloning': the anchor as reference_wav, plus the voice "
                   "description as the (style) prefix."),
)

VARIANT_IDS = tuple(v.id for v in VARIANTS)
RENDER_ORDER = ("cont", "ref-desc", "ultimate", "cont-desc", "ref", "seed")  # B and C first
BASELINE = "r2-direct"  # round-2 vox-direct clips, re-measured here, not re-rendered


def variant(variant_id: str) -> Variant:
    return next(v for v in VARIANTS if v.id == variant_id)


def generate_kwargs(v: Variant, description: str, anchor: str | None, anchor_text: str | None) -> dict:
    """Keyword arguments for mlx-audio's VoxCPM2 Model.generate (text excluded)."""
    kw: dict = {}
    if v.desc:
        kw["instruct"] = description
    if v.anchor and anchor is None:
        raise ValueError(f"{v.id} needs an anchor clip")
    if v.ref:
        kw["ref_audio"] = anchor
    if v.prompt:
        kw["prompt_audio"] = anchor
        kw["prompt_text"] = anchor_text
    return kw


REPO = "mlx-community/VoxCPM2-bf16"
SETTINGS = {"inference_timesteps": 10, "cfg_value": 2.0}  # model defaults, as in round 2
LICENCE = "Apache-2.0 (openbmb/VoxCPM2)"
# The first run's cont and ref-desc renders overlapped a CPU embedding job, which depressed their RTF.
# A separate re-time of the same 10 lines (orc male / orc female, nothing else running) gave:
RETIMED_RTF = {"direct": (1.74, 1.11), "cont": (1.53, 0.92), "ref-desc": (1.46, 1.17), "ultimate": (1.22, 1.16),
               "ref": (1.12, 1.26)}
EMBEDDER = "microsoft/wavlm-base-plus-sv"
EMBEDDER_LABEL = "WavLM-Base-Plus-SV x-vector (microsoft/wavlm-base-plus-sv)"
EMBEDDER_SAME = 0.86  # the model card's suggested same-speaker cosine threshold

# --- who speaks --------------------------------------------------------------

DWARF_F_V2 = (
    "A short, stout dwarf woman in her fifties, a blacksmith and brewer from the mountains, with a strong, "
    "broad Scottish accent: rolled r's, clipped consonants and a bouncing Highland lilt. Her voice is a low, "
    "warm, chesty alto with a gravelly edge from years at a smoky forge. Loud, hearty and cheerful, quick to "
    "laugh, blunt and motherly. Clearly a woman, clearly a dwarf, never posh.")

ANCHOR_TEXT = {
    "orc": ("Blood and thunder. I have seen more battles than you have seen winters, and I am still standing. "
            "Sharpen your blade, keep your wits about you, and do not waste my time with foolish questions."),
    "troll": ("Ah, mon, de spirits told me you be comin'. Sit down by de fire and listen close, because de old "
              "ways don't lie, and dis island got many secrets for dem who be patient."),
    "human": ("Good morning, traveller. It has been a long week in the city, what with the rain and the guards "
              "shouting on every corner. Still, it is good to see a friendly face at last."),
    "dwarf": ("Well, don't just stand there gawkin'. The forge is hot, the ale is cold, and there's work in "
              "these mountains for anyone with a strong back and a stout heart."),
}
ANCHOR_SEEDS = (0, 1, 2)  # best of 3 by ear-proxies + ASR, standing in for the Approval Gate


@dataclass(frozen=True)
class Subject:
    """One voice identity under test: an Archetype's main voice, or one NPC derived from it."""
    key: str             # results key, e.g. "orc_m", "dwarf_f2", "orc_m@3139"
    voice: str           # round-2 voice whose lines (and, unless overridden, description) it uses
    label: str
    description: str
    anchor_seeds: tuple[int, ...]
    group: str           # "main", "dwarf", "npc"

    @property
    def race(self) -> str:
        return self.voice.split("_")[0]

    @property
    def anchor_text(self) -> str:
        return ANCHOR_TEXT[self.race]

    @property
    def target(self) -> round2.Target:
        return round2.voice(self.voice).target

    @property
    def male(self) -> bool:
        return round2.voice(self.voice).male


MAIN_VOICES = ("orc_m", "troll_m", "human_f", "orc_f")
NPC_IDS = (3139, 3143, 3188)  # three orc NPCs from one Archetype; the anchor seed is the NPC id
NPC_REROLL = 1_000_003        # next anchor seed if an NPC's anchor is unintelligible
NPC_ANCHOR_MAX_WER = 0.15
NPC_TRIES = 3


def _main(vid: str) -> Subject:
    v = round2.voice(vid)
    return Subject(vid, vid, v.label, v.prompt, ANCHOR_SEEDS, "main")


SUBJECTS: tuple[Subject, ...] = (
    *(_main(v) for v in MAIN_VOICES),
    Subject("dwarf_f2", "dwarf_f", "Dwarf, female (new description)", DWARF_F_V2, ANCHOR_SEEDS, "dwarf"),
    *(Subject(f"orc_m@{n}", "orc_m", f"Orc NPC {n}", round2.voice("orc_m").prompt, (n,), "npc") for n in NPC_IDS),
)


def subject(key: str) -> Subject:
    return next(s for s in SUBJECTS if s.key == key)


def npc_seeds(npc: int, tries: int = NPC_TRIES) -> list[int]:
    return [npc + k * NPC_REROLL for k in range(tries)]


def lines(s: Subject) -> tuple[round2.Line, ...]:
    return tuple(l for l in round2.lines() if l.voice == s.voice)


def plan(best: str | None) -> list[tuple[str, str]]:
    """(variant id, subject key) pairs to render. Main voices get every variant but "direct" (round 2
    already has it); the new dwarf description gets "direct" and the best variant; the NPCs the best."""
    out = [(v, s.key) for v in RENDER_ORDER for s in SUBJECTS if s.group == "main"]
    if best:
        out += [("direct", "dwarf_f2"), (best, "dwarf_f2")]
        out += [(best, s.key) for s in SUBJECTS if s.group == "npc"]
    return list(dict.fromkeys(out))


# --- anchors -----------------------------------------------------------------

def pick_anchor(cands: dict[str, dict], s: Subject) -> str | None:
    """Main voices: best of ANCHOR_SEEDS by the round-2 Candidate score. NPCs: the first seed whose
    anchor is intelligible (deterministic by NPC id, no human step), else the last tried."""
    if not cands:
        return None
    if s.group == "npc":
        for seed in sorted(cands, key=int):
            if (cands[seed].get("wer") or 0) <= NPC_ANCHOR_MAX_WER:
                return seed
        return max(cands, key=int)
    return round2.pick_candidate(cands, s.target)


# --- consistency statistics --------------------------------------------------

def cosine(a, b) -> float:
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    return float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1))


def within(embs: list) -> dict:
    """Pairwise cosine among one voice's clips: how much it sounds like one person."""
    pairs = [cosine(embs[i], embs[j]) for i in range(len(embs)) for j in range(i + 1, len(embs))]
    if not pairs:
        return {"mean": None, "min": None, "n": len(embs)}
    return {"mean": round(statistics.fmean(pairs), 3), "min": round(min(pairs), 3), "n": len(embs)}


def between(a: list, b: list) -> float:
    """Mean cosine over all clip pairs of two voices (same scale as `within`)."""
    return round(statistics.fmean(cosine(x, y) for x in a for y in b), 3)


def centroid(embs: list) -> np.ndarray:
    c = np.mean(np.asarray(embs, dtype=np.float64), axis=0)
    return c / (np.linalg.norm(c) or 1)


def to_ref(embs: list, ref) -> float:
    return round(statistics.fmean(cosine(e, ref) for e in embs), 3)


def separation(groups: dict[str, list]) -> dict:
    """Within vs between for a set of voices: per voice within-mean, all between-pairs, and the margin
    (mean within minus the highest between: how far apart the closest two voices are)."""
    w = {k: within(e) for k, e in groups.items()}
    keys = list(groups)
    b = {f"{x}|{y}": between(groups[x], groups[y]) for i, x in enumerate(keys) for y in keys[i + 1:]}
    ws = [x["mean"] for x in w.values() if x["mean"] is not None]
    out = {"within": w, "between": b,
           "within_mean": round(statistics.fmean(ws), 3) if ws else None,
           "between_mean": round(statistics.fmean(b.values()), 3) if b else None,
           "between_max": max(b.values()) if b else None}
    if ws and b:
        out["margin"] = round(min(ws) - max(b.values()), 3)
    return out


def semitones(f: float | None, ref: float | None) -> float | None:
    if not f or not ref:
        return None
    return round(12 * math.log2(f / ref), 2)


def median_feature(clips: list[dict], name: str) -> float | None:
    xs = [c.get("features", {}).get(name) for c in clips]
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def mean_wer(clips: list[dict]) -> float | None:
    xs = [c["wer"] for c in clips if c.get("wer") is not None]
    return round(statistics.fmean(xs), 3) if xs else None


# Character guard for picking the best variant: may not drift further than this from round-2 vox-direct.
MAX_F0_ST = 2.0
MAX_HNR_RISE = 3.0
WER_SLACK = 0.05


def keeps_character(row: dict, base: dict, target_f0: float | None = None) -> bool:
    """row/base: {"f0", "hnr", "wer"} medians/means for one voice. Pitch within MAX_F0_ST semitones of
    the baseline (or else closer to the voice's target pitch than the baseline is: an anchor picked for a
    deeper orc is not a loss of character), not much smoother (HNR rise), and WER no worse than
    baseline + slack."""
    st = semitones(row.get("f0"), base.get("f0"))
    if st is None:
        return False
    if abs(st) > MAX_F0_ST:
        toward = (target_f0 is not None
                  and abs(math.log2(row["f0"] / target_f0)) < abs(math.log2(base["f0"] / target_f0)))
        if not toward:
            return False
    if row.get("hnr") is not None and base.get("hnr") is not None and row["hnr"] - base["hnr"] > MAX_HNR_RISE:
        return False
    if row.get("wer") is not None and base.get("wer") is not None and row["wer"] > base["wer"] + WER_SLACK:
        return False
    return True


def pick_best(table: dict[str, dict[str, dict]], base: dict[str, dict]) -> str | None:
    """table[variant][voice] = {"within", "f0", "hnr", "wer"}; base[voice] = round-2 vox-direct row.
    The most consistent variant (mean within-voice cosine) among those that keep character on every
    voice; if none does, the one that keeps it on most voices, then most consistent."""
    scored = []
    for vid, rows in table.items():
        if vid in ("direct", BASELINE) or not rows:
            continue
        ws = [r["within"] for r in rows.values() if r.get("within") is not None]
        if not ws:
            continue
        kept = sum(bool(r["keeps"]) if "keeps" in r else keeps_character(r, base.get(voice, {}))
                   for voice, r in rows.items())
        scored.append((kept == len(rows), kept, statistics.fmean(ws), vid))
    return max(scored)[3] if scored else None


# --- the stats the page and report show -------------------------------------

def by_subject(clips: dict[str, dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for k, c in sorted(clips.items()):
        out.setdefault(k.split("/")[0], []).append(c)
    return out


def _embs(clips: list[dict], emb: dict, prefix: str = "") -> list:
    return [emb[prefix + c["file"]] for c in clips if prefix + c["file"] in emb]


def _rtf(clips: list[dict]) -> float | None:
    wall = sum(c.get("wall_s", 0) for c in clips)
    return round(sum(c.get("audio_s", 0) for c in clips) / wall, 2) if wall else None


def f0_spread(clips: list[dict]) -> float | None:
    """Standard deviation of the lines' median pitch, in semitones: a second, embedding-free drift measure."""
    f0s = [c.get("features", {}).get("f0") for c in clips]
    st = [12 * math.log2(f) for f in f0s if f]
    return round(statistics.stdev(st), 2) if len(st) > 1 else None


def voice_row(clips: list[dict], embs: list) -> dict:
    w = within(embs)
    return {"n": len(clips), "within": w["mean"], "within_min": w["min"], "wer": mean_wer(clips),
            "rtf": _rtf(clips), "f0": median_feature(clips, "f0"), "hnr": median_feature(clips, "hnr"),
            "f0_sd": f0_spread(clips)}


def build_stats(results: dict, r2_clips: dict, emb: dict) -> dict:
    """Pure: round-3 results + round-2 clips + {file key: embedding} in, the numbers tables out.
    Round-2 files are keyed "r2:<file>"; round-3 files by their path under the round-3 output."""
    out: dict = {}
    # 1. Round-2 baseline: within-voice vs between-voice for every round-2 approach.
    r2 = {}
    for app, clips in r2_clips.items():
        groups = {s: e for s, cs in by_subject(clips).items() if len(e := _embs(cs, emb, "r2:")) > 1}
        if len(groups) > 1:
            r2[app] = separation(groups)
    out["r2"] = r2

    # 2. Variants on the main voices, against round-2 vox-direct.
    base_by = by_subject(results.get("clips", {}).get(BASELINE, {}))
    base_rows, arch = {}, {}
    for voice in MAIN_VOICES:
        e = _embs(base_by.get(voice, []), emb)
        if base_by.get(voice):
            base_rows[voice] = voice_row(base_by[voice], e)
        if e:
            arch[voice] = centroid(e)
    table = {}
    for vid in (BASELINE, *RENDER_ORDER):
        by = by_subject(results.get("clips", {}).get(vid, {}))
        rows, groups = {}, {}
        for voice in MAIN_VOICES:
            cs = by.get(voice)
            if not cs:
                continue
            e = _embs(cs, emb)
            groups[voice] = e
            r = voice_row(cs, e)
            anchor = results.get("anchors", {}).get(voice, {}).get("file")
            if vid != BASELINE and variant(vid).anchor and anchor in emb and e:
                r["anchor_sim"] = to_ref(e, emb[anchor])
            if voice in arch and e:
                r["arch_sim"] = to_ref(e, arch[voice])
            b = base_rows.get(voice, {})
            r["d_st"] = semitones(r["f0"], b.get("f0"))
            r["d_hnr"] = (round(r["hnr"] - b["hnr"], 1)
                          if r["hnr"] is not None and b.get("hnr") is not None else None)
            r["keeps"] = keeps_character(r, b, round2.voice(voice).target.f0) if b else None
            rows[voice] = r
        if not rows:
            continue
        sep = separation(groups) if len(groups) > 1 else {}
        ws = [r["within"] for r in rows.values() if r["within"] is not None]
        table[vid] = {"rows": rows, "within_mean": round(statistics.fmean(ws), 3) if ws else None,
                      **{k: sep.get(k) for k in ("between", "between_mean", "between_max", "margin")}}
    out["variants"] = table
    out["best"] = pick_best({vid: t["rows"] for vid, t in table.items()}, base_rows)

    # 3. Dwarf female: round-2 rows and the new description.
    dwarf = {}
    for vid, key in ((BASELINE, "dwarf_f"), ("r2-omni", "dwarf_f"), *((v, "dwarf_f2") for v in VARIANT_IDS)):
        cs = by_subject(results.get("clips", {}).get(vid, {})).get(key)
        if cs:
            dwarf[f"{vid}|{key}"] = voice_row(cs, _embs(cs, emb))
    out["dwarf"] = dwarf

    # 4. Distinct NPCs: three orc NPCs (and the Archetype's own anchor) under one variant.
    npc_keys = [s.key for s in SUBJECTS if s.group == "npc"]
    for vid, clips in results.get("clips", {}).items():
        by = by_subject(clips)
        if not all(k in by for k in npc_keys):
            continue
        groups = {k: _embs(by[k], emb) for k in ["orc_m", *npc_keys] if k in by}
        sep = separation(groups)
        anchors = {k: emb[f] for k in groups if (f := results.get("anchors", {}).get(k, {}).get("file")) in emb}
        ak = list(anchors)
        sep["anchor_pairs"] = {f"{x}|{y}": round(cosine(anchors[x], anchors[y]), 3)
                               for i, x in enumerate(ak) for y in ak[i + 1:]}
        sep["rows"] = {k: voice_row(by[k], groups[k]) for k in groups}
        out["npc"] = {"variant": vid, **sep}
    return out

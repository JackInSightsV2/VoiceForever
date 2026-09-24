"""Round 2 of the bake-off (#10): fantasy race voices.

Round 1 found no acceptable orc or troll voice. Round 2 compares ways to get one while keeping
the per-NPC pipeline (a cloning model conditioned on a reference clip designed from an Archetype):
better-designed references, other cloners, a per-race DSP chain, and voice conversion.

This module is data and pure logic: voices, prompts, targets, approaches, line set, scoring.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from vo.bakeoff import catalog
from vo.bakeoff.lines import LINES as R1_LINES

REF_TEXT = catalog.REF_TEXT
SEEDS = (0, 1, 2, 3)  # reference Candidates per designer per voice


@dataclass(frozen=True)
class Target:
    """What the ear-proxies should show for this voice (see voicefeat)."""
    f0: float                      # Hz, preferred median pitch
    hnr_max: float | None = None   # dB; rougher (lower) is preferred up to here
    centroid_max: float | None = None  # Hz; darker (lower) preferred up to here


@dataclass(frozen=True)
class Voice:
    id: str
    label: str
    male: bool
    lines_from: str      # round-1 voice whose line texts this voice reads
    prompt: str          # voice-design prompt (both designers)
    target: Target


VOICES: tuple[Voice, ...] = (
    Voice("dwarf_f", "Dwarf, female", False, "dwarf_m",
          "A sturdy middle-aged dwarf woman with a warm, full, slightly husky alto voice and a thick, broad "
          "Scottish accent with rolled r's. Hearty, blunt and good-humoured, like a tavern keeper in a mountain "
          "hold who has arm-wrestled miners. Chesty and strong, a touch of gravel.",
          Target(f0=185, hnr_max=14)),
    Voice("human_m", "Human, male", True, "human_f",
          "A clear, steady adult male baritone with a neutral southern English accent. Earnest, kind and a little "
          "weary, like a guard captain or farmer in a medieval kingdom. Natural, grounded, unhurried.",
          Target(f0=115)),
    Voice("human_f", "Human, female", False, "human_f",
          "A clear, warm young adult woman with a gentle southern English accent. Friendly, earnest and a "
          "little anxious, like a townswoman in a medieval city. Natural, expressive, mid pitch.",
          Target(f0=210)),
    Voice("orc_m", "Orc, male", True, "orc_m",
          "A huge, hulking male orc warrior with an extremely deep bass voice, very low pitch, thick gravel and "
          "a guttural growl in the throat, harsh vocal fry on every word. Slow, heavy, menacing and proud, "
          "biting off short forceful phrases like a battle-scarred warchief. Monstrous, not human.",
          Target(f0=75, hnr_max=6, centroid_max=900)),
    Voice("orc_f", "Orc, female", False, "orc_m",
          "A powerful, muscular orc warrior woman with a low, husky, rough contralto voice, gravelly and "
          "throaty with a growl at the edges. Blunt, fierce and commanding, speaking in short hard phrases "
          "like a veteran of many battles. Deep for a woman, strong chest resonance.",
          Target(f0=150, hnr_max=9, centroid_max=1100)),
    Voice("troll_m", "Troll, male", True, "troll_m",
          "A tall, lanky male jungle troll witch doctor with a thick Caribbean Jamaican patois accent, "
          "a deep raspy croaking voice, lazy drawn-out vowels and a relaxed sing-song rhythm. Sly, "
          "mischievous and knowing, with a hint of a hiss on the s sounds.",
          Target(f0=95, hnr_max=9)),
    Voice("troll_f", "Troll, female", False, "troll_m",
          "A tall female jungle troll shaman with a strong Caribbean Jamaican accent, a smoky, raspy low "
          "voice, long drawn-out vowels and a lilting sing-song rhythm. Playful, mystical and a little "
          "dangerous, like a voodoo priestess by the fire.",
          Target(f0=170, hnr_max=11)),
)

VOICE_IDS = tuple(v.id for v in VOICES)


def voice(voice_id: str) -> Voice:
    return next(v for v in VOICES if v.id == voice_id)


@dataclass(frozen=True)
class Line:
    key: str       # "<voice>/<round-1 line id>"
    voice: str
    id: str        # round-1 line id, e.g. "21-greeting"
    kind: str
    text: str


def lines() -> tuple[Line, ...]:
    """10 lines per voice, reusing the round-1 texts of its source voice (greeting, detail, progress,
    completion and lore, twice each)."""
    out = []
    for v in VOICES:
        for l in R1_LINES:
            if l.voice == v.lines_from:
                out.append(Line(f"{v.id}/{l.id}", v.id, l.id, l.kind, l.text))
    return tuple(out)


# --- designers ---------------------------------------------------------------

@dataclass(frozen=True)
class Designer:
    id: str
    label: str
    repo: str
    licence: str


DESIGNERS: tuple[Designer, ...] = (
    Designer("qwen", "Qwen3-TTS 1.7B VoiceDesign", "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16",
             "Apache-2.0 (Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign)"),
    Designer("vox", "VoxCPM2 2B voice design", "mlx-community/VoxCPM2-bf16",
             "Apache-2.0 (openbmb/VoxCPM2)"),
)


def designer(designer_id: str) -> Designer:
    return next(d for d in DESIGNERS if d.id == designer_id)


def candidate_score(features: dict, wer: float | None, target: Target) -> float:
    """Lower is better. Distance of a reference Candidate's ear-proxies from the voice's target,
    with a heavy penalty when ASR could not follow it (an unusable reference)."""
    s = 0.0
    f0 = features.get("f0")
    s += 3.0 * abs(math.log2(f0 / target.f0)) if f0 else 3.0
    hnr = features.get("hnr")
    if target.hnr_max is not None and hnr is not None:
        s += max(0.0, hnr - target.hnr_max) / 4
    cen = features.get("centroid")
    if target.centroid_max is not None and cen is not None:
        s += max(0.0, cen - target.centroid_max) / 500
    if wer is not None:
        s += wer + (5.0 if wer > 0.10 else 0.0)
    return round(s, 3)


def pick_candidate(cands: dict[str, dict], target: Target) -> str | None:
    """Best Candidate id among {id: {"features":..., "wer":...}}; ties go to the lowest id."""
    scored = [(candidate_score(c.get("features", {}), c.get("wer"), target), cid) for cid, c in cands.items()]
    return min(scored)[1] if scored else None


# --- approaches -------------------------------------------------------------

CHATTERBOX = dict(exaggeration=0.5, cfg_weight=0.5, temperature=0.8)
CB_REPO = "mlx-community/chatterbox-fp16"
CB_LICENCE = "MIT (ResembleAI/chatterbox); this MLX port adds no watermark."


@dataclass(frozen=True)
class Approach:
    id: str
    label: str
    engine: str             # engine key in engines2, or "post" for DSP on another approach's output
    ref: str | None         # designer id whose reference clip conditions it
    licence: str
    repo: str = ""
    settings: dict = field(default_factory=dict)
    note: str = ""
    consistent: bool = True  # gives one stable voice per NPC (usable for NPC Voices)
    dsp: bool = False       # needs a non-identity race chain; skipped for humans
    source: str = ""        # for engine "post": the approach whose clips are processed


APPROACHES: tuple[Approach, ...] = (
    Approach("cb-qwen", "Chatterbox ← Qwen3 ref", "chatterbox", "qwen", CB_LICENCE, CB_REPO, CHATTERBOX,
             "Round-1 pipeline with richer prompts and best-of-4 reference selection."),
    Approach("cb-vox", "Chatterbox ← VoxCPM2 ref", "chatterbox", "vox", CB_LICENCE, CB_REPO, CHATTERBOX,
             "Same cloner, reference designed by VoxCPM2."),
    Approach("cb-vox-dsp", "Chatterbox ← VoxCPM2 ref + race DSP", "post", "vox",
             CB_LICENCE + " DSP: Praat (GPL) via parselmouth, pedalboard (GPL-3.0); tools only.",
             note="The cb-vox clips run through the tuned race chain (pitch, formant, sub-octave, rasp, saturation).",
             dsp=True, source="cb-vox"),
    Approach("cb-vc", "Chatterbox, VC to DSP'd timbre", "chatterbox-vc", "vox", CB_LICENCE, CB_REPO, CHATTERBOX,
             "Voice conversion inside Chatterbox: the T3 speech-token model is conditioned on the natural VoxCPM2 "
             "reference (accent, prosody); the S3Gen flow decoder (Chatterbox's VC stage) on that reference after "
             "the race DSP chain (timbre). One pass, no post-processing artefacts on the output.",
             dsp=True),
    Approach("vox", "VoxCPM2 clone ← VoxCPM2 ref", "voxcpm", "vox", "Apache-2.0 (openbmb/VoxCPM2)",
             "mlx-community/VoxCPM2-bf16", {"mode": "ref_audio + prompt continuation", "inference_timesteps": 10,
                                             "cfg_value": 2.0},
             "Design and clone in one model; continuation mode (reference audio + its transcript)."),
    Approach("qwen", "Qwen3-TTS Base clone ← Qwen3 ref", "qwen-base", "qwen",
             "Apache-2.0 (Qwen/Qwen3-TTS-12Hz-1.7B-Base)", "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16", {},
             "Qwen's own design-then-clone path."),
    Approach("fish", "Fish Audio S2 Pro clone ← VoxCPM2 ref", "fish", "vox",
             "Fish Audio Research License: non-commercial only (fishaudio/s2-pro). OK while the addon is free.",
             "mlx-community/fish-audio-s2-pro-8bit", {"temperature": 0.7, "top_p": 0.7}, ""),
    Approach("omni", "OmniVoice clone ← VoxCPM2 ref", "omnivoice", "vox",
             "Apache-2.0 (k2-fsa/OmniVoice GitHub; the HF card has no licence tag)",
             "mlx-community/OmniVoice-bf16", {"num_steps": 32}, "Fast non-autoregressive diffusion."),
    Approach("vox-direct", "VoxCPM2 voice design per line (no ref)", "voxcpm-design", None,
             "Apache-2.0 (openbmb/VoxCPM2)", "mlx-community/VoxCPM2-bf16", {"instruct": "the voice's prompt"},
             "Ceiling for 'fantasy-ness' only: each line is designed afresh from the text prompt, so the voice "
             "drifts line to line and cannot serve as a consistent NPC Voice.", consistent=False),
)

APPROACH_IDS = tuple(a.id for a in APPROACHES)


def approach(approach_id: str) -> Approach:
    return next(a for a in APPROACHES if a.id == approach_id)


def applies(a: Approach, voice_id: str, chains: dict) -> bool:
    """DSP approaches only apply where the voice has a non-identity race chain."""
    if not a.dsp:
        return True
    chain = chains.get(voice_id)
    return chain is not None and not chain.is_identity


# Skipped or failed in probing; shown on the page so the decision is traceable.
NOT_RUN: tuple[tuple[str, str, str], ...] = (
    ("IndexTTS-2 (IndexTeam/IndexTTS-2)", "bilibili Model Use License",
     "mlx-audio 0.5.6 only implements IndexTTS v1; loading mlx-community/IndexTTS-2-fp16 fails "
     "(ModelArgs missing tokenizer_name). The PyTorch release needs CUDA-oriented deps; not attempted on MPS."),
    ("Maya1 (maya-research/maya1)", "Apache-2.0",
     "mlx-community/maya1-4bit fails to load (embedding shape mismatch); the bf16 original loads through "
     "mlx-audio's Orpheus/Llama path but produced no intelligible speech (ASR empty, 30 s runaway) with the "
     "documented <description=\"...\"> prompt. Needs its own prompt/decoder wrapper."),
    ("Higgs Audio v2 (bosonai/higgs-audio-v2-generation-3B-base)", "Boson Higgs Audio 2 Community Licence",
     "Runs (mlx-community/higgs-audio-v2-3B-mlx-q8, RTF ~2.9) but did not hold the reference voice in a probe: "
     "orc reference f0 81 Hz, output 175 Hz."),
    ("Seed-VC (Plachta/Seed-VC)", "GPL-3.0 (code); audio output unrestricted",
     "Not in mlx-audio; the VC comparison uses Chatterbox's own S3Gen VC stage instead (approach cb-vc)."),
    ("Parler-TTS, Spark-TTS", "Apache-2.0 / CC-BY-NC-SA-4.0",
     "Not in mlx-audio; older and weaker than VoxCPM2/Qwen3 for description-driven design."),
)

# Per-NPC variation test: three orc NPCs derived from the orc_m Archetype (seed = NPC id).
VARIATION_VOICE = "orc_m"
VARIATION_NPCS = (3139, 3143, 3188)
VARIATION_LINES = ("21-greeting", "22-detail", "29-completion")

# Round-1 Chatterbox clips kept as the quality benchmark (human-rated good).
BENCHMARK = (("dwarf_m", "Dwarf, male (round 1 Chatterbox, 9/10 picks)",
              ("01-greeting", "02-detail", "04-completion", "05-lore")),
             ("nelf_f", "Night elf, female (round 1 Chatterbox, 7/10 picks)",
              ("11-greeting", "12-detail", "14-completion", "15-lore")))

# DSP chain tuning: strengths tried, and how much WER may rise over the unprocessed clips.
DSP_STRENGTHS = (0.5, 1.0, 1.5)
DSP_WER_BUDGET = 0.05
DSP_TUNE_LINES = 3  # first N lines of each voice


def tune_strength(sweep: dict[float, float], base_wer: float, budget: float = DSP_WER_BUDGET) -> float:
    """Strongest chain strength whose mean WER stays within budget of the unprocessed clips.
    Strengths are checked in ascending order and the search stops at the first failure."""
    best = 0.0
    for k in sorted(sweep):
        if sweep[k] <= base_wer + budget:
            best = k
        else:
            break
    return best

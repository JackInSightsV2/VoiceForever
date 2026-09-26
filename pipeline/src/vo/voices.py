"""`vo voices`: every NPC's own NPC Voice (#13), derived automatically from one of its Archetype's approved anchors.

For each NPC with lines whose Archetype is in approved_voices.json:

0. Its Base Voice: one of the Archetype's approved anchors (up to 8, each a different person; ADR-0006), assigned by
   vo.basevoices (balanced, deterministic, Neighbours spread over different ones) and stored as voice_builds.anchor.
1. Render K (default 4) candidate anchors of its Base Voice's anchor line (strategy below), seeded from the NPC id.
2. Score each: speaker-embedding cosine to its Base Voice (must be >= the ceiling: it still sounds like the
   approved race), median pitch within `f0_band_st` semitones and HNR within `hnr_band_db` dB of the Base
   Voice's (the bake-off's seeded orc that came out at 252 Hz is not an orc), ASR WER <= `max_wer` (design only).
   (Below, "the Archetype anchor" is the NPC's Base Voice.)
3. Of the candidates that pass, keep those whose cosine to every already-voiced Neighbour is <= the floor (they sound
   distinct), and pick the one closest to the Archetype anchor.
4. None clears: re-roll (next attempt: new traits, new seeds) up to `max_rerolls`. Still none: keep the best
   (passing the gate, least similar to its closest Neighbour) and list the NPC as a leftover in `voice_builds`
   (status 'leftover', issue + detail) for the morning report. Never blocking. If even the best fails the gate, the
   NPC gets no own voice and keeps speaking with the Archetype anchor.

Strategies (Config.strategy), chosen by measurement (ADR-0005):
- "dsp" (default): the NPC anchor is the Archetype anchor itself, shifted by a small, seeded DSP variation (Praat
  "Change gender": pitch +-0.8-2 semitones, formant +-1.8-6 %, pace +-6 %; named NPCs up to 3 st / 8 % / 8 %).
  Lines are VoxCPM2 continuations of the shifted anchor, which carry the shift (line pitch follows the anchor's,
  r = 0.86-0.93). It keeps the approved anchor's character by construction and renders nothing up front. Its NPCs are
  less distinct by speaker embedding (WavLM-SV is mostly blind to pitch; formant drives it).
- "design" (`vo voices --strategy design`): VoxCPM2 designs K candidate anchors from the Archetype description plus
  bounded variation phrases (role, level band as age, seeded traits: pitch, pace, rasp, breathiness, age, accent
  strength). Clearly different voices, but it can lose the Archetype's character: for orc_f, 76 of 80 designs were
  over 3 semitones above the approved anchor. Workable for plain voices (human_m: 7 of 10 NPCs cleared the gate).
An NPC whose voice prompt is pinned through `manual_overrides` (field `voice_prompt`) always uses "design", with the
pinned text as its variation.

Named NPCs (`is_named`) get a wider variation budget (larger DSP shifts; more and stronger traits) and prompt hints
from their name and subname (King: regal; Warchief: commanding; ...).

Deterministic: an NPC's traits and seeds come from (NPC id, roll, attempt, candidate), so the same NPC gets the same
voice given the same Neighbour voices. NPCs are built in id order. A dashboard re-roll (review action
`reroll-voice`, from the Separation page or the NPC browser, or auto-queued by repeated spot-check thumbs-down:
vo.ratings) bumps the NPC's roll and rebuilds it. A voice whose Base Voice has changed
since it was built (an approval added to or withdrawn from its Archetype re-assigns them) is rebuilt too.

Thresholds are in Config. The floor is provisional (set so that most dsp NPCs clear it: 14 of 20 on a first real
build): calibrate it by ear on ~20 Neighbour pairs from the Separation page (pairs just under and just over it),
then set it here.

Audio: build/voices/<archetype>/<npc>-r<roll>a<attempt>k<k>.wav. Voice id:
`voxcpm:<archetype>@<candidate>#<npc>-r<roll>a<attempt>k<k>` (see voxcpm.Backend).
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import wave
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from vo import archetypes, basevoices, lock, ratings, speaker

VOICE_ENV = "VO_VOICES_DIR"  # lets `vo run`'s spawned workers find the NPC anchors
PIN_FIELD = "voice_prompt"
REROLL_ACTION = "reroll-voice"
STRATEGIES = ("dsp", "design")


@dataclass(frozen=True)
class Config:
    strategy: str = "dsp"
    candidates: int = 4
    # Archetype ceiling: min cosine (WavLM-SV) of an NPC anchor to its Archetype anchor. Below it, the NPC no longer
    # sounds like the approved voice.
    ceiling: float = 0.80
    # Neighbour floor: max cosine of an NPC anchor to any Neighbour's anchor. Above it, the two sound alike.
    # Provisional: calibrate by ear on ~20 pairs from the Separation page.
    floor: float = 0.95
    max_rerolls: int = 3
    f0_band_st: float = 3.0
    hnr_band_db: float = 5.0
    max_wer: float = 0.25


def default_dir() -> Path:
    return lock.default_path().parent / "voices"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- inputs ----------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Npc:
    id: int
    name: str
    subname: str | None
    role: str | None
    level_min: int | None
    level_max: int | None
    is_named: bool
    archetype: str
    gender: str | None
    pinned: str | None = None  # manual_overrides voice_prompt


@dataclass
class ArchRef:
    """An approved Archetype anchor and its measures (embedding, pitch, HNR)."""
    id: str
    candidate: str
    anchor: str
    transcript: str
    description: str
    mode: str
    male: bool
    embedding: np.ndarray | None = None
    f0: float | None = None
    hnr: float | None = None
    samples: np.ndarray | None = None
    rate: int = 0


def read_wav(path: Path | str) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        rate, n, width, ch = w.getframerate(), w.getnframes(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(n)
    if width != 2:
        raise ValueError(f"{path}: only 16-bit WAV is supported")
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, rate


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    from vo.prepare import write_wav as w
    w(path, samples, rate)


# --- variation: prompt traits (design) and DSP shifts (dsp) ----------------------------------------------------------

def _rng(*key) -> random.Random:
    return random.Random("vo-voice:" + ":".join(map(str, key)))  # str seeds hash stably across processes


ROLE_PHRASES = {
    "guard": "Speaks like a watchful, dutiful guard.",
    "innkeeper": "Speaks like a warm, welcoming innkeeper.",
    "vendor": "Speaks like a merchant keen to make a sale.",
    "trainer": "Speaks like an experienced teacher, patient and instructive.",
    "flightmaster": "Speaks like a practical, busy handler of beasts.",
    "stablemaster": "Speaks like a practical, busy handler of beasts.",
    "banker": "Speaks like a precise, formal clerk.",
    "battlemaster": "Speaks like a stern recruiter for war.",
    "spirit healer": "Calm, soft and otherworldly.",
    "story": "An important figure, speaking with weight.",
}

# Name/subname word -> hint (named NPCs). First match per group; at most two hints.
NAME_HINTS: list[tuple[str, str]] = [
    (r"king|queen|prince|princess|emperor|empress|regent|monarch", "regal and measured"),
    (r"warchief|chieftain|chief|warlord|overlord|high chieftain", "commanding and forceful"),
    (r"lord|lady|baron|baroness|duke|count|countess|magister|noble", "noble and dignified"),
    (r"captain|commander|general|marshal|lieutenant|sergeant|champion|legionnaire|centurion",
     "authoritative, used to giving orders"),
    (r"archmage|mage|magus|sorcerer|sorceress|wizard|scholar|librarian|historian|professor|scribe|arcanist",
     "learned and precise"),
    (r"priest|priestess|bishop|archbishop|father|brother|sister|cleric|high priestess|confessor", "gentle and devout"),
    (r"elder|sage|ancient|grandpa|grandma|old", "old and wise"),
    (r"shaman|witch doctor|seer|oracle|mystic|druid|spiritwalker", "mystical and knowing"),
    (r"smith|blacksmith|miner|forgemaster|foreman|engineer", "gruff and hard-working"),
    (r"cook|chef|bartender|brewer|brewmaster|barmaid", "cheerful and hearty"),
    (r"hunter|tracker|scout|ranger|sentinel|huntress", "terse and alert"),
    (r"thief|rogue|bandit|smuggler|pirate|thug|assassin", "sly and shifty"),
]

# Trait -> (mild options, strong options). Named NPCs may draw strong ones.
TRAITS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "pitch": (("a slightly deeper voice", "a slightly higher voice"),
              ("a noticeably deeper voice", "a noticeably higher voice")),
    "pace": (("speaks a little slowly", "speaks a little quickly"),
             ("speaks slowly and deliberately", "speaks fast and clipped")),
    "rasp": (("a touch more rasp", "a smoother, cleaner tone"),
             ("a heavy, rough rasp", "a very smooth, clean tone")),
    "breath": (("a slightly breathy quality", "a firm, solid tone"),
               ("a breathy, airy quality", "a hard, pressed tone")),
    "accent": (("a slightly lighter accent", "a slightly stronger accent"),
               ("a much lighter accent", "a very strong, thick accent")),
}
AGES = ("young", "adult", "middle-aged", "older", "elderly")


def age_index(level_max: int | None) -> int:
    """Level band as an age proxy: 1-12 young, 13-30 adult, 31-45 middle-aged, 46+ older."""
    lvl = level_max or 0
    return 0 if lvl <= 12 else 1 if lvl <= 30 else 2 if lvl <= 45 else 3


def name_hints(name: str, subname: str | None) -> list[str]:
    text = f"{name} {subname or ''}".lower()
    out = []
    for pattern, hint in NAME_HINTS:
        if re.search(rf"\b(?:{pattern})\b", text) and hint not in out:
            out.append(hint)
    return out[:2]


def variation(npc: Npc, roll: int, attempt: int) -> str:
    """The NPC's description phrases appended to its Archetype description (design strategy). Deterministic."""
    if npc.pinned:
        return npc.pinned.strip()
    rng = _rng("traits", npc.id, roll, attempt)
    parts = []
    hints = name_hints(npc.name, npc.subname) if npc.is_named else []
    if hints:
        parts.append(f"This one is {' and '.join(hints)}.")
    elif npc.role in ROLE_PHRASES:
        parts.append(ROLE_PHRASES[npc.role])
    age = min(len(AGES) - 1, max(0, age_index(npc.level_max) + rng.choice((-1, 0, 0, 1))))
    if AGES[age] != "adult":
        parts.append(f"Sounds {AGES[age]}.")
    n_traits = rng.randint(3, 4) if npc.is_named else rng.randint(2, 3)
    traits = []
    for name in rng.sample(sorted(TRAITS), n_traits):
        mild, strong = TRAITS[name]
        pool = strong if npc.is_named and rng.random() < 0.5 else mild
        traits.append(rng.choice(pool))
    parts.append("With " + ", ".join(traits[:-1]) + (" and " if len(traits) > 1 else "") + traits[-1] + ".")
    return " ".join(parts)


@dataclass(frozen=True)
class Shift:
    pitch_st: float = 0.0
    formant: float = 1.0
    pace: float = 1.0  # duration factor: > 1 slower

    def describe(self) -> str:
        return f"pitch {self.pitch_st:+.2f} st, formant x{self.formant:.3f}, pace x{self.pace:.3f}"


# Bounds: (min |pitch| st, max |pitch| st, max |formant - 1|, max |pace - 1|).
SHIFT_BOUNDS = (0.8, 2.0, 0.06, 0.06)
SHIFT_BOUNDS_NAMED = (0.8, 3.0, 0.08, 0.08)


def shift(npc: Npc, roll: int, attempt: int, k: int) -> Shift:
    """The k-th DSP variation for an NPC (dsp strategy). Pitch always moves at least lo semitones, up or down, so
    candidates differ from the Archetype anchor and from each other; formant follows the pitch direction half the
    time (bigger/smaller speaker), otherwise independent."""
    lo, hi, fmax, pmax = SHIFT_BOUNDS_NAMED if npc.is_named else SHIFT_BOUNDS
    rng = _rng("shift", npc.id, roll, attempt, k)
    sign = rng.choice((-1, 1))
    pitch = sign * rng.uniform(lo, hi)
    f = rng.uniform(0.3, 1.0) * fmax
    fsign = sign if rng.random() < 0.5 else rng.choice((-1, 1))
    pace = 1 + rng.uniform(-pmax, pmax)
    return Shift(round(pitch, 2), round(1 + fsign * f, 4), round(pace, 3))


def apply_shift(samples: np.ndarray, rate: int, s: Shift, seed: int = 0) -> np.ndarray:
    """Praat "Change gender": pitch median moved by pitch_st, formants scaled, duration scaled by pace. Praat's
    PSOLA draws random numbers (unvoiced stretches), so its generator is seeded for a reproducible result."""
    import parselmouth
    from parselmouth.praat import call, run

    snd = parselmouth.Sound(np.asarray(samples, dtype=np.float64), sampling_frequency=rate)
    f0 = snd.to_pitch(pitch_floor=60, pitch_ceiling=600).selected_array["frequency"]
    f0 = f0[f0 > 0]
    median = float(np.median(f0)) if len(f0) else 0.0
    new_median = median * 2 ** (s.pitch_st / 12) if median else 0.0
    run(f"random_initializeWithSeedUnsafelyButPredictably ({int(seed)})")
    try:
        out = call(snd, "Change gender", 60, 600, s.formant, new_median, 1.0, s.pace)
    finally:
        run("random_initializeSafelyAndUnpredictably ()")
    a = np.asarray(out.values[0], dtype=np.float32)
    peak = float(np.max(np.abs(a))) if len(a) else 0.0
    return a / peak * 0.95 if peak > 0.99 else a


# --- candidates and the constraint ----------------------------------------------------------------------------------

@dataclass
class Cand:
    tag: str            # <npc>-r<roll>a<attempt>k<k>
    seed: int
    prompt: str
    params: dict
    samples: np.ndarray | None = None
    rate: int = 0
    embedding: np.ndarray | None = None
    f0: float | None = None
    hnr: float | None = None
    wer: float | None = None
    arch_sim: float = 0.0
    nmax: float = -1.0           # cosine to the closest voiced Neighbour (-1: none)
    neighbour: int | None = None
    reasons: list[str] = field(default_factory=list)  # gate failures


def seed_for(npc_id: int, roll: int, attempt: int, k: int) -> int:
    return npc_id * 10_000 + roll * 1_000 + attempt * 10 + k


def tag_for(npc_id: int, roll: int, attempt: int, k: int) -> str:
    return f"{npc_id}-r{roll}a{attempt}k{k}"


def _semitones(a: float, b: float) -> float:
    return 12 * float(np.log2(a / b))


def score(c: Cand, arch: ArchRef, neighbours: dict[int, np.ndarray], cfg: Config) -> Cand:
    """Fill arch_sim, nmax/neighbour and the gate reasons (ceiling, pitch, hnr, wer)."""
    c.arch_sim = speaker.cosine(c.embedding, arch.embedding)
    c.reasons = []
    if c.arch_sim < cfg.ceiling:
        c.reasons.append(f"ceiling: {c.arch_sim:.3f} < {cfg.ceiling}")
    if arch.f0 and c.f0 and abs(_semitones(c.f0, arch.f0)) > cfg.f0_band_st:
        c.reasons.append(f"pitch: {c.f0:.0f} Hz vs {arch.f0:.0f} Hz ({_semitones(c.f0, arch.f0):+.1f} st)")
    if arch.hnr is not None and c.hnr is not None and abs(c.hnr - arch.hnr) > cfg.hnr_band_db:
        c.reasons.append(f"hnr: {c.hnr:.1f} dB vs {arch.hnr:.1f} dB")
    if c.wer is not None and c.wer > cfg.max_wer:
        c.reasons.append(f"wer: {c.wer:.2f} > {cfg.max_wer}")
    c.nmax, c.neighbour = -1.0, None
    for nid in sorted(neighbours):
        s = speaker.cosine(c.embedding, neighbours[nid])
        if s > c.nmax:
            c.nmax, c.neighbour = s, nid
    return c


@dataclass
class Choice:
    cand: Cand
    attempt: int
    ok: bool
    issue: str | None = None
    detail: str | None = None
    tried: int = 0


def _fallback_key(c: Cand):
    return (not c.reasons, -len(c.reasons), -c.nmax, c.arch_sim)


def choose(cands: list[Cand], cfg: Config) -> Cand | None:
    """The candidate to keep from one attempt: passes the gate and the floor, closest to the Archetype anchor."""
    clear = [c for c in cands if not c.reasons and c.nmax <= cfg.floor]
    return max(clear, key=lambda c: (c.arch_sim, -c.seed)) if clear else None


def resolve(attempts: Callable[[int], list[Cand]], arch: ArchRef, neighbours: dict[int, np.ndarray],
            cfg: Config) -> Choice:
    """Try attempt 0, 1, ... (re-rolls) until a candidate clears; else the best leftover, with why."""
    best: tuple[Cand, int] | None = None
    tried = 0
    for attempt in range(cfg.max_rerolls + 1):
        cands = [score(c, arch, neighbours, cfg) for c in attempts(attempt)]
        tried += len(cands)
        got = choose(cands, cfg)
        if got is not None:
            return Choice(got, attempt, True, tried=tried)
        for c in cands:
            if best is None or _fallback_key(c) > _fallback_key(best[0]):
                best = (c, attempt)
    c, attempt = best
    if c.reasons:
        issue, detail = "ceiling" if c.reasons[0].startswith("ceiling") else c.reasons[0].split(":")[0], "; ".join(
            c.reasons)
    else:
        issue, detail = "floor", f"cosine {c.nmax:.3f} > {cfg.floor} to Neighbour {c.neighbour}"
    return Choice(c, attempt, False, issue, detail, tried)


# --- strategies ------------------------------------------------------------------------------------------------------

class Engine(Protocol):
    def design(self, text: str, description: str, seed: int) -> tuple[np.ndarray, int]: ...


Embed = Callable[[np.ndarray, int], np.ndarray]
Measure = Callable[[np.ndarray, int, bool], dict]
Heard = Callable[[np.ndarray, int], str]


def _measure(samples, rate, male):
    from vo import voicefeat
    return voicefeat.measure(samples, rate, male)


@dataclass
class Tools:
    """The heavy parts, injectable for tests: VoxCPM2, the speaker embedder, pitch/HNR measure, ASR."""
    engine: Engine | None = None
    embed: Embed | None = None
    measure: Measure = _measure
    heard: Heard | None = None

    def get_engine(self) -> Engine:
        if self.engine is None:
            from vo import voxcpm
            self.engine = voxcpm.engine()
        return self.engine

    def get_embed(self) -> Embed:
        if self.embed is None:
            self.embed = speaker.embedder()
        return self.embed

    def get_heard(self) -> Heard:
        if self.heard is None:
            import tempfile
            from vo import asr

            def heard(samples, rate):
                with tempfile.TemporaryDirectory() as tmp:
                    p = Path(tmp) / "c.wav"
                    write_wav(p, samples, rate)
                    return asr.whisper().transcribe(p).strip()
            self.heard = heard
        return self.heard


def _features(c: Cand, tools: Tools, male: bool) -> Cand:
    c.embedding = speaker.normalise(tools.get_embed()(c.samples, c.rate))
    m = tools.measure(c.samples, c.rate, male)
    c.f0, c.hnr = m.get("f0"), m.get("hnr")
    return c


def dsp_candidates(npc: Npc, arch: ArchRef, roll: int, attempt: int, k: int, tools: Tools) -> list[Cand]:
    out = []
    for i in range(k):
        s = shift(npc, roll, attempt, i)
        c = Cand(tag_for(npc.id, roll, attempt, i), seed_for(npc.id, roll, attempt, i), f"dsp: {s.describe()}",
                 {"strategy": "dsp", "pitch_st": s.pitch_st, "formant": s.formant, "pace": s.pace})
        c.samples, c.rate = apply_shift(arch.samples, arch.rate, s, c.seed), arch.rate
        out.append(_features(c, tools, arch.male))
    return out


def design_candidates(npc: Npc, arch: ArchRef, roll: int, attempt: int, k: int, tools: Tools) -> list[Cand]:
    from vo.prepare import dialect_wer

    extra = variation(npc, roll, attempt)
    description = f"{arch.description.strip()} {extra}".strip()
    out = []
    for i in range(k):
        sd = seed_for(npc.id, roll, attempt, i)
        c = Cand(tag_for(npc.id, roll, attempt, i), sd, description,
                 {"strategy": "design", "variation": extra, "pinned": bool(npc.pinned)})
        c.samples, c.rate = tools.get_engine().design(arch.transcript, description, sd)
        _features(c, tools, arch.male)
        c.wer = dialect_wer(arch.transcript, tools.get_heard()(c.samples, c.rate))
        out.append(c)
    return out


def candidates_for(npc: Npc, arch: ArchRef, roll: int, attempt: int, cfg: Config, tools: Tools) -> list[Cand]:
    if cfg.strategy == "design" or npc.pinned:
        return design_candidates(npc, arch, roll, attempt, cfg.candidates, tools)
    return dsp_candidates(npc, arch, roll, attempt, cfg.candidates, tools)


# --- DB --------------------------------------------------------------------------------------------------------------

def npc_voice_id(arch: ArchRef, tag: str) -> str:
    return f"{lock.BACKEND}:{arch.id}@{arch.candidate}#{tag}"


def load_archetype(aid: str, entry: dict, tools: Tools, gender: str | None = None) -> ArchRef:
    samples, rate = read_wav(entry["anchor"])
    male = not aid.endswith("_f")
    a = ArchRef(aid, entry["candidate"], entry["anchor"], entry["transcript"], entry.get("description") or "",
                entry.get("mode") or "cont", male, samples=samples, rate=rate)
    a.embedding = speaker.normalise(tools.get_embed()(samples, rate))
    m = tools.measure(samples, rate, male)
    a.f0, a.hnr = m.get("f0"), m.get("hnr")
    return a


def load_npcs(conn: sqlite3.Connection) -> dict[int, Npc]:
    """Every NPC with a spoken line and a mapped (non-Narrator) Archetype."""
    pins = {r[0]: r[1] for r in conn.execute(
        "SELECT npc_id, value FROM manual_overrides WHERE field = ? ORDER BY rowid", (PIN_FIELD,))}
    aids = archetypes.npc_archetypes(conn)
    out = {}
    for r in conn.execute(
            "SELECT n.* FROM npcs n WHERE EXISTS (SELECT 1 FROM lines l WHERE l.npc_id = n.id"
            " AND COALESCE(l.tts_text, '') != '') ORDER BY n.id"):
        aid = aids.get(r["id"])
        if aid is None or aid == archetypes.NARRATOR:
            continue
        out[r["id"]] = Npc(r["id"], r["name"] or "", r["subname"], r["role"], r["level_min"], r["level_max"],
                           bool(r["is_named"]), aid, r["gender"], pins.get(r["id"]))
    return out


def neighbour_ids(conn: sqlite3.Connection, npc_id: int) -> list[int]:
    return [r[0] for r in conn.execute("SELECT b FROM neighbours WHERE a = ? UNION SELECT a FROM neighbours WHERE b = ?",
                                       (npc_id, npc_id))]


def embeddings(conn: sqlite3.Connection, ids: list[int]) -> dict[int, np.ndarray]:
    if not ids:
        return {}
    out = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in conn.execute(f"SELECT npc_id, embedding FROM voices WHERE embedding IS NOT NULL AND npc_id IN"
                              f" ({','.join('?' * len(part))})", part):
            out[r[0]] = speaker.from_blob(r[1])
    return out


def update_sims(conn: sqlite3.Connection, npc_id: int | None = None) -> int:
    """Store the anchor cosine on each Neighbour pair where both have a voice (all pairs, or those of one NPC)."""
    where, params = ("WHERE a = ? OR b = ?", (npc_id, npc_id)) if npc_id is not None else ("", ())
    pairs = conn.execute(f"SELECT a, b FROM neighbours {where}", params).fetchall()
    embs = embeddings(conn, sorted({x for p in pairs for x in p}))
    rows = [(speaker.cosine(embs[a], embs[b]) if a in embs and b in embs else None, a, b) for a, b in pairs]
    with conn:
        conn.executemany("UPDATE neighbours SET sim = ? WHERE a = ? AND b = ?",
                         [(None if s is None else round(s, 4), a, b) for s, a, b in rows])
    return sum(s is not None for s, _, _ in rows)


def consume_rerolls(conn: sqlite3.Connection, log: Callable[[str], None] = print) -> int:
    """Apply the Separation page's `reroll-voice` actions: the NPC's next build uses roll + 1 (new traits, seeds)."""
    n = 0
    for row in conn.execute("SELECT * FROM review_actions WHERE consumed_at IS NULL AND action = ? ORDER BY id",
                            (REROLL_ACTION,)).fetchall():
        with conn:
            try:
                npc = int(row["target"])
                got = conn.execute("UPDATE voice_builds SET roll = roll + 1, stale = 1, updated_at = ? WHERE npc_id = ?"
                                   " RETURNING roll", (_now(), npc)).fetchone()
                log(f"review action {row['id']}: NPC {npc} " + (f"re-roll {got[0]} queued" if got else
                                                              "has no voice yet; dropped"))
            except (TypeError, ValueError) as e:
                log(f"review action {row['id']} ({REROLL_ACTION} {row['target']}): invalid, dropped: {e}")
            conn.execute("UPDATE review_actions SET consumed_at = ? WHERE id = ?", (_now(), row["id"]))
        n += 1
    return n


def todo(conn: sqlite3.Connection, npcs: dict[int, Npc], assigned: dict[int, str], *, only_npcs=None,
         only_archetypes=None, limit: int | None = None) -> list[Npc]:
    """NPCs to (re)build, in id order: no voice yet, re-roll queued, or built on another Base Voice than the one
    assigned (`assigned`: vo.basevoices). NPCs of an Archetype without Base Voices wait."""
    built = {r["npc_id"]: r for r in conn.execute("SELECT npc_id, stale, anchor FROM voice_builds")}
    out = []
    for nid, npc in npcs.items():
        if only_npcs and nid not in only_npcs:
            continue
        if only_archetypes and npc.archetype not in only_archetypes:
            continue
        want = assigned.get(nid)
        if want is None:
            continue
        b = built.get(nid)
        if b is None or b["stale"] or b["anchor"] != want:
            out.append(npc)
    return out[:limit] if limit else out


def _base_of(voice_id: str) -> str:
    """The Base Voice (candidate id) an NPC voice id is built on: `voxcpm:<aid>@<candidate>#<tag>`."""
    return voice_id.split("@", 1)[1].split("#", 1)[0]


def drop_stale(conn: sqlite3.Connection, lock_data: dict) -> int:
    """Bring the NPC Voices in step with the Base Voices (vo.basevoices). An approval added or withdrawn re-assigns
    its Archetype's NPCs: one whose Base Voice changed is marked stale with its new Base Voice stored, and its voice is
    removed (`vo run` speaks its lines in its Base Voice until `vo voices` rebuilds it). A voice whose Archetype has no
    Base Voice any more is removed too. Returns how many NPCs went stale."""
    assigned = basevoices.assignments(conn, lock_data)
    bases = basevoices.bases_of(lock_data)
    npc_arch = basevoices.voiced_archetypes(conn)
    own = dict(conn.execute("SELECT npc_id, voice_id FROM voices WHERE voice_id LIKE ?", (f"{lock.BACKEND}:%#%",)))
    builds = {r["npc_id"]: r for r in conn.execute("SELECT npc_id, anchor, base_voices FROM voice_builds")}
    gone, moved, signed = set(), [], []
    for npc in sorted(set(own) | set(builds)):
        want, vid, b = assigned.get(npc), own.get(npc), builds.get(npc)
        if want is None:  # its Archetype has no Base Voice (or the NPC no line) now
            if vid:
                gone.add(npc)
            continue
        sig = basevoices.signature(bases[npc_arch[npc]])
        if (vid and _base_of(vid) != want) or (b is not None and b["anchor"] != want):
            if vid:
                gone.add(npc)
            if b is not None:
                moved.append((want, sig, npc))
        elif b is not None and b["base_voices"] != sig:
            signed.append((sig, npc))  # same Base Voice under the new set: keep it (sticky from now on)
    with conn:
        conn.executemany("DELETE FROM voices WHERE npc_id = ?", [(n,) for n in sorted(gone)])
        conn.executemany("UPDATE voice_builds SET stale = 1 WHERE npc_id = ?", [(n,) for n in sorted(gone)])
        conn.executemany("UPDATE voice_builds SET anchor = ?, base_voices = ?, stale = 1 WHERE npc_id = ?", moved)
        conn.executemany("UPDATE voice_builds SET base_voices = ? WHERE npc_id = ?", signed)
    return len(gone | {m[2] for m in moved})


def store(conn: sqlite3.Connection, npc: Npc, arch: ArchRef, ch: Choice, roll: int, out_dir: Path,
          strategy: str, base_voices: str | None = None) -> str | None:
    """Write the NPC's anchor, voices row and voice_builds row. A leftover whose best candidate still fails the
    gate (it doesn't sound like the approved Archetype) gets no own voice: it keeps speaking with the Archetype anchor,
    which is safe for character. Returns the voice id, or None in that case."""
    c = ch.cand
    own = ch.ok or not c.reasons
    path = out_dir / arch.id / f"{c.tag}.wav"
    vid = npc_voice_id(arch, c.tag) if own else None
    detail = ch.detail if own else f"{ch.detail}; speaks with the Archetype anchor"
    old = conn.execute("SELECT ref_clip FROM voices WHERE npc_id = ?", (npc.id,)).fetchone()
    if own:
        write_wav(path, c.samples, c.rate)
    with conn:
        if own:
            conn.execute("INSERT OR REPLACE INTO voices (npc_id, voice_id, archetype, prompt, ref_clip, embedding)"
                         " VALUES (?, ?, ?, ?, ?, ?)",
                         (npc.id, vid, arch.id, c.prompt, str(path.resolve()), speaker.to_blob(c.embedding)))
        else:
            conn.execute("DELETE FROM voices WHERE npc_id = ?", (npc.id,))
        conn.execute(
            "INSERT OR REPLACE INTO voice_builds (npc_id, anchor, base_voices, roll, attempt, strategy, seed, params,"
            " archetype_sim, neighbour_sim, neighbour, f0, hnr, wer, tried, status, issue, detail, stale, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (npc.id, arch.candidate, base_voices, roll, ch.attempt, c.params.get("strategy", strategy), c.seed,
             json.dumps(c.params), round(c.arch_sim, 4), None if c.neighbour is None else round(c.nmax, 4),
             c.neighbour, c.f0, c.hnr, c.wer, ch.tried, "ok" if ch.ok else "leftover", ch.issue, detail, _now()))
    if old and old[0] and (not own or old[0] != str(path.resolve())):
        Path(old[0]).unlink(missing_ok=True)
    update_sims(conn, npc.id)
    return vid


@dataclass
class Summary:
    rerolls: int = 0
    stale: int = 0
    built: int = 0
    ok: int = 0
    leftovers: int = 0
    skipped: int = 0  # Archetype not approved
    attempts: list[int] = field(default_factory=list)
    arch_sims: list[float] = field(default_factory=list)
    nsims: list[float] = field(default_factory=list)
    spread: list[basevoices.Spread] = field(default_factory=list)  # NPCs per Base Voice, per Archetype


def build(conn: sqlite3.Connection, lock_data: dict, out_dir: Path, *, cfg: Config = Config(),
          tools: Tools | None = None, only_npcs=None, only_archetypes=None, limit: int | None = None,
          log: Callable[[str], None] = print) -> Summary:
    """See the module docstring. Resumable: each NPC is committed as it lands; a rerun only builds what's left."""
    if cfg.strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {cfg.strategy!r}; one of {STRATEGIES}")
    tools = tools or Tools()
    s = Summary()
    ratings.fold(conn, log)  # spot-check thumbs-down may queue re-rolls
    s.rerolls = consume_rerolls(conn, log)
    s.stale = drop_stale(conn, lock_data)
    npcs = load_npcs(conn)
    bases = basevoices.bases_of(lock_data)
    assigned = basevoices.assignments(conn, lock_data)
    s.spread = basevoices.spread(conn, lock_data, assigned)
    for sp in s.spread:
        log(f"Base Voices {sp.text()}")
    s.skipped = sum(1 for n in npcs.values() if n.archetype not in bases)
    work = todo(conn, npcs, assigned, only_npcs=only_npcs, only_archetypes=only_archetypes, limit=limit)
    arch_cache: dict[str, ArchRef] = {}  # by Base Voice
    rolls = dict(conn.execute("SELECT npc_id, roll FROM voice_builds").fetchall())
    for npc in work:
        base = assigned[npc.id]
        arch = arch_cache.get(base)
        if arch is None:
            arch = arch_cache[base] = load_archetype(npc.archetype, lock.find(lock_data, npc.archetype, base), tools)
        roll = rolls.get(npc.id, 0)
        neighbours = embeddings(conn, [n for n in neighbour_ids(conn, npc.id) if n != npc.id])
        ch = resolve(lambda a: candidates_for(npc, arch, roll, a, cfg, tools), arch, neighbours, cfg)
        vid = store(conn, npc, arch, ch, roll, out_dir, cfg.strategy, basevoices.signature(bases[npc.archetype]))
        s.built += 1
        s.ok += ch.ok
        s.leftovers += not ch.ok
        s.attempts.append(ch.attempt)
        s.arch_sims.append(ch.cand.arch_sim)
        if ch.cand.neighbour is not None:
            s.nsims.append(ch.cand.nmax)
        c = ch.cand
        log(f"NPC {npc.id} {npc.name} [{base}] {'ok' if ch.ok else 'LEFTOVER ' + (ch.detail or '')}:"
            f" {vid.split('#')[1] if vid else 'Archetype anchor'} arch {c.arch_sim:.3f}"
            + (f" nearest Neighbour {c.neighbour} {c.nmax:.3f}" if c.neighbour is not None else "")
            + f" f0 {c.f0} hnr {c.hnr} ({ch.tried} candidates)")
    return s


def summary_text(s: Summary, cfg: Config) -> str:
    def stat(xs):
        return f"mean {np.mean(xs):.3f}, min {np.min(xs):.3f}, max {np.max(xs):.3f}" if xs else "n/a"
    rerolled = sum(1 for a in s.attempts if a > 0)
    return (f"built {s.built} NPC voices ({cfg.strategy}): {s.ok} ok, {s.leftovers} leftovers; {rerolled} needed a"
            f" re-roll; {s.rerolls} dashboard re-rolls applied; {s.stale} stale voices dropped;"
            f" {s.skipped} NPCs wait for their Archetype's approval.\n"
            f"  Archetype similarity (ceiling {cfg.ceiling}): {stat(s.arch_sims)}\n"
            f"  closest-Neighbour similarity (floor {cfg.floor}): {stat(s.nsims)}"
            + "".join(f"\n  Base Voices {sp.text()}" for sp in s.spread))


def leftovers(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT b.*, n.name FROM voice_builds b JOIN npcs n ON n.id = b.npc_id"
                        " WHERE b.status = 'leftover' ORDER BY b.npc_id").fetchall()

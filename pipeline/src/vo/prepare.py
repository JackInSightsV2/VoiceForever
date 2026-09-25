"""`vo prepare`: the Approval Gate's render side.

1. Apply the Approval page's review_actions (approve, reject, regenerate with a note; accept or correct a Lexicon
   name).
2. Sync the Archetype list from the NPCs (vo.archetypes) and the race style guide into `archetypes`, and the Lexicon's
   names and drafts (vo.lexicon) into `lexicon`.
3. For every Archetype without an approved anchor, render N Candidates (VoxCPM2 voice design of its anchor line,
   seeded, deterministic; with an anchor chain, the designed clip goes through it once and the processed clip is
   the Candidate's anchor, see vo.effects), measure them (pitch, HNR, spectral centroid, ASR WER), and render a few
   of the Archetype's real lines as continuation from each, so you hear whether a Candidate holds up over lines.
   An Archetype whose style guide turns design off (orc_m) gets no fresh Candidates unless asked (`design=True`);
   `import_bakeoff` seeds its Candidates from bake-off anchors instead (vo.bakeoff_seeds).
   For each top Lexicon name, render a sample sentence with its spelling: in the approved anchor of the speaking
   NPC's Archetype (VoxCPM2) if there is one, else the Narrator (Kokoro).
4. Once every Archetype has an approved anchor, write the locked approved_voices.json (vo.lock); once every top
   Lexicon name is reviewed, the locked lexicon.json (vo.lexicon). The Approval Gate is complete with both.

Idempotent and resumable: a clip already in the DB with its file on disk is not rendered again, and every clip is
committed as it lands. Audio goes under build/candidates/<archetype>/.
"""
from __future__ import annotations

import json
import sqlite3
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from vo import archetypes, asr, audio, bakeoff_seeds, effects, lexicon, lock, tts, voicefeat, voxcpm

DEFAULT_CANDIDATES = 8
DEFAULT_SAMPLES = 3
SEED_STRIDE = 1000  # generation g, candidate i -> seed g * SEED_STRIDE + i
NARRATOR_ID = "narrator"
NARRATOR_CANDIDATE = "narrator/fixed"
NARRATOR_NOTE = "Kokoro bm_lewis, fixed (bake-off round 1, #10). Not an NPC voice; outside the Approval Gate."
SAMPLE_TYPES = ("gossip", "quest_detail", "quest_complete", "quest_progress")
SAMPLE_LEN = (60, 260)  # characters: long enough to hear the voice settle, short enough to review quickly

# Script dialect spellings and their standard forms: ASR writes the standard word, which isn't a misreading.
DIALECT = {"de": "the", "dem": "them", "dis": "this", "dat": "that", "mon": "man", "an": "and", "da": "the",
           "comin": "coming", "stealin": "stealing", "gawkin": "gawking", "ssso": "so"}


class Engine(Protocol):
    def design(self, text: str, description: str, seed: int) -> tuple[np.ndarray, int]: ...

    def continue_(self, text: str, anchor: Path | str, anchor_text: str, seed: int,
                  mode: str = "cont") -> tuple[np.ndarray, int]: ...


Renderer = Callable[[str], tuple[np.ndarray, int]]  # the Narrator: text -> audio


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def dialect_wer(text: str, heard: str) -> float:
    """WER with dialect spellings folded to standard words on both sides (a troll's "de"/"dem" isn't an error)."""
    fold = lambda s: " ".join(DIALECT.get(w, w) for w in asr.normalise(s))
    return round(asr.wer(fold(text), fold(heard)), 3)


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    """16-bit mono WAV, written atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    pcm = (np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1, 1) * 32767).astype("<i2")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    tmp.replace(path)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """A 16-bit PCM WAV as mono float32."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit PCM")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
        return x.reshape(-1, w.getnchannels()).mean(axis=1), w.getframerate()


def candidate_id(aid: str, generation: int, i: int) -> str:
    return f"{aid}/g{generation}s{i}"


def seed(generation: int, i: int) -> int:
    return generation * SEED_STRIDE + i


def describe(base: str, notes: list[str]) -> str:
    """The style guide description with the review notes appended, in order."""
    return " ".join([base.strip(), *(n.strip().rstrip(".") + "." for n in notes if n.strip())])


# --- 1. review actions -----------------------------------------------------------------------------------------------

def _payload(row: sqlite3.Row) -> dict:
    return json.loads(row["payload"]) if row["payload"] else {}


def _approve(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    cid = row["target"]
    c = conn.execute("SELECT archetype FROM candidates WHERE id = ?", (cid,)).fetchone()
    if c is None:
        raise ValueError(f"no candidate {cid}")
    aid, now = c["archetype"], _now()
    conn.execute("UPDATE candidates SET status = 'pending' WHERE archetype = ? AND status = 'approved'", (aid,))
    conn.execute("UPDATE candidates SET status = 'approved', reviewed_at = ? WHERE id = ?", (now, cid))
    conn.execute("UPDATE archetypes SET approved = ?, approved_at = ?, updated_at = ? WHERE id = ?",
                 (cid, now, now, aid))
    return f"{aid}: approved {cid}"


def _reject(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    cid = row["target"]
    c = conn.execute("SELECT archetype FROM candidates WHERE id = ?", (cid,)).fetchone()
    if c is None:
        raise ValueError(f"no candidate {cid}")
    now = _now()
    conn.execute("UPDATE candidates SET status = 'rejected', reviewed_at = ? WHERE id = ?", (now, cid))
    conn.execute("UPDATE archetypes SET approved = NULL, approved_at = NULL, updated_at = ? WHERE id = ? AND approved = ?",
                 (now, c["archetype"], cid))
    return f"{c['archetype']}: rejected {cid}"


def _regenerate(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    """Append the note to the Archetype's description and start a fresh generation of Candidates (the current ones
    are superseded, an approval is withdrawn)."""
    aid = row["target"]
    a = conn.execute("SELECT * FROM archetypes WHERE id = ? AND kind != 'narrator'", (aid,)).fetchone()
    if a is None:
        raise ValueError(f"no archetype {aid}")
    note = str(_payload(row).get("note") or "").strip()
    notes = json.loads(a["notes"] or "[]") + ([note] if note else [])
    now = _now()
    conn.execute("UPDATE candidates SET status = 'superseded', reviewed_at = ? WHERE archetype = ? AND generation = ?"
                 " AND status != 'rejected'", (now, aid, a["generation"]))
    conn.execute("UPDATE archetypes SET notes = ?, description = ?, generation = generation + 1, approved = NULL,"
                 " approved_at = NULL, updated_at = ? WHERE id = ?",
                 (json.dumps(notes), describe(a["base_description"], notes), now, aid))
    return f"{aid}: regenerating (generation {a['generation'] + 1})" + (f" with note {note!r}" if note else "")


ACTIONS: dict[str, Callable[[sqlite3.Connection, sqlite3.Row], str]] = {
    "approve-candidate": _approve,
    "reject-candidate": _reject,
    "regenerate-archetype": _regenerate,
    **lexicon.ACTIONS,  # accept-lexicon, correct-lexicon
}


def consume_actions(conn: sqlite3.Connection, log: Callable[[str], None] = print) -> int:
    """Apply unconsumed Approval review_actions in order (other actions are left for `vo run`). An invalid one is
    logged and dropped."""
    n = 0
    rows = conn.execute(f"SELECT * FROM review_actions WHERE consumed_at IS NULL AND action IN"
                        f" ({','.join('?' * len(ACTIONS))}) ORDER BY id", tuple(ACTIONS)).fetchall()
    for row in rows:
        with conn:
            try:
                log(f"review action {row['id']}: " + ACTIONS[row["action"]](conn, row))
            except (ValueError, TypeError) as e:
                log(f"review action {row['id']} ({row['action']} {row['target']}): invalid, dropped: {e}")
            conn.execute("UPDATE review_actions SET consumed_at = ? WHERE id = ?", (_now(), row["id"]))
        n += 1
    return n


# --- 2. the Archetype list -------------------------------------------------------------------------------------------

def sync_archetypes(conn: sqlite3.Connection) -> list[str]:
    """Upsert every derived Archetype with its style guide entry (keeping review notes, generation and approval),
    plus the Narrator row. Returns the Archetype ids (Narrator excluded), in display order."""
    derived = archetypes.derive(conn)
    now = _now()
    with conn:
        for a in derived:
            s = archetypes.style(a.id)
            row = conn.execute("SELECT notes FROM archetypes WHERE id = ?", (a.id,)).fetchone()
            notes = json.loads(row["notes"] or "[]") if row else []
            vals = (a.label, a.kind, a.gender, json.dumps(a.races), a.npcs, a.lines, s.description, json.dumps(notes),
                    describe(s.description, notes), s.anchor_text, s.mode, s.effect_chain, s.anchor_chain)
            if row is None:
                conn.execute("INSERT INTO archetypes (label, kind, gender, races, npcs, lines, base_description, notes,"
                             " description, anchor_text, mode, effect_chain, anchor_chain, id, updated_at)"
                             " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (*vals, a.id, now))
            else:
                conn.execute("UPDATE archetypes SET label = ?, kind = ?, gender = ?, races = ?, npcs = ?, lines = ?,"
                             " base_description = ?, notes = ?, description = ?, anchor_text = ?, mode = ?,"
                             " effect_chain = ?, anchor_chain = ? WHERE id = ?", (*vals, a.id))
        n_lines = conn.execute("SELECT COUNT(*) FROM lines WHERE npc_id IS NULL").fetchone()[0]
        conn.execute("INSERT INTO archetypes (id, label, kind, lines, base_description, notes, description, mode,"
                     " approved, updated_at) VALUES (?, 'Narrator', 'narrator', ?, ?, '[]', ?, 'kokoro', ?, ?)"
                     " ON CONFLICT (id) DO UPDATE SET lines = excluded.lines, description = excluded.description",
                     (NARRATOR_ID, n_lines, NARRATOR_NOTE, f"{NARRATOR_NOTE} Voice {tts.NARRATOR_VOICE_ID}.",
                      NARRATOR_CANDIDATE, now))
    return [a.id for a in derived]


def sample_lines(conn: sqlite3.Connection, npcs: list[int], n: int) -> list[tuple[int, str]]:
    """Up to n of these NPCs' real lines to hear a Candidate over: one per line type where possible (gossip, detail,
    completion, progress), each of reviewable length, closest to 150 characters first. Deterministic."""
    if not npcs or n <= 0:
        return []
    marks = ",".join("?" * len(npcs))
    rows = conn.execute(
        f"SELECT id, type, tts_text FROM lines WHERE npc_id IN ({marks}) AND COALESCE(tts_text, '') != ''"
        f" ORDER BY (length(tts_text) NOT BETWEEN ? AND ?), abs(length(tts_text) - 150), id",
        (*npcs, *SAMPLE_LEN)).fetchall()
    picked: list[sqlite3.Row] = []
    for t in SAMPLE_TYPES:
        r = next((r for r in rows if r["type"] == t and r not in picked
                  and SAMPLE_LEN[0] <= len(r["tts_text"]) <= SAMPLE_LEN[1]), None)
        if r is not None and len(picked) < n:
            picked.append(r)
    texts = {r["tts_text"] for r in picked}
    for r in rows:  # fill: any length-ok line, then anything
        if len(picked) >= n:
            break
        if r["tts_text"] not in texts:
            picked.append(r)
            texts.add(r["tts_text"])
    return [(r["id"], r["tts_text"]) for r in picked[:n]]


# --- 3. rendering ----------------------------------------------------------------------------------------------------

@dataclass
class Summary:
    actions: int = 0
    archetypes: int = 0
    approved: int = 0
    candidates: int = 0
    samples: int = 0
    lock: str = ""
    names: int = 0             # Lexicon names found in the lines
    names_top: int = 0         # ... of which need review
    names_reviewed: int = 0
    name_samples: int = 0
    lexicon_lock: str = ""
    log: list[str] = field(default_factory=list)


class _Check:
    """ASR + ear-proxies for a clip; the ASR model loads on first use."""

    def __init__(self, asr_backend: asr.ASRBackend | None, asr_model: str):
        self._asr, self._model = asr_backend, asr_model

    def heard(self, wav: Path) -> str:
        if self._asr is None:
            self._asr = asr.whisper(self._model)
        return self._asr.transcribe(wav).strip()


def _exists(path: str | None) -> bool:
    return bool(path) and Path(path).exists()


def _render_candidates(conn, a: sqlite3.Row, out: Path, engine: Engine, check: _Check, n: int, log) -> int:
    aid, gen = a["id"], a["generation"]
    done = 0
    for i in range(n):
        cid = candidate_id(aid, gen, i)
        row = conn.execute("SELECT path FROM candidates WHERE id = ?", (cid,)).fetchone()
        if row is not None and _exists(row["path"]):
            continue
        s = seed(gen, i)
        samples, rate = engine.design(a["anchor_text"], a["description"], s)
        _add_candidate(conn, a, cid, s, a["description"], samples, rate, out / aid / f"g{gen}s{i}.wav",
                       a["anchor_chain"], None, check, log)
        done += 1
    return done


def _add_candidate(conn, a: sqlite3.Row, cid: str, s: int, description: str, samples: np.ndarray, rate: int,
                   wav: Path, chain: str | None, label: str | None, check: _Check, log) -> None:
    """Write a Candidate anchor and its row. With an anchor chain, the clip as designed is kept as <name>_raw.wav
    and the processed clip (applied here, once) is the anchor: measured, heard, continued from, locked."""
    raw = None
    if chain:
        raw = wav.with_name(f"{wav.stem}_raw.wav")
        write_wav(raw, samples, rate)
        samples = effects.apply(chain, samples, rate)
    write_wav(wav, samples, rate)
    feats = voicefeat.measure(samples, rate, a["gender"] != "female")
    heard = check.heard(wav)
    w = dialect_wer(a["anchor_text"], heard)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO candidates (id, archetype, generation, seed, description, anchor_text, path,"
            " duration_s, f0, hnr, centroid, asr, wer, status, created_at, anchor_chain, raw_path, label)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
            (cid, a["id"], a["generation"], s, description, a["anchor_text"], str(wav.resolve()),
             round(len(samples) / rate, 3), feats["f0"], feats["hnr"], feats["centroid"], heard, w, _now(), chain,
             str(raw.resolve()) if raw else None, label))
    log(f"candidate {cid} seed {s}{f' + {chain} chain' if chain else ''}: {len(samples) / rate:.1f}s f0 {feats['f0']}"
        f" hnr {feats['hnr']} WER {w:.0%}")


BAKEOFF_ROOT = Path(__file__).resolve().parents[3] / "build"


def seed_from_bakeoff(conn, a: sqlite3.Row, out: Path, check: _Check, root: Path = BAKEOFF_ROOT, log=print) -> int:
    """Seed the Archetype's Candidates from bake-off anchors (vo.bakeoff_seeds), in its current generation.
    Idempotent: a seed already in the DB with its file on disk is left alone (status and review kept)."""
    aid = a["id"]
    seeds = bakeoff_seeds.SEEDS.get(aid)
    if not seeds:
        raise ValueError(f"no bake-off anchors for {aid}; seeded: {', '.join(sorted(bakeoff_seeds.SEEDS))}")
    missing = [str(root / sd.source) for sd in seeds if not (root / sd.source).exists()]
    if missing:
        raise ValueError(f"bake-off anchor(s) not found: {', '.join(missing)}")
    done = 0
    for sd in seeds:
        cid = f"{aid}/{sd.key}"
        row = conn.execute("SELECT path FROM candidates WHERE id = ?", (cid,)).fetchone()
        if row is not None and _exists(row["path"]):
            continue
        samples, rate = read_wav(root / sd.source)
        _add_candidate(conn, a, cid, sd.seed, sd.description, samples, rate, out / aid / "bakeoff" / f"{sd.key}.wav",
                       sd.chain, sd.label, check, log)
        done += 1
    return done


SAMPLE_TRIES = 3


def _continue_audible(engine: Engine, a: sqlite3.Row, c: sqlite3.Row, text: str, idx: int):
    """Continue `text` from the Candidate's anchor, re-seeding when VoxCPM2 returns silence (it occasionally does)."""
    for attempt in range(SAMPLE_TRIES):
        samples, rate = engine.continue_(text, c["path"], c["anchor_text"], idx + 1000 * attempt, a["mode"] or "cont")
        try:
            return audio.trim(effects.apply(a["effect_chain"], samples, rate), rate), rate
        except ValueError:
            if attempt == SAMPLE_TRIES - 1:
                raise


def _render_samples(conn, a: sqlite3.Row, out: Path, engine: Engine, check: _Check, lines: list[tuple[int, str]],
                    log) -> int:
    done = 0
    cands = conn.execute("SELECT * FROM candidates WHERE archetype = ? AND generation = ? AND status IN"
                         " ('pending', 'approved') ORDER BY id", (a["id"], a["generation"])).fetchall()
    for c in cands:
        if not _exists(c["path"]):
            continue
        for idx, (line_id, text) in enumerate(lines, 1):
            row = conn.execute("SELECT path, text FROM candidate_samples WHERE candidate = ? AND idx = ?",
                               (c["id"], idx)).fetchone()
            if row is not None and row["text"] == text and _exists(row["path"]):
                continue
            try:
                samples, rate = _continue_audible(engine, a, c, text, idx)
            except ValueError as e:  # silent or empty after every retry: skip it, the Candidate stays reviewable
                log(f"  sample {c['id']} #{idx} (line {line_id}): skipped ({e})")
                continue
            wav = Path(c["path"]).with_name(f"{Path(c['path']).stem}_{idx}.wav")
            write_wav(wav, samples, rate)
            heard = check.heard(wav)
            w = dialect_wer(text, heard)
            with conn:
                conn.execute("INSERT OR REPLACE INTO candidate_samples (candidate, idx, line_id, text, path,"
                             " duration_s, asr, wer) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (c["id"], idx, line_id, text, str(wav.resolve()), round(len(samples) / rate, 3), heard, w))
            log(f"  sample {c['id']} #{idx} (line {line_id}): {len(samples) / rate:.1f}s WER {w:.0%}")
            done += 1
    return done


def _render_narrator(conn, out: Path, narrator: Renderer, log) -> int:
    row = conn.execute("SELECT path FROM candidates WHERE id = ?", (NARRATOR_CANDIDATE,)).fetchone()
    if row is not None and _exists(row["path"]):
        return 0
    got = conn.execute("SELECT tts_text FROM lines WHERE npc_id IS NULL AND COALESCE(tts_text, '') != ''"
                       " ORDER BY (length(tts_text) NOT BETWEEN ? AND ?), abs(length(tts_text) - 150), id LIMIT 1",
                       SAMPLE_LEN).fetchone()
    text = got["tts_text"] if got else "A weathered poster hangs here. Wanted: the bandit leader, dead or alive."
    samples, rate = narrator(text)
    wav = out / NARRATOR_ID / "fixed.wav"
    write_wav(wav, samples, rate)
    with conn:
        conn.execute("INSERT OR REPLACE INTO candidates (id, archetype, generation, seed, description, anchor_text, path,"
                     " duration_s, status, created_at) VALUES (?, ?, 0, 0, ?, ?, ?, ?, 'approved', ?)",
                     (NARRATOR_CANDIDATE, NARRATOR_ID, tts.NARRATOR_VOICE_ID, text, str(wav.resolve()),
                      round(len(samples) / rate, 3), _now()))
    log(f"narrator sample ({tts.NARRATOR_VOICE_ID}): {len(samples) / rate:.1f}s")
    return 1


def _lexicon_voice(conn, npc: int | None, npc_map: dict[int, str]) -> sqlite3.Row | None:
    """The approved anchor (candidate row + Archetype mode/effects) of the NPC's Archetype, if it has one on disk."""
    aid = npc_map.get(npc) if npc is not None else None
    if aid is None or aid == archetypes.NARRATOR:
        return None
    r = conn.execute("SELECT a.id AS archetype, a.mode, a.effect_chain, c.id, c.path, c.anchor_text FROM archetypes a"
                     " JOIN candidates c ON c.id = a.approved WHERE a.id = ?", (aid,)).fetchone()
    return r if r is not None and _exists(r["path"]) else None


def _render_lexicon(conn, out: Path, get_engine: Callable[[], Engine], narrator: Renderer, log,
                    top: int = lexicon.TOP) -> int:
    """A sample per top Lexicon name, re-rendered when its spelling changes: the sentence of a line that says it,
    with the name respelled, in the speaking NPC's approved Archetype anchor (VoxCPM2 continuation) if there is one,
    else the Narrator (Kokoro)."""
    npc_map, done = archetypes.npc_archetypes(conn), 0
    rows = conn.execute("SELECT * FROM lexicon WHERE rank <= ? ORDER BY rank", (top,)).fetchall()
    for r in rows:
        if r["sample_spelling"] == r["spelling"] and _exists(r["sample_path"]):
            continue
        ids = lexicon.lines_saying(conn, [r["name"]])[:40]
        lines = [conn.execute("SELECT id, npc_id, raw_text, player_gender FROM lines WHERE id = ?", (i,)).fetchone()
                 for i in ids]
        picks = [(lexicon.sample_sentence(l["raw_text"], l["player_gender"], r["name"]), l) for l in lines]
        picks = [(s_, l) for s_, l in picks if lexicon.key(r["name"]) in lexicon.key(s_)]
        if picks:
            written, line = min(picks, key=lambda p: (abs(len(p[0]) - 90), p[1]["id"]))
        else:
            written, line = f"Have you heard of {r['name']}?", None
        spoken = lexicon.Lexicon({r["name"]: r["spelling"]})(written)
        voice = _lexicon_voice(conn, line["npc_id"] if line else None, npc_map)
        if voice is not None:
            samples, rate = get_engine().continue_(spoken, voice["path"], voice["anchor_text"], 0, voice["mode"] or "cont")
            samples = audio.trim(effects.apply(voice["effect_chain"], samples, rate), rate)
            label = f"VoxCPM2, {voice['archetype']} anchor {voice['id']}"
        else:
            samples, rate = narrator(spoken)
            label = f"Narrator, {tts.NARRATOR_VOICE_ID}"
        slug = "".join(c if c.isalnum() else "_" for c in r["name"])
        wav = out / "lexicon" / f"{slug}.wav"
        write_wav(wav, samples, rate)
        with conn:
            conn.execute("UPDATE lexicon SET sample_line = ?, sample_text = ?, sample_spoken = ?, sample_spelling = ?,"
                         " sample_path = ?, sample_voice = ? WHERE name = ?",
                         (line["id"] if line else None, written, spoken, r["spelling"], str(wav.resolve()), label,
                          r["name"]))
        log(f"lexicon sample {r['name']} -> {r['spelling']!r} ({label})")
        done += 1
    return done


def _kokoro_narrator(text: str) -> tuple[np.ndarray, int]:
    name, voice = tts.split_voice_id(tts.NARRATOR_VOICE_ID)
    return tts.backend(name).render(text, voice, 0)


# --- 4. the lock -----------------------------------------------------------------------------------------------------

def lock_data(conn: sqlite3.Connection, ids: list[str]) -> dict | None:
    """approved_voices.json's content if every Archetype in `ids` has an approved anchor on disk, else None."""
    entries = {}
    for aid in ids:
        r = conn.execute("SELECT a.label, a.races, a.mode, a.effect_chain, c.id AS cand, c.path, c.anchor_text,"
                         " c.description, c.seed, c.anchor_chain, c.raw_path FROM archetypes a JOIN candidates c"
                         " ON c.id = a.approved WHERE a.id = ?", (aid,)).fetchone()
        if r is None or not _exists(r["path"]):
            return None
        entries[aid] = {"label": r["label"], "candidate": r["cand"], "anchor": r["path"], "transcript": r["anchor_text"],
                        "description": r["description"], "seed": r["seed"], "mode": r["mode"] or "cont",
                        "effect_chain": r["effect_chain"], "races": json.loads(r["races"] or "{}")}
        if r["anchor_chain"]:  # `anchor` is the processed clip; the unprocessed design is kept for reference
            entries[aid].update(anchor_chain=r["anchor_chain"], raw_anchor=r["raw_path"])
    for aid, e in entries.items():
        e["voice_id"] = lock.voice_id(aid, e)
    return {"locked": True, "model": voxcpm.REPO, "settings": voxcpm.SETTINGS,
            "narrator": {"voice_id": tts.NARRATOR_VOICE_ID}, "archetypes": entries}


def update_lock(conn: sqlite3.Connection, ids: list[str], p: Path, log) -> str:
    """Write the lock once everything is approved (unchanged content keeps the file and its locked_at); remove a
    lock that no longer matches, so `vo run` can't use an anchor that was re-opened."""
    data = lock_data(conn, ids)
    if data is None:
        if lock.remove(p):
            log(f"removed {p}: an Archetype no longer has an approved anchor")
        return "open"
    if p.exists():
        try:
            old = json.loads(p.read_text())
            if {k: v for k, v in old.items() if k != "locked_at"} == data:
                return "unchanged"
        except json.JSONDecodeError:
            pass
    lock.write(p, {**data, "locked_at": _now()})
    log(f"wrote {p} ({len(data['archetypes'])} Archetypes)")
    return "written"


# --- the command -----------------------------------------------------------------------------------------------------

def prepare(conn: sqlite3.Connection, out: Path, *, engine: Engine | None = None,
            asr_backend: asr.ASRBackend | None = None, asr_model: str = asr.DEFAULT_MODEL,
            narrator: Renderer | None = None, only: list[str] | None = None,
            candidates: int = DEFAULT_CANDIDATES, samples: int = DEFAULT_SAMPLES, design: bool = False,
            import_bakeoff: list[str] | None = None, bakeoff_root: Path = BAKEOFF_ROOT,
            lock_path: Path | None = None, lexicon_path: Path | None = None, areas: list[str] | tuple = (),
            log: Callable[[str], None] = print) -> Summary:
    """See the module docstring. `only` limits rendering (not action consumption or the locks) to those Archetypes,
    and skips the Lexicon samples. `import_bakeoff` seeds those Archetypes' Candidates from the bake-off first (and
    implies `only` them if unset); `design` renders fresh Candidates even where the style guide turns design off.
    `areas` are zone/area names (the world DB's), to flag Lexicon names as zones."""
    s = Summary()
    s.actions = consume_actions(conn, log)
    ids = sync_archetypes(conn)
    found = lexicon.sync(conn, areas)
    s.names, s.names_top = found["names"], found["top"]
    s.archetypes = len(ids)
    only = only or import_bakeoff
    if only:
        unknown = sorted((set(only) | set(import_bakeoff or [])) - set(ids))
        if unknown:
            raise ValueError(f"unknown Archetype(s) {unknown}; one of: {', '.join(ids)}")
        unseeded = sorted(set(import_bakeoff or []) - set(bakeoff_seeds.SEEDS))
        if unseeded:
            raise ValueError(f"no bake-off anchors for {unseeded}; seeded: {', '.join(sorted(bakeoff_seeds.SEEDS))}")
    engine = engine or voxcpm.engine()
    check = _Check(asr_backend, asr_model)
    s.candidates += _render_narrator(conn, out, narrator or _kokoro_narrator, log)
    if not only:
        s.name_samples = _render_lexicon(conn, out, lambda: engine, narrator or _kokoro_narrator, log)
    npc_map = archetypes.npc_archetypes(conn)
    for aid in ids:
        a = conn.execute("SELECT * FROM archetypes WHERE id = ?", (aid,)).fetchone()
        if import_bakeoff and aid in import_bakeoff:
            s.candidates += seed_from_bakeoff(conn, a, out, check, bakeoff_root, log)
        if a["approved"] or (only and aid not in only):
            continue
        if not (design or archetypes.style(aid).design):
            log(f"{aid} ({a['label']}): no fresh Candidates by design (seeded by --import-bakeoff {aid};"
                f" --design renders fresh ones)")
            lines = sample_lines(conn, [n for n, x in npc_map.items() if x == aid], samples)
            s.samples += _render_samples(conn, a, out, engine, check, lines, log)
            continue
        # Every Candidate of this generation rejected: start the next generation (same description).
        live = conn.execute("SELECT COUNT(*) FROM candidates WHERE archetype = ? AND generation = ? AND status !="
                            " 'rejected'", (aid, a["generation"])).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM candidates WHERE archetype = ? AND generation = ?",
                             (aid, a["generation"])).fetchone()[0]
        if total >= candidates and live == 0:
            with conn:
                conn.execute("UPDATE archetypes SET generation = generation + 1, updated_at = ? WHERE id = ?",
                             (_now(), aid))
            log(f"{aid}: every Candidate rejected, rendering generation {a['generation'] + 1}")
            a = conn.execute("SELECT * FROM archetypes WHERE id = ?", (aid,)).fetchone()
        log(f"{aid} ({a['label']}, generation {a['generation']})")
        s.candidates += _render_candidates(conn, a, out, engine, check, candidates, log)
        lines = sample_lines(conn, [n for n, x in npc_map.items() if x == aid], samples)
        s.samples += _render_samples(conn, a, out, engine, check, lines, log)
    s.approved = conn.execute(f"SELECT COUNT(*) FROM archetypes WHERE approved IS NOT NULL AND id IN"
                              f" ({','.join('?' * len(ids))})", ids).fetchone()[0] if ids else 0
    s.lock = update_lock(conn, ids, lock_path or lock.path(), log)
    s.names_reviewed = lexicon.progress(conn)[0]
    s.lexicon_lock = lexicon.update_lock(conn, lexicon_path or lexicon.path(), log)
    return s


def summary_text(s: Summary) -> str:
    lock_note = {"open": "lock not written until every Archetype is approved", "written": "lock written",
                 "unchanged": "lock unchanged"}[s.lock]
    lex_note = {"open": "lexicon.json not written until every top name is reviewed", "written": "lexicon.json written",
                "unchanged": "lexicon.json unchanged"}[s.lexicon_lock]
    gate = "Approval Gate complete" if s.lock != "open" and s.lexicon_lock != "open" else "Approval Gate open"
    return (f"{s.approved}/{s.archetypes} Archetypes approved; rendered {s.candidates} candidates and {s.samples}"
            f" samples; applied {s.actions} review actions; {lock_note}. Lexicon: {s.names} names,"
            f" {s.names_reviewed}/{s.names_top} top names reviewed, {s.name_samples} samples rendered; {lex_note}."
            f" {gate}.")

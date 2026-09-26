"""Pipeline state: one SQLite database shared by every stage."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS npcs (
  id INTEGER PRIMARY KEY,
  name TEXT, subname TEXT,
  race TEXT, gender TEXT,
  model TEXT, faction INTEGER,
  role TEXT,
  level_min INTEGER, level_max INTEGER,
  is_named INTEGER DEFAULT 0,
  source TEXT DEFAULT 'core'
);
CREATE TABLE IF NOT EXISTS spawns (npc_id INTEGER, map INTEGER, zone INTEGER, x REAL, y REAL, z REAL);
CREATE TABLE IF NOT EXISTS lines (
  id INTEGER PRIMARY KEY,
  npc_id INTEGER,
  type TEXT,
  quest_id INTEGER,
  player_gender TEXT,
  raw_text TEXT,
  tts_text TEXT,
  match_pattern TEXT,
  text_hash TEXT,
  source TEXT DEFAULT 'core',
  UNIQUE (type, quest_id, npc_id, player_gender, raw_text)
);
CREATE TABLE IF NOT EXISTS voices (npc_id INTEGER PRIMARY KEY, voice_id TEXT, archetype TEXT, prompt TEXT, ref_clip TEXT, embedding BLOB);
CREATE TABLE IF NOT EXISTS audio (line_id INTEGER, voice_id TEXT, path TEXT, duration_s REAL, status TEXT, PRIMARY KEY (line_id, voice_id));
CREATE TABLE IF NOT EXISTS capture (
  id INTEGER PRIMARY KEY, upload_id TEXT, npc_id INTEGER, npc_name TEXT, unit_sex INTEGER, creature_type TEXT,
  zone INTEGER, x REAL, y REAL, event TEXT, quest_id INTEGER, text TEXT, text_hash TEXT, locale TEXT, seen_at TEXT
);
CREATE TABLE IF NOT EXISTS manual_overrides (npc_id INTEGER, field TEXT, value TEXT);
-- vo run's queue: one job per line and voice; tts_hash is the text it was queued for, so a text change requeues it.
-- status: pending | running | done | quarantined | skipped. attempts count failures since last queued (3 -> quarantined);
-- tries count every render ever, and seed each new take.
CREATE TABLE IF NOT EXISTS jobs (
  line_id INTEGER, voice_id TEXT, tts_hash TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0, tries INTEGER NOT NULL DEFAULT 0,
  reason TEXT, wer REAL, transcript TEXT, updated_at TEXT,
  PRIMARY KEY (line_id, voice_id)
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status);
-- The dashboard's queue of human actions, consumed by vo run before it generates.
CREATE TABLE IF NOT EXISTS review_actions (
  id INTEGER PRIMARY KEY, action TEXT NOT NULL, target TEXT, payload TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, consumed_at TEXT
);
-- Resolution flags for review: unresolved race/gender, mixed displays, defaulted gender, missing spawn/zone.
CREATE TABLE IF NOT EXISTS npc_issues (npc_id INTEGER, issue TEXT, detail TEXT);
-- One row per vo run: its history, and a heartbeat the dashboard uses to tell a live run from a dead one.
-- status: running | paused | finished | until | killed | failed. Times are local ISO, like jobs.updated_at.
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, pid INTEGER, workers INTEGER,
  started_at TEXT, last_heartbeat TEXT, ended_at TEXT,
  status TEXT NOT NULL DEFAULT 'running', until TEXT, summary TEXT, error TEXT
);
-- Line flags for review, e.g. Capture text whose player name couldn't be re-tokenised.
CREATE TABLE IF NOT EXISTS line_issues (line_id INTEGER, issue TEXT, detail TEXT);
-- Approval Gate (vo prepare, the dashboard's Approval page). One row per Archetype, plus the fixed Narrator
-- (kind 'narrator'). description = base_description (race style guide) + notes (JSON list, from "Regenerate with
-- note"). generation is bumped by each regenerate and seeds a fresh batch. An Archetype's Base Voices are its
-- candidates with status 'approved' (up to vo.basevoices.MAX, ADR-0006); approved is a summary kept in step with them:
-- the first approved candidate id (NULL: none), approved_at the latest approval.
CREATE TABLE IF NOT EXISTS archetypes (
  id TEXT PRIMARY KEY, label TEXT, kind TEXT, gender TEXT,
  races TEXT, npcs INTEGER, lines INTEGER,
  base_description TEXT, notes TEXT, description TEXT, anchor_text TEXT, mode TEXT, effect_chain TEXT,
  anchor_chain TEXT, generation INTEGER NOT NULL DEFAULT 0, approved TEXT, approved_at TEXT, updated_at TEXT
);
-- A Candidate anchor: VoxCPM2 voice design of the Archetype's anchor line. id is "<archetype>/g<gen>s<i>".
-- Bake-off seeds (vo.bakeoff_seeds) are "<archetype>/<key>" with a label; game voice anchors (vo.gamevoice, ADR-0007)
-- "<archetype>/gv-<key>", with their own anchor_text (the clips' transcript) and mode (NULL: the Archetype's). With
-- an anchor chain (vo.effects), path is the processed anchor and raw_path the clip before it. Variations of a
-- Candidate (vo.variations) are "<source id>~v<k>", with `variation` set. status: pending |
-- approved | rejected | superseded (an older generation) | retired (vo prepare --retire-unapproved: hidden, not
-- replaced, no samples; files kept).
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY, archetype TEXT NOT NULL, generation INTEGER, seed INTEGER,
  description TEXT, anchor_text TEXT, path TEXT, duration_s REAL,
  f0 REAL, hnr REAL, centroid REAL, asr TEXT, wer REAL,
  status TEXT NOT NULL DEFAULT 'pending', created_at TEXT, reviewed_at TEXT,
  anchor_chain TEXT, raw_path TEXT, label TEXT, mode TEXT, variation TEXT
);
CREATE INDEX IF NOT EXISTS candidates_archetype ON candidates (archetype);
-- Variation Candidates (vo.variations) asked for with review action vary-candidate: slots k0 .. k0+n-1 of the
-- source's variations "<source>~v<k>", rendered by vo prepare (done_at set once every slot is made or dropped).
-- method: NULL mixes styles and DSP shifts; 'style' is style cloning only, with the note as the style.
-- A variation Candidate's candidates.variation is JSON: source, method (style | dsp), its style or shift, similarity.
CREATE TABLE IF NOT EXISTS variation_requests (
  id INTEGER PRIMARY KEY, action_id INTEGER, source TEXT NOT NULL, archetype TEXT, note TEXT, k0 INTEGER NOT NULL,
  n INTEGER NOT NULL, method TEXT, created_at TEXT, done_at TEXT
);
-- Sample lines per Candidate, rendered as continuation from its anchor, as vo run would.
CREATE TABLE IF NOT EXISTS candidate_samples (
  candidate TEXT, idx INTEGER, line_id INTEGER, text TEXT, path TEXT, duration_s REAL, asr TEXT, wer REAL,
  PRIMARY KEY (candidate, idx)
);
-- Approval Gate sample clips that came back silent on every seed (vo prepare): clip is "<candidate>#<idx>", text the
-- text tried. Not retried while the text is the same; a regenerate clears its Archetype's (new generation, new ids).
CREATE TABLE IF NOT EXISTS sample_skips (clip TEXT PRIMARY KEY, text TEXT, reason TEXT, at TEXT);
-- The Lexicon (#12, vo.lexicon): one row per lore name found in the lines. spelling is the one in use (the draft,
-- the reviewed spelling, or for an auto name its alt-th alternative). status: pending (a top name awaiting review) |
-- accepted | corrected | auto. rank is by lines (NULL once a reviewed name leaves the lines). The sample_* columns
-- are the Approval page's rendered sample of a top name.
CREATE TABLE IF NOT EXISTS lexicon (
  name TEXT PRIMARY KEY, lines INTEGER, rank INTEGER, npc INTEGER, zone INTEGER, example_line INTEGER,
  draft TEXT, spelling TEXT, status TEXT NOT NULL DEFAULT 'auto', alt INTEGER NOT NULL DEFAULT 0,
  sample_line INTEGER, sample_text TEXT, sample_spoken TEXT, sample_spelling TEXT, sample_path TEXT,
  sample_voice TEXT, reviewed_at TEXT, updated_at TEXT
);
-- Lines where ASR missed a Lexicon name since its spelling last changed (vo run).
CREATE TABLE IF NOT EXISTS lexicon_misses (name TEXT, line_id INTEGER, at TEXT, PRIMARY KEY (name, line_id));
-- Neighbours (#13): NPC pairs a player hears close together, a < b. reason: spawn | quest | spawn+quest; distance:
-- closest spawns in yards (spawn pairs); sim: WavLM-SV cosine of their NPC anchors once both have one (vo voices).
CREATE TABLE IF NOT EXISTS neighbours (a INTEGER, b INTEGER, reason TEXT, distance REAL, sim REAL, PRIMARY KEY (a, b));
-- How each NPC Voice was built (vo voices): roll (bumped by a dashboard re-roll), the attempt that cleared (re-rolls),
-- its similarity to its Base Voice and to its closest Neighbour, and status ok | leftover (issue: ceiling,
-- floor, pitch, hnr, wer; listed for the morning report, never blocking). anchor: the Archetype candidate it is
-- built on: its Base Voice (vo.basevoices), and base_voices the Archetype's approved candidates (JSON list) when that
-- was assigned; a change to them re-assigns the Archetype's NPCs. stale = 1: rebuild on the next vo voices.
CREATE TABLE IF NOT EXISTS voice_builds (
  npc_id INTEGER PRIMARY KEY, anchor TEXT, roll INTEGER NOT NULL DEFAULT 0, attempt INTEGER, strategy TEXT, seed INTEGER,
  params TEXT, archetype_sim REAL, neighbour_sim REAL, neighbour INTEGER, f0 REAL, hnr REAL, wer REAL, tried INTEGER,
  status TEXT, issue TEXT, detail TEXT, stale INTEGER NOT NULL DEFAULT 0, updated_at TEXT, base_voices TEXT
);
-- A line's previous text, kept when Capture (Drift) replaces it.
CREATE TABLE IF NOT EXISTS line_history (
  line_id INTEGER, raw_text TEXT, tts_text TEXT, text_hash TEXT, reason TEXT, capture_id INTEGER,
  changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Zone names by AreaTable id (as spawns.zone), from the world DB's area_template (vo.coverage), so the dashboard and
-- the morning report can name zones without the world DB.
CREATE TABLE IF NOT EXISTS zones (id INTEGER PRIMARY KEY, name TEXT, map INTEGER);
-- Each line's Voice Pack (vo.packs) and zone (its NPC's main spawn zone, else its quest's zone), refreshed by
-- vo extract, vo package and vo run when the world DB is there. Coverage (dashboard, morning report) reads it.
CREATE TABLE IF NOT EXISTS line_packs (line_id INTEGER PRIMARY KEY, pack TEXT, zone INTEGER);
-- Spot-check ratings (vo.ratings), folded in from the dashboard's rate-line / flag-line / flag-voice review_actions
-- by vo run and vo voices. rating: up | down | flag. line_id is NULL for a flag on a whole voice. at: the action's
-- created_at (UTC). A line's latest up/down counts; a voice with REROLL_DOWNS lines rated down is queued a re-roll.
CREATE TABLE IF NOT EXISTS ratings (
  id INTEGER PRIMARY KEY, action_id INTEGER UNIQUE, line_id INTEGER, voice_id TEXT, npc_id INTEGER,
  rating TEXT NOT NULL, note TEXT, at TEXT
);
CREATE INDEX IF NOT EXISTS ratings_voice ON ratings (voice_id);
"""

# Columns added after a table was first created: (table, column, type). connect() adds any that are missing.
MIGRATIONS = [
    ("capture", "kind", "TEXT"),            # miss | drift
    ("capture", "expected_hash", "TEXT"),   # Drift: the pack's hash the displayed text didn't match
    ("capture", "guid_type", "TEXT"),       # Creature, Vehicle, GameObject, ...
    ("capture", "record_key", "TEXT"),      # de-duplication key across uploads
    # A core line's identity: the Source Data wording it was extracted from. raw_text differs from it once Capture
    # recorded Drift (the line is then drifted); NULL for Capture lines.
    ("lines", "source_text", "TEXT"),
    # Approval Gate anchor chains (vo.effects) and bake-off seeds, for DBs made before them (see SCHEMA).
    ("archetypes", "anchor_chain", "TEXT"),
    ("candidates", "anchor_chain", "TEXT"),
    ("candidates", "raw_path", "TEXT"),
    ("candidates", "label", "TEXT"),
    ("candidates", "mode", "TEXT"),         # a Candidate's own continuation mode (game voice); NULL: the Archetype's
    ("candidates", "variation", "TEXT"),    # a variation Candidate's source and method (vo.variations), JSON
    ("capture", "ingested_at", "TEXT"),     # local ISO time vo ingest stored it (the morning report's "new Capture")
    ("voice_builds", "base_voices", "TEXT"),  # the Archetype's Base Voices when the NPC's was assigned (ADR-0006)
]

# DBs from before several Base Voices (ADR-0006) knew one Anchor per Archetype through archetypes.approved: make sure
# that candidate carries the 'approved' status the Base Voices are now read from. Idempotent (vo prepare keeps the
# column in step with the statuses).
BACKFILL_APPROVED = """
UPDATE candidates SET status = 'approved'
WHERE status = 'pending' AND id IN (SELECT approved FROM archetypes WHERE approved IS NOT NULL AND kind != 'narrator')
"""

# Fills source_text on core lines: the text before their first Drift update, else their current text.
BACKFILL_SOURCE_TEXT = """
UPDATE lines SET source_text = COALESCE(
  (SELECT h.raw_text FROM line_history h WHERE h.line_id = lines.id AND h.reason LIKE 'drift%' ORDER BY h.rowid LIMIT 1),
  raw_text)
WHERE source = 'core' AND source_text IS NULL
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(SCHEMA)
    for table, column, kind in MIGRATIONS:
        if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            if (table, column) == ("lines", "source_text"):
                conn.execute(BACKFILL_SOURCE_TEXT)
                conn.commit()
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS capture_record_key ON capture (record_key)")
    conn.execute(BACKFILL_APPROVED)
    conn.commit()  # (even with no row changed: the UPDATE opened a write transaction)
    return conn

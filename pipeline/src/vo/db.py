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
-- note"). generation is bumped by each regenerate and seeds a fresh batch. approved = the Anchor's candidate id.
CREATE TABLE IF NOT EXISTS archetypes (
  id TEXT PRIMARY KEY, label TEXT, kind TEXT, gender TEXT,
  races TEXT, npcs INTEGER, lines INTEGER,
  base_description TEXT, notes TEXT, description TEXT, anchor_text TEXT, mode TEXT, effect_chain TEXT,
  anchor_chain TEXT, generation INTEGER NOT NULL DEFAULT 0, approved TEXT, approved_at TEXT, updated_at TEXT
);
-- A Candidate anchor: VoxCPM2 voice design of the Archetype's anchor line. id is "<archetype>/g<gen>s<i>".
-- Bake-off seeds (vo.bakeoff_seeds) are "<archetype>/<key>" with a label. With an anchor chain (vo.effects), path is
-- the processed anchor and raw_path the clip before it. status: pending | approved | rejected | superseded (an
-- older generation).
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY, archetype TEXT NOT NULL, generation INTEGER, seed INTEGER,
  description TEXT, anchor_text TEXT, path TEXT, duration_s REAL,
  f0 REAL, hnr REAL, centroid REAL, asr TEXT, wer REAL,
  status TEXT NOT NULL DEFAULT 'pending', created_at TEXT, reviewed_at TEXT,
  anchor_chain TEXT, raw_path TEXT, label TEXT
);
CREATE INDEX IF NOT EXISTS candidates_archetype ON candidates (archetype);
-- Sample lines per Candidate, rendered as continuation from its anchor, as vo run would.
CREATE TABLE IF NOT EXISTS candidate_samples (
  candidate TEXT, idx INTEGER, line_id INTEGER, text TEXT, path TEXT, duration_s REAL, asr TEXT, wer REAL,
  PRIMARY KEY (candidate, idx)
);
-- A line's previous text, kept when Capture (Drift) replaces it.
CREATE TABLE IF NOT EXISTS line_history (
  line_id INTEGER, raw_text TEXT, tts_text TEXT, text_hash TEXT, reason TEXT, capture_id INTEGER,
  changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
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
]

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
    return conn

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
-- Line flags for review, e.g. Capture text whose player name couldn't be re-tokenised.
CREATE TABLE IF NOT EXISTS line_issues (line_id INTEGER, issue TEXT, detail TEXT);
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
]


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
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS capture_record_key ON capture (record_key)")
    return conn

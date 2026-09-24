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
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(SCHEMA)
    return conn

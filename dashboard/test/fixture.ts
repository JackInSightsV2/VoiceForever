// A fixture pipeline DB built from the pipeline's own schema (pipeline/src/vo/db.py), so the two can't drift.
import { Database } from "bun:sqlite";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { localIso } from "../src/time";

const DB_PY = resolve(import.meta.dir, "../../pipeline/src/vo/db.py");

export function pipelineSchema(): string {
  const m = readFileSync(DB_PY, "utf8").match(/SCHEMA = """([\s\S]*?)"""/);
  if (!m) throw new Error("SCHEMA not found in db.py");
  return m[1];
}

export const NOW = new Date(2026, 8, 24, 3, 0, 0); // 03:00 local
export const at = (minutesFromNow: number) => localIso(new Date(NOW.getTime() + minutesFromNow * 60_000));
export const VOICE = "kokoro:am_michael";

/** Temp dir with vo.sqlite (WAL) and an audio dir. The returned `db` is the writable "pipeline" connection. */
export function makeFixture() {
  const dir = mkdtempSync(join(tmpdir(), "vo-dash-"));
  const path = join(dir, "vo.sqlite");
  const audio = join(dir, "audio");
  mkdirSync(join(audio, "823"), { recursive: true });
  const db = new Database(path, { create: true });
  db.run("PRAGMA journal_mode = WAL");
  db.exec(pipelineSchema());
  db.run("INSERT INTO npcs (id, name, race, gender) VALUES (823, 'Deputy Willem', 'human', 'male')");
  for (let i = 1; i <= 10; i++) {
    db.run(
      "INSERT INTO lines (id, npc_id, type, quest_id, raw_text, tts_text) VALUES (?, 823, 'quest_detail', ?, ?, ?)",
      [i, i, `Hello $N, line ${i}.`, `Hello friend, line ${i}.`],
    );
  }
  return { dir, path, audio, db };
}

type Status = "pending" | "running" | "done" | "quarantined" | "skipped";

export function job(db: Database, line: number, status: Status, updatedMin: number, extra: Record<string, any> = {}) {
  db.run(
    `INSERT INTO jobs (line_id, voice_id, tts_hash, status, attempts, tries, reason, wer, transcript, updated_at)
     VALUES (?, ?, 'h', ?, ?, ?, ?, ?, ?, ?)`,
    [line, VOICE, status, extra.attempts ?? 0, extra.tries ?? 1, extra.reason ?? null, extra.wer ?? null,
      extra.transcript ?? null, at(updatedMin)],
  );
}

export function audioRow(db: Database, audioDir: string, line: number, duration: number, status = "done") {
  const path = join(audioDir, "823", `${line}.ogg`);
  writeFileSync(path, "OggS");
  db.run("INSERT INTO audio (line_id, voice_id, path, duration_s, status) VALUES (?, ?, ?, ?, ?)", [
    line, VOICE, path, duration, status,
  ]);
  return path;
}

export function runRow(db: Database, r: Record<string, any>) {
  db.run(
    `INSERT INTO runs (id, pid, workers, started_at, last_heartbeat, ended_at, status, until, summary, error)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [r.id, r.pid ?? 4242, r.workers ?? 3, r.started_at, r.last_heartbeat, r.ended_at ?? null, r.status,
      r.until ?? null, r.summary ? JSON.stringify(r.summary) : null, r.error ?? null],
  );
}

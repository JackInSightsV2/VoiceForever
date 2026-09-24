// Read side: page snapshots built from the pipeline's SQLite. Every function takes a (read-only) Database.
import type { Database } from "bun:sqlite";
import { existsSync, realpathSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { localIso, parseLocal, secondsBetween } from "./time";

/** A run whose heartbeat is older than this is treated as dead (killed without cleanup). */
export const HEARTBEAT_STALE_S = 30;
/** Throughput is measured over this trailing window while a run is live. */
export const THROUGHPUT_WINDOW_S = 15 * 60;
export const JOB_STATUSES = ["done", "running", "pending", "quarantined", "skipped"] as const;

type Row = Record<string, any>;

export type RunState = "never" | "running" | "paused" | "lost" | "idle";

export interface RunRow {
  id: number;
  pid: number;
  workers: number;
  started_at: string;
  last_heartbeat: string;
  ended_at: string | null;
  status: string;
  until: string | null;
  summary: Row | null;
  error: string | null;
}

function runRow(r: Row): RunRow {
  return { ...(r as RunRow), summary: r.summary ? JSON.parse(r.summary) : null };
}

/** The latest run and whether it is live: status running/paused with a fresh heartbeat. */
export function runState(db: Database, now: Date): { state: RunState; run: RunRow | null } {
  const r = db.query("SELECT * FROM runs ORDER BY id DESC LIMIT 1").get() as Row | null;
  if (!r) return { state: "never", run: null };
  const run = runRow(r);
  const beat = parseLocal(run.last_heartbeat);
  const fresh = beat !== null && secondsBetween(beat, now) <= HEARTBEAT_STALE_S;
  if (run.status === "running" || run.status === "paused") {
    return { state: fresh ? (run.status as RunState) : "lost", run };
  }
  return { state: "idle", run };
}

export function jobCounts(db: Database): Record<string, number> & { total: number; stale: number } {
  const out: Record<string, number> = Object.fromEntries(JOB_STATUSES.map((s) => [s, 0]));
  for (const r of db.query("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").all() as Row[]) {
    out[r.status] = r.n;
  }
  const total = Object.values(out).reduce((a, b) => a + b, 0);
  const stale = (db.query("SELECT COUNT(*) AS n FROM audio WHERE status = 'stale'").get() as Row).n;
  return { ...out, total, stale };
}

/** Lines finished and audio produced between two times. */
export function throughput(db: Database, from: Date, to: Date) {
  const r = db
    .query(
      `SELECT COUNT(*) AS lines, COALESCE(SUM(a.duration_s), 0) AS audio_s FROM jobs j
       LEFT JOIN audio a ON a.line_id = j.line_id AND a.voice_id = j.voice_id AND a.status = 'done'
       WHERE j.status = 'done' AND j.updated_at >= ? AND j.updated_at <= ?`,
    )
    .get(localIso(from), localIso(to)) as Row;
  const secs = Math.max(1, secondsBetween(from, to));
  return {
    from: localIso(from),
    to: localIso(to),
    lines: r.lines as number,
    audio_s: r.audio_s as number,
    lines_per_min: (r.lines / secs) * 60,
    audio_hours_per_hour: r.audio_s / secs,
  };
}

export function overview(db: Database, now: Date = new Date()) {
  const { state, run } = runState(db, now);
  const counts = jobCounts(db);
  const live = state === "running" || state === "paused";

  let rate: ReturnType<typeof throughput> & { basis: string } | null = null;
  if (live && run) {
    const started = parseLocal(run.started_at)!;
    const from = new Date(Math.max(started.getTime(), now.getTime() - THROUGHPUT_WINDOW_S * 1000));
    rate = { ...throughput(db, from, now), basis: "trailing" };
  } else if (run?.ended_at) {
    rate = { ...throughput(db, parseLocal(run.started_at)!, parseLocal(run.ended_at)!), basis: "last run" };
  }

  const remaining = counts.pending + counts.running;
  let eta: { minutes: number; at: string; after_until: boolean } | null = null;
  if (live && rate && rate.lines_per_min > 0 && remaining > 0) {
    const minutes = remaining / rate.lines_per_min;
    const at = new Date(now.getTime() + minutes * 60_000);
    const until = parseLocal(run?.until);
    eta = { minutes, at: localIso(at), after_until: until !== null && at > until };
  }

  const workers = live
    ? (db
        .query(
          `SELECT j.line_id, j.voice_id, j.tries, j.attempts, j.updated_at AS since, l.type, l.npc_id, n.name AS npc
           FROM jobs j JOIN lines l ON l.id = j.line_id LEFT JOIN npcs n ON n.id = l.npc_id
           WHERE j.status = 'running' ORDER BY j.updated_at`,
        )
        .all() as Row[])
    : [];

  const errors = db
    .query(
      `SELECT j.line_id, j.voice_id, j.status, j.reason, j.attempts, j.updated_at, n.name AS npc
       FROM jobs j JOIN lines l ON l.id = j.line_id LEFT JOIN npcs n ON n.id = l.npc_id
       WHERE j.reason IS NOT NULL AND j.status IN ('pending', 'quarantined')
       ORDER BY j.updated_at DESC LIMIT 10`,
    )
    .all() as Row[];

  const history = (db.query("SELECT * FROM runs ORDER BY id DESC LIMIT 15").all() as Row[]).map(runRow);

  const queued = db
    .query("SELECT id, action, target, payload, created_at FROM review_actions WHERE consumed_at IS NULL ORDER BY id")
    .all() as Row[];

  return {
    now: localIso(now),
    state,
    run,
    counts,
    rate,
    eta,
    workers,
    errors,
    history,
    queued_actions: queued,
    run_errors: history.filter((r) => r.error).map((r) => ({ run: r.id, at: r.ended_at, error: r.error })),
  };
}

/** Map a stored audio path to its URL under /audio/, if it exists inside the audio root. */
export function audioUrl(audioRoot: string, path: string | null): string | null {
  if (!path || !existsSync(path)) return null;
  let root: string;
  try {
    root = realpathSync(audioRoot);
  } catch {
    return null;
  }
  const real = realpathSync(path);
  if (!real.startsWith(root + sep)) return null;
  return "/audio/" + relative(root, real).split(sep).map(encodeURIComponent).join("/");
}

export function quarantine(db: Database, audioRoot: string) {
  const rows = db
    .query(
      `SELECT j.line_id, j.voice_id, j.reason, j.wer, j.transcript, j.attempts, j.tries, j.updated_at,
              l.npc_id, l.type, l.quest_id, l.player_gender, l.raw_text, l.tts_text,
              n.name AS npc, n.race, n.gender, a.path AS audio_path, a.status AS audio_status
       FROM jobs j JOIN lines l ON l.id = j.line_id LEFT JOIN npcs n ON n.id = l.npc_id
       LEFT JOIN audio a ON a.line_id = j.line_id AND a.voice_id = j.voice_id
       WHERE j.status = 'quarantined' ORDER BY j.updated_at DESC, j.line_id`,
    )
    .all() as Row[];
  const queued = db
    .query(
      `SELECT id, action, target, payload FROM review_actions
       WHERE consumed_at IS NULL AND action IN ('retry-line', 'skip-line', 'edit-tts-text') ORDER BY id`,
    )
    .all() as Row[];
  return {
    lines: rows.map((r) => {
      // The last kept take: its audio row, else the file at the path vo run writes to.
      const fallback = join(resolve(audioRoot), String(r.npc_id ?? "narrator"), `${r.line_id}.ogg`);
      const url = audioUrl(audioRoot, r.audio_path) ?? audioUrl(audioRoot, fallback);
      const { audio_path, ...rest } = r;
      return {
        ...rest,
        audio_url: url,
        queued: queued
          .filter((q) => q.target === String(r.line_id))
          .filter((q) => {
            const voice = q.payload ? JSON.parse(q.payload).voice_id : undefined;
            return !voice || voice === r.voice_id;
          })
          .map((q) => q.action),
      };
    }),
  };
}

// Spot-check (#14): a weighted random voiced line, and the rating actions. The dashboard writes only review_actions:
// rate-line / flag-line / flag-voice are folded into `ratings` by vo run and vo voices (vo.ratings), which also queue
// a re-roll for an NPC Voice with REROLL_DOWNS lines rated down. Until folded, queued rating actions count too.
import type { Database } from "bun:sqlite";
import { ActionError } from "./actions";
import { zoneName, zoneNames } from "./coverage";
import { audioUrl } from "./queries";
import { localIso } from "./time";

type Row = Record<string, any>;

export const RATING_ACTIONS = ["rate-line", "flag-line", "flag-voice"] as const;
export const REROLL_DOWNS = 3; // vo.ratings.REROLL_DOWNS
export const MAX_NOTE = 500;
/** Weights: a line generated since the latest run started (or in the last day), a named NPC, and ASR WER. */
export const WEIGHT = { fresh: 4, named: 2, werPerUnit: 20, rated: 0.1 };
export const FRESH_S = 24 * 3600;

export interface Rating {
  line_id: number | null;
  voice_id: string | null;
  npc_id: number | null;
  rating: "up" | "down" | "flag";
  note: string | null;
  queued: boolean; // still a review_action, not folded yet
}

const parse = (s: string | null) => {
  try {
    const v = s ? JSON.parse(s) : {};
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  } catch {
    return {};
  }
};

/** Every rating, folded and queued, oldest first. `where` narrows by line / voice / NPC. */
export function ratings(db: Database, where: { line?: number; voice?: string; npc?: number } = {}): Rating[] {
  const folded = (db.query("SELECT line_id, voice_id, npc_id, rating, note FROM ratings ORDER BY id").all() as Row[]).map(
    (r) => ({ ...r, queued: false }) as Rating,
  );
  const queued = (
    db
      .query(
        `SELECT r.action, r.target, r.payload, l.npc_id FROM review_actions r
         LEFT JOIN lines l ON r.action != 'flag-voice' AND l.id = CAST(r.target AS INTEGER)
         WHERE r.consumed_at IS NULL AND r.action IN ('rate-line', 'flag-line', 'flag-voice') ORDER BY r.id`,
      )
      .all() as Row[]
  ).map((r) => {
    const p = parse(r.payload);
    const voice = typeof p.voice_id === "string" ? p.voice_id : null;
    if (r.action === "flag-voice") {
      const npc = Number(r.target);
      const own = db.query("SELECT voice_id FROM voices WHERE npc_id = ?").get(npc) as Row | null;
      return { line_id: null, voice_id: voice ?? own?.voice_id ?? null, npc_id: npc, rating: "flag", note: p.note ?? null, queued: true } as Rating;
    }
    return {
      line_id: Number(r.target), voice_id: voice, npc_id: r.npc_id ?? null,
      rating: r.action === "flag-line" ? "flag" : p.rating, note: p.note ?? null, queued: true,
    } as Rating;
  });
  return [...folded, ...queued].filter(
    (r) =>
      (where.line === undefined || r.line_id === where.line) &&
      (where.voice === undefined || r.voice_id === where.voice) &&
      (where.npc === undefined || r.npc_id === where.npc),
  );
}

/** A voice's tally: lines whose latest thumbs rating is up / down, and flags. */
export function tally(list: Rating[]) {
  const latest = new Map<string, "up" | "down">();
  let flags = 0;
  for (const r of list) {
    if (r.rating === "flag") flags++;
    else if (r.rating === "up" || r.rating === "down") latest.set(`${r.line_id}|${r.voice_id}`, r.rating);
  }
  const vals = [...latest.values()];
  return { up: vals.filter((v) => v === "up").length, down: vals.filter((v) => v === "down").length, flags };
}

export interface SpotOptions {
  exclude?: number[];
  rng?: () => number;
  now?: Date;
}

/** Candidates: every done line with done audio, weighted. Exported for tests. */
export function candidates(db: Database, now: Date = new Date()) {
  const run = db.query("SELECT started_at FROM runs ORDER BY id DESC LIMIT 1").get() as Row | null;
  const since = run?.started_at ?? localIso(new Date(now.getTime() - FRESH_S * 1000));
  const rated = new Set(
    (
      db
        .query(
          `SELECT line_id FROM ratings WHERE line_id IS NOT NULL UNION SELECT CAST(target AS INTEGER) FROM review_actions
           WHERE consumed_at IS NULL AND action IN ('rate-line', 'flag-line')`,
        )
        .all() as Row[]
    ).map((r) => r.line_id),
  );
  const rows = db
    .query(
      `SELECT j.line_id, j.voice_id, j.wer, j.updated_at, COALESCE(n.is_named, 0) AS named
       FROM jobs j JOIN audio a ON a.line_id = j.line_id AND a.voice_id = j.voice_id AND a.status = 'done'
       LEFT JOIN lines l ON l.id = j.line_id LEFT JOIN npcs n ON n.id = l.npc_id
       WHERE j.status = 'done'`,
    )
    .all() as Row[];
  return rows.map((r) => {
    const why: string[] = [];
    let w = 1;
    if (r.updated_at && r.updated_at >= since) {
      w *= WEIGHT.fresh;
      why.push("new");
    }
    if (r.named) {
      w *= WEIGHT.named;
      why.push("named");
    }
    if (r.wer != null && r.wer > 0) {
      w *= 1 + WEIGHT.werPerUnit * Math.min(r.wer, 1);
      why.push(`WER ${r.wer.toFixed(2)}`);
    }
    if (rated.has(r.line_id)) {
      w *= WEIGHT.rated;
      why.push("rated before");
    }
    return { line_id: r.line_id as number, voice_id: r.voice_id as string, weight: w, why };
  });
}

/** Pick one weighted line (skipping `exclude`), with everything the player shows. */
export function spotCheck(db: Database, audioRoot: string, opts: SpotOptions = {}) {
  const rng = opts.rng ?? Math.random;
  const exclude = new Set(opts.exclude ?? []);
  const pool = candidates(db, opts.now).filter((c) => !exclude.has(c.line_id));
  const all = ratings(db);
  const checked = new Set(all.filter((r) => r.line_id != null).map((r) => r.line_id)).size;
  const stats = { voiced: pool.length + exclude.size, checked, ...tally(all) };
  let total = pool.reduce((a, c) => a + c.weight, 0);
  while (pool.length) {
    let x = rng() * total;
    let i = 0;
    while (i < pool.length - 1 && x >= pool[i].weight) x -= pool[i++].weight;
    const pick = pool[i];
    const line = spotLine(db, audioRoot, pick.line_id, pick.voice_id);
    if (line?.audio_url) return { line: { ...line, why: pick.why }, stats };
    total -= pick.weight; // audio file missing: try another
    pool.splice(i, 1);
  }
  return { line: null, stats };
}

/** One line in one voice, for the player: text, NPC, zone, ASR, audio, ratings of the line and its voice. */
export function spotLine(db: Database, audioRoot: string, lineId: number, voiceId: string) {
  const r = db
    .query(
      `SELECT j.line_id, j.voice_id, j.wer, j.transcript, j.tries, j.updated_at, l.type, l.quest_id, l.player_gender,
              l.raw_text, l.tts_text, l.npc_id, n.name AS npc, n.subname, n.race, n.gender, n.role, n.is_named,
              a.path AS audio_path, a.duration_s, v.voice_id AS npc_voice
       FROM jobs j JOIN lines l ON l.id = j.line_id LEFT JOIN npcs n ON n.id = l.npc_id
       LEFT JOIN audio a ON a.line_id = j.line_id AND a.voice_id = j.voice_id AND a.status = 'done'
       LEFT JOIN voices v ON v.npc_id = l.npc_id
       WHERE j.line_id = ? AND j.voice_id = ?`,
    )
    .get(lineId, voiceId) as Row | null;
  if (!r) return null;
  const zone = db
    .query(
      `SELECT COALESCE((SELECT lp.zone FROM line_packs lp WHERE lp.line_id = ?),
         (SELECT zone FROM spawns WHERE npc_id = ? AND zone IS NOT NULL GROUP BY zone ORDER BY COUNT(*) DESC, zone LIMIT 1)) AS zone`,
    )
    .get(lineId, r.npc_id) as Row;
  const { audio_path, npc_voice, ...rest } = r;
  const lineRatings = ratings(db, { line: lineId, voice: voiceId });
  return {
    ...rest,
    zone: zone.zone,
    zone_name: zoneName(zoneNames(db), zone.zone),
    audio_url: audioUrl(audioRoot, audio_path),
    own_voice: npc_voice === voiceId, // an NPC Voice (re-rollable), not a shared Archetype anchor
    rated: lineRatings.length ? lineRatings[lineRatings.length - 1].rating : null,
    voice: tally(ratings(db, { voice: voiceId })),
  };
}

function note(payload: Record<string, unknown>) {
  const n = payload.note ?? "";
  if (typeof n !== "string") throw new ActionError("note must be text");
  if (n.trim().length > MAX_NOTE) throw new ActionError(`note is over ${MAX_NOTE} characters`);
  return n.trim();
}

/** rate-line (line, voice_id, rating up|down), flag-line (line, voice_id, note?), flag-voice (NPC, note?). */
export function validateRating(read: Database, action: string, target: unknown, payload: Record<string, unknown>) {
  if (action === "flag-voice") {
    const npc = Number(target);
    if (!Number.isInteger(npc) || npc <= 0) throw new ActionError("target must be an NPC id");
    if (!read.query("SELECT 1 FROM npcs WHERE id = ?").get(npc)) throw new ActionError(`no NPC ${npc}`, 404);
    const out: Record<string, unknown> = {};
    if (payload.voice_id !== undefined) {
      if (typeof payload.voice_id !== "string") throw new ActionError("voice_id must be a string");
      out.voice_id = payload.voice_id;
    }
    const n = note(payload);
    if (n) out.note = n;
    return { action, target: String(npc), payload: Object.keys(out).length ? JSON.stringify(out) : null };
  }
  const line = Number(target);
  if (!Number.isInteger(line) || line <= 0) throw new ActionError("target must be a line id");
  const voice = payload.voice_id;
  if (typeof voice !== "string" || !voice) throw new ActionError("voice_id is required");
  if (!read.query("SELECT 1 FROM jobs WHERE line_id = ? AND voice_id = ?").get(line, voice)) {
    throw new ActionError(`no job for line ${line} [${voice}]`, 404);
  }
  const out: Record<string, unknown> = { voice_id: voice };
  if (action === "rate-line") {
    if (payload.rating !== "up" && payload.rating !== "down") throw new ActionError("rating must be up or down");
    out.rating = payload.rating;
  } else {
    const n = note(payload);
    if (n) out.note = n;
  }
  return { action, target: String(line), payload: JSON.stringify(out) };
}

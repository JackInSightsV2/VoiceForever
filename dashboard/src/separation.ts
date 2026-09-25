// The Separation page (#13): the closest same-Archetype Neighbour pairs by NPC anchor similarity (`vo voices` stores
// it on `neighbours.sim`), both anchors and a sample line each, the leftovers `vo voices` couldn't separate, and the
// `reroll-voice` action it consumes.
import type { Database } from "bun:sqlite";
import { ActionError } from "./actions";
import { audioUrl } from "./queries";

type Row = Record<string, any>;

export const SEPARATION_ACTIONS = ["reroll-voice"] as const;
export const DEFAULT_PAIRS = 60;

function side(r: Row, p: "a" | "b", voicesRoot: string, audioRoot: string, queued: Set<string>) {
  const id = r[p];
  return {
    npc_id: id,
    name: r[`${p}_name`],
    subname: r[`${p}_subname`],
    is_named: !!r[`${p}_named`],
    voice_id: r[`${p}_voice`],
    prompt: r[`${p}_prompt`],
    anchor_url: audioUrl(voicesRoot, r[`${p}_clip`], "/voices/"),
    sample: r[`${p}_line`]
      ? { line_id: r[`${p}_line`], text: r[`${p}_text`], url: audioUrl(audioRoot, r[`${p}_audio`]) }
      : null,
    roll: r[`${p}_roll`],
    status: r[`${p}_status`],
    issue: r[`${p}_issue`],
    queued: queued.has(String(id)),
  };
}

const SIDE = (p: "a" | "b") => `
  n_${p}.name AS ${p}_name, n_${p}.subname AS ${p}_subname, n_${p}.is_named AS ${p}_named,
  v_${p}.voice_id AS ${p}_voice, v_${p}.prompt AS ${p}_prompt, v_${p}.ref_clip AS ${p}_clip,
  b_${p}.roll AS ${p}_roll, b_${p}.status AS ${p}_status, b_${p}.issue AS ${p}_issue,
  (SELECT au.line_id FROM audio au WHERE au.voice_id = v_${p}.voice_id AND au.status = 'done'
     ORDER BY au.line_id LIMIT 1) AS ${p}_line`;

/** Page snapshot: stats, the `limit` most similar same-Archetype Neighbour pairs, the leftovers, queued re-rolls. */
export function separation(db: Database, voicesRoot: string, audioRoot: string, limit = DEFAULT_PAIRS) {
  const pairs = db
    .query(
      `SELECT x.*, la.tts_text AS a_text, aa.path AS a_audio, lb.tts_text AS b_text, ab.path AS b_audio FROM (
         SELECT g.a, g.b, g.reason, g.distance, g.sim, v_a.archetype, ${SIDE("a")}, ${SIDE("b")}
         FROM neighbours g
         JOIN voices v_a ON v_a.npc_id = g.a JOIN voices v_b ON v_b.npc_id = g.b AND v_b.archetype = v_a.archetype
         JOIN npcs n_a ON n_a.id = g.a JOIN npcs n_b ON n_b.id = g.b
         LEFT JOIN voice_builds b_a ON b_a.npc_id = g.a LEFT JOIN voice_builds b_b ON b_b.npc_id = g.b
         WHERE g.sim IS NOT NULL ORDER BY g.sim DESC, g.a, g.b LIMIT ?) x
       LEFT JOIN lines la ON la.id = x.a_line LEFT JOIN audio aa ON aa.line_id = x.a_line AND aa.voice_id = x.a_voice
       LEFT JOIN lines lb ON lb.id = x.b_line LEFT JOIN audio ab ON ab.line_id = x.b_line AND ab.voice_id = x.b_voice`,
    )
    .all(limit) as Row[];
  const queued = new Set(
    (
      db
        .query(`SELECT target FROM review_actions WHERE consumed_at IS NULL AND action = 'reroll-voice'`)
        .all() as Row[]
    ).map((r) => String(r.target)),
  );
  const leftovers = db
    .query(
      `SELECT b.npc_id, n.name, n.subname, v.archetype, b.issue, b.detail, b.neighbour, b.neighbour_sim,
              b.archetype_sim, b.roll, v.ref_clip
       FROM voice_builds b JOIN npcs n ON n.id = b.npc_id LEFT JOIN voices v ON v.npc_id = b.npc_id
       WHERE b.status = 'leftover' ORDER BY b.neighbour_sim DESC, b.npc_id`,
    )
    .all() as Row[];
  const stats = db
    .query(
      `SELECT (SELECT COUNT(*) FROM neighbours) AS pairs,
              (SELECT COUNT(*) FROM neighbours WHERE sim IS NOT NULL) AS voiced_pairs,
              (SELECT COUNT(*) FROM voices WHERE voice_id LIKE '%#%') AS voices,
              (SELECT COUNT(*) FROM voice_builds WHERE status = 'leftover') AS leftovers,
              (SELECT COUNT(DISTINCT n) FROM (SELECT a AS n FROM neighbours UNION SELECT b FROM neighbours)) AS npcs`,
    )
    .get() as Row;
  return {
    stats: { ...stats, per_npc: stats.npcs ? (2 * stats.pairs) / stats.npcs : 0 },
    pairs: pairs.map((r) => ({
      a: side(r, "a", voicesRoot, audioRoot, queued),
      b: side(r, "b", voicesRoot, audioRoot, queued),
      archetype: r.archetype,
      reason: r.reason,
      distance: r.distance,
      sim: r.sim,
    })),
    leftovers: leftovers.map(({ ref_clip, ...l }) => ({
      ...l,
      anchor_url: audioUrl(voicesRoot, ref_clip, "/voices/"),
      queued: queued.has(String(l.npc_id)),
    })),
    queued: [...queued].map(Number),
  };
}

/** `reroll-voice`: target is an NPC that has an NPC Voice (a voice_builds row). */
export function validateSeparation(read: Database, action: string, target: unknown) {
  const npc = Number(target);
  if (!Number.isInteger(npc) || npc <= 0) throw new ActionError("target must be an NPC id");
  if (!read.query("SELECT 1 FROM voice_builds WHERE npc_id = ?").get(npc)) {
    throw new ActionError(`NPC ${npc} has no NPC Voice to re-roll`, 404);
  }
  const dup = read
    .query("SELECT 1 FROM review_actions WHERE consumed_at IS NULL AND action = ? AND target = ?")
    .get(action, String(npc));
  if (dup) throw new ActionError(`a re-roll of NPC ${npc} is already queued`, 409);
  return { action, target: String(npc), payload: null };
}

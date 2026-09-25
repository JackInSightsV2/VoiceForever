// The NPC browser (#14): search NPCs by name, zone, race / Archetype and role; one NPC's voice (its own NPC anchor, or
// the Archetype anchor it speaks with until `vo voices` builds one) and every line with its player, status and ASR.
// Actions: flag-voice (vo.ratings), reroll-voice (vo voices), retry-line (vo run) — all review_actions.
import type { Database } from "bun:sqlite";
import { LINE_CTES, zoneName, zoneNames } from "./coverage";
import { audioUrl } from "./queries";
import { ratings, tally } from "./spotcheck";

type Row = Record<string, any>;

export const SEARCH_LIMIT = 200;

export interface Roots {
  audio: string;
  voices: string;
  candidates: string;
}

/** Archetype id per "<race> <gender>" label, from the Approval Gate's archetypes (races JSON). */
function archetypeByLabel(db: Database) {
  const out = new Map<string, string>();
  for (const a of db.query("SELECT id, races FROM archetypes WHERE kind != 'narrator'").all() as Row[]) {
    try {
      for (const label of Object.keys(JSON.parse(a.races ?? "{}"))) out.set(label.toLowerCase(), a.id);
    } catch {}
  }
  return out;
}

/** Every NPC with its main zone, Archetype, voice state and line counts. */
function allNpcs(db: Database) {
  const arch = archetypeByLabel(db);
  const names = zoneNames(db);
  const rows = db
    .query(
      `WITH ${LINE_CTES},
       counts AS (SELECT l.npc_id, COUNT(*) AS lines, COALESCE(SUM(j.status = 'done'), 0) AS done,
                         COALESCE(SUM(j.status = 'quarantined'), 0) AS quarantined
                  FROM lines l LEFT JOIN jobs j ON j.line_id = l.id WHERE l.npc_id IS NOT NULL GROUP BY l.npc_id)
       SELECT n.id, n.name, n.subname, n.race, n.gender, n.role, n.is_named, n.source, mz.zone,
              v.archetype AS voice_archetype, v.voice_id, b.status AS build_status,
              COALESCE(c.lines, 0) AS lines, COALESCE(c.done, 0) AS done, COALESCE(c.quarantined, 0) AS quarantined
       FROM npcs n LEFT JOIN main_zone mz ON mz.npc_id = n.id LEFT JOIN voices v ON v.npc_id = n.id
       LEFT JOIN voice_builds b ON b.npc_id = n.id LEFT JOIN counts c ON c.npc_id = n.id`,
    )
    .all() as Row[];
  return rows.map(({ voice_archetype, ...r }) => ({
    ...r,
    zone_name: zoneName(names, r.zone),
    archetype: voice_archetype ?? arch.get(`${r.race ?? ""} ${r.gender ?? ""}`.toLowerCase()) ?? null,
    own_voice: typeof r.voice_id === "string" && r.voice_id.includes("#"),
  }));
}

/** Search: q (name, subname or id), zone (id or "none"), race, archetype, role, gender. Named NPCs, then most lines. */
export function npcSearch(db: Database, params: URLSearchParams = new URLSearchParams()) {
  const all = allNpcs(db);
  const q = (params.get("q") ?? "").trim().toLowerCase();
  const f = (k: string) => params.get(k) || null;
  const zone = f("zone");
  const hits = all.filter(
    (n) =>
      (!q || String(n.id) === q || (n.name ?? "").toLowerCase().includes(q) || (n.subname ?? "").toLowerCase().includes(q)) &&
      (zone === null || (zone === "none" ? n.zone == null : n.zone === Number(zone))) &&
      (!f("race") || n.race === f("race")) &&
      (!f("archetype") || n.archetype === f("archetype")) &&
      (!f("role") || n.role === f("role")) &&
      (!f("gender") || n.gender === f("gender")),
  );
  hits.sort((a, b) => b.is_named - a.is_named || b.lines - a.lines || String(a.name).localeCompare(String(b.name)));
  const uniq = (xs: any[]) => [...new Set(xs.filter((x) => x != null && x !== ""))].sort();
  const zones = new Map<number, string>();
  for (const n of all) if (n.zone != null) zones.set(n.zone, n.zone_name);
  return {
    total: hits.length,
    npcs: hits.slice(0, SEARCH_LIMIT),
    facets: {
      zones: [...zones].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name)),
      races: uniq(all.map((n) => n.race)),
      archetypes: uniq(all.map((n) => n.archetype)),
      roles: uniq(all.map((n) => n.role)),
    },
  };
}

/** One NPC: details, its voice and anchor, and every line with player, status, ASR and ratings. */
export function npcDetail(db: Database, id: number, roots: Roots) {
  const n = allNpcs(db).find((x) => x.id === id);
  if (!n) return null;
  const names = zoneNames(db);
  const zones = (
    db
      .query("SELECT zone, COUNT(*) AS spawns FROM spawns WHERE npc_id = ? AND zone IS NOT NULL GROUP BY zone ORDER BY spawns DESC, zone")
      .all(id) as Row[]
  ).map((z) => ({ ...z, name: zoneName(names, z.zone) }));
  const v = db.query("SELECT * FROM voices WHERE npc_id = ?").get(id) as Row | null;
  const build = db
    .query("SELECT roll, attempt, strategy, archetype_sim, neighbour_sim, neighbour, status, issue, detail, updated_at FROM voice_builds WHERE npc_id = ?")
    .get(id) as Row | null;
  // The Archetype anchor: the approved Candidate's clip (under /candidates/).
  const arch = n.archetype
    ? (db
        .query(
          `SELECT a.id, a.label, a.approved, c.path FROM archetypes a LEFT JOIN candidates c ON c.id = a.approved WHERE a.id = ?`,
        )
        .get(n.archetype) as Row | null)
    : null;
  const lines = db
    .query(
      `SELECT l.id AS line_id, l.type, l.quest_id, l.player_gender, l.raw_text, l.tts_text, l.source,
              j.voice_id, j.status, j.wer, j.transcript, j.reason, j.attempts, j.tries, j.updated_at,
              a.path AS audio_path, a.status AS audio_status
       FROM lines l LEFT JOIN jobs j ON j.line_id = l.id
       LEFT JOIN audio a ON a.line_id = l.id AND a.voice_id = j.voice_id
       WHERE l.npc_id = ? ORDER BY l.quest_id IS NULL, l.quest_id, l.id`,
    )
    .all(id) as Row[];
  const queued = db
    .query(
      `SELECT id, action, target, payload FROM review_actions WHERE consumed_at IS NULL AND (
         (action IN ('reroll-voice', 'flag-voice') AND target = ?) OR
         (action IN ('retry-line', 'rate-line', 'flag-line') AND CAST(target AS INTEGER) IN (SELECT id FROM lines WHERE npc_id = ?)))
       ORDER BY id`,
    )
    .all(String(id), id) as Row[];
  const all = ratings(db, { npc: id });
  const voiceId = v?.voice_id ?? lines.find((l) => l.voice_id)?.voice_id ?? null;
  return {
    npc: { ...n, zones },
    voice: {
      voice_id: voiceId,
      own: n.own_voice,
      prompt: v?.prompt ?? null,
      anchor_url: n.own_voice ? audioUrl(roots.voices, v?.ref_clip ?? null, "/voices/") : null,
      archetype: arch ? { id: arch.id, label: arch.label, approved: arch.approved, anchor_url: audioUrl(roots.candidates, arch.path ?? null, "/candidates/") } : null,
      build,
      can_reroll: build !== null,
      ratings: tally(voiceId ? ratings(db, { voice: voiceId }) : []),
      flags: all.filter((r) => r.line_id == null).map((r) => ({ note: r.note, queued: r.queued })),
    },
    issues: db.query("SELECT issue, detail FROM npc_issues WHERE npc_id = ?").all(id),
    lines: lines.map(({ audio_path, ...l }) => {
      const mine = all.filter((r) => r.line_id === l.line_id && (!l.voice_id || r.voice_id === l.voice_id));
      const thumbs = mine.filter((r) => r.rating !== "flag");
      return {
        ...l,
        status: l.status ?? "unqueued",
        audio_url: audioUrl(roots.audio, audio_path),
        rating: thumbs.length ? thumbs[thumbs.length - 1].rating : null,
        flags: mine.filter((r) => r.rating === "flag").map((r) => r.note),
        queued: queued.filter((q) => q.target === String(l.line_id) && q.action !== "flag-voice" && q.action !== "reroll-voice").map((q) => q.action),
      };
    }),
    queued: queued.filter((q) => q.action === "reroll-voice" || q.action === "flag-voice").map((q) => q.action),
  };
}

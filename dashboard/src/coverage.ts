// The Coverage page (#14): per-zone and per-Voice-Pack line counts (total, done, failed, quarantined, % complete,
// spot-checked), drilling down zone -> NPC (-> the NPC browser for its lines). Each line's zone and pack come from
// `line_packs` (vo.coverage, written by vo extract/package/run with the world DB); a line not in it falls back to its
// NPC's main spawn zone and "Unassigned". Zone names come from `zones`.
import type { Database } from "bun:sqlite";

type Row = Record<string, any>;

export const UNKNOWN_ZONE = "Unknown zone";
export const UNASSIGNED = "Unassigned";

/** SQL CTEs: `main_zone` (NPC -> zone with most spawns, ties to the lowest id), `line_zone` (line -> zone, pack),
 * `rated` (lines spot-checked: folded ratings, or rating actions still queued). */
export const LINE_CTES = `
  main_zone AS (
    SELECT npc_id, zone FROM (
      SELECT npc_id, zone, ROW_NUMBER() OVER (PARTITION BY npc_id ORDER BY COUNT(*) DESC, zone) AS rn
      FROM spawns WHERE zone IS NOT NULL GROUP BY npc_id, zone)
    WHERE rn = 1),
  line_zone AS (
    SELECT l.id AS line_id, l.npc_id, CASE WHEN lp.line_id IS NULL THEN mz.zone ELSE lp.zone END AS zone,
           COALESCE(lp.pack, '${UNASSIGNED}') AS pack
    FROM lines l LEFT JOIN line_packs lp ON lp.line_id = l.id LEFT JOIN main_zone mz ON mz.npc_id = l.npc_id),
  rated AS (
    SELECT line_id FROM ratings WHERE line_id IS NOT NULL
    UNION SELECT CAST(target AS INTEGER) FROM review_actions
    WHERE consumed_at IS NULL AND action IN ('rate-line', 'flag-line'))`;

const COUNTS = `COUNT(*) AS total,
  COALESCE(SUM(j.status = 'done'), 0) AS done,
  COALESCE(SUM(j.status = 'pending' AND j.attempts > 0), 0) AS failed,
  COALESCE(SUM(j.status = 'quarantined'), 0) AS quarantined,
  COALESCE(SUM(j.status = 'skipped'), 0) AS skipped,
  COALESCE(SUM(lz.line_id IN (SELECT line_id FROM rated)), 0) AS checked`;

export function zoneNames(db: Database): Map<number, string> {
  return new Map((db.query("SELECT id, name FROM zones").all() as Row[]).map((r) => [r.id, r.name]));
}

export const zoneName = (names: Map<number, string>, zone: number | null) =>
  zone == null ? UNKNOWN_ZONE : (names.get(zone) ?? `Zone ${zone}`);

function withPct<T extends Row>(r: T) {
  return { ...r, pct: r.total ? (100 * r.done) / r.total : 0 };
}

/** Per-zone rows (id, name and counts), most lines first. */
export function zoneRows(db: Database) {
  const names = zoneNames(db);
  const rows = db
    .query(
      `WITH ${LINE_CTES} SELECT lz.zone, ${COUNTS} FROM line_zone lz LEFT JOIN jobs j ON j.line_id = lz.line_id
       GROUP BY lz.zone`,
    )
    .all() as Row[];
  return rows
    .map((r) => withPct({ zone: r.zone, name: zoneName(names, r.zone), ...r }))
    .sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));
}

export function packRows(db: Database) {
  return (
    db
      .query(
        `WITH ${LINE_CTES} SELECT lz.pack, ${COUNTS} FROM line_zone lz LEFT JOIN jobs j ON j.line_id = lz.line_id
         GROUP BY lz.pack ORDER BY lz.pack`,
      )
      .all() as Row[]
  ).map(withPct);
}

/** One zone's NPCs (and the Narrator, npc_id null) with their counts in that zone. `zone` null: lines with no zone. */
export function zoneNpcs(db: Database, zone: number | null) {
  const rows = db
    .query(
      `WITH ${LINE_CTES} SELECT lz.npc_id, n.name, n.subname, n.race, n.gender, n.role, n.is_named, ${COUNTS}
       FROM line_zone lz LEFT JOIN jobs j ON j.line_id = lz.line_id LEFT JOIN npcs n ON n.id = lz.npc_id
       WHERE lz.zone IS ? GROUP BY lz.npc_id ORDER BY total DESC, lz.npc_id IS NULL, n.name`,
    )
    .all(zone) as Row[];
  return rows.map(withPct);
}

export function coverage(db: Database, params: URLSearchParams = new URLSearchParams()) {
  const z = params.get("zone");
  const out: Row = {
    zones: zoneRows(db),
    packs: packRows(db),
    assigned: (db.query("SELECT COUNT(*) AS n FROM line_packs").get() as Row).n,
  };
  if (z !== null) {
    const zone = z === "none" || z === "" ? null : Number(z);
    out.zone = { zone, name: zoneName(zoneNames(db), zone), npcs: zoneNpcs(db, zone) };
  }
  return out;
}

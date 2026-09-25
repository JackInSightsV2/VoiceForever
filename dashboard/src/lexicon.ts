// The Approval page's Lexicon section (#12): the top names by line count, each with its drafted spelling and a
// rendered sample, to accept or correct. Actions are review_actions consumed by `vo prepare` (vo.lexicon), which
// writes the read-only lexicon.json once every top name is reviewed.
import type { Database } from "bun:sqlite";

export const LEXICON_TOP = 300; // vo.lexicon.TOP
export const LEXICON_ACTIONS = ["accept-lexicon", "correct-lexicon"] as const;
export const MAX_SPELLING = 120; // vo.lexicon.MAX_SPELLING
const REVIEWED = ["accepted", "corrected"];

type Row = Record<string, any>;

export class LexiconActionError extends Error {
  constructor(
    message: string,
    public status = 400,
  ) {
    super(message);
  }
}

function hasTable(db: Database) {
  return !!db.query("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'lexicon'").get();
}

/** Top names with their spelling, sample and queued actions; progress; how many names are automatic. */
export function lexiconSnapshot(db: Database, url: (p: string | null) => string | null) {
  if (!hasTable(db)) return { progress: { reviewed: 0, total: 0, queued: 0 }, complete: true, auto: 0, names: [] };
  const rows = db.query("SELECT * FROM lexicon WHERE rank <= ? ORDER BY rank").all(LEXICON_TOP) as Row[];
  const auto = (db.query("SELECT COUNT(*) AS n FROM lexicon WHERE status = 'auto'").get() as Row).n as number;
  const queued = db
    .query(
      `SELECT id, action, target, payload FROM review_actions WHERE consumed_at IS NULL
       AND action IN (${LEXICON_ACTIONS.map(() => "?").join(",")}) ORDER BY id`,
    )
    .all(...LEXICON_ACTIONS) as Row[];
  const queuedFor = new Map<string, Row[]>();
  for (const q of queued) {
    let spelling: string | null = null;
    try {
      spelling = q.payload ? (JSON.parse(q.payload).spelling ?? null) : null;
    } catch {}
    const list = queuedFor.get(q.target) ?? [];
    list.push({ id: q.id, action: q.action, spelling });
    queuedFor.set(q.target, list);
  }
  const names = rows.map((r) => ({
    name: r.name,
    rank: r.rank,
    lines: r.lines,
    npc: !!r.npc,
    zone: !!r.zone,
    draft: r.draft,
    spelling: r.spelling,
    status: r.status,
    reviewed_at: r.reviewed_at,
    sample: {
      text: r.sample_text,
      spoken: r.sample_spoken,
      spelling: r.sample_spelling,
      voice: r.sample_voice,
      url: url(r.sample_path),
      stale: r.sample_spelling !== r.spelling, // re-rendered by the next vo prepare
    },
    queued: queuedFor.get(r.name) ?? [],
  }));
  const reviewed = names.filter((n) => REVIEWED.includes(n.status)).length;
  const queuedNames = names.filter((n) => !REVIEWED.includes(n.status) && n.queued.length).length;
  return {
    progress: { reviewed, total: names.length, queued: queuedNames },
    complete: reviewed === names.length,
    auto,
    names,
  };
}

/** Validate an accept-lexicon / correct-lexicon request; returns the review_actions row. */
export function validateLexiconAction(read: Database, action: string, target: unknown, payload: Record<string, unknown>) {
  const name = typeof target === "string" ? target : "";
  const r = hasTable(read)
    ? (read.query("SELECT rank FROM lexicon WHERE name = ?").get(name) as Row | null)
    : null;
  if (!r) throw new LexiconActionError(`no Lexicon name ${JSON.stringify(target)}`, 404);
  if (r.rank === null || r.rank > LEXICON_TOP) {
    throw new LexiconActionError(`${name} is not a top name: its spelling is automatic`, 409);
  }
  const raw = payload.spelling;
  if (raw !== undefined && typeof raw !== "string") throw new LexiconActionError("spelling must be text");
  const spelling = typeof raw === "string" ? raw.trim() : "";
  if (action === "correct-lexicon" && !spelling) throw new LexiconActionError("a correction needs a spelling");
  if (spelling.length > MAX_SPELLING) throw new LexiconActionError(`spelling is over ${MAX_SPELLING} characters`);
  if (/[\r\n]/.test(spelling)) throw new LexiconActionError("spelling must be one line");
  return { action, target: name, payload: spelling ? JSON.stringify({ spelling }) : null };
}

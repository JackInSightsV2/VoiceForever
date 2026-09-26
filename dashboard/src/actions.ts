// Write side: the dashboard's only writes are review_actions rows, consumed by `vo run`. It never touches audio.
import type { Database } from "bun:sqlite";
import { LEXICON_ACTIONS, LexiconActionError, validateLexiconAction } from "./lexicon";
import { APPROVAL_ACTIONS, MAX_BASE_VOICES, approvedAfterQueue, runState } from "./queries";
import { SEPARATION_ACTIONS, validateSeparation } from "./separation";
import { RATING_ACTIONS, validateRating } from "./spotcheck";

export const LINE_ACTIONS = ["retry-line", "skip-line", "edit-tts-text"] as const;
export const RUN_ACTIONS = ["pause-run", "resume-run", "set-until"] as const;
export { APPROVAL_ACTIONS };
export const MAX_TTS_TEXT = 4000;
export const MAX_NOTE = 500;

export class ActionError extends Error {
  constructor(
    message: string,
    public status = 400,
  ) {
    super(message);
  }
}

export interface ActionRequest {
  action: string;
  target?: string | number;
  payload?: Record<string, unknown>;
}

/** Validate a request against the current state and return the row to insert. */
export function validate(read: Database, req: ActionRequest, now: Date = new Date()) {
  const { action } = req;
  const payload = req.payload ?? {};
  if (typeof payload !== "object" || Array.isArray(payload)) throw new ActionError("payload must be an object");

  if ((LINE_ACTIONS as readonly string[]).includes(action)) {
    const line = Number(req.target);
    if (!Number.isInteger(line) || line <= 0) throw new ActionError("target must be a line id");
    const voice = payload.voice_id;
    if (voice !== undefined && typeof voice !== "string") throw new ActionError("voice_id must be a string");
    const job = read
      .query(`SELECT 1 FROM jobs WHERE line_id = ? ${voice ? "AND voice_id = ?" : ""} LIMIT 1`)
      .get(...((voice ? [line, voice] : [line]) as [number, string]));
    if (!job) throw new ActionError(`no job for line ${line}${voice ? ` [${voice}]` : ""}`, 404);
    const out: Record<string, unknown> = {};
    if (action === "edit-tts-text") {
      const text = typeof payload.tts_text === "string" ? payload.tts_text.trim() : "";
      if (!text) throw new ActionError("tts_text must be non-empty text");
      if (text.length > MAX_TTS_TEXT) throw new ActionError(`tts_text is over ${MAX_TTS_TEXT} characters`);
      out.tts_text = text;
    } else {
      if (voice) out.voice_id = voice;
      if (action === "skip-line" && typeof payload.reason === "string" && payload.reason.trim()) {
        out.reason = payload.reason.trim();
      }
    }
    return { action, target: String(line), payload: Object.keys(out).length ? JSON.stringify(out) : null };
  }

  if ((RUN_ACTIONS as readonly string[]).includes(action)) {
    const { state, run } = runState(read, now);
    if (state !== "running" && state !== "paused") throw new ActionError("no live vo run to control", 409);
    const target = `run:${run!.id}`;
    if (req.target !== undefined && String(req.target) !== target) {
      throw new ActionError(`run ${req.target} is not the live run (${target})`, 409);
    }
    if (action === "set-until") {
      const until = payload.until ?? null;
      if (until !== null && (typeof until !== "string" || !/^([01]\d|2[0-3]):[0-5]\d$/.test(until))) {
        throw new ActionError("until must be HH:MM or null");
      }
      return { action, target, payload: JSON.stringify({ until }) };
    }
    return { action, target, payload: null };
  }

  // Approval Gate: consumed by `vo prepare` (approve / unapprove / reject a Candidate; regenerate an Archetype with a
  // note). Approving adds a Base Voice (up to MAX_BASE_VOICES per Archetype); unapproving withdraws one.
  if (action === "approve-candidate" || action === "unapprove-candidate" || action === "reject-candidate") {
    const id = typeof req.target === "string" ? req.target : "";
    const c = read.query("SELECT archetype, status FROM candidates WHERE id = ?").get(id) as Record<string, any> | null;
    if (!c || c.archetype === "narrator") throw new ActionError(`no candidate ${JSON.stringify(req.target)}`, 404);
    const willBe = approvedAfterQueue(read, c.archetype);
    if (action === "unapprove-candidate") {
      if (!willBe.has(id)) throw new ActionError(`candidate ${id} is not approved`, 409);
      return { action, target: id, payload: null };
    }
    if (c.status === "superseded") throw new ActionError(`candidate ${id} was superseded by a regenerate`, 409);
    if (action === "approve-candidate") {
      if (willBe.has(id)) throw new ActionError(`candidate ${id} is already approved`, 409);
      if (willBe.size >= MAX_BASE_VOICES) {
        throw new ActionError(`${c.archetype} already has ${willBe.size} Base Voices (at most ${MAX_BASE_VOICES}); unapprove one first`, 409);
      }
    }
    return { action, target: id, payload: null };
  }
  if (action === "regenerate-archetype") {
    const id = typeof req.target === "string" ? req.target : "";
    const a = read.query("SELECT kind FROM archetypes WHERE id = ?").get(id) as Record<string, any> | null;
    if (!a) throw new ActionError(`no archetype ${JSON.stringify(req.target)}`, 404);
    if (a.kind === "narrator") throw new ActionError("the Narrator is fixed", 409);
    const note = payload.note ?? "";
    if (typeof note !== "string") throw new ActionError("note must be text");
    if (note.trim().length > MAX_NOTE) throw new ActionError(`note is over ${MAX_NOTE} characters`);
    return { action, target: id, payload: note.trim() ? JSON.stringify({ note: note.trim() }) : null };
  }

  // Lexicon: accept or correct a top name's spelling, consumed by `vo prepare`.
  if ((LEXICON_ACTIONS as readonly string[]).includes(action)) {
    try {
      return validateLexiconAction(read, action, req.target, payload as Record<string, unknown>);
    } catch (e) {
      if (e instanceof LexiconActionError) throw new ActionError(e.message, e.status);
      throw e;
    }
  }
  // Spot-check and NPC browser: ratings and flags, folded into `ratings` by vo run / vo voices (vo.ratings).
  if ((RATING_ACTIONS as readonly string[]).includes(action)) {
    return validateRating(read, action, req.target, payload as Record<string, unknown>);
  }
  // Separation page and NPC browser: re-roll an NPC Voice, consumed by `vo voices`.
  if ((SEPARATION_ACTIONS as readonly string[]).includes(action)) return validateSeparation(read, action, req.target);

  throw new ActionError(`unknown action ${JSON.stringify(action)}`);
}

export function insertAction(read: Database, write: Database, req: ActionRequest, now: Date = new Date()) {
  const row = validate(read, req, now);
  const r = write
    .query("INSERT INTO review_actions (action, target, payload) VALUES (?, ?, ?) RETURNING id")
    .get(row.action, row.target, row.payload) as { id: number };
  return { id: r.id, ...row };
}

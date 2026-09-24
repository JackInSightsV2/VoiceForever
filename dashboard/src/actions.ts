// Write side: the dashboard's only writes are review_actions rows, consumed by `vo run`. It never touches audio.
import type { Database } from "bun:sqlite";
import { runState } from "./queries";

export const LINE_ACTIONS = ["retry-line", "skip-line", "edit-tts-text"] as const;
export const RUN_ACTIONS = ["pause-run", "resume-run", "set-until"] as const;
export const MAX_TTS_TEXT = 4000;

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

  throw new ActionError(`unknown action ${JSON.stringify(action)}`);
}

export function insertAction(read: Database, write: Database, req: ActionRequest, now: Date = new Date()) {
  const row = validate(read, req, now);
  const r = write
    .query("INSERT INTO review_actions (action, target, payload) VALUES (?, ?, ?) RETURNING id")
    .get(row.action, row.target, row.payload) as { id: number };
  return { id: r.id, ...row };
}

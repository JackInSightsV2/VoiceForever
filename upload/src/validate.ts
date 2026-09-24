// Is an upload a Capture SavedVariables file? Mirrors what `vo ingest` accepts (ingest.read_records and
// ingest.record), without executing anything: the file is only ever parsed as data.
import { type LuaValue, loads, Malformed } from "./savedvars";

export const MAX_BYTES = 5 * 1024 * 1024;

const EVENTS = new Set(["QUEST_DETAIL", "QUEST_PROGRESS", "QUEST_COMPLETE", "QUEST_GREETING", "GOSSIP_SHOW"]);
const HASH = /^[0-9a-f]{8}$/;
const MAX_TEXT = 64 * 1024;

export class Rejected extends Error {
  constructor(message: string, readonly status = 422) {
    super(message);
  }
}

export interface Summary {
  records: number; // entries in VoiceForeverDB.capture
  valid: number; // of those, ones `vo ingest` would take as Capture records
  locales: Record<string, number>; // valid records per client locale
}

const get = (t: LuaValue | undefined, k: string) => (t instanceof Map ? t.get(k) : undefined);

/** ingest.record's checks: kind, event, text and hash (plus expected for Drift). */
export function isRecord(r: LuaValue): boolean {
  if (!(r instanceof Map)) return false;
  const kind = r.get("kind"), text = r.get("text"), hash = r.get("hash"), expected = r.get("expected");
  if ((kind !== "miss" && kind !== "drift") || !EVENTS.has(r.get("event") as string)) return false;
  if (typeof text !== "string" || text.length > MAX_TEXT) return false;
  if (typeof hash !== "string" || !HASH.test(hash)) return false;
  return kind === "miss" || (typeof expected === "string" && HASH.test(expected));
}

/** The file's Capture summary; throws Rejected with a message fit to show the player. */
export function validate(data: Uint8Array, maxBytes = MAX_BYTES): Summary {
  if (data.length > maxBytes) throw new Rejected(`File is too large (${data.length} bytes; the limit is ${maxBytes}).`, 413);
  if (data.length === 0) throw new Rejected("File is empty.");
  let capture: LuaValue | undefined;
  try {
    capture = get(loads(data).get("VoiceForeverDB"), "capture");
  } catch (e) {
    if (e instanceof Malformed) throw new Rejected(`Not a SavedVariables data file (${e.message}).`);
    throw e;
  }
  if (capture === undefined) {
    throw new Rejected("No VoiceForeverDB.capture table: upload VoiceForever.lua from SavedVariables.");
  }
  if (capture instanceof Map) {
    // sparse: keep the numbered records, as ingest does
    capture = [...capture.keys()].filter((k) => typeof k === "number" && Number.isInteger(k))
      .sort((a, b) => (a as number) - (b as number)).map((k) => (capture as Map<unknown, LuaValue>).get(k)!);
  }
  if (!Array.isArray(capture)) throw new Rejected("VoiceForeverDB.capture isn't a table.");
  const locales: Record<string, number> = {};
  let valid = 0;
  for (const r of capture) {
    if (!isRecord(r)) continue;
    valid++;
    const loc = get(r, "locale");
    const key = typeof loc === "string" && /^[A-Za-z]{2,8}$/.test(loc) ? loc : "unknown";
    locales[key] = (locales[key] ?? 0) + 1;
  }
  if (valid === 0) throw new Rejected("The file has no Capture records yet: play with the addon, then /reload or log out.");
  return { records: capture.length, valid, locales };
}

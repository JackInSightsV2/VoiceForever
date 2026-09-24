import { Database } from "bun:sqlite";
import { beforeEach, describe, expect, test } from "bun:test";
import { ActionError, insertAction } from "../src/actions";
import { NOW, VOICE, at, job, makeFixture, runRow } from "./fixture";

let fx: ReturnType<typeof makeFixture>;
let ro: Database;
let rw: Database;

beforeEach(() => {
  fx = makeFixture();
  ro = new Database(fx.path, { readonly: true });
  rw = new Database(fx.path);
  job(fx.db, 2, "quarantined", -3, { attempts: 3 });
});

const rows = () => fx.db.query("SELECT action, target, payload, consumed_at FROM review_actions ORDER BY id").all();
const err = (fn: () => unknown) => {
  try {
    fn();
  } catch (e) {
    return e as ActionError;
  }
  throw new Error("did not throw");
};

describe("line actions", () => {
  test("retry and skip carry the voice; skip carries an optional reason", () => {
    insertAction(ro, rw, { action: "retry-line", target: 2, payload: { voice_id: VOICE } }, NOW);
    insertAction(ro, rw, { action: "skip-line", target: "2", payload: { voice_id: VOICE, reason: " bad text " } }, NOW);
    expect(rows()).toEqual([
      { action: "retry-line", target: "2", payload: JSON.stringify({ voice_id: VOICE }), consumed_at: null },
      { action: "skip-line", target: "2", payload: JSON.stringify({ voice_id: VOICE, reason: "bad text" }), consumed_at: null },
    ]);
  });

  test("edit-tts-text stores trimmed text", () => {
    const r = insertAction(ro, rw, { action: "edit-tts-text", target: 2, payload: { tts_text: "  Kel Thoo zad.\n" } }, NOW);
    expect(r.id).toBeGreaterThan(0);
    expect(rows()).toEqual([
      { action: "edit-tts-text", target: "2", payload: JSON.stringify({ tts_text: "Kel Thoo zad." }), consumed_at: null },
    ]);
  });

  test("rejects bad targets and empty text without writing", () => {
    expect(err(() => insertAction(ro, rw, { action: "retry-line", target: "x" }, NOW)).status).toBe(400);
    expect(err(() => insertAction(ro, rw, { action: "retry-line", target: 99 }, NOW)).status).toBe(404);
    expect(err(() => insertAction(ro, rw, { action: "retry-line", target: 2, payload: { voice_id: "no:such" } }, NOW)).status).toBe(404);
    expect(err(() => insertAction(ro, rw, { action: "edit-tts-text", target: 2, payload: { tts_text: "  " } }, NOW)).status).toBe(400);
    expect(err(() => insertAction(ro, rw, { action: "delete-audio", target: 2 }, NOW)).message).toContain("unknown action");
    expect(rows()).toEqual([]);
  });
});

describe("run actions", () => {
  test("need a live run", () => {
    expect(err(() => insertAction(ro, rw, { action: "pause-run" }, NOW)).status).toBe(409);
    runRow(fx.db, { id: 1, started_at: at(-60), last_heartbeat: at(-2), status: "running" }); // lost
    expect(err(() => insertAction(ro, rw, { action: "pause-run" }, NOW)).status).toBe(409);
  });

  test("target the live run; set-until takes HH:MM or null", () => {
    runRow(fx.db, { id: 7, started_at: at(-60), last_heartbeat: at(0), status: "running" });
    insertAction(ro, rw, { action: "pause-run", target: "run:7" }, NOW);
    insertAction(ro, rw, { action: "resume-run" }, NOW);
    insertAction(ro, rw, { action: "set-until", payload: { until: "07:30" } }, NOW);
    insertAction(ro, rw, { action: "set-until", payload: { until: null } }, NOW);
    expect(rows().map((r: any) => [r.action, r.target, r.payload])).toEqual([
      ["pause-run", "run:7", null],
      ["resume-run", "run:7", null],
      ["set-until", "run:7", '{"until":"07:30"}'],
      ["set-until", "run:7", '{"until":null}'],
    ]);
    expect(err(() => insertAction(ro, rw, { action: "pause-run", target: "run:6" }, NOW)).status).toBe(409);
    expect(err(() => insertAction(ro, rw, { action: "set-until", payload: { until: "25:00" } }, NOW)).status).toBe(400);
  });
});

test("the read connection cannot write", () => {
  expect(() => ro.run("INSERT INTO review_actions (action) VALUES ('x')")).toThrow();
});

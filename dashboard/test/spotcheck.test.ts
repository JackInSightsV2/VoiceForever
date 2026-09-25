import { beforeAll, expect, test } from "bun:test";
import { rmSync } from "node:fs";
import { ActionError, insertAction, validate } from "../src/actions";
import { candidates, ratings, spotCheck, tally, WEIGHT } from "../src/spotcheck";
import { NOW, VOICE, at, audioRow, job, makeFixture, runRow } from "./fixture";

let fx: ReturnType<typeof makeFixture>;

beforeAll(() => {
  fx = makeFixture();
  const db = fx.db;
  // The latest run started 60 min ago: lines 1-2 were done before it, 3-5 during it. Line 4 has a high WER.
  runRow(db, { id: 1, started_at: at(-60), last_heartbeat: at(-1), ended_at: at(-1), status: "finished" });
  job(db, 1, "done", -120);
  job(db, 2, "done", -90);
  job(db, 3, "done", -30);
  job(db, 4, "done", -20, { wer: 0.15, transcript: "hello fiend" });
  job(db, 5, "done", -10);
  job(db, 6, "pending", 0);
  for (const i of [1, 2, 3, 4]) audioRow(db, fx.audio, i, 2);
  audioRow(db, fx.audio, 5, 2);
  rmSync(`${fx.audio}/823/5.ogg`); // its file is gone: never offered
  db.run("INSERT INTO zones (id, name) VALUES (12, 'Elwynn Forest')");
  db.run("INSERT INTO spawns (npc_id, zone) VALUES (823, 12)");
});

const seq = (...xs: number[]) => {
  let i = 0;
  return () => xs[i++ % xs.length];
};

test("weights: new since the latest run, named, ASR WER; rated lines rarely", () => {
  fx.db.run("UPDATE npcs SET is_named = 1 WHERE id = 823");
  const w = Object.fromEntries(candidates(fx.db, NOW).map((c) => [c.line_id, c]));
  expect(w[1].weight).toBe(WEIGHT.named);
  expect(w[3].weight).toBe(WEIGHT.named * WEIGHT.fresh);
  expect(w[4].weight).toBeCloseTo(WEIGHT.named * WEIGHT.fresh * (1 + WEIGHT.werPerUnit * 0.15));
  expect(w[4].why).toEqual(["new", "named", "WER 0.15"]);
  expect(w[6]).toBeUndefined(); // not done
  fx.db.run("UPDATE npcs SET is_named = 0 WHERE id = 823");
});

test("a weighted pick returns the line with audio, zone and voice tally; exclude skips lines", () => {
  const d = spotCheck(fx.db, fx.audio, { rng: seq(0), now: NOW });
  expect(d.line!.line_id).toBe(1);
  expect(d.line!).toMatchObject({ zone_name: "Elwynn Forest", audio_url: "/audio/823/1.ogg", npc: "Deputy Willem", rated: null });
  expect(d.stats).toMatchObject({ voiced: 5, checked: 0, up: 0, down: 0 });
  // The top of the range lands on the last candidate (line 5), whose file is missing: the pick moves on to line 4.
  const last = spotCheck(fx.db, fx.audio, { rng: seq(0.9999), now: NOW });
  expect(last.line!.line_id).toBe(4);
  const skip = spotCheck(fx.db, fx.audio, { rng: seq(0), exclude: [1, 2], now: NOW });
  expect(skip.line!.line_id).toBe(3);
  expect(spotCheck(fx.db, fx.audio, { exclude: [1, 2, 3, 4, 5] }).line).toBeNull();
});

test("the high-WER new line is picked most often", () => {
  const hits: Record<number, number> = {};
  let r = 0.5;
  for (let i = 0; i < 400; i++) {
    r = (r * 9301 + 49297) % 233280; // a fixed LCG
    const id = spotCheck(fx.db, fx.audio, { rng: () => r / 233280, now: NOW }).line!.line_id;
    hits[id] = (hits[id] ?? 0) + 1;
  }
  expect(hits[4]).toBeGreaterThan(hits[3]);
  expect(hits[3]).toBeGreaterThan(hits[1]);
  expect(hits[5]).toBeUndefined();
});

test("rating actions validate and count before the pipeline folds them", () => {
  const ins = (action: string, target: unknown, payload?: Record<string, unknown>) =>
    insertAction(fx.db, fx.db, { action, target: target as any, payload });
  expect(ins("rate-line", 1, { voice_id: VOICE, rating: "down" })).toMatchObject({
    action: "rate-line", target: "1", payload: JSON.stringify({ voice_id: VOICE, rating: "down" }),
  });
  ins("rate-line", 2, { voice_id: VOICE, rating: "down" });
  ins("rate-line", 2, { voice_id: VOICE, rating: "up" }); // changed my mind: the latest counts
  ins("flag-line", 3, { voice_id: VOICE, note: "  wrong name  " });
  ins("flag-voice", 823, { note: "too young" });
  const bad = (action: string, target: unknown, payload: Record<string, unknown>, status = 400) => {
    try {
      validate(fx.db, { action, target: target as any, payload });
      throw new Error("expected an ActionError");
    } catch (e) {
      expect(e).toBeInstanceOf(ActionError);
      expect((e as ActionError).status).toBe(status);
    }
  };
  bad("rate-line", 1, { voice_id: VOICE, rating: "meh" });
  bad("rate-line", 1, { rating: "up" });
  bad("rate-line", 42, { voice_id: VOICE, rating: "up" }, 404);
  bad("flag-voice", 9999, {}, 404);
  bad("flag-line", 1, { voice_id: VOICE, note: "x".repeat(501) });

  const all = ratings(fx.db);
  expect(all.every((r) => r.queued)).toBe(true);
  expect(all.find((r) => r.rating === "flag" && r.line_id === 3)!.note).toBe("wrong name");
  expect(all.find((r) => r.line_id === null)).toMatchObject({ npc_id: 823, rating: "flag", note: "too young" });
  expect(tally(ratings(fx.db, { voice: VOICE }))).toEqual({ up: 1, down: 1, flags: 1 });
  // Folded rows (the pipeline's `ratings`) count the same way.
  fx.db.run("INSERT INTO ratings (line_id, voice_id, npc_id, rating) VALUES (4, ?, 823, 'down')", [VOICE]);
  expect(tally(ratings(fx.db, { voice: VOICE }))).toEqual({ up: 1, down: 2, flags: 1 });
  const d = spotCheck(fx.db, fx.audio, { rng: seq(0), now: NOW });
  expect(d.line!.line_id).toBe(1);
  expect(d.line!.rated).toBe("down");
  expect(d.line!.why).toContain("rated before");
  expect(d.stats).toMatchObject({ checked: 4, down: 2 });
});

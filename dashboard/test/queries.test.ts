import { Database } from "bun:sqlite";
import { beforeEach, describe, expect, test } from "bun:test";
import { join } from "node:path";
import { overview, quarantine, runState } from "../src/queries";
import { NOW, VOICE, at, audioRow, job, makeFixture, runRow } from "./fixture";

let fx: ReturnType<typeof makeFixture>;
let ro: Database;

beforeEach(() => {
  fx = makeFixture();
  ro = new Database(fx.path, { readonly: true });
});

describe("run state", () => {
  test("no runs yet", () => {
    expect(runState(ro, NOW)).toEqual({ state: "never", run: null });
  });

  test("a fresh heartbeat means live; its status says running or paused", () => {
    runRow(fx.db, { id: 1, started_at: at(-60), last_heartbeat: at(-0.1), status: "paused", until: at(240) });
    const { state, run } = runState(ro, NOW);
    expect(state).toBe("paused");
    expect(run!.id).toBe(1);
  });

  test("a stale heartbeat on a running row means the run was lost", () => {
    runRow(fx.db, { id: 1, started_at: at(-60), last_heartbeat: at(-5), status: "running" });
    expect(runState(ro, NOW).state).toBe("lost");
  });

  test("a finished run is idle", () => {
    runRow(fx.db, { id: 1, started_at: at(-60), last_heartbeat: at(-30), ended_at: at(-30), status: "finished" });
    expect(runState(ro, NOW).state).toBe("idle");
  });
});

describe("overview", () => {
  beforeEach(() => {
    runRow(fx.db, { id: 1, started_at: at(-600), last_heartbeat: at(-500), ended_at: at(-500), status: "until",
      summary: { this_run: { done: 3 } } });
    runRow(fx.db, { id: 2, started_at: at(-30), last_heartbeat: at(0), status: "running", workers: 2 });
    // done 10 min ago and 20 min ago (outside the 15-min window, still inside the run)
    job(fx.db, 1, "done", -10);
    job(fx.db, 2, "done", -5);
    job(fx.db, 3, "done", -20);
    audioRow(fx.db, fx.audio, 1, 30);
    audioRow(fx.db, fx.audio, 2, 60);
    audioRow(fx.db, fx.audio, 3, 999);
    job(fx.db, 4, "running", -1, { tries: 2 });
    job(fx.db, 5, "pending", -2, { attempts: 1, reason: "ASR WER 0.50 > 0.2" });
    for (const l of [6, 7, 8]) job(fx.db, l, "pending", -30);
    job(fx.db, 9, "quarantined", -3, { attempts: 3, tries: 3, reason: "error: boom" });
    job(fx.db, 10, "skipped", -3);
    fx.db.run("UPDATE audio SET status = 'stale' WHERE line_id = 3");
  });

  test("counts every status plus stale audio", () => {
    const o = overview(ro, NOW);
    expect(o.counts).toEqual({ done: 3, running: 1, pending: 4, quarantined: 1, skipped: 1, total: 10, stale: 1 });
  });

  test("throughput over the trailing window, audio hours from audio.duration_s", () => {
    const o = overview(ro, NOW);
    expect(o.state).toBe("running");
    expect(o.rate!.lines).toBe(2);
    expect(o.rate!.lines_per_min).toBeCloseTo(2 / 15);
    expect(o.rate!.audio_hours_per_hour).toBeCloseTo(90 / 900);
  });

  test("ETA from remaining pending+running lines at the current rate", () => {
    const o = overview(ro, NOW);
    expect(o.eta!.minutes).toBeCloseTo(5 / (2 / 15));
    expect(o.eta!.after_until).toBe(false);
  });

  test("workers, recent errors, history and queued actions", () => {
    fx.db.run("INSERT INTO review_actions (action, target) VALUES ('pause-run', 'run:2')");
    const o = overview(ro, NOW);
    expect(o.workers.map((w: any) => [w.line_id, w.tries, w.npc])).toEqual([[4, 2, "Deputy Willem"]]);
    expect(o.errors.map((e: any) => [e.line_id, e.status])).toEqual([[5, "pending"], [9, "quarantined"]]);
    expect(o.history.map((h) => [h.id, h.status])).toEqual([[2, "running"], [1, "until"]]);
    expect(o.history[1].summary).toEqual({ this_run: { done: 3 } });
    expect(o.queued_actions.map((a: any) => a.action)).toEqual(["pause-run"]);
  });

  test("with no live run, throughput is the last run's average and there is no ETA", () => {
    fx.db.run("UPDATE runs SET status = 'finished', ended_at = ? WHERE id = 2", [at(0)]);
    const o = overview(ro, NOW);
    expect(o.state).toBe("idle");
    expect(o.rate!.basis).toBe("last run");
    expect(o.rate!.lines).toBe(3);
    expect(o.eta).toBeNull();
    expect(o.workers).toEqual([]);
  });

  test("sees rows committed after it opened (WAL reads while vo run writes)", () => {
    expect(overview(ro, NOW).counts.done).toBe(3);
    fx.db.run("UPDATE jobs SET status = 'done', updated_at = ? WHERE line_id = 6", [at(0)]);
    expect(overview(ro, NOW).counts.done).toBe(4);
  });
});

describe("quarantine", () => {
  test("lists quarantined lines with line details, the last kept take and queued actions", () => {
    job(fx.db, 1, "done", -10);
    job(fx.db, 2, "quarantined", -3, { attempts: 3, tries: 5, reason: "ASR WER 0.90 > 0.2", wer: 0.9, transcript: "garbage" });
    job(fx.db, 3, "quarantined", -4, { attempts: 3, tries: 3, reason: "error: boom" });
    audioRow(fx.db, fx.audio, 2, 3.5, "stale");
    fx.db.run("INSERT INTO review_actions (action, target, payload) VALUES ('retry-line', '3', ?)", [
      JSON.stringify({ voice_id: VOICE }),
    ]);
    fx.db.run("INSERT INTO review_actions (action, target, payload) VALUES ('skip-line', '3', ?)", [
      JSON.stringify({ voice_id: "other:voice" }),
    ]);
    const q = quarantine(ro, fx.audio);
    expect(q.lines.map((l: any) => l.line_id)).toEqual([2, 3]);
    const [two, three] = q.lines as any[];
    expect(two).toMatchObject({
      voice_id: VOICE, reason: "ASR WER 0.90 > 0.2", tries: 5, attempts: 3, npc: "Deputy Willem", type: "quest_detail",
      tts_text: "Hello friend, line 2.", raw_text: "Hello $N, line 2.", audio_url: "/audio/823/2.ogg", audio_status: "stale",
      queued: [],
    });
    expect(two.audio_path).toBeUndefined();
    expect(three.audio_url).toBeNull();
    expect(three.queued).toEqual(["retry-line"]);
  });

  test("falls back to the file vo run writes when there is no audio row", async () => {
    job(fx.db, 4, "quarantined", -3, { attempts: 3 });
    await Bun.write(join(fx.audio, "823", "4.ogg"), "OggS");
    expect((quarantine(ro, fx.audio).lines[0] as any).audio_url).toBe("/audio/823/4.ogg");
  });
});

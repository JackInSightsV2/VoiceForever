import { afterAll, beforeAll, expect, test } from "bun:test";
import { symlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createServer } from "../server";
import { localIso } from "../src/time";
import { VOICE, audioRow, job, makeFixture } from "./fixture";

let fx: ReturnType<typeof makeFixture>;
let app: ReturnType<typeof createServer>;
let base: string;

beforeAll(() => {
  fx = makeFixture();
  const now = localIso(new Date());
  fx.db.run("INSERT INTO runs (id, pid, workers, started_at, last_heartbeat, status) VALUES (1, 1, 2, ?, ?, 'running')", [now, now]);
  job(fx.db, 1, "done", 0);
  job(fx.db, 2, "quarantined", 0, { attempts: 3, reason: "error: boom" });
  audioRow(fx.db, fx.audio, 2, 2.0);
  writeFileSync(join(fx.dir, "secret.txt"), "nope");
  symlinkSync(join(fx.dir, "secret.txt"), join(fx.audio, "escape.ogg"));
  app = createServer({ db: fx.path, audio: fx.audio, port: 0, sseIntervalMs: 100 });
  base = `http://127.0.0.1:${app.server.port}`;
});

afterAll(() => app.stop());

test("pages share the shell; future pages are routed", async () => {
  for (const p of ["/", "/quarantine", "/approval", "/separation"]) {
    const r = await fetch(base + p);
    expect(r.status).toBe(200);
    expect(await r.text()).toContain('data-page="coverage"');
  }
  expect((await fetch(base + "/app.js")).status).toBe(200);
  expect((await fetch(base + "/nope")).status).toBe(404);
});

test("JSON snapshots", async () => {
  const o = await (await fetch(base + "/api/overview")).json();
  expect(o.state).toBe("running");
  expect(o.counts.done).toBe(1);
  const q = await (await fetch(base + "/api/quarantine")).json();
  expect(q.lines[0].audio_url).toBe("/audio/823/2.ogg");
});

test("SSE streams a snapshot, then another", async () => {
  const res = await fetch(base + "/events?page=overview");
  expect(res.headers.get("content-type")).toBe("text/event-stream");
  const reader = res.body!.getReader();
  let text = "";
  while ((text.match(/event: overview/g) ?? []).length < 2) text += new TextDecoder().decode((await reader.read()).value);
  await reader.cancel();
  const data = JSON.parse(text.split("event: overview\ndata: ")[1].split("\n")[0]);
  expect(data.run.id).toBe(1);
});

test("actions are inserted through the API", async () => {
  const post = (body: unknown) =>
    fetch(base + "/api/actions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let r = await post({ action: "edit-tts-text", target: 2, payload: { tts_text: "Better words." } });
  expect(r.status).toBe(201);
  r = await post({ action: "pause-run", target: "run:1" });
  expect(r.status).toBe(201);
  r = await post({ action: "retry-line", target: 42 });
  expect(r.status).toBe(404);
  expect((await (await fetch(base + "/api/quarantine")).json()).lines[0].queued).toEqual(["edit-tts-text"]);
  const rows = fx.db.query("SELECT action, target FROM review_actions ORDER BY id").all();
  expect(rows).toEqual([{ action: "edit-tts-text", target: "2" }, { action: "pause-run", target: "run:1" }]);
});

test("audio is served from the audio dir only", async () => {
  const r = await fetch(base + "/audio/823/2.ogg");
  expect(r.status).toBe(200);
  expect(await r.text()).toBe("OggS");
  expect((await fetch(base + "/audio/escape.ogg")).status).toBe(403);
  expect((await fetch(base + "/audio/..%2Fvo.sqlite")).status).toBe(403);
  expect((await fetch(base + "/audio/823/missing.ogg")).status).toBe(404);
});

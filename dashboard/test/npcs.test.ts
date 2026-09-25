import { afterAll, beforeAll, expect, test } from "bun:test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createServer } from "../server";
import { npcDetail, npcSearch } from "../src/npcs";
import { VOICE, audioRow, job, makeFixture } from "./fixture";

let fx: ReturnType<typeof makeFixture>;
let roots: { audio: string; voices: string; candidates: string };
const OWN = "voxcpm:human_m@human_m/g0s1#824-r0a0k1";

beforeAll(() => {
  fx = makeFixture();
  const db = fx.db;
  roots = { audio: fx.audio, voices: join(fx.dir, "voices"), candidates: join(fx.dir, "candidates") };
  mkdirSync(join(roots.voices, "human_m"), { recursive: true });
  mkdirSync(join(roots.candidates, "human_m"), { recursive: true });
  db.run("UPDATE npcs SET role = 'guard', is_named = 1 WHERE id = 823");
  db.run("INSERT INTO npcs (id, name, subname, race, gender, role) VALUES (824, 'Marshal Dughan', 'Marshal', 'Human', 'male', 'quest')");
  db.run("INSERT INTO npcs (id, name, race, gender, role) VALUES (826, 'Grunta', 'Orc', 'female', 'vendor')");
  db.run("INSERT INTO spawns (npc_id, zone) VALUES (823, 12), (824, 12), (824, 40), (824, 40), (826, 14)");
  db.run("INSERT INTO zones (id, name) VALUES (12, 'Elwynn Forest'), (40, 'Westfall'), (14, 'Durotar')");
  db.run("INSERT INTO archetypes (id, label, kind, gender, races, approved) VALUES ('human_m', 'Human male', 'race', 'male', '{\"Human male\": 2}', 'human_m/g0s1')");
  const anchor = join(roots.candidates, "human_m", "g0s1.wav");
  writeFileSync(anchor, "RIFF");
  db.run("INSERT INTO candidates (id, archetype, path, status) VALUES ('human_m/g0s1', 'human_m', ?, 'approved')", [anchor]);
  // 824 has its own NPC Voice; 823 speaks with the Archetype anchor.
  const clip = join(roots.voices, "human_m", "824-r0a0k1.wav");
  writeFileSync(clip, "RIFF");
  db.run("INSERT INTO voices (npc_id, voice_id, archetype, prompt, ref_clip) VALUES (824, ?, 'human_m', 'dsp: pitch -1 st', ?)", [OWN, clip]);
  db.run("INSERT INTO voice_builds (npc_id, roll, strategy, status) VALUES (824, 0, 'dsp', 'ok')");
  db.run("INSERT INTO lines (id, npc_id, type, quest_id, raw_text, tts_text) VALUES (20, 824, 'quest_detail', 5, 'Go, $N.', 'Go, friend.')");
  db.run("INSERT INTO lines (id, npc_id, type, raw_text, tts_text) VALUES (21, 824, 'gossip', 'Hail.', 'Hail.')");
  job(db, 1, "done", 0, { wer: 0.05 });
  job(db, 2, "quarantined", 0, { attempts: 3, reason: "ASR WER 0.9 > 0.2" });
  audioRow(db, fx.audio, 1, 2);
  db.run("INSERT INTO jobs (line_id, voice_id, tts_hash, status, wer) VALUES (20, ?, 'h', 'done', 0.0)", [OWN]);
  db.run("INSERT INTO ratings (line_id, voice_id, npc_id, rating) VALUES (20, ?, 824, 'down')", [OWN]);
  db.run("INSERT INTO review_actions (action, target) VALUES ('reroll-voice', '824')");
});

const search = (q: string) => npcSearch(fx.db, new URLSearchParams(q));

test("search by name, id, zone, race, Archetype and role", () => {
  expect(search("").npcs.map((n) => n.id)).toEqual([823, 824, 826]); // named first, then most lines
  expect(search("q=marsh").npcs.map((n) => n.id)).toEqual([824]); // subname too
  expect(search("q=826").npcs.map((n) => n.name)).toEqual(["Grunta"]);
  expect(search("zone=40").npcs.map((n) => n.id)).toEqual([824]); // main zone: most spawns
  expect(search("race=Orc").npcs.map((n) => n.id)).toEqual([826]);
  expect(search("archetype=human_m").npcs.map((n) => n.id)).toEqual([823, 824]); // derived, or from voices
  expect(search("role=guard").npcs.map((n) => n.id)).toEqual([823]);
  const f = search("").facets;
  expect(f.zones.map((z) => z.name)).toEqual(["Durotar", "Elwynn Forest", "Westfall"]);
  expect(f.archetypes).toEqual(["human_m"]);
  expect(f.roles).toEqual(["guard", "quest", "vendor"]);
  const willem = search("q=willem").npcs[0];
  expect(willem).toMatchObject({ lines: 10, done: 1, quarantined: 1, zone_name: "Elwynn Forest", own_voice: false });
});

test("an NPC speaking with its Archetype anchor: lines with player, status and WER", () => {
  const d = npcDetail(fx.db, 823, roots)!;
  expect(d.voice).toMatchObject({ own: false, can_reroll: false, anchor_url: null, voice_id: VOICE });
  expect(d.voice.archetype).toMatchObject({ id: "human_m", anchor_url: "/candidates/human_m/g0s1.wav" });
  expect(d.lines).toHaveLength(10);
  expect(d.lines[0]).toMatchObject({ line_id: 1, status: "done", wer: 0.05, audio_url: "/audio/823/1.ogg" });
  expect(d.lines[1]).toMatchObject({ status: "quarantined", reason: "ASR WER 0.9 > 0.2", audio_url: null });
  expect(d.lines[2].status).toBe("unqueued");
});

test("an NPC with its own voice: anchor, build, ratings and queued re-roll", () => {
  const d = npcDetail(fx.db, 824, roots)!;
  expect(d.npc.zones.map((z) => z.name)).toEqual(["Westfall", "Elwynn Forest"]);
  expect(d.voice).toMatchObject({ own: true, can_reroll: true, anchor_url: "/voices/human_m/824-r0a0k1.wav", voice_id: OWN });
  expect(d.voice.ratings).toEqual({ up: 0, down: 1, flags: 0 });
  expect(d.queued).toEqual(["reroll-voice"]);
  expect(d.lines.map((l) => [l.line_id, l.rating])).toEqual([[20, "down"], [21, null]]);
  expect(npcDetail(fx.db, 9999, roots)).toBeNull();
});

let app: ReturnType<typeof createServer>;
afterAll(() => app?.stop());

test("server: coverage, NPC and spot-check APIs, reports, and the NPC browser's actions", async () => {
  const reports = join(fx.dir, "reports");
  mkdirSync(reports);
  writeFileSync(join(reports, "3.html"), "<h1>Morning report</h1>");
  writeFileSync(join(reports, "latest.html"), "<h1>Morning report</h1>");
  app = createServer({ db: fx.path, audio: fx.audio, voices: roots.voices, candidates: roots.candidates, reports, port: 0 });
  const base = `http://127.0.0.1:${app.server.port}`;
  const get = async (p: string) => (await fetch(base + p)).json();
  for (const p of ["/coverage", "/npcs", "/spot-check"]) expect((await fetch(base + p)).status).toBe(200);
  expect((await get("/api/coverage?zone=12")).zone.name).toBe("Elwynn Forest");
  expect((await get("/api/npcs?q=grunta")).npcs[0].id).toBe(826);
  expect((await get("/api/npc?id=824")).voice.own).toBe(true);
  expect((await fetch(base + "/api/npc?id=5")).status).toBe(404);
  expect((await get("/api/spot-check?exclude=20")).line.line_id).toBe(1);
  const o = await get("/api/overview");
  expect(o.reports).toMatchObject({ latest: "/reports/latest.html", runs: [{ run: 3, url: "/reports/3.html" }] });
  expect(o.zones.find((z: any) => z.name === "Elwynn Forest")).toMatchObject({ total: 10, done: 1 });
  const page = await fetch(base + "/reports/latest.html");
  expect(page.headers.get("content-type")).toContain("text/html");
  expect(await page.text()).toContain("Morning report");
  expect((await fetch(base + "/report", { redirect: "manual" })).headers.get("location")).toBe("/reports/latest.html");
  const post = (body: unknown) =>
    fetch(base + "/api/actions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  expect((await post({ action: "flag-voice", target: 823, payload: { note: "too chipper" } })).status).toBe(201);
  expect((await post({ action: "retry-line", target: 1, payload: { voice_id: VOICE } })).status).toBe(201);
  expect((await post({ action: "reroll-voice", target: 823 })).status).toBe(404); // no NPC Voice yet
  expect((await post({ action: "reroll-voice", target: 824 })).status).toBe(409); // already queued
  const d = await get("/api/npc?id=823");
  expect(d.voice.flags).toEqual([{ note: "too chipper", queued: true }]);
  expect(d.lines[0].queued).toEqual(["retry-line"]);
});

import { afterAll, beforeAll, expect, test } from "bun:test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createServer } from "../server";
import { ActionError, insertAction, validate } from "../src/actions";
import { separation } from "../src/separation";
import { makeFixture } from "./fixture";

let fx: ReturnType<typeof makeFixture>;
let voicesDir: string;

const voice = (npc: number, aid: string, tag: string) => `voxcpm:${aid}@${aid}/g0s1#${tag}`;

beforeAll(() => {
  fx = makeFixture();
  voicesDir = join(fx.dir, "voices");
  mkdirSync(join(voicesDir, "human_m"), { recursive: true });
  const db = fx.db;
  // 823 (fixture), 824, 825: human_m; 826: orc_f. 823-824 closest, 824-825 next; 823-826 cross-Archetype.
  for (const [id, name, race, gender, sub] of [
    [824, "Marshal Dughan", "Human", "male", "Marshal"],
    [825, "Guard Thomas", "Human", "male", null],
    [826, "Grunta", "Orc", "female", null],
  ] as const) {
    db.run("INSERT INTO npcs (id, name, subname, race, gender, is_named) VALUES (?, ?, ?, ?, ?, 1)", [id, name, sub, race, gender]);
  }
  for (const [npc, aid] of [[823, "human_m"], [824, "human_m"], [825, "human_m"], [826, "orc_f"]] as const) {
    const tag = `${npc}-r0a0k1`;
    const clip = join(voicesDir, aid, `${tag}.wav`);
    mkdirSync(join(voicesDir, aid), { recursive: true });
    writeFileSync(clip, "RIFF");
    db.run("INSERT INTO voices (npc_id, voice_id, archetype, prompt, ref_clip) VALUES (?, ?, ?, ?, ?)", [
      npc, voice(npc, aid, tag), aid, `dsp: pitch +1.00 st (${npc})`, clip,
    ]);
    db.run(
      `INSERT INTO voice_builds (npc_id, roll, attempt, strategy, status, issue, detail, neighbour_sim, archetype_sim)
       VALUES (?, 0, 0, 'dsp', ?, ?, ?, ?, 0.95)`,
      npc === 824 ? [npc, "leftover", "floor", "cosine 0.980 > 0.9 to Neighbour 823", 0.98] : [npc, "ok", null, null, 0.8],
    );
  }
  db.run("INSERT INTO neighbours (a, b, reason, distance, sim) VALUES (823, 824, 'spawn', 12.5, 0.98)");
  db.run("INSERT INTO neighbours (a, b, reason, distance, sim) VALUES (824, 825, 'quest', NULL, 0.91)");
  db.run("INSERT INTO neighbours (a, b, reason, distance, sim) VALUES (823, 826, 'spawn', 3, 0.99)");
  db.run("INSERT INTO neighbours (a, b, reason, distance, sim) VALUES (823, 825, 'spawn', 40, NULL)");
  // A rendered line in 823's voice.
  const path = join(fx.audio, "823", "3.ogg");
  writeFileSync(path, "OggS");
  db.run("INSERT INTO audio (line_id, voice_id, path, duration_s, status) VALUES (3, ?, ?, 2.0, 'done')", [
    voice(823, "human_m", "823-r0a0k1"), path,
  ]);
});

test("closest same-Archetype Neighbour pairs, with anchors, a sample line and leftovers", () => {
  const d = separation(fx.db, voicesDir, fx.audio);
  expect(d.pairs.map((p) => [p.a.npc_id, p.b.npc_id, p.sim])).toEqual([
    [823, 824, 0.98],
    [824, 825, 0.91],
  ]);
  const [top] = d.pairs;
  expect(top.archetype).toBe("human_m");
  expect(top.reason).toBe("spawn");
  expect(top.a.anchor_url).toBe("/voices/human_m/823-r0a0k1.wav");
  expect(top.a.sample).toEqual({ line_id: 3, text: "Hello friend, line 3.", url: "/audio/823/3.ogg" });
  expect(top.b.sample).toBeNull();
  expect(top.b.status).toBe("leftover");
  expect(top.b.subname).toBe("Marshal");
  expect(d.leftovers.map((l) => [l.npc_id, l.issue, l.anchor_url])).toEqual([
    [824, "floor", "/voices/human_m/824-r0a0k1.wav"],
  ]);
  expect(d.stats).toMatchObject({ pairs: 4, voiced_pairs: 3, voices: 4, leftovers: 1, npcs: 4, per_npc: 2 });
});

test("reroll-voice is validated and shows as queued", () => {
  const fresh = makeFixture();
  expect(() => validate(fresh.db, { action: "reroll-voice", target: "x" })).toThrow(ActionError);
  try {
    validate(fx.db, { action: "reroll-voice", target: 999 });
    throw new Error("no throw");
  } catch (e) {
    expect((e as ActionError).status).toBe(404);
  }
  const row = insertAction(fx.db, fx.db, { action: "reroll-voice", target: 825 });
  expect(row).toMatchObject({ action: "reroll-voice", target: "825", payload: null });
  try {
    insertAction(fx.db, fx.db, { action: "reroll-voice", target: 825 });
    throw new Error("no throw");
  } catch (e) {
    expect((e as ActionError).status).toBe(409);
  }
  const d = separation(fx.db, voicesDir, fx.audio);
  expect(d.pairs[1].b.queued).toBe(true);
  expect(d.pairs[0].a.queued).toBe(false);
  expect(d.queued).toEqual([825]);
  fx.db.run("UPDATE review_actions SET consumed_at = 'now' WHERE action = 'reroll-voice'");
});

test("served: the snapshot, the anchors under /voices/, the action", async () => {
  const app = createServer({ db: fx.path, audio: fx.audio, voices: voicesDir, port: 0 });
  const base = `http://127.0.0.1:${app.server.port}`;
  try {
    const d = await (await fetch(base + "/api/separation")).json();
    expect(d.pairs[0].a.anchor_url).toBe("/voices/human_m/823-r0a0k1.wav");
    const clip = await fetch(base + d.pairs[0].a.anchor_url);
    expect(clip.status).toBe(200);
    expect(await clip.text()).toBe("RIFF");
    expect((await fetch(base + "/voices/../vo.sqlite")).status).not.toBe(200);
    const r = await fetch(base + "/api/actions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "reroll-voice", target: 823 }),
    });
    expect(r.status).toBe(201);
    expect((await fetch(base + "/separation")).status).toBe(200);
  } finally {
    app.stop();
  }
});

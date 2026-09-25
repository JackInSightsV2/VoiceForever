import { beforeAll, expect, test } from "bun:test";
import { coverage, UNASSIGNED, UNKNOWN_ZONE } from "../src/coverage";
import { job, makeFixture } from "./fixture";

let fx: ReturnType<typeof makeFixture>;

beforeAll(() => {
  fx = makeFixture();
  const db = fx.db;
  // 823 spawns twice in Elwynn (12), once in Westfall (40): main zone Elwynn. 824 only in Westfall. Line 11: Narrator.
  db.run("INSERT INTO npcs (id, name, race, gender) VALUES (824, 'Gryan Stoutmantle', 'Human', 'male')");
  db.run("INSERT INTO spawns (npc_id, zone) VALUES (823, 12), (823, 12), (823, 40), (824, 40)");
  db.run("INSERT INTO zones (id, name) VALUES (12, 'Elwynn Forest'), (40, 'Westfall')");
  db.run("INSERT INTO lines (id, npc_id, type, raw_text, tts_text) VALUES (11, 824, 'gossip', 'Hi', 'Hi')");
  db.run("INSERT INTO lines (id, npc_id, type, quest_id, raw_text, tts_text) VALUES (12, NULL, 'quest_detail', 99, 'A note', 'A note')");
  for (let i = 1; i <= 6; i++) job(db, i, "done", -i);
  job(db, 7, "quarantined", 0, { attempts: 3 });
  job(db, 8, "pending", 0, { attempts: 1, reason: "ASR WER 0.5 > 0.2" });
  job(db, 9, "skipped", 0);
  job(db, 11, "done", 0);
  // Line packs for 1-10 and 12 (the Narrator line placed in Westfall by its quest); 11 falls back (Unassigned).
  for (let i = 1; i <= 10; i++) db.run("INSERT INTO line_packs VALUES (?, 'VoiceForever_Alliance_1-10', 12)", [i]);
  db.run("INSERT INTO line_packs VALUES (12, 'VoiceForever_Alliance_10-20', 40)");
  // Spot-checked: line 1 folded, line 2 still a queued action.
  db.run("INSERT INTO ratings (line_id, voice_id, npc_id, rating) VALUES (1, 'v', 823, 'up')");
  db.run("INSERT INTO review_actions (action, target, payload) VALUES ('rate-line', '2', '{\"voice_id\":\"v\",\"rating\":\"down\"}')");
});

test("per-zone rows: total, done, failed, quarantined, % complete, spot-checked", () => {
  const d = coverage(fx.db);
  const elwynn = d.zones.find((z) => z.name === "Elwynn Forest")!;
  expect(elwynn).toMatchObject({ zone: 12, total: 10, done: 6, failed: 1, quarantined: 1, skipped: 1, checked: 2, pct: 60 });
  const westfall = d.zones.find((z) => z.name === "Westfall")!;
  expect(westfall).toMatchObject({ total: 2, done: 1, checked: 0 }); // 824's line (fallback) + the Narrator's
  expect(d.zones[0].name).toBe("Elwynn Forest"); // most lines first
});

test("per-Voice-Pack rows; lines not in line_packs are Unassigned", () => {
  const packs = Object.fromEntries(coverage(fx.db).packs.map((p) => [p.pack, p.total]));
  expect(packs).toEqual({ "VoiceForever_Alliance_1-10": 10, "VoiceForever_Alliance_10-20": 1, [UNASSIGNED]: 1 });
});

test("drill down: a zone's NPCs, with the Narrator", () => {
  const z = coverage(fx.db, new URLSearchParams("zone=40")).zone!;
  expect(z.name).toBe("Westfall");
  expect(z.npcs.map((n) => [n.npc_id, n.name, n.total, n.done])).toEqual([
    [824, "Gryan Stoutmantle", 1, 1],
    [null, null, 1, 0],
  ]);
  const none = coverage(fx.db, new URLSearchParams("zone=none")).zone!;
  expect(none.name).toBe(UNKNOWN_ZONE);
  expect(none.npcs).toEqual([]);
});

test("zone names fall back to the id", () => {
  fx.db.run("INSERT INTO spawns (npc_id, zone) VALUES (825, 999)");
  fx.db.run("INSERT INTO npcs (id, name) VALUES (825, 'Stranger')");
  fx.db.run("INSERT INTO lines (id, npc_id, type, raw_text, tts_text) VALUES (13, 825, 'gossip', 'Yo', 'Yo')");
  expect(coverage(fx.db).zones.map((z) => z.name)).toContain("Zone 999");
});

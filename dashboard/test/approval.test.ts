import { Database } from "bun:sqlite";
import { afterAll, beforeAll, beforeEach, describe, expect, test } from "bun:test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createServer } from "../server";
import { ActionError, insertAction } from "../src/actions";
import { approval } from "../src/queries";
import { makeFixture } from "./fixture";

/** Two Archetypes (orc_f approved, troll_m open with a regenerated generation) and the fixed Narrator. */
function approvalFixture() {
  const fx = makeFixture();
  const cands = join(fx.dir, "candidates");
  const wav = (rel: string) => {
    const p = join(cands, rel);
    mkdirSync(join(p, ".."), { recursive: true });
    writeFileSync(p, "RIFF");
    return p;
  };
  const arch = (id: string, kind: string, extra: Record<string, any> = {}) =>
    fx.db.run(
      `INSERT INTO archetypes (id, label, kind, gender, races, npcs, lines, base_description, notes, description,
       anchor_text, mode, generation, approved, approved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [id, extra.label ?? id, kind, extra.gender ?? null, extra.races ?? "{}", extra.npcs ?? 1, extra.lines ?? 10,
        extra.base ?? "Base.", extra.notes ?? "[]", extra.description ?? "Base.", "Anchor line.", "cont",
        extra.generation ?? 0, extra.approved ?? null, extra.approved ? "2026-09-25T10:00:00" : null],
    );
  const cand = (id: string, archetype: string, gen: number, seed: number, status = "pending") =>
    fx.db.run(
      `INSERT INTO candidates (id, archetype, generation, seed, description, anchor_text, path, duration_s, f0, hnr, centroid, asr, wer, status)
       VALUES (?, ?, ?, ?, 'Base.', 'Anchor line.', ?, 9.5, 180.2, 8.1, 1200, 'anchor line', 0.05, ?)`,
      [id, archetype, gen, seed, wav(`${id.replace("/", "/")}.wav`), status],
    );
  arch("narrator", "narrator", { label: "Narrator", description: "Kokoro bm_lewis, fixed.", approved: "narrator/fixed", lines: 721 });
  fx.db.run(
    `INSERT INTO candidates (id, archetype, generation, seed, description, anchor_text, path, status)
     VALUES ('narrator/fixed', 'narrator', 0, 0, 'kokoro:bm_lewis', 'Wanted: Hogger.', ?, 'approved')`,
    [wav("narrator/fixed.wav")],
  );
  arch("orc_f", "race", { gender: "female", races: '{"Orc female": 48}', npcs: 48, lines: 352, approved: "orc_f/g0s1" });
  cand("orc_f/g0s0", "orc_f", 0, 0);
  cand("orc_f/g0s1", "orc_f", 0, 1, "approved");
  arch("troll_m", "race", { gender: "male", generation: 1, notes: '["raspier"]', description: "Base. raspier.", lines: 649 });
  cand("troll_m/g0s0", "troll_m", 0, 0, "superseded");
  cand("troll_m/g1s0", "troll_m", 1, 1000);
  cand("troll_m/g1s1", "troll_m", 1, 1001, "rejected");
  fx.db.run(
    "INSERT INTO candidate_samples (candidate, idx, line_id, text, path, duration_s, asr, wer) VALUES (?, 1, 20, 'You got dem feathers?', ?, 3.2, 'you got them feathers', 0)",
    ["troll_m/g1s0", wav("troll_m/g1s0_1.wav")],
  );
  return { ...fx, cands };
}

describe("approval snapshot", () => {
  test("Archetypes, current Candidates with metrics and samples, the Narrator and progress", () => {
    const fx = approvalFixture();
    const d = approval(fx.db, fx.cands);
    expect(d.progress).toEqual({ approved: 1, total: 2, base_voices: 1, queued_approvals: 0, max_base_voices: 100 });
    expect(d.complete).toBe(false);
    expect(d.narrator).toMatchObject({ voice_id: "kokoro:bm_lewis", url: "/candidates/narrator/fixed.wav", lines: 721 });
    expect(d.archetypes.map((a: any) => a.id)).toEqual(["troll_m", "orc_f"]); // most lines first
    const troll = d.archetypes[0];
    expect(troll.notes).toEqual(["raspier"]);
    expect(troll.superseded).toBe(1);
    expect(troll.candidates.map((c: any) => [c.id, c.status])).toEqual([
      ["troll_m/g1s0", "pending"],
      ["troll_m/g1s1", "rejected"],
    ]);
    expect(troll.candidates[0]).toMatchObject({ url: "/candidates/troll_m/g1s0.wav", f0: 180.2, hnr: 8.1, wer: 0.05 });
    expect(troll.candidates[0].samples).toEqual([
      { idx: 1, line_id: 20, text: "You got dem feathers?", url: "/candidates/troll_m/g1s0_1.wav", duration_s: 3.2, asr: "you got them feathers", wer: 0 },
    ]);
    const orc = d.archetypes[1];
    expect(orc.approved).toEqual(["orc_f/g0s1"]);
    expect(orc.approved_count).toBe(1);
    expect(orc.candidates.map((c: any) => [c.id, c.approved, c.will_be_approved])).toEqual([
      ["orc_f/g0s0", false, false],
      ["orc_f/g0s1", true, true],
    ]);
    expect(orc.races).toEqual({ "Orc female": 48 });
  });

  test("bake-off seeds show their label, anchor chain and the clip before the chain", () => {
    const fx = approvalFixture();
    fx.db.run("UPDATE archetypes SET anchor_chain = 'orc' WHERE id = 'troll_m'");
    const raw = join(fx.cands, "troll_m/bakeoff/r4-s6-orc_raw.wav");
    mkdirSync(join(raw, ".."), { recursive: true });
    writeFileSync(raw, "RIFF");
    writeFileSync(join(fx.cands, "troll_m/bakeoff/r4-s6-orc.wav"), "RIFF");
    fx.db.run(
      `INSERT INTO candidates (id, archetype, generation, seed, path, raw_path, anchor_chain, label, status)
       VALUES ('troll_m/r4-s6-orc', 'troll_m', 1, 6, ?, ?, 'orc', 'r4-s6 + orc chain (bake-off winner)', 'pending')`,
      [join(fx.cands, "troll_m/bakeoff/r4-s6-orc.wav"), raw],
    );
    const troll = approval(fx.db, fx.cands).archetypes[0];
    expect(troll.anchor_chain).toBe("orc");
    expect(troll.candidates.find((c: any) => c.id === "troll_m/r4-s6-orc")).toMatchObject({
      label: "r4-s6 + orc chain (bake-off winner)", anchor_chain: "orc", url: "/candidates/troll_m/bakeoff/r4-s6-orc.wav",
      raw_url: "/candidates/troll_m/bakeoff/r4-s6-orc_raw.wav",
    });
    expect(troll.candidates.find((c: any) => c.id === "troll_m/g1s0")).toMatchObject({ label: null, anchor_chain: null, raw_url: null });
  });

  test("queued actions show on their Archetype until vo prepare consumes them", () => {
    const fx = approvalFixture();
    const ro = new Database(fx.path, { readonly: true });
    insertAction(ro, fx.db, { action: "approve-candidate", target: "troll_m/g1s0" });
    insertAction(ro, fx.db, { action: "regenerate-archetype", target: "orc_f", payload: { note: " deeper " } });
    const d = approval(fx.db, fx.cands);
    expect(d.progress.queued_approvals).toBe(1);
    expect(d.archetypes[0].queued).toMatchObject([{ action: "approve-candidate", target: "troll_m/g1s0", note: null }]);
    expect(d.archetypes[1].queued).toMatchObject([{ action: "regenerate-archetype", target: "orc_f", note: "deeper" }]);
    fx.db.run("UPDATE review_actions SET consumed_at = 'now'");
    expect(approval(fx.db, fx.cands).archetypes[0].queued).toEqual([]);
  });
});

describe("approval actions", () => {
  let fx: ReturnType<typeof approvalFixture>;
  let ro: Database;
  beforeEach(() => {
    fx = approvalFixture();
    ro = new Database(fx.path, { readonly: true });
  });
  const err = (req: any) => {
    try {
      insertAction(ro, fx.db, req);
    } catch (e) {
      return e as ActionError;
    }
    throw new Error("did not throw");
  };
  const rows = () => fx.db.query("SELECT action, target, payload FROM review_actions ORDER BY id").all();

  test("approve, reject and regenerate with a note are queued", () => {
    insertAction(ro, fx.db, { action: "approve-candidate", target: "troll_m/g1s0" });
    insertAction(ro, fx.db, { action: "reject-candidate", target: "orc_f/g0s0" });
    insertAction(ro, fx.db, { action: "regenerate-archetype", target: "troll_m", payload: { note: "  less theatrical " } });
    insertAction(ro, fx.db, { action: "regenerate-archetype", target: "orc_f" });
    expect(rows()).toEqual([
      { action: "approve-candidate", target: "troll_m/g1s0", payload: null },
      { action: "reject-candidate", target: "orc_f/g0s0", payload: null },
      { action: "regenerate-archetype", target: "troll_m", payload: JSON.stringify({ note: "less theatrical" }) },
      { action: "regenerate-archetype", target: "orc_f", payload: null },
    ]);
  });

  test("bad targets and notes are refused", () => {
    expect(err({ action: "approve-candidate", target: "orc_f/g9s9" }).status).toBe(404);
    expect(err({ action: "approve-candidate", target: 7 }).status).toBe(404);
    expect(err({ action: "approve-candidate", target: "narrator/fixed" }).status).toBe(404);
    expect(err({ action: "approve-candidate", target: "troll_m/g0s0" }).status).toBe(409); // superseded
    expect(err({ action: "regenerate-archetype", target: "murloc_m" }).status).toBe(404);
    expect(err({ action: "regenerate-archetype", target: "narrator" }).status).toBe(409);
    expect(err({ action: "regenerate-archetype", target: "orc_f", payload: { note: 5 } }).status).toBe(400);
    expect(err({ action: "regenerate-archetype", target: "orc_f", payload: { note: "x".repeat(501) } }).status).toBe(400);
    expect(rows()).toEqual([]);
  });
});

describe("Base Voices: approve toggles, several per Archetype", () => {
  let fx: ReturnType<typeof approvalFixture>;
  let ro: Database;
  beforeEach(() => {
    fx = approvalFixture();
    ro = new Database(fx.path, { readonly: true });
  });
  const status = (req: any) => {
    try {
      insertAction(ro, fx.db, req);
      return 201;
    } catch (e) {
      return (e as ActionError).status;
    }
  };
  const orc = () => approval(fx.db, fx.cands).archetypes.find((a: any) => a.id === "orc_f");

  test("approve adds a second Base Voice; unapprove withdraws one; the snapshot shows both after the queue", () => {
    expect(status({ action: "approve-candidate", target: "orc_f/g0s0" })).toBe(201); // orc_f/g0s1 stays approved
    expect(status({ action: "approve-candidate", target: "orc_f/g0s0" })).toBe(409); // already (queued)
    expect(status({ action: "approve-candidate", target: "orc_f/g0s1" })).toBe(409); // already approved
    let a = orc();
    expect([a.approved_count, a.approved_after_queue]).toEqual([1, 2]);
    expect(a.candidates.map((c: any) => c.will_be_approved)).toEqual([true, true]);

    expect(status({ action: "unapprove-candidate", target: "orc_f/g0s1" })).toBe(201);
    expect(status({ action: "unapprove-candidate", target: "orc_f/g0s1" })).toBe(409); // not approved any more
    expect(status({ action: "unapprove-candidate", target: "troll_m/g1s0" })).toBe(409); // never approved
    expect(status({ action: "unapprove-candidate", target: "narrator/fixed" })).toBe(404);
    a = orc();
    expect(a.candidates.map((c: any) => [c.id, c.will_be_approved])).toEqual([
      ["orc_f/g0s0", true],
      ["orc_f/g0s1", false],
    ]);
    // Toggling back before vo prepare runs: approve again after the unapprove.
    expect(status({ action: "approve-candidate", target: "orc_f/g0s1" })).toBe(201);
    expect(orc().approved_after_queue).toBe(2);
    expect(fx.db.query("SELECT action, target FROM review_actions ORDER BY id").all()).toEqual([
      { action: "approve-candidate", target: "orc_f/g0s0" },
      { action: "unapprove-candidate", target: "orc_f/g0s1" },
      { action: "approve-candidate", target: "orc_f/g0s1" },
    ]);
  });

  test("progress counts Archetypes with an approval and every Base Voice; more than 8 allowed", () => {
    for (let i = 2; i <= 8; i++) {
      fx.db.run(
        `INSERT INTO candidates (id, archetype, generation, seed, path, status) VALUES (?, 'orc_f', 0, ?, '', 'approved')`,
        [`orc_f/g0s${i}`, i],
      );
    }
    fx.db.run(`INSERT INTO candidates (id, archetype, generation, seed, path, status) VALUES ('orc_f/g0s9', 'orc_f', 0, 9, '', 'pending')`);
    const d = approval(fx.db, fx.cands);
    expect(d.progress).toMatchObject({ approved: 1, total: 2, base_voices: 8 });
    expect(orc().approved).toHaveLength(8);
    expect(status({ action: "approve-candidate", target: "orc_f/g0s9" })).toBe(201); // the ninth is fine
    expect(status({ action: "unapprove-candidate", target: "orc_f/g0s2" })).toBe(201);
  });
});

describe("approval over HTTP", () => {
  let fx: ReturnType<typeof approvalFixture>;
  let app: ReturnType<typeof createServer>;
  let base: string;
  beforeAll(() => {
    fx = approvalFixture();
    writeFileSync(join(fx.dir, "secret.txt"), "nope");
    app = createServer({ db: fx.path, audio: fx.audio, candidates: fx.cands, port: 0 });
    base = `http://127.0.0.1:${app.server.port}`;
  });
  afterAll(() => app.stop());

  test("page, snapshot, candidate audio and actions", async () => {
    expect(await (await fetch(base + "/approval")).text()).toContain('data-page="approval"');
    const d = await (await fetch(base + "/api/approval")).json();
    expect(d.progress.total).toBe(2);
    const r = await fetch(base + d.archetypes[0].candidates[0].url);
    expect(r.status).toBe(200);
    expect(await r.text()).toBe("RIFF");
    expect((await fetch(base + "/candidates/..%2Fsecret.txt")).status).toBe(403);
    const post = await fetch(base + "/api/actions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "approve-candidate", target: "troll_m/g1s0" }),
    });
    expect(post.status).toBe(201);
    const after = await (await fetch(base + "/api/approval")).json();
    expect(after.progress.queued_approvals).toBe(1);
  });
});

import { Database } from "bun:sqlite";
import { afterAll, beforeAll, beforeEach, describe, expect, test } from "bun:test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createServer } from "../server";
import { ActionError, insertAction } from "../src/actions";
import { approval } from "../src/queries";
import { makeFixture } from "./fixture";

/** Three top names (one accepted), one automatic name past the top 300, and one approved Archetype. */
function lexiconFixture() {
  const fx = makeFixture();
  const cands = join(fx.dir, "candidates");
  mkdirSync(join(cands, "lexicon"), { recursive: true });
  const wav = (rel: string) => {
    const p = join(cands, rel);
    writeFileSync(p, "RIFF");
    return p;
  };
  const name = (n: string, rank: number, status: string, extra: Record<string, any> = {}) =>
    fx.db.run(
      `INSERT INTO lexicon (name, lines, rank, npc, zone, draft, spelling, status, sample_text, sample_spoken,
       sample_spelling, sample_path, sample_voice) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [n, extra.lines ?? 100 - rank, rank, extra.npc ?? 0, extra.zone ?? 0, extra.draft ?? n, extra.spelling ?? extra.draft ?? n,
        status, extra.text ?? null, extra.spoken ?? null, extra.sample_spelling ?? extra.spelling ?? extra.draft ?? n,
        extra.path ?? null, extra.voice ?? null],
    );
  name("Ahn'Qiraj", 1, "pending", {
    draft: "Ahn-kee-rahj", zone: 1, text: "Ahn'Qiraj stirs.", spoken: "Ahn-kee-rahj stirs.",
    path: wav("lexicon/Ahn_Qiraj.wav"), voice: "Narrator, kokoro:bm_lewis",
  });
  name("Kel'Thuzad", 2, "accepted", { draft: "Kel-thoo-zahd", npc: 1 });
  name("Qiraji", 3, "pending", { draft: "Kirajee", spelling: "Kee-rah-jee", sample_spelling: "Kirajee" });
  name("Zzorgoth", 301, "auto");
  fx.db.run(
    `INSERT INTO archetypes (id, label, kind, lines, approved) VALUES ('human_m', 'Human, male', 'race', 10, 'human_m/g0s0')`,
  );
  return { ...fx, cands };
}

describe("lexicon in the approval snapshot", () => {
  test("top names with spelling, sample and progress; the Gate needs both voices and names", () => {
    const fx = lexiconFixture();
    const d = approval(fx.db, fx.cands);
    expect(d.voices_complete).toBe(true);
    expect(d.complete).toBe(false); // the Lexicon isn't reviewed
    const x = d.lexicon;
    expect(x.progress).toEqual({ reviewed: 1, total: 3, queued: 0 });
    expect(x.auto).toBe(1);
    expect(x.names.map((n: any) => n.name)).toEqual(["Ahn'Qiraj", "Kel'Thuzad", "Qiraji"]);
    expect(x.names[0]).toMatchObject({
      rank: 1, zone: true, npc: false, draft: "Ahn-kee-rahj", spelling: "Ahn-kee-rahj", status: "pending",
      sample: { text: "Ahn'Qiraj stirs.", spoken: "Ahn-kee-rahj stirs.", url: "/candidates/lexicon/Ahn_Qiraj.wav",
        voice: "Narrator, kokoro:bm_lewis", stale: false },
    });
    expect(x.names[2].sample.stale).toBe(true); // corrected: re-rendered by the next vo prepare
    fx.db.run("UPDATE lexicon SET status = 'corrected' WHERE rank <= 3");
    const done = approval(fx.db, fx.cands);
    expect(done.lexicon.complete).toBe(true);
    expect(done.complete).toBe(true);
  });

  test("queued accepts and corrections show on their name until vo prepare consumes them", () => {
    const fx = lexiconFixture();
    const ro = new Database(fx.path, { readonly: true });
    insertAction(ro, fx.db, { action: "accept-lexicon", target: "Ahn'Qiraj", payload: { spelling: "Ahn-kee-rahj" } });
    insertAction(ro, fx.db, { action: "correct-lexicon", target: "Qiraji", payload: { spelling: " Kee-rah-jee " } });
    const x = approval(fx.db, fx.cands).lexicon;
    expect(x.progress.queued).toBe(2);
    expect(x.names[0].queued).toMatchObject([{ action: "accept-lexicon", spelling: "Ahn-kee-rahj" }]);
    expect(x.names[2].queued).toMatchObject([{ action: "correct-lexicon", spelling: "Kee-rah-jee" }]);
    fx.db.run("UPDATE review_actions SET consumed_at = 'now'");
    expect(approval(fx.db, fx.cands).lexicon.names[0].queued).toEqual([]);
  });
});

describe("lexicon actions", () => {
  let fx: ReturnType<typeof lexiconFixture>;
  let ro: Database;
  beforeEach(() => {
    fx = lexiconFixture();
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

  test("accept and correct are queued for vo prepare", () => {
    insertAction(ro, fx.db, { action: "accept-lexicon", target: "Ahn'Qiraj" });
    insertAction(ro, fx.db, { action: "correct-lexicon", target: "Qiraji", payload: { spelling: "  Kee-rah-jee " } });
    expect(rows()).toEqual([
      { action: "accept-lexicon", target: "Ahn'Qiraj", payload: null },
      { action: "correct-lexicon", target: "Qiraji", payload: JSON.stringify({ spelling: "Kee-rah-jee" }) },
    ]);
  });

  test("unknown or automatic names and bad spellings are refused", () => {
    expect(err({ action: "accept-lexicon", target: "Nobody" }).status).toBe(404);
    expect(err({ action: "accept-lexicon", target: 5 }).status).toBe(404);
    expect(err({ action: "accept-lexicon", target: "Zzorgoth" }).status).toBe(409); // past the top 300
    expect(err({ action: "correct-lexicon", target: "Qiraji" }).status).toBe(400);
    expect(err({ action: "correct-lexicon", target: "Qiraji", payload: { spelling: "   " } }).status).toBe(400);
    expect(err({ action: "correct-lexicon", target: "Qiraji", payload: { spelling: 7 } }).status).toBe(400);
    expect(err({ action: "correct-lexicon", target: "Qiraji", payload: { spelling: "a\nb" } }).status).toBe(400);
    expect(err({ action: "correct-lexicon", target: "Qiraji", payload: { spelling: "x".repeat(121) } }).status).toBe(400);
    expect(rows()).toEqual([]);
  });
});

describe("lexicon over HTTP", () => {
  let fx: ReturnType<typeof lexiconFixture>;
  let app: ReturnType<typeof createServer>;
  let base: string;
  beforeAll(() => {
    fx = lexiconFixture();
    app = createServer({ db: fx.path, audio: fx.audio, candidates: fx.cands, port: 0 });
    base = `http://127.0.0.1:${app.server.port}`;
  });
  afterAll(() => app.stop());

  test("snapshot, sample audio and a correction", async () => {
    const d = await (await fetch(base + "/api/approval")).json();
    expect(d.lexicon.progress.total).toBe(3);
    const r = await fetch(base + d.lexicon.names[0].sample.url);
    expect(r.status).toBe(200);
    const post = await fetch(base + "/api/actions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "correct-lexicon", target: "Ahn'Qiraj", payload: { spelling: "Ahn-kee-raj" } }),
    });
    expect(post.status).toBe(201);
    const after = await (await fetch(base + "/api/approval")).json();
    expect(after.lexicon.names[0].queued[0]).toMatchObject({ action: "correct-lexicon", spelling: "Ahn-kee-raj" });
  });
});

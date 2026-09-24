import { afterAll, beforeAll, expect, test } from "bun:test";
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createServer } from "../server";
import { RateLimit } from "../src/ratelimit";
import { uploadId } from "../src/store";

const FIXTURE = join(import.meta.dir, "..", "..", "pipeline", "tests", "fixtures", "VoiceForever.lua");
let dir: string;
let app: ReturnType<typeof createServer>;
let base: string;

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), "vo-upload-"));
  app = createServer({ store: join(dir, "store"), port: 0, maxBytes: 64 * 1024, rateLimit: 6 });
  base = `http://127.0.0.1:${app.server.port}`;
});

afterAll(() => {
  app.stop();
  rmSync(dir, { recursive: true, force: true });
});

const post = (body: BodyInit) => fetch(`${base}/api/upload`, { method: "POST", body });

test("serves the upload page", async () => {
  const r = await fetch(base + "/");
  expect(r.status).toBe(200);
  expect(await r.text()).toContain("WTF/Account/&lt;ACCOUNT&gt;/SavedVariables/VoiceForever.lua");
  expect((await fetch(base + "/nope")).status).toBe(404);
  expect((await fetch(base + "/api/upload")).status).toBe(405);
});

test("stores a valid upload once, content-hash named, with a manifest line", async () => {
  const data = readFileSync(FIXTURE);
  const first = await post(data);
  expect(first.status).toBe(201);
  const body = await first.json();
  expect(body).toEqual({ ok: true, duplicate: false, id: uploadId(data), records: 9, locales: { enUS: 8, deDE: 1 } });

  const again = await post(data);
  expect(again.status).toBe(200);
  expect((await again.json()).duplicate).toBe(true);

  const store = join(dir, "store");
  expect(readdirSync(store).sort()).toEqual([`${body.id}.lua`, "manifest.jsonl"]);
  expect(readFileSync(join(store, `${body.id}.lua`)).equals(data)).toBe(true);
  const manifest = readFileSync(join(store, "manifest.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
  expect(manifest).toHaveLength(1);
  expect(manifest[0]).toMatchObject({ id: body.id, file: `${body.id}.lua`, size: data.length, records: 9, valid: 9,
    locales: { enUS: 8, deDE: 1 } });
  expect(Date.parse(manifest[0].received_at)).toBeGreaterThan(0);
});

test("rejects invalid and oversized uploads without storing them", async () => {
  const bad = await post('VoiceForeverDB = os.execute("rm -rf /")');
  expect(bad.status).toBe(422);
  expect((await bad.json()).error).toContain("only data is allowed");

  const big = await post(new Uint8Array(64 * 1024 + 1).fill(32));
  expect(big.status).toBe(413);
  expect((await big.json()).error).toContain("too large");

  expect(readdirSync(join(dir, "store"))).toHaveLength(2); // still only the fixture and the manifest
});

test("rate-limits uploads per IP", async () => {
  // 4 uploads so far in this file's window; the limit is 6.
  expect((await post("x = 1")).status).toBe(422);
  expect((await post("x = 1")).status).toBe(422);
  const limited = await post(readFileSync(FIXTURE));
  expect(limited.status).toBe(429);
  expect(Number(limited.headers.get("retry-after"))).toBeGreaterThan(0);
  expect((await fetch(base + "/")).status).toBe(200); // the page itself isn't limited
});

test("RateLimit windows are per IP and reset", () => {
  let now = 0;
  const rl = new RateLimit(2, 1000, () => now);
  expect([rl.take("a"), rl.take("a"), rl.take("a"), rl.take("b")]).toEqual([true, true, false, true]);
  expect(rl.retryAfter("a")).toBe(1);
  now = 1000;
  expect(rl.take("a")).toBe(true);
});

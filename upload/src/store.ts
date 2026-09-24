// The upload store: one file per distinct upload, named by content hash, plus manifest.jsonl (one line per stored
// upload). Nothing here voices or publishes anything: `vo ingest <store>/*.lua` is a separate, manual step.
import { appendFileSync, existsSync, mkdirSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Summary } from "./validate";

export interface Entry extends Summary {
  id: string; // sha256[:16], the same id `vo ingest` reports as the upload id
  file: string;
  size: number;
  received_at: string;
}

export function uploadId(data: Uint8Array): string {
  return new Bun.CryptoHasher("sha256").update(data).digest("hex").slice(0, 16);
}

/** Store an accepted upload unless an identical file is already stored. */
export function store(dir: string, data: Uint8Array, summary: Summary, now = new Date()): { entry: Entry; duplicate: boolean } {
  mkdirSync(dir, { recursive: true });
  const id = uploadId(data);
  const file = `${id}.lua`;
  const path = join(dir, file);
  const entry: Entry = { id, file, size: data.length, received_at: now.toISOString(), ...summary };
  if (existsSync(path)) return { entry, duplicate: true };
  const tmp = `${path}.${process.pid}.tmp`;
  writeFileSync(tmp, data, { mode: 0o644 });
  renameSync(tmp, path); // atomic: ingest never sees a half-written file
  appendFileSync(join(dir, "manifest.jsonl"), JSON.stringify(entry) + "\n");
  return { entry, duplicate: false };
}

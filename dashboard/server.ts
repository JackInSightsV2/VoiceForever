// `vo dashboard`: serves the review dashboard on localhost. Reads SQLite read-only (WAL, so it reads while `vo run`
// writes); its only writes are review_actions rows through a separate connection. Audio is served, never written.
import { Database } from "bun:sqlite";
import { existsSync, realpathSync } from "node:fs";
import { join, resolve, sep } from "node:path";
import { parseArgs } from "node:util";
import { ActionError, insertAction } from "./src/actions";
import { coverage } from "./src/coverage";
import { npcDetail, npcSearch } from "./src/npcs";
import { approval, overview, quarantine } from "./src/queries";
import { separation } from "./src/separation";
import { spotCheck } from "./src/spotcheck";

const PUBLIC = join(import.meta.dir, "public");
const PAGES = ["/", "/quarantine", "/approval", "/coverage", "/npcs", "/spot-check", "/separation"];
export const SSE_INTERVAL_MS = 5000;

export interface Options {
  db: string;
  audio: string;
  candidates?: string; // Approval Gate audio (vo prepare's build/candidates), served under /candidates/; default next to audio
  voices?: string; // NPC anchors (vo voices' build/voices), served under /voices/; default next to audio
  reports?: string; // morning reports (vo run's build/reports), served under /reports/; default next to audio
  port?: number;
  host?: string;
  sseIntervalMs?: number;
}

export function openDatabases(path: string) {
  if (!existsSync(path)) throw new Error(`${path} not found: run \`vo init\` (or \`vo run\`) first`);
  // The writer opens first and touches the file: a read-only connection can't create the WAL's -shm itself, so with
  // no pipeline process holding the DB open it would fail with SQLITE_CANTOPEN.
  const write = new Database(path, { readwrite: true, create: false });
  write.run("PRAGMA busy_timeout = 10000");
  write.query("SELECT COUNT(*) FROM sqlite_master").get();
  const read = new Database(path, { readonly: true });
  read.run("PRAGMA busy_timeout = 5000");
  return { read, write };
}

// Page snapshots (GET /api/<name>, and SSE /events?page=<name>), each given the request's query parameters.
const snapshots: Record<string, (o: Options, db: Database, p: URLSearchParams) => unknown> = {
  overview: (o, db) => overview(db, new Date(), reportsDir(o)),
  quarantine: (o, db) => quarantine(db, o.audio),
  approval: (o, db) => approval(db, candidatesDir(o)),
  separation: (o, db) => separation(db, voicesDir(o), o.audio),
  coverage: (_o, db, p) => coverage(db, p),
  npcs: (_o, db, p) => npcSearch(db, p),
  npc: (o, db, p) => npcDetail(db, Number(p.get("id")), { audio: o.audio, voices: voicesDir(o), candidates: candidatesDir(o) }),
  "spot-check": (o, db, p) =>
    spotCheck(db, o.audio, { exclude: (p.get("exclude") ?? "").split(",").filter(Boolean).map(Number) }),
};

function reportsDir(o: Options) {
  return o.reports ?? join(resolve(o.audio), "..", "reports");
}

function voicesDir(o: Options) {
  return o.voices ?? join(resolve(o.audio), "..", "voices");
}

function candidatesDir(o: Options) {
  return o.candidates ?? join(resolve(o.audio), "..", "candidates");
}

function json(data: unknown, status = 200) {
  return Response.json(data, { status, headers: { "Cache-Control": "no-store" } });
}

function sse(opts: Options, db: Database, page: string, params: URLSearchParams, signal: AbortSignal) {
  const snap = snapshots[page];
  const enc = new TextEncoder();
  let timer: ReturnType<typeof setInterval>;
  const stream = new ReadableStream({
    start(ctrl) {
      const send = () => {
        try {
          ctrl.enqueue(enc.encode(`event: ${page}\ndata: ${JSON.stringify(snap(opts, db, params))}\n\n`));
        } catch (e) {
          ctrl.enqueue(enc.encode(`event: error\ndata: ${JSON.stringify(String(e))}\n\n`));
        }
      };
      ctrl.enqueue(enc.encode(`retry: ${opts.sseIntervalMs ?? SSE_INTERVAL_MS}\n\n`));
      send();
      timer = setInterval(send, opts.sseIntervalMs ?? SSE_INTERVAL_MS);
      signal.addEventListener("abort", () => {
        clearInterval(timer);
        try {
          ctrl.close();
        } catch {}
      });
    },
    cancel() {
      clearInterval(timer);
    },
  });
  return new Response(stream, {
    headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store", Connection: "keep-alive" },
  });
}

function serveAudio(audioRoot: string, rel: string) {
  let root: string, file: string;
  try {
    root = realpathSync(audioRoot);
    file = realpathSync(resolve(root, decodeURIComponent(rel)));
  } catch {
    return new Response("not found", { status: 404 });
  }
  if (!file.startsWith(root + sep)) return new Response("forbidden", { status: 403 });
  return new Response(Bun.file(file));
}

export function createServer(opts: Options) {
  const { read, write } = openDatabases(opts.db);
  const server = Bun.serve({
    hostname: opts.host ?? "127.0.0.1",
    port: opts.port ?? 8787,
    idleTimeout: 60, // SSE sends every 5 s, well inside this
    async fetch(req) {
      const url = new URL(req.url);
      const path = url.pathname;
      if (req.method === "GET" && PAGES.includes(path)) return new Response(Bun.file(join(PUBLIC, "index.html")));
      if (req.method === "GET" && (path === "/app.js" || path === "/app.css")) {
        return new Response(Bun.file(join(PUBLIC, path.slice(1))));
      }
      if (req.method === "GET" && path.startsWith("/api/")) {
        const page = path.slice(5);
        if (!snapshots[page]) return json({ error: "not found" }, 404);
        const data = snapshots[page](opts, read, url.searchParams);
        return data === null ? json({ error: "not found" }, 404) : json(data);
      }
      if (req.method === "GET" && path === "/events") {
        const page = url.searchParams.get("page") ?? "overview";
        if (!snapshots[page]) return json({ error: `unknown page ${page}` }, 404);
        return sse(opts, read, page, url.searchParams, req.signal);
      }
      if (req.method === "POST" && path === "/api/actions") {
        let body: any;
        try {
          body = await req.json();
        } catch {
          return json({ error: "body must be JSON" }, 400);
        }
        try {
          return json(insertAction(read, write, body), 201);
        } catch (e) {
          if (e instanceof ActionError) return json({ error: e.message }, e.status);
          throw e;
        }
      }
      if (req.method === "GET" && path.startsWith("/audio/")) return serveAudio(opts.audio, path.slice(7));
      if (req.method === "GET" && path.startsWith("/candidates/")) return serveAudio(candidatesDir(opts), path.slice(12));
      if (req.method === "GET" && path.startsWith("/voices/")) return serveAudio(voicesDir(opts), path.slice(8));
      if (req.method === "GET" && path === "/report") {
        return new Response(null, { status: 302, headers: { Location: "/reports/latest.html" } });
      }
      if (req.method === "GET" && path.startsWith("/reports/")) return serveAudio(reportsDir(opts), path.slice(9));
      return json({ error: "not found" }, 404);
    },
    error(e) {
      return json({ error: String(e) }, 500);
    },
  });
  return {
    server,
    stop() {
      server.stop(true);
      read.close();
      write.close();
    },
  };
}

if (import.meta.main) {
  const repo = resolve(import.meta.dir, "..");
  const { values } = parseArgs({
    args: Bun.argv.slice(2),
    options: {
      db: { type: "string", default: join(repo, "build", "vo.sqlite") },
      audio: { type: "string", default: join(repo, "build", "audio") },
      candidates: { type: "string", default: join(repo, "build", "candidates") },
      voices: { type: "string", default: join(repo, "build", "voices") },
      reports: { type: "string", default: join(repo, "build", "reports") },
      port: { type: "string", default: "8787" },
      host: { type: "string", default: "127.0.0.1" },
    },
  });
  const { server } = createServer({
    db: values.db!,
    audio: values.audio!,
    candidates: values.candidates!,
    voices: values.voices!,
    reports: values.reports!,
    port: Number(values.port),
    host: values.host,
  });
  console.log(`vo dashboard: http://${server.hostname}:${server.port}  (db ${values.db})`);
}

// `vo dashboard`: serves the review dashboard on localhost. Reads SQLite read-only (WAL, so it reads while `vo run`
// writes); its only writes are review_actions rows through a separate connection. Audio is served, never written.
import { Database } from "bun:sqlite";
import { existsSync, realpathSync } from "node:fs";
import { join, resolve, sep } from "node:path";
import { parseArgs } from "node:util";
import { ActionError, insertAction } from "./src/actions";
import { overview, quarantine } from "./src/queries";

const PUBLIC = join(import.meta.dir, "public");
const PAGES = ["/", "/quarantine", "/approval", "/coverage", "/npcs", "/spot-check", "/separation"];
export const SSE_INTERVAL_MS = 5000;

export interface Options {
  db: string;
  audio: string;
  port?: number;
  host?: string;
  sseIntervalMs?: number;
}

export function openDatabases(path: string) {
  if (!existsSync(path)) throw new Error(`${path} not found: run \`vo init\` (or \`vo run\`) first`);
  const read = new Database(path, { readonly: true });
  read.run("PRAGMA busy_timeout = 5000");
  const write = new Database(path, { readwrite: true, create: false });
  write.run("PRAGMA busy_timeout = 10000");
  return { read, write };
}

const snapshots: Record<string, (o: Options, db: Database) => unknown> = {
  overview: (_o, db) => overview(db),
  quarantine: (o, db) => quarantine(db, o.audio),
};

function json(data: unknown, status = 200) {
  return Response.json(data, { status, headers: { "Cache-Control": "no-store" } });
}

function sse(opts: Options, db: Database, page: string, signal: AbortSignal) {
  const snap = snapshots[page];
  const enc = new TextEncoder();
  let timer: ReturnType<typeof setInterval>;
  const stream = new ReadableStream({
    start(ctrl) {
      const send = () => {
        try {
          ctrl.enqueue(enc.encode(`event: ${page}\ndata: ${JSON.stringify(snap(opts, db))}\n\n`));
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
        return json(snapshots[page](opts, read));
      }
      if (req.method === "GET" && path === "/events") {
        const page = url.searchParams.get("page") ?? "overview";
        if (!snapshots[page]) return json({ error: `unknown page ${page}` }, 404);
        return sse(opts, read, page, req.signal);
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
      port: { type: "string", default: "8787" },
      host: { type: "string", default: "127.0.0.1" },
    },
  });
  const { server } = createServer({
    db: values.db!,
    audio: values.audio!,
    port: Number(values.port),
    host: values.host,
  });
  console.log(`vo dashboard: http://${server.hostname}:${server.port}  (db ${values.db})`);
}

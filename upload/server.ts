// Capture upload service: a public page where players upload their VoiceForever.lua SavedVariables file.
// Uploads are untrusted: size-capped, rate-limited per IP, parsed as data only (never executed), and stored
// content-hash named for a maintainer to run through `vo ingest`. Nothing is voiced or published from here.
//
//   PORT=8790 STORE=upload-store bun run upload/server.ts
import { join } from "node:path";
import { RateLimit } from "./src/ratelimit";
import { store } from "./src/store";
import { MAX_BYTES, Rejected, validate } from "./src/validate";

const PAGE = join(import.meta.dir, "public", "index.html");

export interface Options {
  store: string;
  port?: number;
  host?: string;
  maxBytes?: number;
  rateLimit?: number; // uploads per IP per window
  rateWindowMs?: number;
  trustProxy?: boolean; // take the client IP from X-Forwarded-For (only behind a proxy that sets it)
}

function json(data: unknown, status = 200, headers: Record<string, string> = {}) {
  return Response.json(data, { status, headers: { "Cache-Control": "no-store", ...headers } });
}

/** The request body, or null once it passes `max` bytes (stops reading there). */
async function readCapped(req: Request, max: number): Promise<Uint8Array | null> {
  if (Number(req.headers.get("content-length") ?? 0) > max) return null;
  if (!req.body) return new Uint8Array();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for await (const chunk of req.body) {
    size += chunk.length;
    if (size > max) return null;
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

export function createServer(opts: Options) {
  const maxBytes = opts.maxBytes ?? MAX_BYTES;
  const limiter = new RateLimit(opts.rateLimit ?? 20, opts.rateWindowMs ?? 60 * 60 * 1000);

  const server = Bun.serve({
    port: opts.port ?? 8790,
    hostname: opts.host ?? "127.0.0.1",
    maxRequestBodySize: maxBytes + 64 * 1024,
    async fetch(req, srv) {
      const url = new URL(req.url);
      if (url.pathname === "/" && (req.method === "GET" || req.method === "HEAD")) {
        return new Response(Bun.file(PAGE), {
          headers: {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'",
          },
        });
      }
      if (url.pathname !== "/api/upload") return new Response("Not found", { status: 404 });
      if (req.method !== "POST") return json({ error: "Use POST." }, 405, { Allow: "POST" });

      const forwarded = opts.trustProxy ? req.headers.get("x-forwarded-for")?.split(",").at(-1)?.trim() : undefined;
      const ip = forwarded || srv.requestIP(req)?.address || "unknown";
      if (!limiter.take(ip)) {
        const retry = limiter.retryAfter(ip);
        return json({ error: `Too many uploads from your address; try again in ${Math.ceil(retry / 60)} min.` }, 429,
          { "Retry-After": String(retry) });
      }

      const data = await readCapped(req, maxBytes);
      if (data === null) return json({ error: `File is too large (the limit is ${maxBytes} bytes).` }, 413);
      try {
        const { entry, duplicate } = store(opts.store, data, validate(data, maxBytes));
        return json({ ok: true, duplicate, id: entry.id, records: entry.valid, locales: entry.locales }, duplicate ? 200 : 201);
      } catch (e) {
        if (e instanceof Rejected) return json({ error: e.message }, e.status);
        console.error(e);
        return json({ error: "Server error; please try again later." }, 500);
      }
    },
  });
  return { server, stop: () => server.stop(true) };
}

if (import.meta.main) {
  const env = process.env;
  const app = createServer({
    store: env.STORE ?? "upload-store",
    port: Number(env.PORT ?? 8790),
    host: env.HOST ?? "127.0.0.1",
    maxBytes: env.MAX_BYTES ? Number(env.MAX_BYTES) : undefined,
    rateLimit: env.RATE_LIMIT ? Number(env.RATE_LIMIT) : undefined,
    rateWindowMs: env.RATE_WINDOW_S ? Number(env.RATE_WINDOW_S) * 1000 : undefined,
    trustProxy: env.TRUST_PROXY === "1",
  });
  console.log(`Capture upload page on http://${app.server.hostname}:${app.server.port} storing to ${env.STORE ?? "upload-store"}`);
}

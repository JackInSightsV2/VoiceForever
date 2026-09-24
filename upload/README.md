# Capture upload page

A public page where players upload their `VoiceForever.lua` SavedVariables file (issue #18). Uploads are untrusted:
each is size-capped, rate-limited per IP and parsed as data only (`src/savedvars.ts`, a port of the pipeline's
`savedvars.py`; nothing is executed). Valid files are stored content-hash named. Nothing is voiced or published from
here: a maintainer pulls the store into `vo ingest`.

## Run locally

```sh
PORT=8790 STORE=upload-store bun run upload/server.ts   # http://127.0.0.1:8790
cd upload && bun test
```

| Env | Default | |
| --- | --- | --- |
| `PORT` | `8790` | |
| `HOST` | `127.0.0.1` | `0.0.0.0` to listen publicly |
| `STORE` | `upload-store` | where uploads and `manifest.jsonl` go |
| `MAX_BYTES` | `5242880` | upload size cap |
| `RATE_LIMIT` / `RATE_WINDOW_S` | `20` / `3600` | uploads per IP per window |
| `TRUST_PROXY` | off | `1`: take the client IP from the last `X-Forwarded-For` entry (only behind a proxy that sets it) |

`POST /api/upload` takes the raw file as the body (`curl --data-binary @VoiceForever.lua`) and answers `201`
(stored), `200` (identical file already stored), `413` (too large), `422` (not a Capture SavedVariables file) or
`429` (rate-limited), with a JSON `error` message for the player.

## Store

- `<id>.lua`: the file as uploaded; `<id>` is the first 16 hex digits of its SHA-256, the same upload id `vo ingest`
  prints, so an identical re-upload is not stored twice.
- `manifest.jsonl`: one line per stored file: `id`, `file`, `size`, `received_at`, `records` (entries in
  `VoiceForeverDB.capture`), `valid` (those that look like Capture records) and `locales` (valid records per locale).

## Into the pipeline

```sh
vo ingest upload-store/*.lua
```

Re-running over the whole store is safe: ingest de-duplicates records across uploads. Ingest writes Capture lines
and Drift into the database; they are voiced only by a later `vo run`.

## Hosting (not decided yet)

Needed to put this online:

- A host that runs Bun (any small VM or container) with a persistent disk for `STORE`, and a backup of it.
- TLS in front (a reverse proxy such as Caddy or nginx, or the platform's), with `HOST=127.0.0.1` behind it and
  `TRUST_PROXY=1` so rate limiting sees real client IPs. Cap the request body there too.
- The rate limiter is in memory and per process: run one instance, or move it to shared storage.
- A way to get the store to the machine running `vo` (rsync, object storage sync).
- Set the real URL in the addon's `VF.UPLOAD_URL` (`addon/VoiceForever/VoiceForever.lua`), shown by `/vf export`.

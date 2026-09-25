"""The morning report: a static HTML summary written at the end of every `vo run` (build/reports/<run id>.html and
latest.html). Nothing in it needs action: quarantined lines are retried by the next run.

It shows lines done and remaining with an ETA at this run's pace, coverage per zone and Voice Pack (vo.coverage),
quarantined lines with their reasons, the Neighbour-separation leftovers (voice_builds), Drift and new Capture
ingested since the previous run, and a few random sample clips per zone, named NPCs first. Clip paths are relative
to the report, so it plays opened from disk and when the dashboard serves it (/reports/).
"""
import html
import json
import os
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from vo import coverage

SAMPLES_PER_ZONE = 3
MAX_ROWS = 200  # quarantined lines, Drift and Capture rows listed (the counts are always complete)


def _local(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def _utc(local: datetime) -> str:
    """A naive local time as the naive UTC text of SQLite's CURRENT_TIMESTAMP (line_history.changed_at)."""
    return local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def data(conn: sqlite3.Connection, run_id: int, out_dir: Path, samples_per_zone: int = SAMPLES_PER_ZONE) -> dict:
    """Everything the report shows, as plain data."""
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"no run {run_id}")
    run = dict(run)
    run["summary"] = json.loads(run["summary"]) if run["summary"] else None
    prev = conn.execute("SELECT * FROM runs WHERE id < ? ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
    since = _local(prev["ended_at"] or prev["started_at"]) if prev else None

    counts = dict(conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
    total = sum(counts.values())
    remaining = counts.get("pending", 0) + counts.get("running", 0)
    started, ended = _local(run["started_at"]), _local(run["ended_at"]) or _local(run["last_heartbeat"])
    done_here = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'done' AND updated_at >= ? AND updated_at <= ?",
                             (run["started_at"], (ended or datetime.now()).isoformat(timespec="seconds"))).fetchone()[0]
    minutes = max(1 / 60, ((ended - started).total_seconds() / 60)) if started and ended else None
    rate = done_here / minutes if minutes else 0.0
    eta = remaining / rate if rate > 0 and remaining else None

    quarantined = [dict(r) for r in conn.execute(
        "SELECT j.line_id, j.voice_id, j.reason, j.wer, j.tries, l.type, l.tts_text, n.name AS npc"
        " FROM jobs j JOIN lines l ON l.id = j.line_id LEFT JOIN npcs n ON n.id = l.npc_id"
        " WHERE j.status = 'quarantined' ORDER BY j.line_id")]
    leftovers = [dict(r) for r in conn.execute(
        "SELECT b.npc_id, n.name, v.archetype, b.issue, b.detail FROM voice_builds b JOIN npcs n ON n.id = b.npc_id"
        " LEFT JOIN voices v ON v.npc_id = b.npc_id WHERE b.status = 'leftover' ORDER BY b.neighbour_sim DESC, b.npc_id")]

    cap_where, cap_args = ("ingested_at > ?", (since.isoformat(timespec="seconds"),)) if since else ("1", ())
    capture = dict(conn.execute(f"SELECT COALESCE(kind, 'miss'), COUNT(*) FROM capture WHERE {cap_where}"
                                " GROUP BY 1", cap_args).fetchall())
    new_capture = [dict(r) for r in conn.execute(
        f"SELECT npc_name, event, zone, text FROM capture WHERE {cap_where} AND COALESCE(kind, 'miss') = 'miss'"
        " ORDER BY id LIMIT ?", (*cap_args, MAX_ROWS))]
    drift_where, drift_args = ("h.changed_at > ?", (_utc(since),)) if since else ("1", ())
    drift = [dict(r) for r in conn.execute(
        f"SELECT h.line_id, h.raw_text AS old_text, l.raw_text AS new_text, n.name AS npc FROM line_history h"
        f" JOIN lines l ON l.id = h.line_id LEFT JOIN npcs n ON n.id = l.npc_id"
        f" WHERE h.reason LIKE 'drift%' AND {drift_where} ORDER BY h.rowid", drift_args)]

    zones = coverage.names(conn)
    return {
        "run": run, "since": since and since.isoformat(timespec="seconds"),
        "counts": counts, "total": total, "remaining": remaining,
        "done_this_run": done_here, "rate_per_min": rate, "eta_minutes": eta,
        "zones": coverage.table(conn, "zone"), "packs": coverage.table(conn, "pack"),
        "quarantined": quarantined, "leftovers": leftovers,
        "capture": capture, "new_capture": new_capture, "drift": drift,
        "samples": samples(conn, run_id, out_dir, zones, samples_per_zone),
    }


def samples(conn: sqlite3.Connection, seed: int, out_dir: Path, zones: dict[int, str],
            per_zone: int = SAMPLES_PER_ZONE) -> list[tuple[str, list[dict]]]:
    """[(zone name, clips)], zones by name; per zone up to `per_zone` random voiced lines of different NPCs, named
    NPCs first. Clips whose file is missing are left out; `src` is relative to out_dir."""
    by_zone: dict[str, list[dict]] = {}
    lines = {l.line_id: l for l in coverage.lines(conn)}
    for r in conn.execute(
            "SELECT a.line_id, a.path, a.duration_s, l.tts_text, l.type, l.npc_id, n.name, n.subname,"
            " COALESCE(n.is_named, 0) AS named FROM audio a JOIN lines l ON l.id = a.line_id"
            " LEFT JOIN npcs n ON n.id = l.npc_id WHERE a.status = 'done' ORDER BY a.line_id"):
        if not r["path"] or not Path(r["path"]).exists() or r["line_id"] not in lines:
            continue
        zone = coverage.zone_name(zones, lines[r["line_id"]].zone)
        by_zone.setdefault(zone, []).append(dict(r, src=Path(os.path.relpath(r["path"], out_dir)).as_posix()))
    rng = random.Random(seed)
    out = []
    for zone in sorted(by_zone):
        clips = by_zone[zone]
        rng.shuffle(clips)
        clips.sort(key=lambda c: -c["named"])  # stable: random within named, then within the rest
        picked, npcs = [], set()
        for c in clips:
            if c["npc_id"] not in npcs:
                picked.append(c)
                npcs.add(c["npc_id"])
            if len(picked) == per_zone:
                break
        out.append((zone, picked))
    return out


# --- HTML --------------------------------------------------------------------------------------------------------

CSS = """
:root { --bg:#f6f6f4; --surface:#fcfcfb; --line:#e3e3de; --ink:#1d1d1b; --ink-2:#55554f; --muted:#8a8a83;
  --good:#0ca30c; --critical:#d03b3b; --pending:#b9b9b1; color-scheme: light dark; }
@media (prefers-color-scheme: dark) { :root { --bg:#121211; --surface:#1a1a19; --line:#2e2e2b; --ink:#ececea;
  --ink-2:#b4b4ad; --muted:#85857e; --pending:#5a5a55; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui,-apple-system,sans-serif; }
main { max-width:1100px; margin:0 auto; padding:20px 16px 60px; }
h1 { font-size:20px; margin:0 0 4px; } h2 { font-size:13px; margin:0 0 10px; color:var(--ink-2);
  text-transform:uppercase; letter-spacing:.04em; }
section { background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:16px; margin-top:16px; }
.muted { color:var(--muted); } .bad { color:var(--critical); }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; }
.tile b { display:block; font-size:24px; font-variant-numeric:tabular-nums; } .tile span { color:var(--muted); font-size:12px; }
.scroll { overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }
th { text-align:left; font-weight:500; color:var(--muted); font-size:12px; padding:4px 8px 6px 0; border-bottom:1px solid var(--line); }
td { padding:5px 8px 5px 0; border-bottom:1px solid var(--line); vertical-align:top; }
td.n, th.n { text-align:right; }
.meter { display:inline-block; width:60px; height:6px; background:var(--pending); border-radius:3px; vertical-align:middle; }
.meter i { display:block; height:100%; background:var(--good); border-radius:3px; }
.clips { display:grid; gap:10px; grid-template-columns:repeat(auto-fill,minmax(min(100%,300px),1fr)); }
.clip { border:1px solid var(--line); border-radius:8px; padding:8px 10px; background:var(--bg); min-width:0; }
.clip audio { width:100%; height:30px; margin-top:4px; }
.clip p { margin:2px 0 0; color:var(--ink-2); font-size:12.5px; overflow:hidden; display:-webkit-box;
  -webkit-line-clamp:2; -webkit-box-orient:vertical; }
h3 { font-size:14px; margin:14px 0 6px; }
.text { max-width:520px; }
"""


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _n(v, d: int = 0) -> str:
    return "–" if v is None else f"{v:,.{d}f}"


def _dur(minutes: float) -> str:
    return f"{minutes:.0f} min" if minutes < 60 else f"{int(minutes // 60)} h {minutes % 60:.0f} min"


def _cut(s: str | None, n: int = 160) -> str:
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


def _coverage(rows: list[coverage.Row], label: str) -> str:
    body = "".join(
        f"<tr><td>{_e(r.key)}</td><td class=n>{_n(r.total)}</td><td class=n>{_n(r.done)}</td>"
        f"<td class=n>{_n(r.failed)}</td><td class=n>{_n(r.quarantined)}</td>"
        f"<td class=n>{_n(r.pct, 1)}% <span class=meter><i style='width:{r.pct:.1f}%'></i></span></td>"
        f"<td class=n>{_n(r.checked)}</td></tr>" for r in rows)
    return (f"<div class=scroll><table><tr><th>{label}</th><th class=n>Lines</th><th class=n>Done</th>"
            f"<th class=n>Failed</th><th class=n>Quarantined</th><th class=n>Complete</th>"
            f"<th class=n>Spot-checked</th></tr>{body}</table></div>")


def _clip(c: dict) -> str:
    sub = f" <span class=muted>&lt;{_e(c['subname'])}&gt;</span>" if c["subname"] else ""
    return (f"<div class=clip><b>{_e(c['name'] or 'Narrator')}</b>{sub}"
            f" <span class=muted>line {c['line_id']} · {_e(c['type'])}</span><p>{_e(_cut(c['tts_text'], 200))}</p>"
            f"<audio controls preload=none src=\"{_e(c['src'])}\"></audio></div>")


def render(d: dict) -> str:
    run, c = d["run"], d["counts"]
    pct = 100 * c.get("done", 0) / d["total"] if d["total"] else 0
    eta = (_dur(d["eta_minutes"]) if d["eta_minutes"] is not None
           else ("all done" if not d["remaining"] else "–"))
    head = (f"Run #{run['id']} {_e(run['status'])} · {_e(run['started_at'])} to {_e(run['ended_at'] or '–')}"
            f" · {_e(run['workers'])} workers" + (f" · error: {_e(run['error'])}" if run["error"] else ""))
    q = d["quarantined"]
    qrows = "".join(f"<tr><td>{r['line_id']}</td><td>{_e(r['npc'] or 'Narrator')}</td><td class=bad>{_e(r['reason'])}</td>"
                    f"<td class=n>{r['tries']}</td><td class=text>{_e(_cut(r['tts_text']))}</td></tr>" for r in q[:MAX_ROWS])
    lrows = "".join(f"<tr><td>{r['npc_id']}</td><td>{_e(r['name'])}</td><td>{_e(r['archetype'])}</td>"
                    f"<td>{_e(r['detail'] or r['issue'])}</td></tr>" for r in d["leftovers"])
    drows = "".join(f"<tr><td>{r['line_id']}</td><td>{_e(r['npc'] or 'Narrator')}</td>"
                    f"<td class=text>{_e(_cut(r['new_text']))}</td></tr>" for r in d["drift"][:MAX_ROWS])
    crows = "".join(f"<tr><td>{_e(r['npc_name'])}</td><td>{_e(r['event'])}</td>"
                    f"<td class=text>{_e(_cut(r['text']))}</td></tr>" for r in d["new_capture"])
    since = f"since {_e(d['since'])} (the previous run)" if d["since"] else "(no previous run: everything)"
    clips = "".join(f"<h3>{_e(zone)}</h3><div class=clips>{''.join(map(_clip, picked))}</div>"
                    for zone, picked in d["samples"] if picked)
    cap = d["capture"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark"><title>Morning report · run #{run['id']}</title><style>{CSS}</style></head>
<body><main>
<h1>Morning report</h1>
<div class=muted>{head}. Nothing here needs action: quarantined lines are retried by the next <code>vo run</code>.</div>
<section><h2>Progress</h2><div class=tiles>
  <div class=tile><b>{pct:.1f}%</b><span>{_n(c.get('done', 0))} of {_n(d['total'])} lines done</span></div>
  <div class=tile><b>{_n(d['remaining'])}</b><span>lines remaining</span></div>
  <div class=tile><b>{_n(d['done_this_run'])}</b><span>done this run · {d['rate_per_min']:.1f} lines/min</span></div>
  <div class=tile><b>{eta}</b><span>ETA at this run's pace</span></div>
  <div class=tile><b class="{'bad' if q else ''}">{_n(len(q))}</b><span>quarantined</span></div>
  <div class=tile><b>{_n(c.get('skipped', 0))}</b><span>skipped</span></div>
</div></section>
<section><h2>Coverage by zone</h2>{_coverage(d['zones'], 'Zone')}</section>
<section><h2>Coverage by Voice Pack</h2>{_coverage(d['packs'], 'Voice Pack')}</section>
<section><h2>Quarantined lines ({len(q)})</h2>{f"<div class=scroll><table><tr><th>Line</th><th>NPC</th><th>Reason</th><th class=n>Takes</th><th>Text</th></tr>{qrows}</table></div>" if q else "<div class=muted>None.</div>"}
{f"<p class=muted>First {MAX_ROWS} shown.</p>" if len(q) > MAX_ROWS else ""}</section>
<section><h2>Neighbour-separation leftovers ({len(d['leftovers'])})</h2>{f"<div class=scroll><table><tr><th>NPC</th><th>Name</th><th>Archetype</th><th>Issue</th></tr>{lrows}</table></div>" if lrows else "<div class=muted>None.</div>"}</section>
<section><h2>Drift and new Capture</h2><div class=muted>Ingested {since}: {_n(sum(cap.values()))} Capture records
({_n(cap.get('miss', 0))} new dialogue, {_n(cap.get('drift', 0))} Drift); {_n(len(d['drift']))} lines updated by Drift.</div>
{f"<h3>Drift updates</h3><div class=scroll><table><tr><th>Line</th><th>NPC</th><th>New text</th></tr>{drows}</table></div>" if drows else ""}
{f"<h3>New Capture</h3><div class=scroll><table><tr><th>NPC</th><th>Event</th><th>Text</th></tr>{crows}</table></div>" if crows else ""}</section>
<section><h2>Samples</h2><div class=muted>A few random clips per zone, named NPCs first, for optional spot-checking.</div>
{clips or "<div class=muted>No voiced lines yet.</div>"}</section>
<p class=muted>Generated {datetime.now().isoformat(timespec="seconds")}.</p>
</main></body></html>
"""


def generate(conn: sqlite3.Connection, run_id: int, out_dir: Path, samples_per_zone: int = SAMPLES_PER_ZONE) -> Path:
    """Write out_dir/<run id>.html and out_dir/latest.html; returns the former."""
    out_dir.mkdir(parents=True, exist_ok=True)
    page = render(data(conn, run_id, out_dir, samples_per_zone))
    path = out_dir / f"{run_id}.html"
    for p in (path, out_dir / "latest.html"):
        tmp = p.with_name(p.name + ".part")
        tmp.write_text(page, encoding="utf-8")
        os.replace(tmp, p)
    return path

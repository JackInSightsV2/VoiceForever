// VoiceForever dashboard client: one page per path, live snapshots over SSE, actions POSTed as review_actions.
"use strict";

const PAGES = {
  "/": "overview",
  "/quarantine": "quarantine",
  "/approval": "approval",
  "/coverage": "coverage",
  "/npcs": "npcs",
  "/spot-check": "spot-check",
  "/separation": "separation",
};
const SOON = {
  approval: "Approve Archetypes, the Narrator and top Lexicon entries.",
  coverage: "Per-zone and per-Voice-Pack completion, drilling down to NPCs.",
  npcs: "Search NPCs; each with its voice, reference clip and every line.",
  "spot-check": "Keyboard-driven random line player with ratings.",
  separation: "Closest same-race Neighbour pairs, side by side.",
};
const STATUS_ORDER = ["done", "running", "pending", "quarantined", "skipped"];

const $ = (s) => document.querySelector(s);
const main = $("#main");
const page = PAGES[location.pathname] || "overview";

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d }));
const time = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "–");
const when = (iso) => {
  if (!iso) return "–";
  const d = new Date(iso);
  const today = new Date().toDateString() === d.toDateString();
  return today ? time(iso) : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};
const ago = (iso, now) => {
  if (!iso) return "";
  const s = Math.max(0, (new Date(now) - new Date(iso)) / 1000);
  return s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${fmt(s / 3600, 1)} h`;
};
const dur = (minutes) =>
  minutes < 60 ? `${Math.round(minutes)} min` : `${Math.floor(minutes / 60)} h ${Math.round(minutes % 60)} min`;

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => t.classList.remove("show"), 2600);
}

async function act(action, target, payload) {
  const res = await fetch("/api/actions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, target, payload }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    toast(`Failed: ${body.error || res.status}`);
    return false;
  }
  toast(`Queued ${action} (#${body.id}), consumed by vo run`);
  refresh();
  return true;
}

async function refresh() {
  if (!(page in RENDER)) return;
  const res = await fetch(`/api/${page}`);
  if (res.ok) show(await res.json());
}

// --- Overview ---------------------------------------------------------------------------------------------------

function renderOverview(d) {
  const run = d.run;
  const live = d.state === "running" || d.state === "paused";
  const label = {
    never: "No run yet",
    running: "Running",
    paused: "Paused",
    lost: "Lost (no heartbeat)",
    idle: "Idle",
  }[d.state];
  const c = d.counts;
  const queuedRun = d.queued_actions.filter((a) => run && a.target === `run:${run.id}`);
  const untilVal = run?.until ? run.until.slice(11, 16) : "";

  const stateLine =
    d.state === "never"
      ? "Start one with <span class=mono>vo run</span>."
      : live
        ? `Run #${run.id} · pid ${run.pid} · ${run.workers} worker${run.workers === 1 ? "" : "s"} · started ${when(run.started_at)} · heartbeat ${ago(run.last_heartbeat, d.now)} ago`
        : d.state === "lost"
          ? `Run #${run.id} stopped heartbeating ${ago(run.last_heartbeat, d.now)} ago (killed?). The next <span class=mono>vo run</span> resumes.`
          : `Last run #${run.id} ${esc(run.status)} ${when(run.ended_at)}`;

  const bar = STATUS_ORDER.filter((s) => c[s] > 0)
    .map((s) => `<span class="s-${s}" style="flex:${c[s]}" title="${s}: ${fmt(c[s])}"></span>`)
    .join("");
  const pct = c.total ? (100 * c.done) / c.total : 0;

  const r = d.rate;
  const rateNote = r ? (r.basis === "trailing" ? `last ${fmt((new Date(r.to) - new Date(r.from)) / 60000)} min` : "last run average") : "";
  const eta = d.eta
    ? `${dur(d.eta.minutes)}<div class="l">done ≈ ${when(d.eta.at)}${d.eta.after_until ? " · after until" : ""}</div>`
    : `–<div class="l">${live ? (d.state === "paused" ? "paused" : "measuring") : "no live run"}</div>`;

  const workers = live
    ? Array.from({ length: Math.max(run.workers, d.workers.length) }, (_, i) => d.workers[i])
        .map((w, i) =>
          w
            ? `<tr><td class=num>${i + 1}</td><td>line <b>${w.line_id}</b> · ${esc(w.npc || (w.npc_id ? `NPC ${w.npc_id}` : "Narrator"))}</td>
               <td>${esc(w.type)}</td><td class=mono>${esc(w.voice_id)}</td><td class=num>take ${w.tries}</td><td class=num>${ago(w.since, d.now)}</td></tr>`
            : `<tr><td class=num>${i + 1}</td><td class=muted colspan=5>${d.state === "paused" ? "paused" : "idle"}</td></tr>`,
        )
        .join("")
    : "";

  const errors = d.errors
    .map(
      (e) => `<tr><td class=num>${e.line_id}</td><td>${esc(e.npc || "")}</td><td>${esc(e.reason)}</td>
      <td>${e.status === "quarantined" ? "quarantined" : `retry ${e.attempts}`}</td><td class=num>${when(e.updated_at)}</td></tr>`,
    )
    .join("");
  const runErrors = d.run_errors
    .map((e) => `<tr><td>run #${e.run}</td><td></td><td>${esc(e.error)}</td><td>run failed</td><td class=num>${when(e.at)}</td></tr>`)
    .join("");

  const history = d.history
    .map((h) => {
      const done = h.summary?.this_run?.done;
      const q = h.summary?.this_run?.quarantined;
      return `<tr><td class=num>#${h.id}</td><td>${esc(h.id === run?.id && d.state === "lost" ? "lost" : h.status)}</td>
        <td class=num>${when(h.started_at)}</td><td class=num>${h.ended_at ? when(h.ended_at) : "–"}</td>
        <td class=num>${h.workers ?? ""}</td><td class=num>${h.until ? time(h.until) : "–"}</td>
        <td class=num>${done == null ? "–" : fmt(done)}</td><td class=num>${q == null ? "–" : fmt(q)}</td></tr>`;
    })
    .join("");

  main.innerHTML = `
  <div class="grid">
    <section class="card wide">
      <div class="state">
        <span class="pill ${d.state}"><i></i>${label}</span>
        <span class="muted">${stateLine}</span>
        <div class="controls">
          ${
            d.state === "paused"
              ? `<button class=primary data-act="resume-run">Resume</button>`
              : `<button data-act="pause-run" ${live ? "" : "disabled"}>Pause</button>`
          }
          <label class="muted" for="until">Until</label>
          <input type="time" id="until" value="${untilVal}" ${live ? "" : "disabled"}>
          <button data-act="set-until" ${live ? "" : "disabled"}>Set</button>
          <button data-act="clear-until" ${live && run?.until ? "" : "disabled"}>Clear</button>
        </div>
      </div>
      ${queuedRun.length ? `<p class="muted">Waiting for the run to pick up: ${queuedRun.map((a) => `<span class=queued>${esc(a.action)}</span>`).join(" ")}</p>` : ""}
    </section>

    <section class="card wide">
      <h2>Progress</h2>
      <div class="tiles">
        <div class="tile"><div class="v">${fmt(pct, 1)}%</div><div class="l">${fmt(c.done)} of ${fmt(c.total)} lines done</div></div>
        <div class="tile"><div class="v">${r ? fmt(r.lines_per_min, 1) : "–"}</div><div class="l">lines / min ${rateNote && `· ${rateNote}`}</div></div>
        <div class="tile"><div class="v">${r ? fmt(r.audio_hours_per_hour, 2) : "–"}</div><div class="l">audio hours / hour</div></div>
        <div class="tile"><div class="v">${eta}</div></div>
      </div>
      <div class="bar" role="img" aria-label="Job status breakdown">${bar || `<span class="s-pending" style="flex:1"></span>`}</div>
      <div class="legend">
        ${STATUS_ORDER.map((s) => `<span><i class="sw s-${s}"></i>${s} <b>${fmt(c[s])}</b></span>`).join("")}
        <span><i class="sw s-stale"></i>stale audio <b>${fmt(c.stale)}</b></span>
      </div>
    </section>

    <section class="card">
      <h2>Workers</h2>
      ${live ? `<div class=scroll><table><tbody>${workers}</tbody></table></div>` : `<div class=empty>No live run.</div>`}
    </section>

    <section class="card">
      <h2>Recent errors</h2>
      ${
        errors || runErrors
          ? `<div class=scroll><table><thead><tr><th>Line</th><th>NPC</th><th>Reason</th><th>State</th><th>When</th></tr></thead>
             <tbody>${runErrors}${errors}</tbody></table></div>`
          : `<div class=empty>None.</div>`
      }
    </section>

    <section class="card wide">
      <h2>Run history</h2>
      ${
        history
          ? `<div class=scroll><table><thead><tr><th>Run</th><th>Status</th><th>Started</th><th>Ended</th><th>Workers</th><th>Until</th><th>Done</th><th>Quarantined</th></tr></thead>
             <tbody>${history}</tbody></table></div>`
          : `<div class=empty>No runs yet.</div>`
      }
    </section>
  </div>`;

  const target = run ? `run:${run.id}` : undefined;
  main.querySelectorAll("[data-act]").forEach((b) =>
    b.addEventListener("click", () => {
      const a = b.dataset.act;
      if (a === "set-until") {
        const v = $("#until").value;
        if (!v) return toast("Pick a time first");
        act("set-until", target, { until: v });
      } else if (a === "clear-until") act("set-until", target, { until: null });
      else act(a, target);
    }),
  );
}

// --- Quarantine -------------------------------------------------------------------------------------------------

function renderQuarantine(d) {
  $("#nav-q").textContent = d.lines.length || "";
  if (!d.lines.length) {
    main.innerHTML = `<h1>Quarantine</h1><div class="card empty">No quarantined lines.</div>`;
    return;
  }
  main.innerHTML = `<h1>Quarantine <span class="muted">(${d.lines.length})</span></h1>
  <p class="muted">Lines that failed every take of a run. Actions are queued and applied by the next <span class=mono>vo run</span> (or the live one within 5 s).</p>
  <div class="q">${d.lines.map(qline).join("")}</div>`;
  main.querySelectorAll(".qline").forEach(wireLine);
}

function qline(l) {
  const who = l.npc || (l.npc_id ? `NPC ${l.npc_id}` : "Narrator");
  const key = `${l.line_id}|${l.voice_id}`;
  return `<article class="qline" data-key="${esc(key)}" data-line="${l.line_id}" data-voice="${esc(l.voice_id)}">
    <div class="qhead">
      <span class="who">${esc(who)}</span>
      ${l.race ? `<span class="tag">${esc(l.race)} ${esc(l.gender || "")}</span>` : ""}
      <span class="tag">${esc(l.type)}</span>
      ${l.quest_id ? `<span class="tag">quest ${l.quest_id}</span>` : ""}
      ${l.player_gender ? `<span class="tag">player ${esc(l.player_gender)}</span>` : ""}
      <span class="muted mono">line ${l.line_id} · ${esc(l.voice_id)}</span>
      <span class="muted" style="margin-left:auto">${fmt(l.tries)} take${l.tries === 1 ? "" : "s"} · ${l.attempts} failed in a row · ${when(l.updated_at)}</span>
    </div>
    <div class="reason">${esc(l.reason || "no reason recorded")}</div>
    <div class="qtext">${esc(l.tts_text)}</div>
    ${l.transcript ? `<div class="muted">ASR heard: <i>${esc(l.transcript)}</i>${l.wer != null ? ` · WER ${fmt(l.wer, 2)}` : ""}</div>` : ""}
    ${l.raw_text !== l.tts_text ? `<details><summary>On-screen text</summary><div class="qtext">${esc(l.raw_text)}</div></details>` : ""}
    <div class="qactions">
      ${l.audio_url ? `<audio controls preload="none" src="${esc(l.audio_url)}"></audio><span class="muted">last kept take${l.audio_status === "stale" ? " (stale)" : ""}</span>` : `<span class="muted">no kept take</span>`}
      <span style="flex:1"></span>
      ${l.queued.map((a) => `<span class="queued">queued: ${esc(a)}</span>`).join("")}
      <button data-a="retry">Retry</button>
      <button data-a="edit">Edit TTS text</button>
      <button class="danger" data-a="skip">Skip</button>
    </div>
    <div class="editor" hidden>
      <textarea spellcheck="true">${esc(l.tts_text)}</textarea>
      <div class="qactions" style="margin:0"><button class="primary" data-a="save">Save and requeue</button><button data-a="cancel">Cancel</button></div>
    </div>
  </article>`;
}

let editing = new Set(); // lines with an open editor: live updates wait so typing isn't lost

function wireLine(el) {
  const line = Number(el.dataset.line);
  const voice = el.dataset.voice;
  const key = el.dataset.key;
  const ed = el.querySelector(".editor");
  if (editing.has(key)) ed.hidden = false;
  el.addEventListener("click", async (ev) => {
    const a = ev.target.dataset?.a;
    if (!a) return;
    if (a === "retry") act("retry-line", line, { voice_id: voice });
    if (a === "skip" && confirm(`Skip line ${line}? It will not be voiced.`)) act("skip-line", line, { voice_id: voice });
    if (a === "edit") {
      editing.add(key);
      ed.hidden = false;
      ed.querySelector("textarea").focus();
    }
    if (a === "cancel") {
      editing.delete(key);
      ed.hidden = true;
      if (pending) show(pending);
    }
    if (a === "save") {
      const text = ed.querySelector("textarea").value.trim();
      if (!text) return toast("TTS text can't be empty");
      editing.delete(key);
      await act("edit-tts-text", line, { tts_text: text });
    }
  });
}

// --- Coming pages -----------------------------------------------------------------------------------------------

function renderSoon() {
  const name = document.querySelector(`nav a[data-page="${page}"]`).textContent;
  main.innerHTML = `<div class="card soon-page"><h1>${esc(name)}</h1><p>${esc(SOON[page])}</p><p>Coming in #14.</p></div>`;
}

// --- Live updates -----------------------------------------------------------------------------------------------

const RENDER = { overview: renderOverview, quarantine: renderQuarantine };
let pending = null;

function show(d) {

  if (page === "quarantine" && editing.size) {
    pending = d; // don't clobber an open editor
    return;
  }
  pending = null;
  RENDER[page](d);
}

function connect() {
  const live = $("#live");
  const es = new EventSource(`/events?page=${page}`);
  es.addEventListener(page, (e) => {
    live.className = "live ok";
    live.lastChild.textContent = `live · ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
    show(JSON.parse(e.data));
  });
  es.onerror = () => {
    live.className = "live bad";
    live.lastChild.textContent = "reconnecting";
  };
}

document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("on", a.dataset.page === page));
if (RENDER[page]) connect();
else renderSoon();
if (page !== "quarantine") fetch("/api/quarantine").then((r) => r.json()).then((d) => ($("#nav-q").textContent = d.lines.length || ""));

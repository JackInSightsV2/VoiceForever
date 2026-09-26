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
  const by =
    action.endsWith("-candidate") || action === "regenerate-archetype" || action.endsWith("-lexicon")
      ? "vo prepare"
      : action === "reroll-voice"
        ? "vo voices"
        : "vo run";
  toast(`Queued ${action} (#${body.id}), applied by ${by}`);
  refresh();
  return true;
}

// The API behind this page, with its query (coverage drill-down, NPC search or one NPC).
const apiUrl = () =>
  page === "npcs" && params.get("id") ? `/api/npc?id=${encodeURIComponent(params.get("id"))}` : `/api/${page}${location.search}`;

async function refresh() {
  if (!(page in RENDER) || page === "spot-check") return; // spot-check moves on with N, not with refreshes
  const res = await fetch(apiUrl());
  if (res.ok) show(await res.json());
  else if (res.status === 404) show({ error: "not found" });
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

    <section class="card">
      <h2>Morning report</h2>
      ${
        d.reports.latest
          ? `<p><a class="btn primary" href="${esc(d.reports.latest)}" target="_blank" rel="noopener">Open the latest report</a></p>
             <p class="muted small">Written by every <span class=mono>vo run</span> as it ends: progress, coverage, quarantine,
             separation leftovers, Drift and new Capture, sample clips. Nothing in it needs action.</p>
             ${d.reports.runs.length ? `<div class="reports">${d.reports.runs.map((r) => `<a href="${esc(r.url)}" target="_blank" rel="noopener">run #${r.run}</a>`).join("")}</div>` : ""}`
          : `<div class=empty>No report yet: every <span class=mono>vo run</span> writes one as it ends (or <span class=mono>vo report</span>).</div>`
      }
    </section>

    <section class="card">
      <h2>Spot-checked by zone</h2>
      ${
        d.zones.length
          ? `<div class="scroll tall"><table><thead><tr><th>Zone</th><th class=num>Done</th><th class=num>Checked</th><th></th></tr></thead><tbody>
             ${d.zones
               .map(
                 (z) => `<tr><td><a href="/coverage?zone=${z.zone ?? "none"}">${esc(z.name)}</a></td>
                 <td class=num>${fmt(z.done)} / ${fmt(z.total)}</td><td class="num ${z.done && !z.checked ? "muted" : ""}">${fmt(z.checked)}</td>
                 <td>${meter(z.done ? (100 * z.checked) / z.done : 0)}</td></tr>`,
               )
               .join("")}</tbody></table></div>
             <p class="muted small">Checked: lines rated or flagged on <a href="/spot-check">Spot-check</a>, against lines done. The bar is the share of done lines checked.</p>`
          : `<div class=empty>No lines yet.</div>`
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

const meter = (pct) => `<span class="meter" title="${fmt(pct, 1)}%"><i style="width:${Math.min(100, Math.max(0, pct)).toFixed(1)}%"></i></span>`;

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

// --- Approval ---------------------------------------------------------------------------------------------------

const openArch = new Set(); // expanded Archetype cards survive live re-renders
let archFilter = "open";
let maxBase = 8; // Base Voices per Archetype (the snapshot's progress.max_base_voices)
const pct = (n) => (n == null ? "–" : `${Math.round(n * 100)}%`);

const approvalOpen = (d) => d.progress.total - d.progress.approved + (d.lexicon ? d.lexicon.progress.total - d.lexicon.progress.reviewed : 0);

function renderApproval(d) {
  const p = d.progress;
  $("#nav-a").textContent = approvalOpen(d) || "";
  maxBase = p.max_base_voices || 8;
  const shown = d.archetypes.filter((a) => archFilter === "all" || (archFilter === "open" ? !a.approved_count : a.approved_count));
  const n = d.narrator;
  main.innerHTML = `<h1>Approval <span class="muted">(${p.approved} / ${p.total} Archetypes approved · ${p.base_voices} Base Voices)</span></h1>
  <p class="explainer"><b>Approve every candidate you'd be happy to hear as an NPC of this race — each becomes a different
  base voice; NPCs are spread across them, near neighbours getting different ones.</b> Up to ${maxBase} per Archetype.</p>
  <p class="muted">Judge each Candidate by ear: the right character, holding up over its sample lines (each is a
  continuation of that anchor, as <span class=mono>vo run</span> will speak every line). Approve again to toggle it off.
  Actions are queued; <span class=mono>vo prepare</span> applies them, re-renders regenerated Archetypes, and writes
  <span class=mono>approved_voices.json</span> once every Archetype has at least one approved.</p>
  <section class="card">
    <div class="bar" role="img" aria-label="Approved Archetypes">
      <span class="s-done" style="flex:${p.approved}"></span><span class="s-pending" style="flex:${p.total - p.approved || (p.total ? 0 : 1)}"></span>
    </div>
    <div class="legend"><span><i class="sw s-done"></i>Archetypes with an approval <b>${p.approved}</b></span>
      <span><i class="sw s-pending"></i>open <b>${p.total - p.approved}</b></span>
      <span>Base Voices approved <b>${p.base_voices}</b></span>
      ${p.queued_approvals ? `<span>approvals queued for <span class=mono>vo prepare</span> <b>${p.queued_approvals}</b></span>` : ""}
      <span>${d.complete ? "All approved: <b>vo prepare</b> writes both locks, then <b>vo run</b> can start." : "<span class=mono>vo run</span> is locked until every Archetype and every top Lexicon name is approved."}</span>
    </div>
  </section>
  ${
    n
      ? `<section class="card arch narrator"><div class="ahead"><b>Narrator</b><span class="tag ok">fixed</span>
         <span class="mono">${esc(n.voice_id)}</span><span class="muted">${fmt(n.lines)} lines with no NPC</span></div>
         <div class="muted">${esc(n.description)}</div>
         ${n.url ? `<div class="sample"><div class="slabel"><span class="stext">${esc(n.text)}</span></div><audio controls preload="none" src="${esc(n.url)}"></audio></div>` : ""}</section>`
      : ""
  }
  <div class="filters">Show
    ${["open", "approved", "all"].map((f) => `<button data-filter="${f}" class="${archFilter === f ? "on" : ""}">${f === "open" ? "Needs approval" : f[0].toUpperCase() + f.slice(1)}</button>`).join("")}
  </div>
  ${d.archetypes.length ? "" : `<div class="card empty">No Archetypes yet: run <span class=mono>vo prepare</span>.</div>`}
  <div class="archs">${shown.map(archCard).join("")}</div>
  ${d.lexicon ? lexiconSection(d.lexicon) : ""}`;

  main.querySelectorAll("[data-filter]").forEach((b) =>
    b.addEventListener("click", () => {
      archFilter = b.dataset.filter;
      renderApproval(d);
    }),
  );
  main.querySelectorAll("details.arch").forEach((el) =>
    el.addEventListener("toggle", () => (el.open ? openArch.add(el.dataset.id) : openArch.delete(el.dataset.id))),
  );
  wireLexicon(d);
  main.querySelectorAll("[data-a]").forEach((b) =>
    b.addEventListener("click", () => {
      const { a, target } = b.dataset;
      if (a === "approve") act("approve-candidate", target);
      if (a === "unapprove") act("unapprove-candidate", target);
      if (a === "reject") act("reject-candidate", target);
      if (a === "regen") {
        const note = main.querySelector(`textarea[data-note="${CSS.escape(target)}"]`).value.trim();
        if (!note && !confirm(`Regenerate ${target} without a note?`)) return;
        act("regenerate-archetype", target, { note });
      }
    }),
  );
}

function archCard(a) {
  const races = Object.entries(a.races).map(([r, k]) => `${esc(r)} (${k})`).join(", ");
  const queued = a.queued.map((q) => `<span class="queued">queued: ${esc(q.action.replace("-candidate", "").replace("-archetype", ""))} ${esc(q.action === "regenerate-archetype" ? q.note || "" : q.target.split("/")[1])}</span>`).join("");
  const after = a.approved_after_queue !== a.approved_count ? ` <span class="queued">→ ${a.approved_after_queue} after vo prepare</span>` : "";
  const status = `<span class="tag ${a.approved_count ? "ok" : ""}">${a.approved_count} approved</span>${after}
    <span class="tag">${a.candidates.filter((c) => c.status !== "rejected").length} candidates</span>`;
  const notes = a.notes.length ? ` <span class="note">${a.notes.map(esc).join(". ")}.</span>` : "";
  return `<details class="card arch" data-id="${esc(a.id)}" ${openArch.has(a.id) ? "open" : ""}>
    <summary class="ahead"><b>${esc(a.label)}</b><span class="mono muted">${esc(a.id)}</span>${status}
      ${a.kind === "family" ? `<span class="tag">Creature Family</span>` : ""}
      <span class="muted">${fmt(a.npcs)} NPCs · ${fmt(a.lines)} lines</span>${queued}</summary>
    <div class="muted small">${races}</div>
    <p class="desc">${esc(a.base_description)}${notes}</p>
    <div class="muted small">Anchor line: <i>${esc(a.anchor_text)}</i> · continuation <span class=mono>${esc(a.mode)}</span>
      · generation ${a.generation}${a.superseded ? ` (${a.superseded} superseded)` : ""}${a.anchor_chain ? ` · anchor effect chain <span class=mono>${esc(a.anchor_chain)}</span> (applied once to each anchor; lines continue from the processed clip)` : ""}${a.effect_chain ? ` · per-line effect chain <span class=mono>${esc(a.effect_chain)}</span>` : ""}</div>
    ${a.approved_count ? `<div class="muted small">Base Voices: ${a.approved.map((id) => `<span class=mono>${esc(id.split("/")[1])}</span>`).join(", ")} · last approved ${when(a.approved_at)}.</div>` : ""}
    ${a.candidates.length ? `<div class="cands">${a.candidates.map((c) => candCard(a, c)).join("")}</div>` : `<div class="empty">No Candidates yet: run <span class=mono>vo prepare --archetype ${esc(a.id)}</span>.</div>`}
    <div class="regen">
      <textarea data-note="${esc(a.id)}" rows="2" placeholder="Note for a regenerate, e.g. deeper, less theatrical (appended to the description)"></textarea>
      <button data-a="regen" data-target="${esc(a.id)}">Regenerate with note</button>
    </div>
  </details>`;
}

function candCard(a, c) {
  const on = c.will_be_approved; // approved, or will be once vo prepare applies the queue
  const full = !on && a.approved_after_queue >= maxBase;
  const qd = a.queued.filter((q) => q.target === c.id).map((q) => q.action);
  const metric = (label, v, unit = "") => `<span title="${label}"><span class="muted">${label}</span> ${v == null ? "–" : esc(v) + unit}</span>`;
  return `<div class="cand ${c.status}${on ? " picked" : ""}">
    <div class="chead"><b class="mono">${esc(c.id.split("/")[1])}</b><span class="muted">seed ${c.seed}</span>
      ${c.label ? `<span class="tag">${esc(c.label)}</span>` : ""}
      ${c.status !== "pending" ? `<span class="tag ${c.status === "approved" ? "ok" : ""}">${esc(c.status)}</span>` : ""}
      ${qd.map((q) => `<span class="queued">queued: ${esc(q.replace("-candidate", ""))}</span>`).join("")}</div>
    ${c.url ? `<audio controls preload="none" src="${esc(c.url)}"></audio>` : `<span class="muted">audio missing</span>`}
    ${c.anchor_chain ? `<details class="small"><summary class="muted">anchor through the <span class=mono>${esc(c.anchor_chain)}</span> chain; before it</summary>${c.raw_url ? `<audio controls preload="none" src="${esc(c.raw_url)}"></audio>` : ""}</details>` : ""}
    <div class="metrics">${metric("f0", c.f0, " Hz")}${metric("HNR", c.hnr, " dB")}${metric("centroid", c.centroid, " Hz")}
      <span title="${esc(c.asr || "")}"><span class="muted">WER</span> <b class="${c.wer > 0.15 ? "bad" : ""}">${pct(c.wer)}</b></span></div>
    ${c.samples
      .map(
        (s) => `<div class="sample"><div class="slabel"><span class="muted">#${s.idx}</span><span class="stext" title="${esc(s.text)}">${esc(s.text)}</span>
        <span class="${s.wer > 0.15 ? "bad" : "muted"}" title="ASR: ${esc(s.asr || "")}">${pct(s.wer)}</span></div>
        ${s.url ? `<audio controls preload="none" src="${esc(s.url)}"></audio>` : ""}</div>`,
      )
      .join("")}
    <div class="qactions">
      ${on
        ? `<button class="on" data-a="unapprove" data-target="${esc(c.id)}" title="Approved: click to withdraw">Unapprove</button>`
        : `<button class="primary" data-a="approve" data-target="${esc(c.id)}" ${full ? `disabled title="${maxBase} Base Voices already: unapprove one first"` : ""}>Approve</button>`}
      <button class="danger" data-a="reject" data-target="${esc(c.id)}" ${c.status === "rejected" ? "disabled" : ""}>Reject</button>
    </div>
  </div>`;
}

// --- Approval: Lexicon ------------------------------------------------------------------------------------------

let lexFilter = "open";
const LEX_REVIEWED = ["accepted", "corrected"];

function lexiconSection(x) {
  const p = x.progress;
  const shown = x.names.filter((n) =>
    lexFilter === "all" || (lexFilter === "open" ? !LEX_REVIEWED.includes(n.status) : LEX_REVIEWED.includes(n.status)));
  return `<h2 id="lexicon">Lexicon <span class="muted">(${p.reviewed} / ${p.total} top names reviewed)</span></h2>
  <p class="muted">The most-spoken lore names, each with a drafted respelling and a sample rendered with it (the voice is
  named on each: the Narrator, or the approved anchor of the NPC's Archetype). Accept it, or type a better spelling
  and save. <span class=mono>vo prepare</span> applies these, re-renders corrected samples, and writes
  <span class=mono>lexicon.json</span> once all are reviewed. ${fmt(x.auto)} other names are drafted automatically and
  respelled when ASR keeps missing them.</p>
  <section class="card">
    <div class="bar" role="img" aria-label="Reviewed Lexicon names">
      <span class="s-done" style="flex:${p.reviewed}"></span><span class="s-pending" style="flex:${p.total - p.reviewed || (p.total ? 0 : 1)}"></span>
    </div>
    <div class="legend"><span><i class="sw s-done"></i>reviewed <b>${p.reviewed}</b></span>
      <span><i class="sw s-pending"></i>open <b>${p.total - p.reviewed}</b></span>
      ${p.queued ? `<span>queued for <span class=mono>vo prepare</span> <b>${p.queued}</b></span>` : ""}
      <span>${x.complete ? "Lexicon complete." : "<span class=mono>vo run</span> is locked until every top name is reviewed."}</span>
    </div>
  </section>
  <div class="filters">Show
    ${["open", "reviewed", "all"].map((f) => `<button data-lexfilter="${f}" class="${lexFilter === f ? "on" : ""}">${f === "open" ? "Needs review" : f[0].toUpperCase() + f.slice(1)}</button>`).join("")}
  </div>
  ${x.names.length ? "" : `<div class="card empty">No Lexicon names yet: run <span class=mono>vo prepare</span>.</div>`}
  <div class="lex">${shown.map(lexRow).join("")}</div>`;
}

function lexRow(n) {
  const reviewed = LEX_REVIEWED.includes(n.status);
  const flags = [n.npc ? "NPC" : "", n.zone ? "zone" : ""].filter(Boolean).join(", ");
  const queued = n.queued.map((q) => `<span class="queued">queued: ${q.action === "accept-lexicon" ? "accept" : `correct to ${esc(q.spelling)}`}</span>`).join("");
  const s = n.sample;
  return `<div class="card lexrow ${reviewed ? "reviewed" : ""}" data-name="${esc(n.name)}">
    <div class="lexhead"><span class="muted mono">#${n.rank}</span><b>${esc(n.name)}</b>
      <span class="muted">${fmt(n.lines)} lines${flags ? ` · ${flags}` : ""}</span>
      ${reviewed ? `<span class="tag ok">${esc(n.status)}</span>` : ""}${queued}</div>
    ${s.url ? `<div class="sample"><div class="slabel"><span class="stext" title="Spoken: ${esc(s.spoken)}">${esc(s.text)}</span></div>
      <audio controls preload="none" src="${esc(s.url)}"></audio>
      <div class="muted small">${esc(s.voice || "")}, spelled <span class=mono>${esc(s.spelling)}</span>${s.stale ? " · re-rendered with the new spelling by the next <span class=mono>vo prepare</span>" : ""}</div></div>`
      : `<div class="muted small">No sample yet: run <span class=mono>vo prepare</span>.</div>`}
    <div class="lexedit">
      <input data-spell="${esc(n.name)}" value="${esc(n.spelling)}" maxlength="120" aria-label="Spelling for ${esc(n.name)}" spellcheck="false">
      <button class="primary" data-lex="accept" data-target="${esc(n.name)}">Accept</button>
      <button data-lex="correct" data-target="${esc(n.name)}">Save correction</button>
    </div>
    ${n.draft !== n.spelling ? `<div class="muted small">Draft: <span class=mono>${esc(n.draft)}</span></div>` : ""}
  </div>`;
}

function wireLexicon(d) {
  main.querySelectorAll("[data-lexfilter]").forEach((b) =>
    b.addEventListener("click", () => {
      lexFilter = b.dataset.lexfilter;
      renderApproval(d);
    }),
  );
  main.querySelectorAll("[data-lex]").forEach((b) =>
    b.addEventListener("click", () => {
      const name = b.dataset.target;
      const input = main.querySelector(`input[data-spell="${CSS.escape(name)}"]`);
      const spelling = input.value.trim();
      if (b.dataset.lex === "correct") {
        if (!spelling) return toast("Type a spelling first");
        act("correct-lexicon", name, { spelling });
      } else if (spelling !== input.defaultValue.trim()) {
        act("correct-lexicon", name, { spelling }); // edited then accepted: that's a correction
      } else {
        act("accept-lexicon", name, { spelling });
      }
    }),
  );
  main.querySelectorAll("input[data-spell]").forEach((i) =>
    i.addEventListener("keydown", (e) => {
      if (e.key === "Enter") main.querySelector(`button[data-lex="correct"][data-target="${CSS.escape(i.dataset.spell)}"]`).click();
    }),
  );
}

// --- Separation -------------------------------------------------------------------------------------------------

function renderSeparation(d) {
  const s = d.stats;
  const who = (v) =>
    `<b>${esc(v.name)}</b>${v.subname ? ` <span class="muted">&lt;${esc(v.subname)}&gt;</span>` : ""} <span class="mono muted">${v.npc_id}</span>`;
  const voiceCol = (v) => `<div class="sep-side">
      <div class="chead">${who(v)}${v.is_named ? `<span class="tag">named</span>` : ""}
        ${v.status === "leftover" ? `<span class="tag bad" title="${esc(v.issue || "")}">leftover: ${esc(v.issue || "")}</span>` : ""}
        ${v.roll ? `<span class="muted small">roll ${v.roll}</span>` : ""}</div>
      <div class="sample"><div class="slabel"><span class="muted">anchor</span><span class="stext" title="${esc(v.prompt || "")}">${esc(v.prompt || "")}</span></div>
        ${v.anchor_url ? `<audio controls preload="none" src="${esc(v.anchor_url)}"></audio>` : `<span class="muted">anchor missing</span>`}</div>
      ${
        v.sample
          ? `<div class="sample"><div class="slabel"><span class="muted">line ${v.sample.line_id}</span><span class="stext" title="${esc(v.sample.text)}">${esc(v.sample.text)}</span></div>
             ${v.sample.url ? `<audio controls preload="none" src="${esc(v.sample.url)}"></audio>` : ""}</div>`
          : `<div class="muted small">No line rendered in this voice yet.</div>`
      }
      <div class="qactions">${
        v.queued
          ? `<span class="queued">queued: re-roll</span>`
          : `<button data-reroll="${v.npc_id}">Re-roll ${esc(v.name)}</button>`
      }</div>
    </div>`;
  main.innerHTML = `<h1>Separation</h1>
  <p class="muted">Neighbours (spawns within 150 yd, or a shared quest chain) must sound clearly different. These are the
  most similar same-Archetype Neighbour pairs by their anchors' speaker similarity (WavLM-SV cosine; 1 = same voice).
  Listen to both; if they sound like one person, re-roll either voice. <span class=mono>vo voices</span> applies
  re-rolls. Use these pairs to calibrate the Neighbour floor by ear.</p>
  <section class="card"><div class="tiles">
    <div class="tile"><div class="v">${fmt(s.voices)}</div><div class="l">NPC Voices</div></div>
    <div class="tile"><div class="v">${fmt(s.pairs)}</div><div class="l">Neighbour pairs</div></div>
    <div class="tile"><div class="v">${fmt(s.per_npc, 1)}</div><div class="l">Neighbours per NPC</div></div>
    <div class="tile"><div class="v">${fmt(s.voiced_pairs)}</div><div class="l">pairs with both voiced</div></div>
    <div class="tile"><div class="v ${s.leftovers ? "bad" : ""}">${fmt(s.leftovers)}</div><div class="l">leftovers</div></div>
  </div></section>
  ${d.pairs.length ? "" : `<div class="card empty">No voiced Neighbour pairs yet: run <span class=mono>vo voices</span>.</div>`}
  <div class="seps">${d.pairs
    .map(
      (p) => `<section class="card sep">
      <div class="ahead"><span class="sim" title="anchor similarity">${p.sim.toFixed(3)}</span>
        <span class="mono muted">${esc(p.archetype)}</span><span class="tag">${esc(p.reason)}</span>
        ${p.distance != null ? `<span class="muted small">${fmt(p.distance)} yd apart</span>` : ""}</div>
      <div class="sep-pair">${voiceCol(p.a)}${voiceCol(p.b)}</div></section>`,
    )
    .join("")}</div>
  ${
    d.leftovers.length
      ? `<h2 style="margin-top:24px">Leftovers (${d.leftovers.length})</h2>
    <p class="muted small">NPCs whose voice missed a constraint after every re-roll. They keep their best voice; nothing is blocked.</p>
    <section class="card scroll"><table><tr><th>NPC</th><th>Archetype</th><th>Issue</th><th>Anchor</th><th></th></tr>
    ${d.leftovers
      .map(
        (l) => `<tr><td><b>${esc(l.name)}</b> <span class="mono muted">${l.npc_id}</span></td><td class="mono">${esc(l.archetype)}</td>
        <td>${esc(l.detail || l.issue)}</td>
        <td>${l.anchor_url ? `<audio controls preload="none" src="${esc(l.anchor_url)}"></audio>` : ""}</td>
        <td>${l.queued ? `<span class="queued">queued</span>` : `<button data-reroll="${l.npc_id}">Re-roll</button>`}</td></tr>`,
      )
      .join("")}</table></section>`
      : ""
  }`;
  main.querySelectorAll("[data-reroll]").forEach((b) =>
    b.addEventListener("click", () => act("reroll-voice", Number(b.dataset.reroll))),
  );
}

// --- Coverage ---------------------------------------------------------------------------------------------------

const params = new URLSearchParams(location.search);

function covTable(rows, label, key) {
  if (!rows.length) return `<div class=empty>No lines yet.</div>`;
  const tot = rows.reduce(
    (a, r) => ({ total: a.total + r.total, done: a.done + r.done, failed: a.failed + r.failed, quarantined: a.quarantined + r.quarantined, checked: a.checked + r.checked }),
    { total: 0, done: 0, failed: 0, quarantined: 0, checked: 0 },
  );
  const row = (r, name) => `<tr><td>${name}</td><td class=num>${fmt(r.total)}</td><td class=num>${fmt(r.done)}</td>
    <td class="num ${r.failed ? "warn" : ""}">${fmt(r.failed)}</td><td class="num ${r.quarantined ? "bad" : ""}">${fmt(r.quarantined)}</td>
    <td class=num>${fmt(r.total ? (100 * r.done) / r.total : 0, 1)}% ${meter(r.total ? (100 * r.done) / r.total : 0)}</td><td class=num>${fmt(r.checked)}</td></tr>`;
  return `<div class=scroll><table class="cov"><thead><tr><th>${label}</th><th class=num>Lines</th><th class=num>Done</th><th class=num>Failed</th>
    <th class=num>Quarantined</th><th class=num>Complete</th><th class=num>Spot-checked</th></tr></thead>
    <tbody>${rows.map((r) => row(r, key(r))).join("")}</tbody><tfoot>${row(tot, "<b>All</b>")}</tfoot></table></div>`;
}

function renderCoverage(d) {
  if (d.zone) {
    const z = d.zone;
    main.innerHTML = `<h1><a href="/coverage">Coverage</a> <span class="muted">/</span> ${esc(z.name)}</h1>
    <p class="muted">This zone's lines by NPC (an NPC counts in its main spawn zone). Open an NPC for its voice and every line.</p>
    <section class="card">${covTable(z.npcs, "NPC", (n) =>
      n.npc_id == null
        ? "Narrator <span class=muted>(object and item quests)</span>"
        : `<a href="/npcs?id=${n.npc_id}">${esc(n.name || `NPC ${n.npc_id}`)}</a>${n.subname ? ` <span class="muted">&lt;${esc(n.subname)}&gt;</span>` : ""}
           <span class="muted small">${esc([n.race, n.gender, n.role].filter(Boolean).join(" · "))}</span>`,
    )}</section>`;
    return;
  }
  main.innerHTML = `<h1>Coverage</h1>
  <p class="muted">Failed: a take failed and waits for its retry. Quarantined: failed every take of a run (retried by the next run).
  Spot-checked: rated or flagged on <a href="/spot-check">Spot-check</a>. Open a zone for its NPCs.</p>
  ${d.assigned ? "" : `<div class="card warnbox">Zones are named and Voice Packs assigned by <span class=mono>vo extract --all</span>, <span class=mono>vo package</span> or <span class=mono>vo run</span> with the world DB; until then lines count under their NPC's zone id and "Unassigned".</div>`}
  <section class="card"><h2>By zone</h2>${covTable(d.zones, "Zone", (r) => `<a href="/coverage?zone=${r.zone ?? "none"}">${esc(r.name)}</a>`)}</section>
  <section class="card" style="margin-top:16px"><h2>By Voice Pack</h2>${covTable(d.packs, "Voice Pack", (r) => `<span class=mono>${esc(r.pack)}</span>`)}</section>`;
}

// --- NPC browser ------------------------------------------------------------------------------------------------

const STATUS_TAG = { done: "ok", quarantined: "bad", skipped: "", pending: "", running: "", unqueued: "" };
let npcForm = null; // the search form survives live results

function renderNpcs(d) {
  if (params.get("id")) return renderNpc(d);
  if (!npcForm) {
    const f = d.facets;
    const opt = (xs, cur, label = (x) => x, val = (x) => x) =>
      xs.map((x) => `<option value="${esc(val(x))}" ${String(val(x)) === cur ? "selected" : ""}>${esc(label(x))}</option>`).join("");
    main.innerHTML = `<h1>NPC browser</h1>
    <form class="card search" id="npc-search" autocomplete="off">
      <input type="search" name="q" placeholder="Name, title or id" value="${esc(params.get("q") || "")}" aria-label="Name">
      <select name="zone" aria-label="Zone"><option value="">Any zone</option><option value="none" ${params.get("zone") === "none" ? "selected" : ""}>No zone</option>${opt(f.zones, params.get("zone") || "", (z) => z.name, (z) => z.id)}</select>
      <select name="race" aria-label="Race"><option value="">Any race</option>${opt(f.races, params.get("race") || "")}</select>
      <select name="archetype" aria-label="Archetype"><option value="">Any Archetype</option>${opt(f.archetypes, params.get("archetype") || "")}</select>
      <select name="role" aria-label="Role"><option value="">Any role</option>${opt(f.roles, params.get("role") || "")}</select>
    </form>
    <div id="npc-results"></div>`;
    npcForm = $("#npc-search");
    let t;
    const search = () => {
      const q = new URLSearchParams([...new FormData(npcForm)].filter(([, v]) => v !== ""));
      history.replaceState(null, "", `/npcs${q.toString() ? `?${q}` : ""}`);
      for (const k of [...params.keys()]) params.delete(k);
      q.forEach((v, k) => params.set(k, v));
      refresh();
    };
    npcForm.addEventListener("input", () => (clearTimeout(t), (t = setTimeout(search, 250))));
    npcForm.addEventListener("submit", (e) => (e.preventDefault(), search()));
  }
  $("#npc-results").innerHTML = d.npcs.length
    ? `<p class="muted small">${fmt(d.total)} NPC${d.total === 1 ? "" : "s"}${d.total > d.npcs.length ? `, first ${d.npcs.length} shown` : ""}.</p>
      <section class="card scroll"><table><thead><tr><th>NPC</th><th>Race</th><th>Archetype</th><th>Zone</th><th>Role</th><th class=num>Lines done</th><th>Voice</th></tr></thead><tbody>
      ${d.npcs
        .map(
          (n) => `<tr><td><a href="/npcs?id=${n.id}"><b>${esc(n.name || `NPC ${n.id}`)}</b></a>${n.subname ? ` <span class="muted">&lt;${esc(n.subname)}&gt;</span>` : ""}
          <span class="mono muted">${n.id}</span></td><td>${esc([n.race, n.gender].filter(Boolean).join(" "))}</td>
          <td class=mono>${esc(n.archetype || "–")}</td><td>${esc(n.zone_name)}</td><td>${esc(n.role || "")}</td>
          <td class=num>${fmt(n.done)} / ${fmt(n.lines)}${n.quarantined ? ` <span class=bad>(${n.quarantined} q)</span>` : ""}</td>
          <td>${n.own_voice ? `<span class="tag ${n.build_status === "leftover" ? "bad" : "ok"}">${n.build_status === "leftover" ? "leftover" : "own"}</span>` : `<span class="tag">Archetype</span>`}</td></tr>`,
        )
        .join("")}</tbody></table></section>`
    : `<div class="card empty">No NPCs match.</div>`;
}

function renderNpc(d) {
  if (d.error) {
    main.innerHTML = `<h1><a href="/npcs">NPC browser</a></h1><div class="card empty">No NPC ${esc(params.get("id"))}.</div>`;
    return;
  }
  const n = d.npc;
  const v = d.voice;
  const r = v.ratings;
  const queuedVoice = d.queued.map((a) => `<span class="queued">queued: ${esc(a)}</span>`).join("");
  const anchor = v.own
    ? `<div class="sample"><div class="slabel"><span class="muted">NPC anchor</span><span class="stext" title="${esc(v.prompt || "")}">${esc(v.prompt || "")}</span></div>
       ${v.anchor_url ? `<audio controls preload="none" src="${esc(v.anchor_url)}"></audio>` : `<span class="muted">anchor file missing</span>`}</div>`
    : `<div class="sample"><div class="slabel"><span class="muted">Base Voice${v.archetype ? ` · ${esc(v.archetype.label)}${v.archetype.approved ? ` <span class=mono>${esc(v.archetype.approved.split("/")[1])}</span> (1 of ${v.archetype.base_voices})` : ""}` : ""}</span>
       <span class="stext">No NPC Voice yet: speaks with its Base Voice (one of its Archetype's approved anchors) until <span class=mono>vo voices</span> builds one.</span></div>
       ${v.archetype?.anchor_url ? `<audio controls preload="none" src="${esc(v.archetype.anchor_url)}"></audio>` : `<span class="muted">${v.archetype?.approved ? "anchor file missing" : "Archetype not approved yet"}</span>`}</div>`;
  const b = v.build;
  main.innerHTML = `<h1><a href="/npcs">NPC browser</a> <span class="muted">/</span> ${esc(n.name || `NPC ${n.id}`)}${n.subname ? ` <span class="muted">&lt;${esc(n.subname)}&gt;</span>` : ""}</h1>
  <section class="card">
    <div class="ahead"><span class="mono muted">${n.id}</span>
      ${n.race || n.gender ? `<span class="tag">${esc([n.race, n.gender].filter(Boolean).join(" "))}</span>` : ""}
      ${n.archetype ? `<span class="tag mono">${esc(n.archetype)}</span>` : ""}
      ${n.role ? `<span class="tag">${esc(n.role)}</span>` : ""}${n.is_named ? `<span class="tag">named</span>` : ""}
      ${n.source !== "core" ? `<span class="tag">${esc(n.source)}</span>` : ""}
      <span class="muted">${n.zones.map((z) => `<a href="/coverage?zone=${z.zone}">${esc(z.name)}</a>`).join(", ") || "no spawns"}</span></div>
    ${d.issues.length ? `<div class="small warn">${d.issues.map((i) => `${esc(i.issue)}: ${esc(i.detail || "")}`).join(" · ")}</div>` : ""}
    <div class="voice">
      ${anchor}
      <div class="muted small"><span class="mono">${esc(v.voice_id || "no voice yet")}</span>${b ? ` · roll ${b.roll} · ${esc(b.strategy || "")} · ${esc(b.status || "")}${b.issue ? ` (${esc(b.detail || b.issue)})` : ""}` : ""}</div>
      <div class="muted small">Ratings in this voice: <b>${r.up}</b> up · <b class="${r.down ? "bad" : ""}">${r.down}</b> down · ${r.flags} flag${r.flags === 1 ? "" : "s"}${v.flags.length ? ` (${v.flags.map((f) => esc(f.note || "no note")).join("; ")})` : ""}</div>
      <div class="qactions">${queuedVoice}
        <button data-a="flag-voice">Flag voice</button>
        <button data-a="reroll" ${v.can_reroll ? "" : `disabled title="No NPC Voice to re-roll yet: vo voices builds it"`}>Regenerate voice</button>
      </div>
    </div>
  </section>
  <h2 style="margin-top:20px">Lines (${d.lines.length})</h2>
  <div class="q">${d.lines.map(npcLine).join("")}</div>`;
  main.querySelector('[data-a="flag-voice"]').addEventListener("click", () => {
    const note = prompt(`Flag ${n.name}'s voice. Note (optional):`, "");
    if (note !== null) act("flag-voice", n.id, { voice_id: v.voice_id || undefined, note });
  });
  main.querySelector('[data-a="reroll"]').addEventListener("click", () => {
    if (confirm(`Re-roll ${n.name}'s voice? vo voices builds a new one; vo run then re-renders its lines.`)) act("reroll-voice", n.id);
  });
  main.querySelectorAll("[data-retry]").forEach((btn) =>
    btn.addEventListener("click", () => act("retry-line", Number(btn.dataset.retry), { voice_id: btn.dataset.voice })),
  );
}

function npcLine(l) {
  return `<article class="qline">
    <div class="qhead"><span class="tag ${STATUS_TAG[l.status] ?? ""}">${esc(l.status)}</span><span class="tag">${esc(l.type)}</span>
      ${l.quest_id ? `<span class="tag">quest ${l.quest_id}</span>` : ""}${l.player_gender ? `<span class="tag">player ${esc(l.player_gender)}</span>` : ""}
      <span class="muted mono">line ${l.line_id}</span>
      ${l.wer != null ? `<span class="${l.wer > 0.15 ? "bad" : "muted"}" title="ASR heard: ${esc(l.transcript || "")}">WER ${fmt(l.wer, 2)}</span>` : ""}
      ${l.rating ? `<span class="tag ${l.rating === "down" ? "bad" : "ok"}">rated ${esc(l.rating)}</span>` : ""}
      ${l.flags.map((f) => `<span class="tag bad" title="${esc(f || "")}">flagged</span>`).join("")}
      <span class="muted" style="margin-left:auto">${l.tries ? `${fmt(l.tries)} take${l.tries === 1 ? "" : "s"} · ` : ""}${when(l.updated_at)}</span></div>
    ${l.reason && l.status !== "done" ? `<div class="reason">${esc(l.reason)}</div>` : ""}
    <div class="qtext">${esc(l.tts_text || l.raw_text)}</div>
    <div class="qactions">
      ${l.audio_url ? `<audio controls preload="none" src="${esc(l.audio_url)}"></audio>${l.audio_status === "stale" ? `<span class="muted">stale take</span>` : ""}` : `<span class="muted">not voiced yet</span>`}
      <span style="flex:1"></span>
      ${l.queued.map((a) => `<span class="queued">queued: ${esc(a)}</span>`).join("")}
      ${l.voice_id ? `<button data-retry="${l.line_id}" data-voice="${esc(l.voice_id)}" ${l.status === "running" ? "disabled" : ""}>Regenerate line</button>` : ""}
    </div>
  </article>`;
}

// --- Spot-check -------------------------------------------------------------------------------------------------

const seen = []; // line ids played this session: not offered again
let spot = null; // the current snapshot
let autoNext = true;
try {
  autoNext = localStorage.getItem("vo.spot.autoNext") !== "0";
} catch {}

async function nextSpot(play = true) {
  const res = await fetch(`/api/spot-check?exclude=${seen.slice(-500).join(",")}`);
  if (!res.ok) return toast(`Failed: ${res.status}`);
  spot = await res.json();
  if (spot.line) seen.push(spot.line.line_id);
  renderSpot(spot);
  const a = main.querySelector("audio");
  if (play && a) a.play().catch(() => {});
}

function renderSpot(d) {
  const s = d.stats;
  const l = d.line;
  const head = `<h1>Spot-check</h1>
  <p class="muted">A random voiced line, weighted toward new lines, named NPCs and low ASR scores. <kbd>Space</kbd> plays,
  <kbd>J</kbd> thumbs up, <kbd>K</kbd> thumbs down, <kbd>N</kbd> next, <kbd>F</kbd> flag. ${REROLL_DOWNS} lines rated down in
  one NPC Voice queue a re-roll (applied by <span class=mono>vo voices</span>).</p>
  <div class="legend spotstats"><span>voiced <b>${fmt(s.voiced)}</b></span><span>checked <b>${fmt(s.checked)}</b></span>
    <span>up <b>${fmt(s.up)}</b></span><span>down <b>${fmt(s.down)}</b></span><span>flags <b>${fmt(s.flags)}</b></span>
    <label><input type="checkbox" id="auto-next" ${autoNext ? "checked" : ""}> next after rating</label></div>`;
  if (!l) {
    main.innerHTML = `${head}<div class="card empty">${seen.length ? "You've heard every voiced line this session." : "No voiced lines yet: vo run renders them."}</div>`;
  } else {
    const who = l.npc ? `<a href="/npcs?id=${l.npc_id}">${esc(l.npc)}</a>` : "Narrator";
    main.innerHTML = `${head}
    <section class="card spot">
      <div class="ahead"><b class="who">${who}</b>${l.subname ? `<span class="muted">&lt;${esc(l.subname)}&gt;</span>` : ""}
        ${l.race ? `<span class="tag">${esc(l.race)} ${esc(l.gender || "")}</span>` : ""}
        <span class="tag">${esc(l.zone_name)}</span><span class="tag">${esc(l.type)}</span>
        ${l.why.map((w) => `<span class="tag why">${esc(w)}</span>`).join("")}
        <span class="muted mono">line ${l.line_id}</span></div>
      <div class="spot-text">${esc(l.tts_text)}</div>
      <audio controls preload="auto" src="${esc(l.audio_url)}"></audio>
      ${l.transcript ? `<div class="muted small">ASR heard: <i>${esc(l.transcript)}</i>${l.wer != null ? ` · WER ${fmt(l.wer, 2)}` : ""}</div>` : ""}
      <div class="muted small"><span class="mono">${esc(l.voice_id)}</span> · this voice: ${l.voice.up} up, <span class="${l.voice.down ? "bad" : ""}">${l.voice.down} down</span>, ${l.voice.flags} flags
        ${l.own_voice ? "" : " · an Archetype anchor (shared): never auto re-rolled"}</div>
      ${l.rated ? `<div><span class="tag ${l.rated === "down" ? "bad" : "ok"}">rated ${esc(l.rated)}</span></div>` : ""}
      <div class="spot-keys">
        <button data-k="space" title="Space">Play <kbd>Space</kbd></button>
        <button data-k="j" class="${l.rated === "up" ? "on" : ""}">Thumbs up <kbd>J</kbd></button>
        <button data-k="k" class="${l.rated === "down" ? "on" : ""}">Thumbs down <kbd>K</kbd></button>
        <button data-k="f">Flag <kbd>F</kbd></button>
        <button data-k="n" class="primary">Next <kbd>N</kbd></button>
      </div>
      <input id="flag-note" class="note-input" placeholder="Flag note (optional), then F or Enter" maxlength="500">
    </section>`;
  }
  $("#auto-next")?.addEventListener("change", (e) => {
    autoNext = e.target.checked;
    try {
      localStorage.setItem("vo.spot.autoNext", autoNext ? "1" : "0");
    } catch {}
  });
  main.querySelectorAll("[data-k]").forEach((b) => b.addEventListener("click", () => (b.blur(), spotKey(b.dataset.k))));
  $("#flag-note")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") (e.preventDefault(), spotKey("f"));
    if (e.key === "Escape") e.target.blur();
  });
}

async function spotKey(k) {
  const l = spot?.line;
  if (k === "n") return nextSpot();
  if (!l) return;
  if (k === "space") {
    const a = main.querySelector("audio");
    if (a) a.paused ? a.play().catch(() => {}) : a.pause();
    return;
  }
  if (k === "j" || k === "k") {
    const ok = await postQuiet("rate-line", l.line_id, { voice_id: l.voice_id, rating: k === "j" ? "up" : "down" });
    if (!ok) return;
    toast(`Rated ${k === "j" ? "up" : "down"}${k === "k" && l.own_voice && l.voice.down + 1 >= REROLL_DOWNS ? `: ${REROLL_DOWNS} down, a re-roll will be queued` : ""}`);
    if (autoNext) return nextSpot();
    l.rated = k === "j" ? "up" : "down";
    renderSpot(spot);
  }
  if (k === "f") {
    const input = $("#flag-note");
    const ok = await postQuiet("flag-line", l.line_id, { voice_id: l.voice_id, note: input?.value || "" });
    if (!ok) return;
    toast("Flagged");
    if (input) input.value = "";
    if (autoNext) return nextSpot();
  }
}

/** POST an action without the page refresh `act` does (the player keeps its place). */
async function postQuiet(action, target, payload) {
  const res = await fetch("/api/actions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, target, payload }),
  });
  if (res.ok) return true;
  const body = await res.json().catch(() => ({}));
  toast(`Failed: ${body.error || res.status}`);
  return false;
}

const REROLL_DOWNS = 3; // vo.ratings.REROLL_DOWNS

document.addEventListener("keydown", (e) => {
  if (page !== "spot-check" || e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = document.activeElement?.tagName;
  if (tag === "INPUT" && document.activeElement.type !== "checkbox") return;
  if (tag === "TEXTAREA") return;
  const k = e.key === " " ? "space" : e.key.toLowerCase();
  if (!["space", "j", "k", "n", "f"].includes(k)) return;
  e.preventDefault();
  spotKey(k);
});

// --- Live updates -----------------------------------------------------------------------------------------------

const RENDER = {
  overview: renderOverview, quarantine: renderQuarantine, approval: renderApproval, separation: renderSeparation,
  coverage: renderCoverage, npcs: renderNpcs, "spot-check": renderSpot,
};
const LISTENING = new Set(["approval", "separation", "npcs"]); // pages for listening: updates wait for the player
const LIVE = new Set(["overview", "quarantine", "approval", "separation", "coverage"]); // SSE; the others load on demand
let pending = null;
let lastSnapshot = null;

// The Approval page is for listening: a live update waits while audio plays or a note is being typed.
const approvalBusy = () =>
  [...main.querySelectorAll("audio")].some((a) => !a.paused) ||
  (document.activeElement?.tagName === "TEXTAREA" && document.activeElement.value.trim() !== "") ||
  [...main.querySelectorAll("input[data-spell]")].some((i) => i.value !== i.defaultValue); // a Lexicon edit

function show(d) {
  if ((page === "quarantine" && editing.size) || (LISTENING.has(page) && approvalBusy())) {
    pending = d; // don't clobber an open editor or a playing clip
    return;
  }
  pending = null;
  const snap = JSON.stringify(d);
  if (LISTENING.has(page) && snap === lastSnapshot) return; // unchanged: keep the players as they are
  lastSnapshot = snap;
  RENDER[page](d);
}

document.addEventListener("ended", () => pending && LISTENING.has(page) && show(pending), true);
document.addEventListener("pause", () => pending && LISTENING.has(page) && show(pending), true);

function connect() {
  const live = $("#live");
  const es = new EventSource(`/events?page=${page}${location.search ? `&${location.search.slice(1)}` : ""}`);
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
if (LIVE.has(page)) connect();
else {
  $("#live").lastChild.textContent = "on demand";
  if (page === "spot-check") nextSpot(false);
  else refresh();
}
if (page !== "quarantine") fetch("/api/quarantine").then((r) => r.json()).then((d) => ($("#nav-q").textContent = d.lines.length || ""));
if (page !== "approval") fetch("/api/approval").then((r) => r.json()).then((d) => ($("#nav-a").textContent = approvalOpen(d) || ""));

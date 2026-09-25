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

// --- Approval ---------------------------------------------------------------------------------------------------

const openArch = new Set(); // expanded Archetype cards survive live re-renders
let archFilter = "open";
const pct = (n) => (n == null ? "–" : `${Math.round(n * 100)}%`);

const approvalOpen = (d) => d.progress.total - d.progress.approved + (d.lexicon ? d.lexicon.progress.total - d.lexicon.progress.reviewed : 0);

function renderApproval(d) {
  const p = d.progress;
  $("#nav-a").textContent = approvalOpen(d) || "";
  const shown = d.archetypes.filter((a) => archFilter === "all" || (archFilter === "open" ? !a.approved : a.approved));
  const n = d.narrator;
  main.innerHTML = `<h1>Approval <span class="muted">(${p.approved} / ${p.total} Archetypes approved)</span></h1>
  <p class="muted">Pick each Archetype's Anchor by ear: the Candidate with the right character, which still holds up over
  its sample lines (each is a continuation of that anchor, as <span class=mono>vo run</span> will speak every line).
  Actions are queued; <span class=mono>vo prepare</span> applies them, re-renders regenerated Archetypes, and writes
  <span class=mono>approved_voices.json</span> once all are approved.</p>
  <section class="card">
    <div class="bar" role="img" aria-label="Approved Archetypes">
      <span class="s-done" style="flex:${p.approved}"></span><span class="s-pending" style="flex:${p.total - p.approved || (p.total ? 0 : 1)}"></span>
    </div>
    <div class="legend"><span><i class="sw s-done"></i>approved <b>${p.approved}</b></span>
      <span><i class="sw s-pending"></i>open <b>${p.total - p.approved}</b></span>
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
  const approved = a.approved ? a.candidates.find((c) => c.id === a.approved) : null;
  const races = Object.entries(a.races).map(([r, k]) => `${esc(r)} (${k})`).join(", ");
  const queued = a.queued.map((q) => `<span class="queued">queued: ${esc(q.action.replace("-candidate", "").replace("-archetype", ""))} ${esc(q.action === "regenerate-archetype" ? q.note || "" : q.target.split("/")[1])}</span>`).join("");
  const status = a.approved
    ? `<span class="tag ok">approved ${esc(a.approved.split("/")[1])}</span>`
    : `<span class="tag">${a.candidates.filter((c) => c.status !== "rejected").length} candidates</span>`;
  const notes = a.notes.length ? ` <span class="note">${a.notes.map(esc).join(". ")}.</span>` : "";
  return `<details class="card arch" data-id="${esc(a.id)}" ${openArch.has(a.id) ? "open" : ""}>
    <summary class="ahead"><b>${esc(a.label)}</b><span class="mono muted">${esc(a.id)}</span>${status}
      ${a.kind === "family" ? `<span class="tag">Creature Family</span>` : ""}
      <span class="muted">${fmt(a.npcs)} NPCs · ${fmt(a.lines)} lines</span>${queued}</summary>
    <div class="muted small">${races}</div>
    <p class="desc">${esc(a.base_description)}${notes}</p>
    <div class="muted small">Anchor line: <i>${esc(a.anchor_text)}</i> · continuation <span class=mono>${esc(a.mode)}</span>
      · generation ${a.generation}${a.superseded ? ` (${a.superseded} superseded)` : ""}${a.anchor_chain ? ` · anchor effect chain <span class=mono>${esc(a.anchor_chain)}</span> (applied once to each anchor; lines continue from the processed clip)` : ""}${a.effect_chain ? ` · per-line effect chain <span class=mono>${esc(a.effect_chain)}</span>` : ""}</div>
    ${approved ? `<div class="muted small">Approved ${when(a.approved_at)}.</div>` : ""}
    ${a.candidates.length ? `<div class="cands">${a.candidates.map((c) => candCard(a, c)).join("")}</div>` : `<div class="empty">No Candidates yet: run <span class=mono>vo prepare --archetype ${esc(a.id)}</span>.</div>`}
    <div class="regen">
      <textarea data-note="${esc(a.id)}" rows="2" placeholder="Note for a regenerate, e.g. deeper, less theatrical (appended to the description)"></textarea>
      <button data-a="regen" data-target="${esc(a.id)}">Regenerate with note</button>
    </div>
  </details>`;
}

function candCard(a, c) {
  const on = c.id === a.approved;
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
      <button class="primary" data-a="approve" data-target="${esc(c.id)}" ${on ? "disabled" : ""}>${on ? "Approved" : "Approve"}</button>
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

// --- Coming pages -----------------------------------------------------------------------------------------------

function renderSoon() {
  const name = document.querySelector(`nav a[data-page="${page}"]`).textContent;
  main.innerHTML = `<div class="card soon-page"><h1>${esc(name)}</h1><p>${esc(SOON[page])}</p><p>Coming in #14.</p></div>`;
}

// --- Live updates -----------------------------------------------------------------------------------------------

const RENDER = { overview: renderOverview, quarantine: renderQuarantine, approval: renderApproval, separation: renderSeparation };
const LISTENING = new Set(["approval", "separation"]); // pages for listening: live updates wait for the player
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
if (page !== "approval") fetch("/api/approval").then((r) => r.json()).then((d) => ($("#nav-a").textContent = approvalOpen(d) || ""));

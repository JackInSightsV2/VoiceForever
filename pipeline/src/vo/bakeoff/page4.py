"""Round-4 pages. Pure: results dict in, HTML out.

pick_page: step 1, the Approval Gate prototype (pick one candidate anchor per voice).
result_page: step 2, the picked anchors' continuations, rated as in round 3.
"""
from __future__ import annotations

from html import escape

from vo.bakeoff import page3, round2, round3, round4
from vo.bakeoff.page import _fmt, _wer_cls

CSS = page3.CSS + """
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px}
.cand{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:12px;min-width:0}
.cand audio{width:100%;height:32px}
.cand.sel{outline:2px solid var(--accent)}
.cand .pick{display:block;margin-top:4px;font-size:13px;cursor:pointer}
.none{margin:8px 0;font-size:14px}
.warn{color:var(--bad)}
textarea.vn{min-height:60px}
dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 10px;font-size:13px;margin:6px 0}
dt{color:var(--muted)}dd{margin:0}
"""

PLAY_JS = """
let seqStop=null;
document.querySelectorAll('button.play').forEach(b=>b.addEventListener('click',()=>{
 if(seqStop){const same=seqStop.btn===b;seqStop();if(same)return;}
 const card=b.closest('.card');const cs=[...card.querySelectorAll('.seq .c')];let i=0,cur=null;
 const stop=()=>{if(cur){cur.pause();cur.onended=null;}cs.forEach(c=>c.classList.remove('playing'));b.textContent='▶ Play all in sequence';seqStop=null;};
 stop.btn=b;seqStop=stop;b.textContent='■ Stop';
 const next=()=>{cs.forEach(c=>c.classList.remove('playing'));if(i>=cs.length){stop();return;}
  const c=cs[i++];c.classList.add('playing');cur=c.querySelector('audio');cur.currentTime=0;
  cur.onended=()=>setTimeout(next,350);cur.play();};
 next();}));
document.addEventListener('play',e=>{document.querySelectorAll('audio').forEach(a=>{if(a!==e.target)a.pause()})},true);
"""

PICK_JS = """
const K='vo-bakeoff-r4-picks-v1';
const load=()=>{try{return JSON.parse(localStorage.getItem(K))||{}}catch(e){return{}}};
const save=s=>{try{localStorage.setItem(K,JSON.stringify(s))}catch(e){}};
const st=load();st.p=st.p||{};st.n=st.n||{};
const mark=()=>document.querySelectorAll('.cand').forEach(c=>{const r=c.querySelector('input');c.classList.toggle('sel',!!(r&&r.checked));});
document.querySelectorAll('input.p').forEach(r=>{
 if(st.p[r.dataset.voice]===r.value)r.checked=true;
 r.addEventListener('change',()=>{st.p[r.dataset.voice]=r.value;save(st);mark();});});
document.querySelectorAll('textarea.vn').forEach(t=>{t.value=st.n[t.dataset.voice]||'';
 t.addEventListener('input',()=>{st.n[t.dataset.voice]=t.value;save(st);});});
mark();
document.getElementById('export').addEventListener('click',()=>{
 const voices=VOICES;
 const picks=voices.filter(v=>st.p[v]).map(v=>`- ${v}: ${st.p[v]}`).join('\\n');
 const notes=voices.filter(v=>(st.n[v]||'').trim()).map(v=>`**${v}**: ${st.n[v].trim().replace(/\\n+/g,' ')}`).join('\\n\\n');
 const md='## Bake-off round 4: anchor picks\\n\\n'+(picks||'(none picked)')+(notes?'\\n\\n### Notes\\n\\n'+notes:'');
 navigator.clipboard.writeText(md).then(()=>{document.getElementById('export').textContent='Copied';});});
"""


def _st(f0: float | None, ref: float | None) -> str:
    d = round3.semitones(f0, ref)
    return "" if d is None else f" ({d:+.1f} st)"


def _median(clips: list[dict], name: str) -> float | None:
    return round3.median_feature(clips, name)


def candidate_card(voice: str, cid: str, c: dict, ref_f0: float | None, ref_hnr: float | None) -> str:
    f = c.get("features") or {}
    wer = c.get("wer")
    warn = (f' <span class="warn">ASR struggled: continuation inherits misreadings</span>'
            if wer is not None and wer > round4.ANCHOR_MAX_WER else "")
    hnr = f.get("hnr")
    d_hnr = f" ({hnr - ref_hnr:+.1f})" if hnr is not None and ref_hnr is not None else ""
    return (f'<div class="cand"><b>{escape(cid)}</b> <small class="muted">{c.get("audio_s", 0):.1f}s · '
            f'score {c.get("score", "–")}</small>'
            f'<audio controls preload="none" src="{escape(c["file"])}"></audio>'
            f'<dl><dt>pitch</dt><dd>{_fmt(f.get("f0"), "{:.0f} Hz")}{_st(f.get("f0"), ref_f0)}</dd>'
            f'<dt>roughness</dt><dd>HNR {_fmt(hnr, "{:.1f} dB")}{d_hnr}</dd>'
            f'<dt>darkness</dt><dd>centroid {_fmt(f.get("centroid"), "{:.0f} Hz")}</dd>'
            f'<dt>ASR</dt><dd class="{_wer_cls(wer)}">WER {_fmt(wer, "{:.0%}")}{warn}</dd></dl>'
            f'<details><summary>heard</summary><small>{escape(c.get("asr", ""))}</small></details>'
            f'<label class="pick"><input type="radio" class="p" name="pick-{escape(voice)}" data-voice="{escape(voice)}" '
            f'value="{escape(cid)}"> pick this anchor</label></div>')


def reference_card(results: dict, voice: str) -> str:
    ref = round4.reference_clips(results, voice)
    if not ref:
        return '<p class="muted">No round-2 vox-direct clips found.</p>'
    ids = [l.id for l in round4.lines(voice)]
    players = "".join(page3._clip(lid, ref.get(lid)) for lid in ids)
    return (f'<div class="card"><b>Character reference: round-2 vox-direct</b> <small class="muted">the clips that won '
            f'round 2 on character (each line designed afresh, so they drift). Median pitch '
            f'{_fmt(_median(list(ref.values()), "f0"), "{:.0f} Hz")}, HNR {_fmt(_median(list(ref.values()), "hnr"), "{:.1f} dB")}.'
            f'</small><div class="ctl"><button class="play" type="button">▶ Play all in sequence</button></div>'
            f'<div class="seq">{players}</div></div>')


def pick_section(results: dict, voice: str) -> str:
    v = round2.voice(voice)
    rec = results.get("anchors", {}).get(voice, {})
    ref = list(round4.reference_clips(results, voice).values())
    ref_f0, ref_hnr = _median(ref, "f0"), _median(ref, "hnr")
    cands = sorted(rec.get("cands", {}).items(), key=lambda x: int(x[0][1:]))
    grid = "".join(candidate_card(voice, cid, c, ref_f0, ref_hnr) for cid, c in cands) or '<p class="muted">Not rendered.</p>'
    return (f'<h2 id="v-{voice}">{escape(v.label)} <small class="muted">({escape(voice)})</small></h2>'
            f'<p><small class="muted">Description: {escape(rec.get("description", v.prompt))}</small></p>'
            f'<p><small class="muted">Anchor transcript (the continuation prompt): "{escape(rec.get("text", round4.anchor_text(voice)))}"'
            f'</small></p>{reference_card(results, voice)}'
            f'<h3>Candidate anchors</h3><p><small class="muted">Pitch and roughness in brackets are relative to the '
            f'round-2 reference above (semitones; HNR dB, lower is rougher). Target pitch {v.target.f0:g} Hz; '
            f'score = round 3\'s auto-pick distance from that target (it picked the anchors that lost character).'
            f'</small></p><div class="grid">{grid}</div>'
            f'<div class="card none"><label class="cand-none"><input type="radio" class="p" name="pick-{escape(voice)}" '
            f'data-voice="{escape(voice)}" value="{round4.NONE}"> <b>None of these have the character</b></label>'
            f'<textarea class="vn" data-voice="{escape(voice)}" placeholder="Notes on {escape(v.label)}: what\'s missing, '
            f'which came closest…"></textarea></div>')


def pick_page(results: dict) -> str:
    meta = results.get("meta", {}).get("step1", {})
    nav = "".join(f'<a href="#v-{v}">{escape(round2.voice(v).label)}</a>' for v in round4.VOICES)
    voices_js = "const VOICES=" + repr(list(round4.VOICES)).replace("'", '"') + ";"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>Pick NPC anchors</title><style>{CSS}</style></head>
<body><main>
<h1>Bake-off round 4, step 1: pick an anchor per voice</h1>
<p class="muted">Ticket #10. VoxCPM2 ({escape(round3.REPO)}) voice design, {len(round4.SEEDS)} seeds per voice.
{escape(meta.get("hardware", ""))}. Updated {escape(meta.get("updated", ""))}.</p>
<nav>{nav}<a href="#export-box">Export</a></nav>
<p>Round 3: continuation from an anchor keeps one identity, and copies the anchor faithfully, including its lack of
character when the anchor was auto-picked by pitch. Here each voice has {len(round4.SEEDS)} candidate anchors designed
from its round-2 description. Listen to the <b>character reference</b> first (the round-2 vox-direct clips you liked),
then pick the one anchor that has that character, or <b>none</b>. Step 2 renders the voice's 10 lines by continuing from
your pick. Picks and notes stay in this browser; <b>Copy as Markdown</b> exports <code>voice: candidate id</code> lines
for <code>vo bakeoff --round 4 --anchors picks.md</code>.</p>
{"".join(pick_section(results, v) for v in round4.VOICES)}
<h2 id="export-box">Export</h2>
<p><button id="export" type="button">Copy as Markdown</button></p>
</main><script>{voices_js}{PLAY_JS}{PICK_JS}</script></body></html>
"""


# --- step 2 ------------------------------------------------------------------

RESULT_JS = (page3.JS.replace("vo-bakeoff-r3-v1", "vo-bakeoff-r4-v1").replace("## Bake-off round 3", "## Bake-off round 4")
             .replace("(variant / voice: rating)", "(card / voice: rating)"))

LABELS = {
    round4.REFERENCE: ("Round-2 vox-direct (character reference)", "Designed afresh every line: the character to keep."),
    "cont": ("B. Continuation from the picked anchor", round3.variant("cont").note),
    "ultimate": ("B2. Ultimate cloning from the picked anchor", round3.variant("ultimate").note),
}


def result_section(results: dict, voice: str, cid: str) -> str:
    v = round2.voice(voice)
    if cid == round4.NONE:
        return (f'<h2 id="v-{voice}">{escape(v.label)}</h2><p class="muted">No anchor had the character (picked '
                f'"none"): not rendered.</p>')
    rows = results.get("stats", {}).get(voice, {})
    anchor = results["anchors"][voice]["cands"][cid]
    ids = [l.id for l in round4.lines(voice)]
    cards = []
    for key in (round4.REFERENCE, *round4.VARIANTS):
        clips = (round4.reference_clips(results, voice) if key == round4.REFERENCE
                 else round4.picked_clips(results, key, voice, cid))
        if not clips:
            continue
        title, note = LABELS[key]
        cards.append(page3.sequence_card(title, note, clips, ids, voice, key, rows.get(key),
                                         None if key == round4.REFERENCE else anchor))
    return (f'<h2 id="v-{voice}">{escape(v.label)} <small class="muted">anchor {escape(cid)}</small></h2>'
            f'<p><small class="muted">Anchor transcript: "{escape(round4.anchor_text(voice))}"</small></p>{"".join(cards)}')


def result_page(results: dict) -> str:
    meta = results.get("meta", {}).get("step2", {})
    picks = results.get("picks", {})
    nav = "".join(f'<a href="#v-{v}">{escape(round2.voice(v).label)}</a>' for v in picks)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>Picked anchor continuations</title><style>{CSS}</style></head>
<body><main>
<h1>Bake-off round 4, step 2: continuation from picked anchors</h1>
<p class="muted">Ticket #10. VoxCPM2 ({escape(round3.REPO)}), {escape(meta.get("hardware", ""))}. Updated {escape(meta.get("updated", ""))}.
Picks: {escape(", ".join(f"{v}: {c}" for v, c in picks.items()))}.</p>
<nav>{nav}<a href="#notes">Notes</a></nav>
<p>Each card reads the voice's 10 lines (the anchor first, where there is one). Press <b>Play all in sequence</b>: is it
<b>the same person / drifts / different people</b>, and is the <b>character kept / weaker / lost</b> against the round-2
reference card? Tick the <b>best</b> card per voice. Ratings stay in this browser; <b>Copy as Markdown</b> exports them.
Numbers: <b>within</b> = mean (min) speaker-embedding cosine over the voice's clips ({escape(round3.EMBEDDER_LABEL)});
<b>to anchor</b> / <b>to vox-direct</b> = mean cosine to the anchor / to the reference clips' centroid; <b>f0</b> and
<b>HNR</b> shifts are against the reference.</p>
{"".join(result_section(results, v, c) for v, c in picks.items())}
<h2 id="notes">Notes</h2>
<textarea id="notes" placeholder="Per voice: did the anchor's character survive continuation?"></textarea>
<p><button id="export" type="button">Copy as Markdown</button></p>
</main><script>{RESULT_JS}</script></body></html>
"""

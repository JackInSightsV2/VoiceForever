"""Round-5 listening page (orc male). Pure: results dict in, HTML out."""
from __future__ import annotations

from html import escape

from vo.bakeoff import page3, page4, round3, round5
from vo.bakeoff.page import _fmt, _wer_cls

CSS = page4.CSS + """
.rank{font-size:11px;color:var(--muted)}
.thirds{font-variant-numeric:tabular-nums}
.anchors{display:flex;flex-wrap:wrap;gap:10px;margin:6px 0}
.anchors .c{min-width:220px;flex:1}
.anchors audio{width:100%;height:30px}
"""

JS = """
const K='vo-bakeoff-r5-v1';
const load=()=>{try{return JSON.parse(localStorage.getItem(K))||{}}catch(e){return{}}};
const save=s=>{try{localStorage.setItem(K,JSON.stringify(s))}catch(e){}};
const st=load();st.r=st.r||{};st.p=st.p||[];
const mark=()=>document.querySelectorAll('.cand').forEach(c=>{const r=c.querySelector('input.pk');c.classList.toggle('sel',!!(r&&r.checked));});
document.querySelectorAll('input.pk').forEach(r=>{
 r.checked=st.p.includes(r.value);
 r.addEventListener('change',()=>{st.p=st.p.filter(x=>x!==r.value);if(r.checked)st.p.push(r.value);save(st);mark();});});
document.querySelectorAll('input.r').forEach(r=>{
 if(st.r[r.name]===r.value)r.checked=true;
 r.addEventListener('change',()=>{st.r[r.name]=r.value;save(st);});});
const notes=document.getElementById('notes');notes.value=st.notes||'';
notes.addEventListener('input',()=>{st.notes=notes.value;save(st);});
mark();
document.getElementById('export').addEventListener('click',()=>{
 const rows=Object.entries(st.r).sort();
 const pick=k=>rows.filter(([n])=>n.startsWith(k)).map(([n,v])=>`- ${n.slice(k.length)}: ${v}`).join('\\n');
 const md='## Bake-off round 5: orc male\\n\\n### Anchor picks (best first)\\n\\n'+(st.p.map(p=>`- pick: ${p}`).join('\\n')||'(none)')
  +'\\n\\n### Best\\n\\n'+(pick('best:')||'(none)')+'\\n\\n### Consistency\\n\\n'+(pick('cons:')||'(none)')
  +'\\n\\n### Character\\n\\n'+(pick('char:')||'(none)')+'\\n\\n## Notes\\n\\n'+(st.notes||'');
 navigator.clipboard.writeText(md).then(()=>{document.getElementById('export').textContent='Copied';});});
""" + page4.PLAY_JS


def _pct(x) -> str:
    return _fmt(x, "{:.0%}")


def _thirds(r: dict) -> str:
    t = r.get("rough_thirds") or []
    return " → ".join(_pct(x) for x in t) if t else "–"


def cand_card(cid: str, c: dict, rank: int | None, src: bool) -> str:
    f, r = c.get("features") or {}, c.get("rough3") or {}
    wer = c.get("wer")
    warn = ' <span class="warn">ASR struggled</span>' if wer is not None and wer > round5.ANCHOR_MAX_WER else ""
    tag = " · used in section 2" if src else ""
    return (f'<div class="cand"><b>{escape(cid)}</b> <span class="rank">#{rank if rank else "–"} · char '
            f'{_fmt(round5.char_score(c), "{:.2f}")}{tag}</span>'
            f'<audio controls preload="none" src="{escape(c["file"])}"></audio>'
            f'<dl><dt>rough</dt><dd class="thirds">{_pct(r.get("rough"))} <small class="muted">(thirds {_thirds(r)})</small></dd>'
            f'<dt>HNR</dt><dd>{_fmt(f.get("hnr"), "{:.1f} dB")} <small class="muted">drift {_fmt(r.get("drift"), "{:+.1f} dB")}</small></dd>'
            f'<dt>pitch</dt><dd>{_fmt(f.get("f0"), "{:.0f} Hz")}</dd>'
            f'<dt>ASR</dt><dd class="{_wer_cls(wer)}">WER {_pct(wer)}{warn}</dd></dl>'
            f'<details><summary>heard</summary><small>{escape(c.get("asr", ""))}</small></details>'
            f'<label class="pick"><input type="checkbox" class="pk" value="{escape(cid)}"> pick this anchor</label></div>')


def pick_section(results: dict) -> str:
    cands = results.get("anchors", {})
    if not cands:
        return '<p class="muted">Not rendered.</p>'
    ranking = results.get("ranking") or round5.rank(cands)
    pos = {cid: i for i, cid in enumerate(ranking, 1)}
    srcs = set(results.get("sources", []))
    top = ", ".join(f"{escape(c)} ({_fmt(round5.char_score(cands[c]), '{:.2f}')})" for c in ranking[:6])
    parts = [f'<p>Top by character proxy: {top}.</p>']
    groups = [("r4", "Round 4's near-miss (s6, round-2 description)",
               "", [round5.R4_ANCHOR] if round5.R4_ANCHOR in cands else [])]
    groups += [(d.id, d.label, d.text, [round5.cand_id(d.id, s) for s in round5.SEEDS]) for d in round5.DESCRIPTIONS]
    for gid, label, text, ids in groups:
        ids = sorted((i for i in ids if i in cands), key=lambda i: pos.get(i, 999))
        if not ids:
            continue
        grid = "".join(cand_card(i, cands[i], pos.get(i), i in srcs) for i in ids)
        desc = f'<p><small class="muted">Description: "{escape(text)}"</small></p>' if text else ""
        parts.append(f'<h3>{escape(label)} <small class="muted">({escape(gid)})</small></h3>{desc}<div class="grid">{grid}</div>')
    return "".join(parts)


def reference_card(results: dict) -> str:
    ref = results.get("reference", {})
    if not ref:
        return '<p class="muted">No round-2 vox-direct clips found.</p>'
    ids = [l.id for l in round5.lines()]
    return page3.sequence_card("Character reference: round-2 vox-direct",
                               "The clips that won round 2 on character. Each line was designed afresh, so they drift.",
                               ref, ids, "orc_m", round5.REFERENCE, results.get("stats", {}).get(round5.REFERENCE),
                               character=False)


def _anchor_strip(results: dict, a: str) -> str:
    raw = results.get("anchors", {}).get(a)
    proc = results.get("dsp_anchors", {}).get(a)
    items = []
    for label, c in (("raw anchor", raw), ("DSP'd anchor", proc)):
        if c:
            r = c.get("rough3") or {}
            items.append(f'<div class="c"><b>{label}</b><audio controls preload="none" src="{escape(c["file"])}"></audio>'
                         f'<small class="muted">rough {_pct(r.get("rough"))} ({_thirds(r)}) · HNR '
                         f'{_fmt((c.get("features") or {}).get("hnr"), "{:.1f}")} · f0 '
                         f'{_fmt((c.get("features") or {}).get("f0"), "{:.0f}")} · WER {_pct(c.get("wer"))}</small></div>')
    return f'<div class="anchors">{"".join(items)}</div>'


def _stat_line(r: dict | None) -> str:
    if not r:
        return ""
    return (f'<div class="stat">rough {_pct(r.get("rough"))} · sustained {_pct(r.get("sustained"))} · first→last third '
            f'{_pct(r.get("first"))} → {_pct(r.get("last"))} · drift {_fmt(r.get("drift"), "{:+.1f} dB")} · '
            f'HNR {_fmt(r.get("hnr"), "{:.1f}")} · f0 {_fmt(r.get("f0"), "{:.0f} Hz")}</div>')


def variant_section(results: dict) -> str:
    srcs = results.get("sources", [])
    if not srcs:
        return '<p class="muted">Not rendered.</p>'
    ids = [l.id for l in round5.lines()]
    st = results.get("stats", {})
    out = [reference_card(results)]
    for a in srcs:
        cards = []
        for v in round5.VARIANTS:
            clips = round5.variant_clips(results, v.id, a)
            if not clips:
                continue
            key = f"{v.id}@{a}"
            note = v.note
            if v.id == "cont-dsp" and results.get("tune", {}).get("chain"):
                note += f' Strength {results["tune"]["k"]:g}: {results["tune"]["chain"]}.'
            card = page3.sequence_card(f"{v.item or 'control'}. {v.label}", note, clips, ids, "orc_m", key, st.get(key))
            cards.append(card.replace('<div class="ctl">', _stat_line(st.get(key)) + '<div class="ctl">', 1))
        how = " (best raw anchor: also post-processed)" if a == srcs[0] else ""
        out.append(f'<h3 id="a-{escape(a)}">From anchor {escape(a)}{how}</h3>{_anchor_strip(results, a)}{"".join(cards)}')
    return "".join(out)


COLS = ("within", "anchor_sim", "arch_sim", "f0", "f0_sd", "hnr", "rough", "sustained", "first", "last", "drift",
        "wer", "rtf")


def numbers_table(results: dict) -> str:
    st = results.get("stats", {})
    if not st:
        return '<p class="muted">No stats yet.</p>'
    fmt = {"within": "{:.3f}", "anchor_sim": "{:.3f}", "arch_sim": "{:.3f}", "f0": "{:.0f}", "f0_sd": "{:.1f}",
           "hnr": "{:.1f}", "rough": "{:.0%}", "sustained": "{:.0%}", "first": "{:.0%}", "last": "{:.0%}",
           "drift": "{:+.1f}", "wer": "{:.1%}", "rtf": "{:.2f}"}
    rows = []
    for key, r in st.items():
        label = "round-2 vox-direct (reference)" if key == round5.REFERENCE else key
        cells = "".join(f'<td class="n">{_fmt(r.get(c), fmt[c])}</td>' for c in COLS)
        rows.append(f'<tr><td>{escape(label)}</td>{cells}</tr>')
    head = ("<th>Variant @ anchor</th><th>Within</th><th>To anchor</th><th>To r2</th><th>f0 Hz</th><th>f0 sd st</th>"
            "<th>HNR dB</th><th>Rough</th><th>Sustained</th><th>1st third</th><th>Last third</th><th>Drift dB</th>"
            "<th>WER</th><th>RTF</th>")
    return (f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'<p class="muted"><small><b>Within</b>: mean speaker-embedding cosine over the 45 pairs of the 10 lines '
            f'({escape(round3.EMBEDDER_LABEL)}; its card suggests {round3.EMBEDDER_SAME} for same speaker). <b>To anchor</b>: '
            f'mean cosine to the clip the lines copy (the DSP\'d anchor for dsp-anchor and cont-vc). <b>To r2</b>: to the '
            f'centroid of the round-2 vox-direct clips. <b>Rough</b>: median share of voiced 10 ms frames with HNR below '
            f'7 dB (orc reference ~75%, the human voices ~45%); <b>sustained</b>: the same for each line\'s cleanest third; '
            f'<b>1st / last third</b> and <b>drift</b> (last minus first third HNR, positive = cleaned up) catch a growl '
            f'that fades within a line. f0 and HNR are medians over lines. The subharmonic stage makes the pitch tracker '
            f'report roughly half the pitch on processed clips: read f0 there as "lower", not as a number. RTF = audio / '
            f'wall time, including the continuation render for the post-processed variants.</small></p>')


def tune_section(results: dict) -> str:
    t = results.get("tune", {})
    if not t.get("sweep"):
        return '<p class="muted">Not run.</p>'
    rows = "".join(f'<tr{" class=hl" if float(k) == t.get("k") else ""}><td>{escape(k)}</td>'
                   f'<td class="n {_wer_cls(v["wer"])}">{v["wer"]:.1%}</td><td class="n">{_pct(v["rough"])}</td>'
                   f'<td><small>{escape(v["chain"])}</small></td></tr>' for k, v in t["sweep"].items())
    ladder = "".join(page3._clip(f"strength {k}", c) for k, c in t.get("ladder", {}).items())
    return (f'<p>Per-line chain strength, tuned on the {escape(t.get("anchor", ""))} continuation lines: the strongest '
            f'whose mean WER stays within {round5.WER_BUDGET:.0%} of the unprocessed lines ({t.get("base_wer", 0):.1%}). '
            f'Picked: <b>{t.get("k", 0):g}</b>.</p>'
            f'<div class="scroll"><table><thead><tr><th>Strength</th><th>WER</th><th>Rough</th><th>Chain</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div><div class="card"><b>One line at each strength</b><div class="seq">{ladder}</div></div>')


def render(results: dict) -> str:
    meta = results.get("meta", {})
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>Orc male growl</title><style>{CSS}</style></head>
<body><main>
<h1>Bake-off round 5: a guttural orc that stays orc</h1>
<p class="muted">Ticket #10. Orc male only. VoxCPM2 ({escape(round3.REPO)}), Chatterbox S3Gen for VC, DSP via Praat
(parselmouth) and pedalboard. {escape(meta.get("hardware", ""))}. Updated {escape(meta.get("updated", ""))}.</p>
<nav><a href="#pick">1. Anchors</a><a href="#variants">2. Variants</a><a href="#numbers">Numbers</a>
<a href="#tune">DSP strength</a><a href="#notes">Notes</a></nav>
<p>Round 4: no VoxCPM2 anchor had the orc character; s6 came closest but "cleaned itself up" into a human male. Here:
<b>(1)</b> {len(round5.DESCRIPTIONS)} harder-pushing descriptions × {len(round5.SEEDS)} seeds, ranked by a character proxy
(roughness sustained over the whole clip); <b>(2)</b> continuation from the orc-DSP'd anchor; <b>(3)</b> continuation with
the orc chain on every line; <b>(4)</b> continuation lines re-voiced by Chatterbox VC to the DSP'd anchor.
Section 2 used anchors: <b>{escape(", ".join(results.get("sources", [])))}</b> ({escape(results.get("sources_how", ""))}).
Picks, ratings and notes stay in this browser; <b>Copy as Markdown</b> exports them. To re-render section 2 from your
picks: save the export as picks.md and run <code>vo bakeoff --round 5 --anchors picks.md</code>.</p>
<p><small class="muted">Orc chain (anchor, strength {round5.ANCHOR_STRENGTH:g}): {escape(round5.ORC_CHAIN.describe())}.
Subharm = period doubling (every other pitch period attenuated); growl = distorted, rasped octave-down layer.</small></p>
<h2 id="pick">1. Anchor candidates</h2>
<p><small class="muted">Anchor transcript: "{escape(round5.ANCHOR_TEXT)}" Char score = sustained roughness + half the
whole-clip roughness, minus 0.1 per dB cleaned up (last vs first third), minus 0.3 per octave above
{round5.HIGH_F0:g} Hz, minus 1 if ASR failed. Rough thirds: share of rough frames in the first → middle → last third.</small></p>
{pick_section(results)}
<h2 id="variants">2. Rendered variants</h2>
<p>Each card reads the 10 orc lines. <b>Play all in sequence</b>: is it the <b>same person / drifts / different</b>, is the
<b>character kept / weaker / lost</b> against the round-2 reference, and which card is <b>best</b>?</p>
{variant_section(results)}
<h2 id="numbers">Numbers</h2>{numbers_table(results)}
<h2 id="tune">Per-line DSP strength</h2>{tune_section(results)}
<h2 id="notes">Notes</h2>
<textarea id="notes" placeholder="Which one is a guttural orc that stays orc? What's still missing?"></textarea>
<p><button id="export" type="button">Copy as Markdown</button></p>
</main><script>{JS}</script></body></html>
"""

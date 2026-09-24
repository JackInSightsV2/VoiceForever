"""Round-3 listening page: is each variant's set of lines one person? Pure: results dict in, HTML out."""
from __future__ import annotations

from html import escape

from vo.bakeoff import round2, round3
from vo.bakeoff.page import CSS as R1_CSS, _fmt, _wer_cls
from vo.bakeoff.page2 import _feat

CSS = R1_CSS + """
h3{font-size:16px;margin:22px 0 6px}
.seq{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:6px 10px;margin-top:8px}
.seq .c{font-size:12px}.seq audio{width:100%;height:30px}
.seq .c.playing{outline:2px solid var(--accent);border-radius:6px}
.ctl{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;margin:6px 0}
.ctl label{white-space:nowrap;font-size:13px}
button.play{font:inherit;font-size:13px;padding:3px 10px;border-radius:6px;border:1px solid var(--line);
 background:var(--card);color:var(--fg);cursor:pointer}
.stat{font-size:12px;color:var(--muted)}
.hl{background:color-mix(in srgb,var(--accent) 12%,transparent)}
code{font-size:12px}
td.pairs{font-size:12px}
"""

JS = """
const K='vo-bakeoff-r3-v1';
const load=()=>{try{return JSON.parse(localStorage.getItem(K))||{}}catch(e){return{}}};
const save=s=>{try{localStorage.setItem(K,JSON.stringify(s))}catch(e){}};
const st=load();st.r=st.r||{};
document.querySelectorAll('input.r').forEach(r=>{
 if(st.r[r.name]===r.value)r.checked=true;
 r.addEventListener('change',()=>{st.r[r.name]=r.value;save(st);});});
const notes=document.getElementById('notes');notes.value=st.notes||'';
notes.addEventListener('input',()=>{st.notes=notes.value;save(st);});
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
document.getElementById('export').addEventListener('click',()=>{
 const rows=Object.entries(st.r).sort();
 const best=rows.filter(([k])=>k.startsWith('best:')).map(([k,v])=>`- ${k.slice(5)}: ${v}`).join('\\n');
 const cons=rows.filter(([k])=>k.startsWith('cons:')).map(([k,v])=>`- ${k.slice(5)}: ${v}`).join('\\n');
 const other=rows.filter(([k])=>!k.startsWith('best:')&&!k.startsWith('cons:')).map(([k,v])=>`- ${k}: ${v}`).join('\\n');
 const md='## Bake-off round 3\\n\\n### Best per voice\\n\\n'+best+'\\n\\n### Consistency (variant / voice: rating)\\n\\n'+cons
  +(other?'\\n\\n### Other\\n\\n'+other:'')+'\\n\\n## Notes\\n\\n'+(st.notes||'');
 navigator.clipboard.writeText(md).then(()=>{document.getElementById('export').textContent='Copied';});});
"""

RESEARCH = """
<p>What VoxCPM2 (openbmb/VoxCPM2, Apache-2.0) offers for a stable voice, from its
<a href="https://huggingface.co/openbmb/VoxCPM2">model card</a>, its
<a href="https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/core.py">reference code</a> and the
<a href="https://github.com/Blaizzy/mlx-audio/tree/main/mlx_audio/tts/models/voxcpm2">mlx-audio port</a> used here (0.5.6):</p>
<ul>
<li><b>Voice design</b>: "(description)text" with no audio. The card warns that design and style-control
"results may vary between runs; generating 1–3 times is recommended". This is round 2's vox-direct.</li>
<li><b>Controllable cloning</b>: a reference clip (<code>reference_wav_path</code>; mlx <code>ref_audio</code>) plus an
optional "(style)" prefix "to steer emotion, pace, and expression while preserving timbre". Variant C.</li>
<li><b>Ultimate cloning</b>: reference clip and its transcript as a continuation prompt (<code>prompt_wav_path</code> +
<code>prompt_text</code>); pass the same clip as reference too "for highest similarity". Variants B (continuation only) and B2 (both).</li>
<li><b>Continuation</b> (mlx mode "prompt_text + prompt_audio"): the new line is generated as the next words after the prompt
audio, so it inherits its voice and delivery; the prompt audio is trimmed from the output.</li>
<li><b>Seed</b>: the reference code takes <code>seed</code>; mlx-audio has none, so <code>mx.random.seed</code> is set
before each line (the only randomness is the diffusion noise). Variant A.</li>
<li><b>Prompt cache</b>: the reference code builds the prompt/reference latents once (<code>build_prompt_cache</code>,
<code>_generate_with_prompt_cache</code>). That saves time, not identity; mlx-audio re-encodes the clip every call.</li>
<li><b>LoRA fine-tuning</b> on 5–10 minutes of audio (card). Per NPC it's out of reach; per Archetype it's a later option.</li>
<li><b>cfg / timesteps</b>: cfg 2.0 and 10 steps are the defaults and were kept, so the variants differ only in conditioning.</li>
</ul>
"""


def _clip(label: str, clip: dict | None) -> str:
    if not clip:
        return f'<div class="c muted">{escape(label)}<br><small>not rendered</small></div>'
    meta = [f'{clip.get("audio_s", 0):.1f}s']
    if clip.get("wer") is not None:
        meta.append(f'<span class="{_wer_cls(clip["wer"])}">WER {clip["wer"]:.0%}</span>')
    f = clip.get("features") or {}
    if f.get("f0") is not None:
        meta.append(f'f0 {f["f0"]:.0f}')
    if f.get("hnr") is not None:
        meta.append(f'HNR {f["hnr"]:.1f}')
    asr = (f'<details><summary>ASR</summary><small>{escape(clip["asr"])}</small></details>'
           if clip.get("asr") else "")
    return (f'<div class="c"><b>{escape(label)}</b><audio controls preload="none" src="{escape(clip["file"])}"></audio>'
            f'<small class="muted">{" · ".join(meta)}</small>{asr}</div>')


def _radios(name: str, options: tuple[tuple[str, str], ...]) -> str:
    return "".join(f'<label><input type="radio" class="r" name="{escape(name)}" value="{escape(v)}"> {escape(t)}</label>'
                   for v, t in options)


CONSISTENCY = (("same", "same person"), ("drifts", "drifts"), ("different", "different people"))
CHARACTER = (("kept", "character kept"), ("weaker", "weaker"), ("lost", "lost"))


def _row_stats(r: dict | None) -> str:
    if not r:
        return ""
    parts = [f'within {_fmt(r.get("within"), "{:.3f}")} (min {_fmt(r.get("within_min"), "{:.3f}")})']
    if r.get("anchor_sim") is not None:
        parts.append(f'to anchor {r["anchor_sim"]:.3f}')
    if r.get("arch_sim") is not None:
        parts.append(f'to vox-direct {r["arch_sim"]:.3f}')
    parts.append(f'WER {_fmt(r.get("wer"), "{:.1%}")}')
    if r.get("rtf"):
        parts.append(f'RTF {r["rtf"]:.2f}')
    if r.get("f0_sd") is not None:
        parts.append(f'f0 sd {r["f0_sd"]:.1f} st')
    if r.get("d_st") is not None:
        parts.append(f'f0 {r["d_st"]:+.1f} st')
    if r.get("d_hnr") is not None:
        parts.append(f'HNR {r["d_hnr"]:+.1f} dB')
    return '<div class="stat">' + " · ".join(parts) + '</div>'


def sequence_card(title: str, note: str, clips: dict[str, dict], line_ids: list[str], group: str, row_id: str,
                  stats: dict | None = None, anchor: dict | None = None, character: bool = True,
                  best: bool = True) -> str:
    """One variant × one voice: its lines playable in sequence, plus the rating controls."""
    players = ""
    if anchor:
        players += _clip("anchor", anchor)
    players += "".join(_clip(lid, clips.get(lid)) for lid in line_ids)
    ctl = _radios(f"cons:{row_id}/{group}", CONSISTENCY)
    if character:
        ctl += '<span class="muted">|</span>' + _radios(f"char:{row_id}/{group}", CHARACTER)
    if best:
        ctl += '<span class="muted">|</span>' + _radios(f"best:{group}", ((row_id, "best for this voice"),))
    return (f'<div class="card"><b>{escape(title)}</b> <small class="muted">{escape(note)}</small>'
            f'{_row_stats(stats)}<div class="ctl"><button class="play" type="button">▶ Play all in sequence</button>'
            f'{ctl}</div><div class="seq">{players}</div></div>')


def _lines(voice: str) -> list[str]:
    return [l.id for l in round2.lines() if l.voice == voice]


def _subject_clips(results: dict, vid: str, key: str) -> dict[str, dict]:
    pre = key + "/"
    return {k[len(pre):]: c for k, c in results.get("clips", {}).get(vid, {}).items() if k.startswith(pre)}


def _anchor(results: dict, key: str) -> dict | None:
    rec = results.get("anchors", {}).get(key)
    if not rec or rec.get("chosen") is None:
        return None
    return rec["cands"][rec["chosen"]]


def _variant_label(vid: str) -> tuple[str, str]:
    if vid == round3.BASELINE:
        return "Round-2 vox-direct (baseline)", "Round 2's winner: designed afresh on every line."
    if vid == "r2-omni":
        return "Round-2 OmniVoice ← VoxCPM2 ref", "Won dwarf female in round 2 (10/10)."
    v = round3.variant(vid)
    return f"{v.letter}. {v.label}", v.note


def anchors_block(results: dict, key: str) -> str:
    rec = results.get("anchors", {}).get(key)
    if not rec:
        return ""
    items = "".join(_clip(f"seed {s} · score {c.get('score', '–')}" + (" · chosen" if s == rec.get("chosen") else ""), c)
                    for s, c in sorted(rec["cands"].items(), key=lambda x: int(x[0])))
    s = round3.subject(key)
    return (f'<details><summary>Anchor candidates</summary><p class="muted"><small>The anchor is the NPC\'s identity '
            f'clip: its description reading "{escape(s.anchor_text)}" The chosen one has the lowest round-2 '
            f'Candidate score (distance from the target pitch/roughness, plus ASR WER).</small></p>'
            f'<div class="seq">{items}</div></details>')


def voice_section(results: dict, voice: str) -> str:
    v = round2.voice(voice)
    rows = results.get("stats", {}).get("variants", {})
    cards = []
    for vid in (round3.BASELINE, *round3.RENDER_ORDER):
        clips = _subject_clips(results, vid, voice)
        if not clips:
            continue
        title, note = _variant_label(vid)
        anchor = _anchor(results, voice) if vid != round3.BASELINE and round3.variant(vid).anchor else None
        cards.append(sequence_card(title, note, clips, _lines(voice), voice, vid,
                                   rows.get(vid, {}).get("rows", {}).get(voice), anchor))
    return (f'<h2 id="v-{voice}">{escape(v.label)}</h2><p><small class="muted">Description: {escape(v.prompt)}</small></p>'
            f'{anchors_block(results, voice)}{"".join(cards) or "<p class=muted>Not rendered.</p>"}')


def dwarf_section(results: dict) -> str:
    st = results.get("stats", {}).get("dwarf", {})
    cards = []
    for vid, key in ((round3.BASELINE, "dwarf_f"), ("r2-omni", "dwarf_f"),
                     *((v, "dwarf_f2") for v in round3.VARIANT_IDS)):
        clips = _subject_clips(results, vid, key)
        if not clips:
            continue
        title, note = _variant_label(vid)
        if key == "dwarf_f2":
            title += " · new description"
        anchor = _anchor(results, key) if vid not in (round3.BASELINE, "r2-omni") and round3.variant(vid).anchor else None
        cards.append(sequence_card(title, note, clips, _lines("dwarf_f"), "dwarf_f", f"{vid}@{key}",
                                   st.get(f"{vid}|{key}"), anchor))
    return (f'<p><small class="muted">Round-2 description: {escape(round2.voice("dwarf_f").prompt)}</small></p>'
            f'<p><small class="muted">New description: {escape(round3.DWARF_F_V2)}</small></p>'
            f'{anchors_block(results, "dwarf_f2")}{"".join(cards)}')


def npc_section(results: dict) -> str:
    npc = results.get("stats", {}).get("npc")
    if not npc:
        return '<p class="muted">Not rendered.</p>'
    vid = npc["variant"]
    title, note = _variant_label(vid)
    cards = []
    for key in npc.get("rows", {}):
        label = "Archetype's own anchor (orc male above)" if key == "orc_m" else f"NPC {key.split('@')[1]}"
        cards.append(sequence_card(label, "", _subject_clips(results, vid, key), _lines("orc_m"), "npc", key,
                                   npc["rows"][key], _anchor(results, key), character=False, best=False))
    pairs = "".join(f'<tr><td>{escape(k.replace("|", " vs "))}</td><td class="n">{s:.3f}</td>'
                    f'<td class="n">{_fmt(npc.get("anchor_pairs", {}).get(k), "{:.3f}")}</td></tr>'
                    for k, s in npc.get("between", {}).items())
    selfs = "".join(f'<tr class="hl"><td>{escape(k)}: its own lines</td><td class="n">{w["mean"]:.3f}</td><td></td></tr>'
                    for k, w in npc.get("within", {}).items() if w.get("mean") is not None)
    tell = ('<div class="card"><b>Can you tell the three NPCs apart?</b><div class="ctl">'
            + _radios("npc:apart", (("all", "all three distinct"), ("some", "two sound alike"), ("none", "all the same")))
            + '</div></div>')
    return (f'<p><small>Variant: <b>{escape(title)}</b>. {escape(note)} Each NPC\'s anchor is designed from the same '
            f'orc male description with the NPC id as the seed (re-rolled if ASR can\'t follow it), so there\'s no human '
            f'step.</small></p>{tell}{"".join(cards)}'
            '<h3>Within-NPC vs between-NPC similarity</h3><div class="scroll"><table><thead><tr><th>Pair</th>'
            '<th>Clip cosine (mean)</th><th>Anchor cosine</th></tr></thead><tbody>' + selfs + pairs +
            '</tbody></table></div>'
            f'<p class="muted"><small>Margin (lowest within minus highest between): {_fmt(npc.get("margin"), "{:+.3f}")}. '
            'The Neighbour floor needs between-NPC scores clearly below within-NPC ones.</small></p>')


def numbers_table(results: dict) -> str:
    st = results.get("stats", {}).get("variants", {})
    if not st:
        return '<p class="muted">No stats yet.</p>'
    head = "".join(f'<th>{escape(round2.voice(v).label)}</th>' for v in round3.MAIN_VOICES)
    rows = []
    best = (results.get("best") or {}).get("variant")
    for vid, t in st.items():
        title, _ = _variant_label(vid)
        cells = []
        for v in round3.MAIN_VOICES:
            r = t["rows"].get(v)
            if not r:
                cells.append('<td class="muted">–</td>')
                continue
            keep = "" if r.get("keeps") is None else (" ✓" if r["keeps"] else ' <span class="bad">✗</span>')
            extra = []
            if r.get("anchor_sim") is not None:
                extra.append(f'anchor {r["anchor_sim"]:.2f}')
            if r.get("arch_sim") is not None:
                extra.append(f'vs r2 {r["arch_sim"]:.2f}')
            if r.get("f0_sd") is not None:
                extra.append(f'f0 sd {r["f0_sd"]:.1f} st')
            cells.append(f'<td class="n"><b>{_fmt(r["within"], "{:.3f}")}</b> <small>min {_fmt(r["within_min"], "{:.2f}")}</small>'
                         f'<br><small>{" · ".join(extra)}</small>'
                         f'<br><small class="{_wer_cls(r["wer"])}">WER {_fmt(r["wer"], "{:.0%}")}</small>'
                         f'<small> · {_fmt(r.get("d_st"), "{:+.1f} st")} · HNR {_fmt(r.get("d_hnr"), "{:+.1f}")}{keep}</small></td>')
        rtfs = [r["rtf"] for r in t["rows"].values() if r.get("rtf")]
        rtf = sum(rtfs) / len(rtfs) if rtfs else None
        cls = ' class="hl"' if vid == best else ""
        rows.append(f'<tr{cls}><td>{escape(title)}<br><small class="muted">{escape(vid)}</small></td>{"".join(cells)}'
                    f'<td class="n"><b>{_fmt(t.get("within_mean"), "{:.3f}")}</b></td>'
                    f'<td class="n">{_fmt(t.get("between_mean"), "{:.3f}")}<br><small>max {_fmt(t.get("between_max"), "{:.3f}")}</small></td>'
                    f'<td class="n">{_fmt(rtf, "{:.2f}")}</td></tr>')
    return ('<div class="scroll"><table><thead><tr><th>Variant</th>' + head +
            '<th>Within (mean)</th><th>Between voices</th><th>RTF</th></tr></thead><tbody>' + "".join(rows) +
            '</tbody></table></div>'
            f'<p class="muted"><small>Speaker embeddings: {escape(round3.EMBEDDER_LABEL)}; its card suggests '
            f'{round3.EMBEDDER_SAME} as a same-speaker cosine threshold. <b>Within</b> = mean (and min) cosine over the 45 '
            'pairs of a voice\'s 10 clips. <b>anchor</b> = mean cosine of the clips to the anchor clip. <b>vs r2</b> = mean '
            'cosine to the centroid of round-2 vox-direct\'s clips of that voice (does it still sound like the voice that '
            'won?). <b>f0 sd</b> = spread of the lines\' median pitch in semitones (drift without embeddings). <b>st</b> / <b>HNR</b> = median pitch (semitones) and roughness (dB, lower is rougher) against round-2 '
            f'vox-direct. ✓ = keeps character by rule: pitch within {round3.MAX_F0_ST:g} st, HNR up by at most '
            f'{round3.MAX_HNR_RISE:g} dB, WER at most {round3.WER_SLACK:.0%} over vox-direct. <b>Between voices</b> = mean '
            '(and max) clip cosine between two different voices of the same variant. Troll WER is inflated by the '
            'dialect spellings in the script ("dem", "de", "mon"), not only by the audio. Accent is not measured: listen.</small></p>')


def r2_table(results: dict) -> str:
    st = results.get("stats", {}).get("r2", {})
    if not st:
        return ""
    voices = round2.VOICE_IDS
    head = "".join(f'<th>{escape(v)}</th>' for v in voices)
    rows = []
    for app, s in st.items():
        cells = "".join(f'<td class="n">{_fmt(s["within"].get(v, {}).get("mean"), "{:.3f}")}</td>' for v in voices)
        cls = ' class="hl"' if app == "vox-direct" else ""
        rows.append(f'<tr{cls}><td>{escape(app)}</td>{cells}<td class="n"><b>{_fmt(s["within_mean"], "{:.3f}")}</b></td>'
                    f'<td class="n">{_fmt(s["between_mean"], "{:.3f}")}</td><td class="n">{_fmt(s["between_max"], "{:.3f}")}</td>'
                    f'<td class="n">{_fmt(s.get("margin"), "{:+.3f}")}</td></tr>')
    return ('<div class="scroll"><table><thead><tr><th>Round-2 approach</th>' + head +
            '<th>Within mean</th><th>Between mean</th><th>Between max</th><th>Margin</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table></div><p class="muted"><small>Round-2 clips re-measured with the same '
            'embedder. Margin = lowest within-voice minus highest between-voice cosine.</small></p>')


def render(results: dict) -> str:
    meta = results.get("meta", {})
    best = (results.get("best") or {})
    nav = "".join(f'<a href="#v-{v}">{escape(round2.voice(v).label)}</a>' for v in round3.MAIN_VOICES)
    voices = "".join(voice_section(results, v) for v in round3.MAIN_VOICES)
    bl = _variant_label(best["variant"])[0] if best.get("variant") else "–"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>NPC voice consistency</title><style>{CSS}</style></head>
<body><main>
<h1>Bake-off round 3: one voice per NPC</h1>
<p class="muted">Ticket #10. VoxCPM2 ({escape(round3.REPO)}), {escape(meta.get("hardware", ""))}. Updated {escape(meta.get("updated", ""))}.</p>
<nav><a href="#numbers">Numbers</a><a href="#r2">Round-2 consistency</a>{nav}<a href="#dwarf">Dwarf female</a><a href="#npcs">Distinct NPCs</a><a href="#notes">Notes</a><a href="#research">VoxCPM2 features</a></nav>
<p>Round 2's vox-direct had the right character but re-designs the voice on every line. Each card below is one
variant reading one voice's 10 lines. Press <b>Play all in sequence</b> and ask: is this the same person? Rate
<b>same person / drifts / different people</b>, whether the <b>character</b> (accent, grit, attitude) survived against the
baseline card, and tick the <b>best</b> card per voice. Ratings stay in this browser; <b>Copy as Markdown</b> exports them.
The dwarf and NPC tests use the variant picked by rule: <b>{escape(bl)}</b> ({escape(best.get("how", ""))}).</p>
<h2 id="numbers">Numbers</h2>{numbers_table(results)}
<h2 id="r2">Round-2 consistency, re-measured</h2>{r2_table(results)}
{voices}
<h2 id="dwarf">Dwarf, female</h2>{dwarf_section(results)}
<h2 id="npcs">Distinct NPCs: three orcs from one description</h2>{npc_section(results)}
<h2 id="notes">Notes</h2>
<textarea id="notes" placeholder="Which variant, per race; what drifted…"></textarea>
<p><button id="export" type="button">Copy as Markdown</button></p>
<h2 id="research">What VoxCPM2 supports</h2>{RESEARCH}
</main><script>{JS}</script></body></html>
"""

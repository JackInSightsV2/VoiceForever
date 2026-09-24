"""Round-2 listening page. Pure: results dict in, HTML string out."""
from __future__ import annotations

import statistics
from html import escape

from vo.bakeoff import catalog, dsp, metrics, round2
from vo.bakeoff.page import CSS as R1_CSS, _fmt, _wer_cls

CSS = R1_CSS + """
h3{font-size:16px;margin:22px 0 6px}
.chosen{outline:2px solid var(--accent);border-radius:6px}
.cands{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:8px 14px}
.prompt{font-style:italic}
td.pick-cell{white-space:nowrap}
.research li{margin:4px 0}
code{font-size:12px}
"""

JS = """
const K='vo-bakeoff-r2-v1';
const load=()=>{try{return JSON.parse(localStorage.getItem(K))||{}}catch(e){return{}}};
const save=s=>{try{localStorage.setItem(K,JSON.stringify(s))}catch(e){}};
const st=load();st.picks=st.picks||{};
function counts(){const t={},v={};Object.entries(st.picks).forEach(([line,m])=>{t[m]=(t[m]||0)+1;
 const voice=line.split('/')[0];v[voice]=v[voice]||{};v[voice][m]=(v[voice][m]||0)+1;});return[t,v];}
function tally(){const[t,v]=counts();
 document.querySelectorAll('[data-tally]').forEach(el=>el.textContent=t[el.dataset.tally]||0);
 document.querySelectorAll('[data-vtally]').forEach(el=>{const[vo,m]=el.dataset.vtally.split('|');el.textContent=(v[vo]||{})[m]||0;});}
document.querySelectorAll('input.pick').forEach(r=>{
 if(st.picks[r.name]===r.value)r.checked=true;
 r.addEventListener('change',()=>{st.picks[r.name]=r.value;save(st);tally();});});
const notes=document.getElementById('notes');notes.value=st.notes||'';
notes.addEventListener('input',()=>{st.notes=notes.value;save(st);});
document.getElementById('export').addEventListener('click',()=>{
 const[t,v]=counts();
 let md='## Bake-off round 2 picks\\n\\n'+Object.entries(t).sort((a,b)=>b[1]-a[1]).map(([m,n])=>`- ${m}: ${n}`).join('\\n');
 md+='\\n\\n### Per voice\\n\\n'+Object.entries(v).sort().map(([vo,ms])=>`- ${vo}: `+Object.entries(ms).sort((a,b)=>b[1]-a[1]).map(([m,n])=>`${m} ${n}`).join(', ')).join('\\n');
 md+='\\n\\n### Per line\\n\\n'+Object.entries(st.picks).sort().map(([l,m])=>`- ${l}: ${m}`).join('\\n')+'\\n\\n## Notes\\n\\n'+(st.notes||'');
 navigator.clipboard.writeText(md).then(()=>{document.getElementById('export').textContent='Copied';});});
document.addEventListener('play',e=>{document.querySelectorAll('audio').forEach(a=>{if(a!==e.target)a.pause()})},true);
tally();
"""

RESEARCH = """
<p>Open-weights models that run locally on Apple silicon (MLX via <code>mlx-audio</code> 0.5.6, or PyTorch MPS), with licences
that allow free distribution of generated audio. Sources are model cards and repos.</p>
<ul class="research">
<li><b>VoxCPM2</b> (OpenBMB, 2B, 48 kHz): voice design from a free-text description <i>and</i> cloning in one model.
Apache-2.0. In mlx-audio. Its card notes that design output varies from run to run, so render several and pick.
<a href="https://huggingface.co/openbmb/VoxCPM2">card</a></li>
<li><b>Qwen3-TTS 1.7B VoiceDesign + Base</b>: free-text design, then the Base model clones the chosen clip to lock the voice.
Apache-2.0. In mlx-audio. <a href="https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign">card</a></li>
<li><b>Chatterbox</b> (Resemble AI): the round-1 winner as a cloner. MIT. Its two stages are separable: T3 (speech tokens:
accent and prosody) and S3Gen (a flow-matching decoder that <i>is</i> its voice-conversion model), so one voice can drive
the words and another the timbre. <a href="https://github.com/resemble-ai/chatterbox">repo</a></li>
<li><b>Fish Audio S2 Pro</b>: strong cloning with inline instructions. Fish Audio Research Licence, <b>non-commercial only</b>
(fine while the addon is free). In mlx-audio. <a href="https://huggingface.co/fishaudio/s2-pro">card</a></li>
<li><b>OmniVoice</b> (k2-fsa, 0.6B): fast non-autoregressive cloning. Apache-2.0 per its GitHub repo. In mlx-audio.
<a href="https://huggingface.co/k2-fsa/OmniVoice">card</a></li>
<li><b>IndexTTS-2</b> separates timbre from emotion, which suits game characters, but mlx-audio only ports v1
(<a href="https://arxiv.org/abs/2506.21619">arXiv 2506.21619</a>). <b>Maya1</b> is the one model card that demos
demon and villain voices (Apache-2.0), but it wouldn't run here (see below). <b>Higgs Audio v2</b> ran but didn't hold the reference voice.</li>
<li><b>Voice conversion</b>: Seed-VC (zero-shot, GPL-3.0, <a href="https://github.com/Plachtaa/seed-vc">repo</a>)
runs in PyTorch on MPS and is tested here. RVC needs about 10 minutes of target audio per voice, so it doesn't suit
thousands of NPC Voices. Chatterbox's S3Gen stage does zero-shot VC in MLX.</li>
<li><b>DSP for creatures</b>: no source shows an open model producing a convincing orc timbre without help. The documented
route for dragon and demon voices is pitch and formant shifting plus saturation and EQ (Praat/Parselmouth + Pedalboard),
e.g. VoiceDesigner, <a href="https://arxiv.org/abs/2608.13613">arXiv 2608.13613</a>.</li>
</ul>
<p><b>Shortlist tested here:</b> (1) better references from VoxCPM2 and Qwen3 VoiceDesign, best of 4 by ear-proxies;
(2) cloning them with Chatterbox, VoxCPM2, Qwen3 Base, Fish S2 Pro and OmniVoice; (3) a per-race DSP chain on Chatterbox output;
(4) voice conversion toward a DSP-shaped timbre, both inside Chatterbox (cb-vc) and with Seed-VC; (5) VoxCPM2 designing every line directly, as a
fantasy-ness ceiling that can't give consistent NPC Voices.</p>
"""


def _feat(f: dict | None) -> str:
    if not f:
        return ""
    parts = []
    if f.get("f0") is not None:
        parts.append(f"f0 {f['f0']:.0f} Hz")
    if f.get("hnr") is not None:
        parts.append(f"HNR {f['hnr']:.1f} dB")
    if f.get("centroid") is not None:
        parts.append(f"cent {f['centroid']:.0f}")
    return " · ".join(parts)


def _player(label: str, clip: dict | None, pick: tuple[str, str] | None = None, cls: str = "clip") -> str:
    if not clip:
        return f'<div class="{cls}"><b>{escape(label)}</b><div class="muted"><small>not rendered</small></div></div>'
    meta = []
    if clip.get("audio_s") is not None:
        meta.append(f'{clip["audio_s"]:.1f}s')
    if clip.get("rtf"):
        meta.append(f'RTF {clip["rtf"]:.2f}')
    if clip.get("wer") is not None:
        meta.append(f'<span class="{_wer_cls(clip["wer"])}">WER {clip["wer"]:.0%}</span>')
    feat = _feat(clip.get("features"))
    radio = ""
    if pick:
        radio = (f' <label class="pick"><input type="radio" class="pick" name="{escape(pick[0])}" '
                 f'value="{escape(pick[1])}"> best</label>')
    asr = (f'<details><summary>ASR heard</summary><small>{escape(clip["asr"])}</small></details>'
           if clip.get("asr") else "")
    return (f'<div class="{cls}"><b>{escape(label)}</b>{radio}'
            f'<audio controls preload="none" src="{escape(clip["file"])}"></audio>'
            f'<small class="muted">{" · ".join(meta)}{"<br>" + feat if feat else ""}</small>{asr}</div>')


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def summary_table(results: dict) -> str:
    rows = []
    for a in round2.APPROACHES:
        info = results.get("approaches", {}).get(a.id, {})
        clips = results.get("clips", {}).get(a.id, {})
        s = metrics.summarise(clips)
        tally = f'<td class="n" data-tally="{escape(a.id)}">0</td>'
        cons = "yes" if a.consistent else '<span class="bad">no</span>'
        head = f'<td>{escape(a.label)}<br><small class="muted">{escape(a.id)}</small></td><td>{cons}</td>'
        lic = f'<td><small>{escape(a.licence)}</small></td>'
        if info.get("error") and not s["lines"]:
            rows.append(f'<tr>{head}<td colspan="4" class="bad">did not run: {escape(info["error"])}</td>{lic}{tally}</tr>')
            continue
        if not s["lines"]:
            rows.append(f'<tr>{head}<td colspan="4" class="muted">not run</td>{lic}{tally}</tr>')
            continue
        rows.append(
            f'<tr>{head}<td class="n">{s["lines"]}</td><td class="n"><b>{s["rtf"]:.2f}×</b></td>'
            f'<td class="n">{_fmt(info.get("peak_mem_gb"), "{:.1f} GB")}</td>'
            f'<td class="n">{_fmt(s["wer_mean"], "{:.1%}")} / {s["wer_over_10pct"]}</td>{lic}{tally}</tr>')
    return ('<div class="scroll"><table><thead><tr><th>Approach</th><th>Consistent per NPC</th><th>Lines</th>'
            '<th>RTF</th><th>Peak mem</th><th>Mean WER / lines &gt;10%</th><th>Licence</th><th>Your picks</th>'
            '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>'
            '<p class="muted"><small>RTF = audio seconds ÷ wall seconds (higher is faster), one worker, model loaded '
            'and warmed up. The DSP approach adds its processing time to the cb-vox render it starts from. '
            f'WER from {escape(catalog.ASR_MODEL)}, apostrophes ignored.</small></p>')


def proxy_table(results: dict) -> str:
    """Median f0 / HNR per approach per voice: did the voice move where the race needs it?"""
    head = "".join(f'<th>{escape(v.label)}<br><small>target f0 {v.target.f0:.0f}'
                   + (f", HNR ≤{v.target.hnr_max:g}" if v.target.hnr_max is not None else "") + '</small></th>'
                   for v in round2.VOICES)
    rows = []
    for a in round2.APPROACHES:
        clips = results.get("clips", {}).get(a.id, {})
        if not clips:
            continue
        cells = []
        for v in round2.VOICES:
            cs = [c for k, c in clips.items() if k.startswith(v.id + "/")]
            if not cs:
                cells.append('<td class="muted">–</td>')
                continue
            f0 = _median([c.get("features", {}).get("f0") for c in cs])
            hnr = _median([c.get("features", {}).get("hnr") for c in cs])
            wer = statistics.fmean([c["wer"] for c in cs if c.get("wer") is not None] or [0])
            cells.append(f'<td class="n">{_fmt(f0, "{:.0f}")} / {_fmt(hnr, "{:.1f}")}'
                         f'<br><small class="{_wer_cls(wer)}">WER {wer:.0%}</small></td>')
        rows.append(f'<tr><td>{escape(a.id)}</td>{"".join(cells)}</tr>')
    return ('<div class="scroll"><table><thead><tr><th>Approach</th>' + head + '</tr></thead><tbody>'
            + "".join(rows) + '</tbody></table></div>'
            '<p class="muted"><small>Cells show median f0 (Hz) / median HNR (dB) over the voice\'s lines. Lower HNR '
            'means rougher. These are ear-proxies only: they can show a voice got deeper or rougher, but not whether it '
            'sounds like an orc or has the right accent.</small></p>')


def voice_tally(v: round2.Voice) -> str:
    cells = "".join(f'<span class="tag">{escape(a.id)}: <b data-vtally="{v.id}|{escape(a.id)}">0</b></span>'
                    for a in round2.APPROACHES)
    return f'<p><small class="muted">Your picks for this voice:</small> {cells}</p>'


def candidates_block(results: dict, v: round2.Voice) -> str:
    out = []
    for d in round2.DESIGNERS:
        cands = results.get("candidates", {}).get(d.id, {}).get(v.id, {})
        if not cands:
            continue
        chosen = results.get("refs", {}).get(d.id, {}).get(v.id)
        items = []
        for seed, c in sorted(cands.items()):
            label = f"seed {seed} · score {c.get('score', '–')}" + (" · chosen" if seed == chosen else "")
            items.append(_player(label, c, cls="clip chosen" if seed == chosen else "clip"))
        out.append(f'<h4>{escape(d.label)} <small class="muted">({escape(d.repo)})</small></h4>'
                   f'<div class="cands">{"".join(items)}</div>')
    if not out:
        return '<p class="muted">No reference Candidates rendered.</p>'
    return ("".join(out) + '<p class="muted"><small>The chosen Candidate (outlined) is the lowest score: distance of '
            'f0 from the target, HNR above its ceiling, spectral centroid above its ceiling, plus ASR WER (a heavy '
            'penalty above 10%). In the real pipeline this is the Approval Gate\'s job; here it is automatic, so '
            'listen to the others too.</small></p>')


def dsp_block(results: dict, v: round2.Voice) -> str:
    rec = results.get("dsp", {}).get(v.id)
    base = dsp.RACE_CHAINS[v.id]
    if base.is_identity:
        return '<p class="muted"><small>No DSP chain for this voice (humans are left as rendered).</small></p>'
    if not rec or "sweep" not in rec:
        return f'<p class="muted"><small>Race chain (strength 1): {escape(base.describe())}. Not tuned yet.</small></p>'
    sweep = "".join(
        f'<tr><td class="n">{escape(k)}{" ✓" if float(k) == rec["k"] else ""}</td><td class="n">{w:.1%}</td>'
        f'<td><small>{escape(_feat(rec.get("sweep_features", {}).get(k)))}</small></td>'
        f'<td>{_player("", {"file": f}) if (f := _tune_file(rec, k)) else ""}</td></tr>'
        for k, w in rec["sweep"].items())
    ref = (_player("DSP'd reference (S3Gen timbre for cb-vc)", {"file": rec["ref_file"]})
           if rec.get("ref_file") else "")
    return (f'<p><small>Race chain at strength 1: <code>{escape(rec["chain"])}</code><br>'
            f'Chosen strength <b>{rec["k"]:g}</b>: <code>{escape(rec["final"])}</code> '
            f'(strongest whose WER stays within {round2.DSP_WER_BUDGET:.0%} of the unprocessed {rec["base_wer"]:.1%}).'
            f'</small></p><div class="scroll"><table><thead><tr><th>Strength</th><th>Mean WER</th><th>Proxies</th>'
            f'<th>Example</th></tr></thead><tbody>{sweep}</tbody></table></div>{ref}')


def _tune_file(rec: dict, k: str) -> str | None:
    for f in rec.get("tune_files", []):
        if f"/k{float(k):g}/" in f:
            return f
    return None


def lines_block(results: dict, v: round2.Voice) -> str:
    clips = results.get("clips", {})
    out = []
    for line in (l for l in round2.lines() if l.voice == v.id):
        if not any(line.key in clips.get(a.id, {}) for a in round2.APPROACHES):
            continue
        players = "".join(_player(a.label, clips.get(a.id, {}).get(line.key), (line.key, a.id))
                          for a in round2.APPROACHES if line.key in clips.get(a.id, {}))
        out.append(f'<div class="card"><span class="tag">{escape(line.id)}</span>'
                   f'<span class="tag">{escape(line.kind)}</span>'
                   f'<div class="line-text">{escape(line.text)}</div><div class="clips">{players}</div></div>')
    return "".join(out) or '<p class="muted">No clips rendered yet.</p>'


def voice_section(results: dict, v: round2.Voice) -> str:
    return (f'<h2 id="v-{v.id}">{escape(v.label)}</h2>'
            f'<p class="prompt"><small>Design prompt: {escape(v.prompt)}</small></p>'
            f'{voice_tally(v)}'
            f'<details><summary>Reference Candidates ({len(round2.SEEDS)} per designer)</summary>'
            f'{candidates_block(results, v)}</details>'
            f'<details><summary>DSP chain and tuning</summary>{dsp_block(results, v)}</details>'
            f'{lines_block(results, v)}')


def benchmark_section(results: dict) -> str:
    out = []
    for vid, rec in results.get("benchmark", {}).items():
        players = "".join(_player(lid, c) for lid, c in rec.get("clips", {}).items())
        ref = _player("round-1 reference (Qwen3 VoiceDesign)", {"file": rec["ref_file"]}) if rec.get("ref_file") else ""
        out.append(f'<div class="card"><b>{escape(rec.get("label", vid))}</b><div class="clips">{ref}{players}</div></div>')
    return "".join(out) or '<p class="muted">Round-1 clips not found.</p>'


VARIATION_METHODS = {
    "dsp": ("A. DSP jitter of the Archetype's timbre",
            f"The NPC's S3Gen timbre reference is the Archetype's designed reference run through the race chain, "
            f"jittered by NPC id (pitch ±{dsp.VARY['pitch_st']:g} st, formant ±{dsp.VARY['formant']:g}, rasp, "
            f"sub-octave, saturation). Words and accent (T3) stay the Archetype's."),
    "design": ("B. Another design seed of the Archetype prompt",
               "The NPC's reference is a different VoxCPM2 Candidate for the same orc prompt, run through the same "
               "race chain. T3 and S3Gen both come from that Candidate."),
}


def variation_section(results: dict) -> str:
    rec = results.get("variation", {})
    if not rec.get("npcs"):
        return '<p class="muted">Not rendered.</p>'
    arch = rec.get("archetype", {})
    out = [f'<p><small>Archetype: the orc male VoxCPM2 reference and its DSP\'d version '
           f'(<code>{escape(arch.get("chain", ""))}</code>), rendered with cb-vc. Two deterministic ways to derive '
           f'NPC Voices from it with no human step:</small></p>',
           '<div class="clips">' + _player("Archetype, designed", {"file": arch.get("ref_file", "")})
           + _player("Archetype, DSP'd", {"file": arch.get("dsp_ref_file", "")}) + '</div>']
    for method, (title, desc) in VARIATION_METHODS.items():
        npcs = {k: n for k, n in rec["npcs"].items() if n.get("method", "dsp") == method}
        if not npcs:
            continue
        out.append(f'<h3>{escape(title)}</h3><p class="muted"><small>{escape(desc)}</small></p>')
        for name, n in npcs.items():
            label = f"NPC {name}" if name.isdigit() else name
            players = "".join(_player(lid, c) for lid, c in n.get("clips", {}).items())
            out.append(f'<div class="card"><b>{escape(label)}</b> <small class="muted">{escape(n["chain"])}</small>'
                       f'<div class="clips">{_player("reference", {"file": n["ref_file"]})}{players}</div></div>')
    sims = rec.get("similarity", {})
    if sims:
        rows = "".join(f'<tr><td>{escape(k.replace("|", " vs "))}</td><td class="n">{s:.3f}</td></tr>'
                       for k, s in sims.items())
        selfs = "".join(f'<tr><td>{escape(k)}: clip vs clip of the same voice</td><td class="n">{s:.3f}</td></tr>'
                        for k, s in rec.get("self_similarity", {}).items())
        out.append('<h3>Speaker-embedding similarity</h3><div class="scroll"><table><thead><tr><th>Pair</th>'
                   '<th>Cosine</th></tr></thead><tbody>' + selfs + rows + '</tbody></table></div>'
                   '<p class="muted"><small>Chatterbox voice-encoder embeddings, averaged over each voice\'s 3 clips '
                   '(the Archetype uses its cb-vc clips of the same lines). "other:" rows are cb-vc clips of other '
                   'races. Two clips of the same voice score about the "same voice" level. Neighbours should score clearly '
                   'lower than that and still well above the other races. The spec\'s floor and ceiling would be set '
                   'from numbers like these.</small></p>')
    return "".join(out)


def not_run_section() -> str:
    rows = "".join(f'<tr><td>{escape(n)}</td><td><small>{escape(l)}</small></td><td><small>{escape(w)}</small></td></tr>'
                   for n, l, w in round2.NOT_RUN)
    return ('<div class="scroll"><table><thead><tr><th>Model</th><th>Licence</th><th>Why not in the comparison</th>'
            '</tr></thead><tbody>' + rows + '</tbody></table></div>')


def settings_table() -> str:
    rows = "".join(
        f'<tr><td>{escape(a.label)}</td><td><code>{escape(a.repo or "–")}</code></td>'
        f'<td><small>{escape(", ".join(f"{k}={v}" for k, v in a.settings.items()))}</small></td>'
        f'<td><small>{escape(a.licence)}</small></td><td><small>{escape(a.note)}</small></td></tr>'
        for a in round2.APPROACHES)
    rows += "".join(f'<tr><td>{escape(d.label)} (reference Candidates)</td><td><code>{escape(d.repo)}</code></td>'
                    f'<td><small>seeds {", ".join(map(str, round2.SEEDS))}</small></td>'
                    f'<td><small>{escape(d.licence)}</small></td><td></td></tr>' for d in round2.DESIGNERS)
    rows += (f'<tr><td>Parakeet (ASR check only)</td><td><code>{escape(catalog.ASR_MODEL)}</code></td><td></td>'
             f'<td><small>{escape(catalog.ASR_LICENCE)}</small></td><td></td></tr>')
    return ('<div class="scroll"><table><thead><tr><th>Approach / model</th><th>Weights</th><th>Settings</th>'
            '<th>Licence</th><th>Note</th></tr></thead><tbody>' + rows + '</tbody></table></div>')


def render(results: dict) -> str:
    meta = results.get("meta", {})
    nav = "".join(f'<a href="#v-{v.id}">{escape(v.label)}</a>' for v in round2.VOICES)
    voices = "".join(voice_section(results, v) for v in round2.VOICES)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>Fantasy voice bake-off</title><style>{CSS}</style></head>
<body><main>
<h1>Bake-off round 2: fantasy voices</h1>
<p class="muted">Ticket #10. {escape(str(meta.get("lines", "?")))} lines ({len(round2.VOICES)} voices × 10) × {len(round2.APPROACHES)} approaches.
{escape(meta.get("hardware", ""))}. Updated {escape(meta.get("updated", ""))}.</p>
<nav><a href="#research">Research</a><a href="#summary">Summary</a><a href="#benchmark">Benchmark</a>{nav}<a href="#variation">NPC variation</a><a href="#notes">Notes</a><a href="#models">Models</a></nav>
<p>For each line, tick <b>best</b> on the approach you prefer. Picks and notes stay in this browser, are tallied
overall and per voice, and <b>Copy as Markdown</b> exports them. Listen to the benchmark first: those round-1 Chatterbox
clips were judged good, so they're the bar. Every approach except <i>vox-direct</i> can give each NPC its own stable voice.</p>
<h2 id="research">Research summary</h2>{RESEARCH}
<h2 id="summary">Summary</h2>{summary_table(results)}
<h3>Ear-proxies per voice</h3>{proxy_table(results)}
<h2 id="benchmark">Benchmark: round-1 winners</h2>{benchmark_section(results)}
{voices}
<h2 id="variation">NPC variation: three orcs from one Archetype</h2>{variation_section(results)}
<h2 id="notnrun">Tried and not compared</h2>{not_run_section()}
<h2 id="notes">Notes</h2>
<textarea id="notes" placeholder="Per race: which approach, what to change…"></textarea>
<p><button id="export" type="button">Copy as Markdown</button></p>
<h2 id="models">Approaches, settings &amp; licences</h2>{settings_table()}
</main><script>{JS}</script></body></html>
"""

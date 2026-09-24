"""Static listening page for the bake-off. Pure: results dict in, HTML string out."""
from html import escape

from vo.bakeoff import catalog, metrics
from vo.bakeoff.lines import LINES, NARRATOR_LINES, by_id

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1c1a;--muted:#6b675f;--card:#fff;--line:#e4e0d7;--accent:#8a5a14;--bad:#b3261e;--good:#2e6b30}
@media (prefers-color-scheme:dark){:root{--bg:#161513;--fg:#ece8df;--muted:#9c978c;--card:#201f1c;--line:#34322d;--accent:#e0a64a;--bad:#f2837b;--good:#8fcf8f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 80px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:36px 0 10px;border-bottom:1px solid var(--line);padding-bottom:4px}
.muted{color:var(--muted)}small{font-size:12px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{font-weight:600;color:var(--muted)}td.n{text-align:right}
.scroll{overflow-x:auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0}
.line-text{margin:4px 0 10px}
.tag{display:inline-block;font-size:11px;border:1px solid var(--line);border-radius:10px;padding:0 7px;margin-right:4px;color:var(--muted)}
.clips{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:8px 16px}
.clip{min-width:0}.clip b{font-size:13px}
audio{width:100%;height:34px;display:block;margin:2px 0}
.bad{color:var(--bad)}.good{color:var(--good)}
label.pick{font-size:12px;color:var(--muted);cursor:pointer}
textarea{width:100%;min-height:180px;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:10px;font:14px/1.4 ui-monospace,monospace}
button{background:var(--accent);color:var(--bg);border:0;border-radius:6px;padding:6px 12px;font-weight:600;cursor:pointer}
details summary{cursor:pointer;color:var(--muted);font-size:12px}
nav a{color:var(--accent);margin-right:12px}
"""

JS = """
const K='vo-bakeoff-v1';
const load=()=>{try{return JSON.parse(localStorage.getItem(K))||{}}catch(e){return{}}};
const save=s=>{try{localStorage.setItem(K,JSON.stringify(s))}catch(e){}};
const st=load();st.picks=st.picks||{};
function tally(){const t={};Object.values(st.picks).forEach(m=>t[m]=(t[m]||0)+1);
 document.querySelectorAll('[data-tally]').forEach(el=>el.textContent=t[el.dataset.tally]||0);}
document.querySelectorAll('input.pick').forEach(r=>{
 if(st.picks[r.name]===r.value)r.checked=true;
 r.addEventListener('change',()=>{st.picks[r.name]=r.value;save(st);tally();});});
const notes=document.getElementById('notes');notes.value=st.notes||'';
notes.addEventListener('input',()=>{st.notes=notes.value;save(st);});
document.getElementById('export').addEventListener('click',()=>{
 const t={};Object.values(st.picks).forEach(m=>t[m]=(t[m]||0)+1);
 const md='## Bake-off picks\\n\\n'+Object.entries(t).sort((a,b)=>b[1]-a[1]).map(([m,n])=>`- ${m}: ${n}`).join('\\n')
  +'\\n\\n### Per line\\n\\n'+Object.entries(st.picks).sort().map(([l,m])=>`- ${l}: ${m}`).join('\\n')+'\\n\\n## Notes\\n\\n'+(st.notes||'');
 navigator.clipboard.writeText(md).then(()=>{document.getElementById('export').textContent='Copied';});});
document.addEventListener('play',e=>{document.querySelectorAll('audio').forEach(a=>{if(a!==e.target)a.pause()})},true);
tally();
"""


def _fmt(x, spec="{:.2f}", none="–"):
    return none if x is None else spec.format(x)


def _wer_cls(w):
    if w is None:
        return ""
    return "bad" if w > 0.10 else ("good" if w == 0 else "")


def _clip(model_label: str, clip: dict | None, pick: tuple[str, str] | None = None) -> str:
    if not clip:
        return f'<div class="clip"><b>{escape(model_label)}</b><div class="muted"><small>not rendered</small></div></div>'
    wer = clip.get("wer")
    meta = f'{clip["audio_s"]:.1f}s · RTF {clip["rtf"]:.2f}'
    if wer is not None:
        meta += f' · <span class="{_wer_cls(wer)}">WER {wer:.0%}</span>'
    radio = ""
    if pick:
        radio = (f'<label class="pick"><input type="radio" class="pick" name="{escape(pick[0])}" '
                 f'value="{escape(pick[1])}"> best</label>')
    asr = (f'<details><summary>ASR heard</summary><small>{escape(clip["asr"])}</small></details>'
           if clip.get("asr") else "")
    return (f'<div class="clip"><b>{escape(model_label)}</b> {radio}'
            f'<audio controls preload="none" src="{escape(clip["file"])}"></audio>'
            f'<small class="muted">{meta}</small>{asr}</div>')


def speed_table(results: dict) -> str:
    rows = []
    for m in catalog.MODELS:
        info = results.get("models", {}).get(m.id, {})
        s = metrics.summarise(results.get("clips", {}).get(m.id, {}))
        if info.get("error") and not s["lines"]:
            rows.append(f'<tr><td>{escape(m.label)}</td><td colspan="8" class="bad">did not run: {escape(info["error"])}</td></tr>')
            continue
        if not s["lines"]:
            rows.append(f'<tr><td>{escape(m.label)}</td><td colspan="8" class="muted">not run</td></tr>')
            continue
        wer_mean = _fmt(s["wer_mean"], "{:.1%}")
        rows.append(
            f'<tr><td>{escape(m.label)}</td><td class="n">{s["lines"]}</td>'
            f'<td class="n">{s["audio_s"]:.0f}s</td><td class="n">{s["wall_s"]:.0f}s</td>'
            f'<td class="n"><b>{s["rtf"]:.2f}×</b></td>'
            f'<td class="n">{s["rtf_median"]:.2f} ({s["rtf_min"]:.2f}–{s["rtf_max"]:.2f})</td>'
            f'<td class="n">{_fmt(info.get("peak_mem_gb"), "{:.1f} GB")}</td>'
            f'<td class="n">{wer_mean} / {s["wer_over_10pct"]}</td>'
            f'<td class="n" data-tally="{escape(m.id)}">0</td></tr>')
    return ('<div class="scroll"><table><thead><tr><th>Model</th><th>Lines</th><th>Audio</th><th>Wall</th>'
            '<th>RTF overall</th><th>RTF per line: median (min–max)</th><th>Peak mem</th>'
            '<th>Mean WER / lines &gt;10%</th><th>Your picks</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table></div>"
            '<p class="muted"><small>RTF = audio seconds ÷ wall seconds (higher is faster; &gt;1 is faster than real time). '
            'One worker, model already loaded and warmed up. Peak memory is MLX peak for the whole run, weights included. '
            f'WER from {escape(catalog.ASR_MODEL)}; apostrophes ignored, so it flags skipped/garbled words '
            'more than accent.</small></p>')


def settings_table() -> str:
    rows = "".join(
        f'<tr><td>{escape(m.label)}</td><td><code>{escape(m.repo)}</code></td>'
        f'<td><small>{escape(", ".join(f"{k}={v}" for k, v in m.settings.items()))}</small></td>'
        f'<td><small>{escape(m.licence)}</small></td><td><small>{escape(m.note)}</small></td></tr>'
        for m in catalog.MODELS)
    rows += (f'<tr><td>Qwen3-TTS VoiceDesign (reference clips only)</td><td><code>{escape(catalog.DESIGN_MODEL)}</code></td>'
             f'<td></td><td><small>{escape(catalog.DESIGN_LICENCE)}</small></td><td></td></tr>'
             f'<tr><td>Parakeet (ASR check only)</td><td><code>{escape(catalog.ASR_MODEL)}</code></td>'
             f'<td></td><td><small>{escape(catalog.ASR_LICENCE)}</small></td><td></td></tr>')
    return ('<div class="scroll"><table><thead><tr><th>Model</th><th>Weights</th><th>Settings</th>'
            '<th>Licence</th><th>Note</th></tr></thead><tbody>' + rows + '</tbody></table></div>')


def refs_section(results: dict) -> str:
    out = []
    for v in catalog.REF_VOICES:
        ref = results.get("refs", {}).get(v.id)
        player = (f'<audio controls preload="none" src="{escape(ref["file"])}"></audio>'
                  f'<small class="muted">{ref["seconds"]:.1f}s'
                  + (f' · ASR WER {ref["wer"]:.0%}' if ref.get("wer") is not None else "") + '</small>'
                  if ref else '<small class="muted">not rendered</small>')
        out.append(f'<div class="card"><b>{escape(v.label)}</b> <span class="tag">{escape(v.id)}</span>'
                   f'<span class="tag">Orpheus stock: {escape(v.orpheus_stock)}</span>'
                   f'<div class="muted"><small>Prompt: {escape(v.prompt)}</small></div>{player}</div>')
    return "".join(out)


def lines_section(results: dict) -> str:
    clips = results.get("clips", {})
    out = []
    for line in LINES:
        if not any(line.id in clips.get(m.id, {}) for m in catalog.MODELS):
            continue
        players = "".join(
            _clip(m.label, clips.get(m.id, {}).get(line.id), (line.id, m.id)) for m in catalog.MODELS)
        voice = catalog.ref_voice(line.voice).label
        out.append(f'<div class="card" id="l{line.id}"><span class="tag">{line.id}</span>'
                   f'<span class="tag">{escape(line.kind)}</span><span class="tag">{escape(voice)}</span>'
                   f'<div class="line-text">{escape(line.text)}</div><div class="clips">{players}</div></div>')
    return "".join(out) or '<p class="muted">No clips rendered yet.</p>'


def narrator_section(results: dict) -> str:
    narr = results.get("narrator", {})
    if not narr:
        return '<p class="muted">Not rendered.</p>'
    texts = by_id()
    out = []
    for lid in NARRATOR_LINES:
        players = "".join(
            _clip(f"Kokoro {v}", narr.get(v, {}).get(lid), (f"narrator/{lid}", f"kokoro:{v}"))
            for v in catalog.KOKORO_NARRATOR_ALTS)
        out.append(f'<div class="card"><span class="tag">{lid}</span>'
                   f'<div class="line-text">{escape(texts[lid].text)}</div><div class="clips">{players}</div></div>')
    return "".join(out)


def render(results: dict) -> str:
    meta = results.get("meta", {})
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>VoiceForever model bake-off</title><style>{CSS}</style></head>
<body><main>
<h1>Model bake-off</h1>
<p class="muted">Ticket #10. {escape(str(meta.get("lines", "?")))} lines × {len(catalog.MODELS)} models.
{escape(meta.get("hardware", ""))}. Updated {escape(meta.get("updated", ""))}.</p>
<nav><a href="#speed">Speed</a><a href="#refs">Reference voices</a><a href="#lines">Lines</a><a href="#narrator">Narrator</a><a href="#notes">Notes</a><a href="#models">Models &amp; licences</a></nav>
<p>Listen line by line and tick <b>best</b> for the model you prefer; picks and notes are kept in this browser
and tallied in the speed table. <b>Copy as Markdown</b> exports them for the decision record.
Cloning models (Chatterbox, F5, Orpheus-clone) are conditioned on the reference clip for the line's race;
Orpheus (stock) uses its nearest built-in voice; Kokoro reads everything as the Narrator.</p>
<h2 id="speed">Speed</h2>{speed_table(results)}
<h2 id="refs">Reference voices</h2>
<p class="muted"><small>Rendered by Qwen3-TTS VoiceDesign from the text prompt below, all reading the same neutral sentence:
“{escape(catalog.REF_TEXT)}”</small></p>{refs_section(results)}
<h2 id="lines">Lines</h2>{lines_section(results)}
<h2 id="narrator">Narrator candidates (Kokoro voices)</h2>{narrator_section(results)}
<h2 id="notes">Notes</h2>
<textarea id="notes" placeholder="Default NPC model, settings, Narrator model/voice, and why…"></textarea>
<p><button id="export" type="button">Copy as Markdown</button></p>
<h2 id="models">Models, settings &amp; licences</h2>{settings_table()}
</main><script>{JS}</script></body></html>
"""

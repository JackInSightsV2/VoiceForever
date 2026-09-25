"""Round 5 orchestration (orc male only).

1. Copy the round-2 vox-direct orc_m clips (character reference) and round-4 anchor s6 in; render 3 descriptions x
   8 seeds of anchor candidates; measure (ASR, pitch/HNR, roughness by thirds); rank by character proxy.
2. Pick the source anchors (round-4 s6 + the top new ones, or --anchors picks); process each through the orc chain;
   render continuation from the raw and from the processed anchor.
3. Tune the per-line chain strength on the best anchor's continuation lines (WER budget), apply it (cont-dsp),
   and convert the same lines with Chatterbox VC to the processed anchor (cont-vc).
4. Embed, stats, index.html.

Resumable: anything in results.json with its WAV on disk is not redone; clips are keyed by the anchor they come
from, so a changed pick renders afresh. DSP is deterministic and re-run only with --force.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from vo.bakeoff import dsp, page5, round5
from vo.bakeoff.run import _hardware
from vo.bakeoff.run2 import Checker, _free, _read, _write, load_results, save_results
from vo.bakeoff.run3 import Lazy
from vo.bakeoff.voicefeat import roughness

R2_OUT = Path("build/bakeoff2")
R4_OUT = Path("build/bakeoff4")


def _defaults(results: dict) -> dict:
    for k in ("meta", "reference", "anchors", "dsp_anchors", "clips", "tune", "stats"):
        results.setdefault(k, {})
    results.setdefault("sources", [])
    return results


def measure(out: Path, wav: Path, text: str, check: Checker, seconds: float | None = None, wall: float | None = None,
            **extra) -> dict:
    audio, sr = _read(wav)
    seconds = len(audio) / sr if seconds is None else seconds
    c = {"file": str(wav.relative_to(out)), "audio_s": round(seconds, 3), **extra}
    if wall:
        c.update(wall_s=round(wall, 3), rtf=round(seconds / wall, 3))
    c.update(check(wav, text, True))
    c["rough3"] = roughness(audio, sr, True)
    return c


def copy_inputs(out: Path, results: dict, r2_out: Path, r4_out: Path, check: Checker, log) -> None:
    r2 = load_results(r2_out).get("clips", {}).get("vox-direct", {})
    for l in round5.lines():
        c = r2.get(f"{round5.VOICE}/{l.id}")
        if not c or l.id in results["reference"]:
            continue
        dst = out / f"reference/{l.id}.wav"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(r2_out / c["file"], dst)
        results["reference"][l.id] = measure(out, dst, l.text, check, wall=c.get("wall_s"))
    if round5.R4_ANCHOR not in results["anchors"]:
        src = r4_out / f"anchors/{round5.VOICE}/s{round5.R4_SEED}.wav"
        if src.exists():
            dst = out / f"anchors/{round5.R4_ANCHOR}.wav"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            c = measure(out, dst, round5.ANCHOR_TEXT, check, desc="r4", seed=round5.R4_SEED)
            c["char"] = round5.char_score(c)
            results["anchors"][round5.R4_ANCHOR] = c
            log(f"anchor {round5.R4_ANCHOR}: char {c['char']} {c['rough3']}")
        else:
            log(f"round-4 anchor {src} not found; skipping it")


def anchors(out: Path, results: dict, tts: Lazy, check: Checker, force: bool, log) -> None:
    for d in round5.DESCRIPTIONS:
        s = round5.subject(d.id)
        for seed in round5.SEEDS:
            cid = round5.cand_id(d.id, seed)
            wav = out / f"anchors/{cid}.wav"
            if not force and wav.exists() and cid in results["anchors"]:
                continue
            t = time.perf_counter()
            audio, sr = tts().design(s, seed)
            wall = time.perf_counter() - t
            _write(wav, audio, sr)
            c = measure(out, wav, s.anchor_text, check, len(audio) / sr, wall, desc=d.id, seed=seed)
            c["char"] = round5.char_score(c)
            results["anchors"][cid] = c
            log(f"anchor {cid}: {c['audio_s']:.1f}s WER {c['wer']:.0%} {c['features']} rough {c['rough3']['rough']} "
                f"thirds {c['rough3']['rough_thirds']} char {c['char']}")
            save_results(out, results)


def process_anchors(out: Path, results: dict, check: Checker, force: bool, log) -> None:
    chain = round5.ORC_CHAIN.scaled(round5.ANCHOR_STRENGTH)
    for a in results["sources"]:
        wav = out / f"anchors/dsp/{a}.wav"
        if not force and wav.exists() and a in results["dsp_anchors"]:
            continue
        audio, sr = _read(out / results["anchors"][a]["file"])
        _write(wav, dsp.apply(audio, sr, chain), sr)
        results["dsp_anchors"][a] = c = measure(out, wav, round5.ANCHOR_TEXT, check, chain=chain.describe())
        log(f"dsp anchor {a}: WER {c['wer']:.0%} {c['features']} rough {c['rough3']['rough']}")
        save_results(out, results)


def render_tts(out: Path, results: dict, jobs: list[tuple[str, str]], tts: Lazy, check: Checker, force: bool,
               log) -> None:
    from vo.bakeoff import round3

    cont = round3.variant("cont")
    s = round5.subject(round5.DESC_IDS[0])  # only its anchor text matters for continuation
    for vid, a in jobs:
        if vid not in ("cont", "dsp-anchor"):
            continue
        anchor = out / (results["dsp_anchors"] if vid == "dsp-anchor" else results["anchors"])[a]["file"]
        clips = results["clips"].setdefault(vid, {})
        for i, line in enumerate(round5.lines(), 1):
            ck = round5.clip_key(a, line.id)
            wav = out / f"audio/{vid}/{a}/{line.id}.wav"
            if not force and wav.exists() and ck in clips:
                continue
            try:
                t = time.perf_counter()
                audio, sr = tts().synth(cont, s, line.text, i, anchor)
                wall = time.perf_counter() - t
            except Exception as e:  # keep going; the page shows the gap
                log(f"{vid} {ck}: FAILED {type(e).__name__}: {e}")
                continue
            _write(wav, audio, sr)
            clips[ck] = c = measure(out, wav, line.text, check, len(audio) / sr, wall, seed=i)
            log(f"{vid} {ck}: {c['audio_s']:.1f}s RTF {c['rtf']:.2f} WER {c['wer']:.0%} {c['features']} "
                f"rough {c['rough3']['rough']} drift {c['rough3']['drift']}")
            save_results(out, results)


def tune(out: Path, results: dict, best: str, check: Checker, force: bool, log) -> float:
    """Per-line chain strength for cont-dsp: the strongest in STRENGTHS whose mean WER over the best anchor's
    continuation lines stays within the budget. Every strength's first line is kept for listening."""
    src = round5.variant_clips(results, "cont", best)
    rec = results["tune"]
    if not force and rec.get("anchor") == best and "k" in rec:
        return rec["k"]
    base = [c["wer"] for c in src.values()]
    rec.clear()
    rec.update(anchor=best, base_wer=round(sum(base) / len(base), 3), sweep={}, ladder={})
    texts = {l.id: l.text for l in round5.lines()}
    first = next(iter(sorted(src)))
    for k in round5.STRENGTHS:
        chain = round5.ORC_CHAIN.scaled(k)
        wers, rough = [], []
        for lid, c in sorted(src.items()):
            audio, sr = _read(out / c["file"])
            wav = out / f"dsp_tune/k{k:g}/{lid}.wav"
            _write(wav, dsp.apply(audio, sr, chain), sr)
            m = measure(out, wav, texts[lid], check)
            wers.append(m["wer"])
            rough.append(m["rough3"]["rough"])
            if lid == first:
                rec["ladder"][f"{k:g}"] = m
        rec["sweep"][f"{k:g}"] = {"wer": round(sum(wers) / len(wers), 3),
                                  "rough": round(sorted(rough)[len(rough) // 2], 3), "chain": chain.describe()}
        log(f"tune k={k:g}: WER {rec['sweep'][f'{k:g}']['wer']:.1%} rough {rec['sweep'][f'{k:g}']['rough']}")
    rec["k"] = round5.tune({float(k): v["wer"] for k, v in rec["sweep"].items()}, rec["base_wer"])
    rec["chain"] = round5.ORC_CHAIN.scaled(rec["k"]).describe()
    log(f"tuned strength {rec['k']:g}: {rec['chain']}")
    save_results(out, results)
    return rec["k"]


def post(out: Path, results: dict, best: str, check: Checker, force: bool, log) -> None:
    """cont-dsp and cont-vc from the best anchor's continuation lines. RTF counts the source render's time too."""
    src = round5.variant_clips(results, "cont", best)
    texts = {l.id: l.text for l in round5.lines()}
    k = tune(out, results, best, check, force, log)
    chain = round5.ORC_CHAIN.scaled(k)
    vc = None
    for vid in ("cont-dsp", "cont-vc"):
        clips = results["clips"].setdefault(vid, {})
        for lid, c in sorted(src.items()):
            ck = round5.clip_key(best, lid)
            wav = out / f"audio/{vid}/{best}/{lid}.wav"
            if not force and wav.exists() and ck in clips:
                continue
            t = time.perf_counter()
            if vid == "cont-dsp":
                audio, sr = _read(out / c["file"])
                audio = dsp.apply(audio, sr, chain)
            else:
                if vc is None:
                    from vo.bakeoff.engines5 import ChatterboxVC

                    vc = ChatterboxVC()
                audio, sr = vc(out / c["file"], out / results["dsp_anchors"][best]["file"])
            wall = time.perf_counter() - t + c.get("wall_s", 0)
            _write(wav, audio, sr)
            clips[ck] = m = measure(out, wav, texts[lid], check, len(audio) / sr, wall)
            log(f"{vid} {ck}: WER {m['wer']:.0%} {m['features']} rough {m['rough3']['rough']}")
        save_results(out, results)


def embed(out: Path, results: dict, log) -> dict:
    path = out / "embeddings.json"
    cache = json.loads(path.read_text()) if path.exists() else {}
    files = [c["file"] for c in results["reference"].values()]
    files += [results["anchors"][a]["file"] for a in results["sources"]]
    files += [c["file"] for c in results["dsp_anchors"].values()]
    files += [c["file"] for clips in results["clips"].values() for c in clips.values()]
    todo = [f for f in dict.fromkeys(files) if f not in cache and (out / f).exists()]
    if todo:
        from vo.bakeoff.engines3 import Embedder

        enc = Embedder()
        for f in todo:
            cache[f] = [round(float(x), 5) for x in enc(out / f)]
        path.write_text(json.dumps(cache))
        log(f"embedded {len(todo)} clips")
    return cache


def bakeoff5(out: Path, r2_out: Path = R2_OUT, r4_out: Path = R4_OUT, picks: Path | None = None, force: bool = False,
             page_only: bool = False, log=print) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    results = _defaults(load_results(out))
    chosen = round5.parse_picks(picks.read_text()) if picks else None
    if chosen is not None:
        errs = round5.check_picks(chosen, results["anchors"])
        if errs:
            raise SystemExit("bad anchor picks:\n  " + "\n  ".join(errs))
    if not page_only:
        results["meta"] = {"hardware": _hardware(), "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
        check, tts = Checker(), Lazy()
        copy_inputs(out, results, r2_out, r4_out, check, log)
        save_results(out, results)
        anchors(out, results, tts, check, force, log)
        results["ranking"] = round5.rank(results["anchors"])
        results["sources"] = round5.sources(results["anchors"], chosen)
        results["sources_how"] = "picks" if chosen else "rule (round5.sources)"
        log("sources: " + ", ".join(results["sources"]))
        process_anchors(out, results, check, force, log)
        jobs = round5.plan(results["sources"])
        render_tts(out, results, jobs, tts, check, force, log)
        _free()
        post(out, results, results["sources"][0], check, force, log)
        results["stats"] = round5.build_stats(results, embed(out, results, log))
        save_results(out, results)
    elif results["clips"]:
        results["stats"] = round5.build_stats(results, embed(out, results, log))
        save_results(out, results)
    page = out / "index.html"
    page.write_text(page5.render(results))
    log(f"wrote {page}")
    return page

"""Round 3 orchestration: anchors, variant renders, ASR/proxies, speaker embeddings, stats, page.

Resumable: clips and anchors already in results.json with their WAV on disk are not redone;
embeddings are cached in embeddings.json by file.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from vo.bakeoff import page3, round3
from vo.bakeoff.run import _hardware
from vo.bakeoff.run2 import Checker, _free, _read, _write, load_results, save_results

R2_OUT = Path("build/bakeoff2")
R2_BASELINE = {"r2-direct": "vox-direct", "r2-omni": "omni"}  # round-2 rows copied in for listening


def _defaults(results: dict) -> dict:
    for k in ("meta", "anchors", "clips", "approaches", "stats"):
        results.setdefault(k, {})
    return results


class Lazy:
    """Loads VoxCPM2 on first use and keeps it for the run."""

    def __init__(self):
        self._tts = None

    def __call__(self):
        if self._tts is None:
            from vo.bakeoff.engines3 import VoxCPM2

            self._tts = VoxCPM2()
            self._tts.model.generate(text="Greetings, friend.", instruct="A calm adult voice")  # warm up
        return self._tts


def _clip(out: Path, wav: Path, seconds: float, wall: float, text: str, male: bool, check: Checker,
          **extra) -> dict:
    c = {"file": str(wav.relative_to(out)), "audio_s": round(seconds, 3), "wall_s": round(wall, 3),
         "rtf": round(seconds / wall, 3) if wall else 0.0, **extra}
    c.update(check(wav, text, male))
    return c


# --- anchors -----------------------------------------------------------------

def anchors(out: Path, results: dict, keys: list[str], tts: Lazy, check: Checker, force: bool, log) -> None:
    for key in keys:
        s = round3.subject(key)
        rec = results["anchors"].setdefault(key, {"cands": {}})
        seeds = list(s.anchor_seeds) if s.group != "npc" else round3.npc_seeds(s.anchor_seeds[0])
        for seed in seeds:
            if s.group == "npc" and rec.get("chosen") is not None and not force:
                break
            wav = out / f"anchors/{key.replace('@', '_')}/s{seed}.wav"
            if not force and wav.exists() and str(seed) in rec["cands"]:
                continue
            t = time.perf_counter()
            audio, sr = tts().design(s, seed)
            wall = time.perf_counter() - t
            _write(wav, audio, sr)
            c = _clip(out, wav, len(audio) / sr, wall, s.anchor_text, s.male, check)
            c["score"] = round3.round2.candidate_score(c["features"], c["wer"], s.target)
            rec["cands"][str(seed)] = c
            log(f"anchor {key} seed {seed}: {c['audio_s']:.1f}s WER {c['wer']:.0%} {c['features']} score {c['score']}")
            save_results(out, results)
            if s.group == "npc" and c["wer"] <= round3.NPC_ANCHOR_MAX_WER:
                break
        rec["chosen"] = round3.pick_anchor(rec["cands"], s)
        rec["file"] = rec["cands"][rec["chosen"]]["file"]


def anchor_path(out: Path, results: dict, key: str) -> Path | None:
    rec = results["anchors"].get(key)
    return out / rec["file"] if rec and rec.get("file") else None


# --- renders -----------------------------------------------------------------

def render(out: Path, results: dict, jobs: list[tuple[str, str]], tts: Lazy, check: Checker, force: bool,
           log) -> None:
    import mlx.core as mx

    for vid, key in jobs:
        v, s = round3.variant(vid), round3.subject(key)
        clips = results["clips"].setdefault(vid, {})
        anchor = anchor_path(out, results, key) if v.anchor else None
        seed_fixed = int(results["anchors"].get(key, {}).get("chosen") or s.anchor_seeds[0])
        mx.reset_peak_memory()
        for i, line in enumerate(round3.lines(s), 1):
            ck = f"{key}/{line.id}"
            wav = out / f"audio/{vid}/{key.replace('@', '_')}/{line.id}.wav"
            if not force and wav.exists() and ck in clips:
                continue
            seed = seed_fixed if v.fixed_seed else i
            try:
                t = time.perf_counter()
                audio, sr = tts().synth(v, s, line.text, seed, anchor)
                wall = time.perf_counter() - t
            except Exception as e:  # keep going; the page shows the gap
                log(f"{vid} {ck}: FAILED {type(e).__name__}: {e}")
                continue
            _write(wav, audio, sr)
            clips[ck] = _clip(out, wav, len(audio) / sr, wall, line.text, s.male, check, seed=seed)
            c = clips[ck]
            log(f"{vid} {ck}: {c['audio_s']:.1f}s RTF {c['rtf']:.2f} WER {c['wer']:.0%} {c['features']}")
        results["approaches"][vid] = {"peak_mem_gb": round(max(mx.get_peak_memory() / 1e9,
                                                                results["approaches"].get(vid, {}).get(
                                                                    "peak_mem_gb", 0)), 2)}
        save_results(out, results)


def copy_baseline(out: Path, results: dict, r2_out: Path) -> None:
    """Round-2 vox-direct (and omni for dwarf female) clips, copied in with their round-2 measurements."""
    r2 = load_results(r2_out)
    for rid, src in R2_BASELINE.items():
        voices = set(round3.MAIN_VOICES) | {"dwarf_f"} if rid == "r2-direct" else {"dwarf_f"}
        clips = results["clips"].setdefault(rid, {})
        for k, c in r2.get("clips", {}).get(src, {}).items():
            if k.split("/")[0] not in voices:
                continue
            dst = out / f"baseline/{rid}/{k}.wav"
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(r2_out / c["file"], dst)
            clips[k] = {**c, "file": str(dst.relative_to(out))}


# --- embeddings and stats ----------------------------------------------------

def embed_all(out: Path, results: dict, r2_out: Path, log) -> dict:
    """{embedding key: vector} for every round-3 clip/anchor and every round-2 clip."""
    path = out / "embeddings.json"
    cache = json.loads(path.read_text()) if path.exists() else {}
    files = {}
    for clips in results["clips"].values():
        for c in clips.values():
            files[c["file"]] = out / c["file"]
    for rec in results["anchors"].values():
        for c in rec["cands"].values():
            files[c["file"]] = out / c["file"]
    for approach, clips in load_results(r2_out).get("clips", {}).items():
        for c in clips.values():
            files["r2:" + c["file"]] = r2_out / c["file"]
    todo = {k: p for k, p in files.items() if k not in cache and p.exists()}
    if todo:
        from vo.bakeoff.engines3 import Embedder

        enc = Embedder()
        for i, (k, p) in enumerate(todo.items(), 1):
            cache[k] = [round(float(x), 5) for x in enc(p)]
            if i % 100 == 0:
                log(f"embedded {i}/{len(todo)}")
                path.write_text(json.dumps(cache))
        path.write_text(json.dumps(cache))
    return cache


def stats(out: Path, results: dict, r2_out: Path, emb: dict) -> dict:
    r2 = load_results(r2_out)
    return round3.build_stats(results, r2.get("clips", {}), emb)


# --- entry point -------------------------------------------------------------

def bakeoff3(out: Path, r2_out: Path = R2_OUT, best: str | None = None, force: bool = False,
             page_only: bool = False, log=print) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    results = _defaults(load_results(out))
    if not page_only:
        results["meta"] = {"hardware": _hardware(), "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
        copy_baseline(out, results, r2_out)
        check, tts = Checker(), Lazy()
        main = [s.key for s in round3.SUBJECTS if s.group == "main"]
        anchors(out, results, main, tts, check, force, log)
        save_results(out, results)
        render(out, results, round3.plan(None), tts, check, force, log)
        emb = embed_all(out, results, r2_out, log)
        results["stats"] = stats(out, results, r2_out, emb)
        chosen = best or results["stats"].get("best")
        results["best"] = {"variant": chosen, "how": "--best" if best else "rule (round3.pick_best)"}
        log(f"best variant: {chosen}")
        extra = [s.key for s in round3.SUBJECTS if s.group != "main"]
        anchors(out, results, extra, tts, check, force, log)
        save_results(out, results)
        render(out, results, [j for j in round3.plan(chosen) if j[1] in extra], tts, check, force, log)
        _free()
        emb = embed_all(out, results, r2_out, log)
        results["stats"] = stats(out, results, r2_out, emb)
        save_results(out, results)
    elif results["clips"]:  # stats are cheap and pure: refresh them from the embedding cache
        results["stats"] = stats(out, results, r2_out, embed_all(out, results, r2_out, log))
        save_results(out, results)
    index = out / "index.html"
    index.write_text(page3.render(results))
    log(f"wrote {index}")
    return index

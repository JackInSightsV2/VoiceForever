"""Round 4 orchestration.

Step 1 (no picks): copy the round-2 vox-direct reference clips in, render 8 candidate anchors per voice,
write index.html (the pick page).
Step 2 (--anchors): render each picked voice's 10 lines by continuation (and ultimate cloning) from the
picked anchor, embed, write continuation.html (the rating page).

Resumable: anything in results.json with its WAV on disk is not redone; step-2 clips are keyed by the
anchor they continue from, so changing a pick renders afresh.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from vo.bakeoff import page4, round2, round3, round4
from vo.bakeoff.run import _hardware
from vo.bakeoff.run2 import Checker, _free, _write, load_results, save_results
from vo.bakeoff.run3 import Lazy, _clip

R2_OUT = Path("build/bakeoff2")
PICK_PAGE = "index.html"
RESULT_PAGE = "continuation.html"


def _defaults(results: dict) -> dict:
    for k in ("meta", "anchors", "reference", "clips", "picks", "stats"):
        results.setdefault(k, {})
    return results


def copy_reference(out: Path, results: dict, r2_out: Path) -> None:
    """Round-2 vox-direct clips of the round-4 voices, with their round-2 measurements."""
    r2 = load_results(r2_out).get("clips", {}).get("vox-direct", {})
    for k, c in r2.items():
        if k.split("/")[0] not in round4.VOICES:
            continue
        dst = out / f"reference/{k}.wav"
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(r2_out / c["file"], dst)
        results["reference"][k] = {**c, "file": str(dst.relative_to(out))}


def anchors(out: Path, results: dict, voices: list[str], tts: Lazy, check: Checker, force: bool, log) -> None:
    for voice in voices:
        s = round4.subject(voice)
        rec = results["anchors"].setdefault(voice, {"text": s.anchor_text, "description": s.description,
                                                    "cands": {}})
        for seed in round4.SEEDS:
            cid = round4.cand_id(seed)
            wav = out / f"anchors/{voice}/{cid}.wav"
            if not force and wav.exists() and cid in rec["cands"]:
                continue
            t = time.perf_counter()
            audio, sr = tts().design(s, seed)
            wall = time.perf_counter() - t
            _write(wav, audio, sr)
            c = _clip(out, wav, len(audio) / sr, wall, s.anchor_text, s.male, check, seed=seed)
            c["score"] = round2.candidate_score(c["features"], c["wer"], s.target)
            rec["cands"][cid] = c
            log(f"anchor {voice} {cid}: {c['audio_s']:.1f}s WER {c['wer']:.0%} {c['features']}")
            save_results(out, results)


def render(out: Path, results: dict, jobs: list[tuple[str, str, str]], tts: Lazy, check: Checker, force: bool,
           log) -> None:
    for vid, voice, cid in jobs:
        v, s = round3.variant(vid), round4.subject(voice)
        anchor = out / results["anchors"][voice]["cands"][cid]["file"]
        clips = results["clips"].setdefault(vid, {})
        for i, line in enumerate(round4.lines(voice), 1):
            ck = round4.clip_key(voice, cid, line.id)
            wav = out / f"audio/{vid}/{voice}/{cid}/{line.id}.wav"
            if not force and wav.exists() and ck in clips:
                continue
            try:
                t = time.perf_counter()
                audio, sr = tts().synth(v, s, line.text, i, anchor)
                wall = time.perf_counter() - t
            except Exception as e:  # keep going; the page shows the gap
                log(f"{vid} {ck}: FAILED {type(e).__name__}: {e}")
                continue
            _write(wav, audio, sr)
            clips[ck] = c = _clip(out, wav, len(audio) / sr, wall, line.text, s.male, check, seed=i)
            log(f"{vid} {ck}: {c['audio_s']:.1f}s RTF {c['rtf']:.2f} WER {c['wer']:.0%} {c['features']}")
            save_results(out, results)


def embed(out: Path, results: dict, log) -> dict:
    """{file: embedding} for the picked voices' anchors, reference clips and step-2 clips (cached)."""
    path = out / "embeddings.json"
    cache = json.loads(path.read_text()) if path.exists() else {}
    files = [c["file"] for c in results["reference"].values()]
    for voice, cid in results["picks"].items():
        if cid != round4.NONE:
            files.append(results["anchors"][voice]["cands"][cid]["file"])
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


def load_picks(path: Path) -> dict[str, str]:
    return round4.parse_picks(path.read_text())


def bakeoff4(out: Path, r2_out: Path = R2_OUT, picks: Path | None = None, force: bool = False,
             page_only: bool = False, log=print) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    results = _defaults(load_results(out))
    if picks is None:
        if not page_only:
            results["meta"]["step1"] = {"hardware": _hardware(),
                                        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
            copy_reference(out, results, r2_out)
            save_results(out, results)
            anchors(out, results, list(round4.VOICES), Lazy(), Checker(), force, log)
            save_results(out, results)
        page = out / PICK_PAGE
        page.write_text(page4.pick_page(results))
    else:
        chosen = load_picks(picks)
        errs = round4.check_picks(chosen, results["anchors"])
        if errs:
            raise SystemExit("bad anchor picks:\n  " + "\n  ".join(errs))
        results["picks"] = chosen
        log("picks: " + ", ".join(f"{v}={c}" for v, c in chosen.items()))
        if not page_only:
            results["meta"]["step2"] = {"hardware": _hardware(),
                                        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
            render(out, results, round4.plan(chosen), Lazy(), Checker(), force, log)
            _free()
        results["stats"] = round4.build_stats(results, embed(out, results, log))
        save_results(out, results)
        page = out / RESULT_PAGE
        page.write_text(page4.result_page(results))
    log(f"wrote {page}")
    return page

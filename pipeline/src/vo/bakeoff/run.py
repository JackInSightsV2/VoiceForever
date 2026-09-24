"""Orchestrates the bake-off: reference clips, renders, timings, ASR check, listening page.

Resumable: a clip already recorded in results.json with its WAV on disk is not re-rendered.
"""
from __future__ import annotations

import gc
import json
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from vo.bakeoff import catalog, metrics, page
from vo.bakeoff.lines import LINES, NARRATOR_LINES, by_id

WARMUP_TEXT = "Greetings, friend. The road is clear today."


def load_results(out: Path) -> dict:
    path = out / "results.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"meta": {}, "refs": {}, "models": {}, "clips": {}, "narrator": {}}


def save_results(out: Path, results: dict) -> None:
    (out / "results.json").write_text(json.dumps(results, indent=1))


def _hardware() -> str:
    try:
        chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
        mem = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) // 2**30
        return f"{chip}, {mem} GB, macOS {platform.mac_ver()[0]}"
    except Exception:
        return platform.platform()


def make_refs(out: Path, results: dict, force: bool, log) -> dict[str, Path]:
    refs = {v.id: out / "refs" / f"{v.id}.wav" for v in catalog.REF_VOICES}
    todo = [v for v in catalog.REF_VOICES if force or not refs[v.id].exists()]
    if todo:
        from vo.bakeoff.engines import Designer

        designer = Designer()
        for v in todo:
            log(f"designing reference voice {v.id}")
            designer(v, refs[v.id])
        del designer
        _free()
    import soundfile as sf

    for v in catalog.REF_VOICES:
        info = sf.info(refs[v.id])
        results["refs"][v.id] = {"file": f"refs/{v.id}.wav", "seconds": info.frames / info.samplerate}
    return refs


def _free() -> None:
    import mlx.core as mx

    gc.collect()
    mx.clear_cache()


def _render(engine, text: str, voice: str, wav: Path) -> dict:
    import soundfile as sf

    t = time.perf_counter()
    audio, sr = engine.synth(text, voice)
    wall = time.perf_counter() - t
    wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(wav, audio, sr, subtype="PCM_16")
    seconds = len(audio) / sr
    return {"voice": voice, "audio_s": round(seconds, 3), "wall_s": round(wall, 3), "rtf": round(seconds / wall, 3)}


def run_model(model_id: str, out: Path, refs: dict[str, Path], results: dict, lines, force: bool, log) -> None:
    import mlx.core as mx
    from vo.bakeoff import engines

    clips = results["clips"].setdefault(model_id, {})
    jobs = [(l.id, l.text, l.voice, out / "audio" / model_id / f"{l.id}.wav") for l in lines]
    narr = model_id == "kokoro"
    if narr:
        texts = by_id()
        for v in catalog.KOKORO_NARRATOR_ALTS:
            for lid in NARRATOR_LINES:
                jobs.append((f"{v}/{lid}", texts[lid].text, v, out / "audio" / "narrator" / v / f"{lid}.wav"))

    def done(key, wav):
        store = results["narrator"].get(key.split("/")[0], {}) if "/" in key else clips
        return not force and wav.exists() and key.split("/")[-1] in store

    pending = [j for j in jobs if not done(j[0], j[3])]
    if not pending:
        log(f"{model_id}: all {len(jobs)} clips already rendered")
        return

    mx.reset_peak_memory()
    t = time.perf_counter()
    try:
        engine = engines.load(model_id, refs)
        load_s = time.perf_counter() - t
        engine.synth(WARMUP_TEXT, lines[0].voice)  # compile/warm caches; not timed
    except Exception as e:  # record why a model could not run, keep going with the others
        results["models"][model_id] = {"error": f"{type(e).__name__}: {e}"}
        log(f"{model_id}: FAILED to load: {e}")
        return

    for i, (key, text, voice, wav) in enumerate(pending, 1):
        try:
            clip = _render(engine, text, voice, wav)
        except Exception as e:
            log(f"{model_id} {key}: FAILED: {e}")
            continue
        clip["file"] = str(wav.relative_to(out))
        if "/" in key:
            v, lid = key.split("/")
            results["narrator"].setdefault(v, {})[lid] = clip
        else:
            clips[key] = clip
        log(f"{model_id} [{i}/{len(pending)}] {key}: {clip['audio_s']:.1f}s audio, RTF {clip['rtf']:.2f}")
        if i % 10 == 0:
            save_results(out, results)

    results["models"][model_id] = {
        "load_s": round(load_s, 1),
        "peak_mem_gb": round(mx.get_peak_memory() / 1e9, 2),
    }
    del engine
    _free()


def transcribe(out: Path, results: dict, force: bool, log) -> None:
    """ASR every clip and score it against the line text (the spec's QA step, as a listening aid)."""
    texts = {l.id: l.text for l in LINES}
    todo = []
    for clips in list(results["clips"].values()) + list(results["narrator"].values()):
        for lid, clip in clips.items():
            if force or "asr" not in clip:
                todo.append((lid, clip))
    if not todo:
        return
    from vo.bakeoff.engines import Transcriber

    asr = Transcriber()
    log(f"transcribing {len(todo)} clips")
    for lid, clip in todo:
        clip["asr"] = asr(out / clip["file"])
        clip["wer"] = round(metrics.wer(texts[lid], clip["asr"]), 3)
    for voice, ref in results["refs"].items():
        ref["asr"] = asr(out / ref["file"])
        ref["wer"] = round(metrics.wer(catalog.REF_TEXT, ref["asr"]), 3)
    del asr
    _free()


def bakeoff(out: Path, models: list[str], limit: int | None = None, force: bool = False,
            asr: bool = True, page_only: bool = False, log=print) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    results = load_results(out)
    if not page_only:
        lines = list(LINES[:limit] if limit else LINES)
        results["meta"] = {
            "hardware": _hardware(),
            "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "lines": len(lines),
        }
        refs = make_refs(out, results, force, log)
        save_results(out, results)
        for model_id in models:
            run_model(model_id, out, refs, results, lines, force, log)
            save_results(out, results)
        if asr:
            transcribe(out, results, force, log)
            save_results(out, results)
    index = out / "index.html"
    index.write_text(page.render(results))
    log(f"wrote {index}")
    return index

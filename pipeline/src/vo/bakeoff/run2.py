"""Round 2 orchestration: reference Candidates, DSP tuning, renders, ASR/proxies, NPC variation, page.

Resumable: anything already recorded in results.json with its WAV on disk is not redone.
"""
from __future__ import annotations

import gc
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from vo.bakeoff import dsp, metrics, page2, round2
from vo.bakeoff.run import WARMUP_TEXT, _hardware

R1_OUT = Path("build/bakeoff")


def load_results(out: Path) -> dict:
    path = out / "results.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _defaults(results: dict) -> dict:
    for k in ("meta", "candidates", "refs", "dsp", "approaches", "clips", "variation", "benchmark"):
        results.setdefault(k, {})
    return results


def save_results(out: Path, results: dict) -> None:
    tmp = out / "results.json.tmp"
    tmp.write_text(json.dumps(results, indent=1))
    tmp.replace(out / "results.json")


def _free() -> None:
    import mlx.core as mx

    gc.collect()
    mx.clear_cache()


def _read(path: Path) -> tuple[np.ndarray, int]:
    import soundfile as sf

    a, sr = sf.read(path, dtype="float32")
    return (a.mean(axis=1) if a.ndim > 1 else a), sr


def _write(path: Path, audio: np.ndarray, sr: int) -> None:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, audio, sr, subtype="PCM_16")


class Checker:
    """ASR + ear-proxies for one clip, loaded lazily and kept for the whole run."""

    def __init__(self):
        self._asr = None

    def __call__(self, wav: Path, text: str, male: bool) -> dict:
        from vo.bakeoff.voicefeat import measure

        if self._asr is None:
            from vo.bakeoff.engines import Transcriber

            self._asr = Transcriber()
        heard = self._asr(wav)
        audio, sr = _read(wav)
        return {"asr": heard, "wer": round(metrics.wer(text, heard), 3), "features": measure(audio, sr, male)}


# --- 1. reference Candidates -------------------------------------------------

def design_refs(out: Path, results: dict, check: Checker, log) -> None:
    for d in round2.DESIGNERS:
        cands = results["candidates"].setdefault(d.id, {})
        todo = [(v, s) for v in round2.VOICES for s in round2.SEEDS
                if not ((out / f"refs/{d.id}/{v.id}_s{s}.wav").exists() and str(s) in cands.get(v.id, {}))]
        if todo:
            from vo.bakeoff.engines2 import Designer

            designer = Designer(d)
            for v, seed in todo:
                wav = out / f"refs/{d.id}/{v.id}_s{seed}.wav"
                t = time.perf_counter()
                audio, sr = designer(v, seed)
                wall = time.perf_counter() - t
                _write(wav, audio, sr)
                c = {"file": str(wav.relative_to(out)), "seconds": round(len(audio) / sr, 2),
                     "rtf": round(len(audio) / sr / wall, 2)}
                c.update(check(wav, round2.REF_TEXT, v.male))
                c["score"] = round2.candidate_score(c["features"], c["wer"], v.target)
                cands.setdefault(v.id, {})[str(seed)] = c
                log(f"design {d.id} {v.id} seed {seed}: {c['features']} WER {c['wer']:.0%} score {c['score']}")
                save_results(out, results)
            del designer
            _free()
        chosen = results["refs"].setdefault(d.id, {})
        for v in round2.VOICES:
            seed = round2.pick_candidate(cands[v.id], v.target)
            chosen[v.id] = seed
            shutil.copyfile(out / cands[v.id][seed]["file"], out / f"refs/{d.id}/{v.id}.wav")


def ref_paths(out: Path, designer_id: str) -> dict[str, Path]:
    return {v.id: out / f"refs/{designer_id}/{v.id}.wav" for v in round2.VOICES}


# --- 2. renders --------------------------------------------------------------

def _jobs(a: round2.Approach, out: Path, results: dict, chains: dict, force: bool):
    clips = results["clips"].setdefault(a.id, {})
    for line in round2.lines():
        if not round2.applies(a, line.voice, chains):
            continue
        wav = out / f"audio/{a.id}/{line.key}.wav"
        if force or not (wav.exists() and line.key in clips):
            yield line, wav


def render(a: round2.Approach, out: Path, results: dict, chains: dict, dsp_refs: dict, check: Checker,
           force: bool, log) -> None:
    import mlx.core as mx

    jobs = list(_jobs(a, out, results, chains, force))
    if not jobs:
        log(f"{a.id}: nothing to render")
        return
    clips = results["clips"][a.id]
    if a.engine == "post":
        for line, wav in jobs:
            src = out / f"audio/{a.source}/{line.key}.wav"
            audio, sr = _read(src)
            t = time.perf_counter()
            y = dsp.apply(audio, sr, chains[line.voice])
            wall = time.perf_counter() - t + results["clips"][a.source][line.key]["wall_s"]
            _write(wav, y, sr)
            clips[line.key] = _clip(out, wav, len(y) / sr, wall, line, check)
            log(f"{a.id} {line.key}: WER {clips[line.key]['wer']:.0%}")
        save_results(out, results)
        return

    from vo.bakeoff import engines2

    mx.reset_peak_memory()
    t = time.perf_counter()
    try:
        engine = engines2.load(a, ref_paths(out, a.ref or "vox"), dsp_refs)
        load_s = time.perf_counter() - t
        engine.synth(WARMUP_TEXT, jobs[0][0].voice)
    except Exception as e:
        results["approaches"][a.id] = {"error": f"{type(e).__name__}: {e}"}
        log(f"{a.id}: FAILED to load: {e}")
        return
    for i, (line, wav) in enumerate(jobs, 1):
        mx.random.seed(i)
        try:
            t = time.perf_counter()
            audio, sr = engine.synth(line.text, line.voice)
            wall = time.perf_counter() - t
        except Exception as e:
            log(f"{a.id} {line.key}: FAILED: {type(e).__name__}: {e}")
            continue
        _write(wav, audio, sr)
        clips[line.key] = _clip(out, wav, len(audio) / sr, wall, line, check)
        c = clips[line.key]
        log(f"{a.id} [{i}/{len(jobs)}] {line.key}: {c['audio_s']:.1f}s RTF {c['rtf']:.2f} WER {c['wer']:.0%} "
            f"{c['features']}")
        if i % 5 == 0:
            save_results(out, results)
    results["approaches"][a.id] = {"load_s": round(load_s, 1), "peak_mem_gb": round(mx.get_peak_memory() / 1e9, 2)}
    save_results(out, results)
    del engine
    _free()


def _clip(out: Path, wav: Path, seconds: float, wall: float, line: round2.Line, check: Checker) -> dict:
    c = {"file": str(wav.relative_to(out)), "voice": line.voice, "audio_s": round(seconds, 3),
         "wall_s": round(wall, 3), "rtf": round(seconds / wall, 3) if wall else 0.0}
    c.update(check(wav, line.text, round2.voice(line.voice).male))
    return c


# --- DSP chain tuning --------------------------------------------------------

def tune_dsp(out: Path, results: dict, check: Checker, log) -> dict[str, dsp.Chain]:
    """Per voice: try the race chain at several strengths on the cb-vox clips; keep the strongest
    whose ASR WER stays within budget of the unprocessed clips."""
    chains = {}
    src = results["clips"].get("cb-vox", {})
    for v in round2.VOICES:
        base = dsp.RACE_CHAINS[v.id]
        rec = results["dsp"].setdefault(v.id, {})
        if base.is_identity:
            chains[v.id] = base
            rec.update({"chain": base.describe(), "k": 0, "final": base.describe()})
            continue
        if "k" not in rec:
            keys = [l.key for l in round2.lines() if l.voice == v.id][: round2.DSP_TUNE_LINES]
            keys = [k for k in keys if k in src]
            base_wer = float(np.mean([src[k]["wer"] for k in keys]))
            sweep, feats = {}, {}
            for k in round2.DSP_STRENGTHS:
                chain = base.scaled(k)
                wers, fs = [], []
                for key in keys:
                    audio, sr = _read(out / src[key]["file"])
                    wav = out / f"dsp_tune/{v.id}/k{k:g}/{key.split('/')[1]}.wav"
                    _write(wav, dsp.apply(audio, sr, chain), sr)
                    line = next(l for l in round2.lines() if l.key == key)
                    r = check(wav, line.text, v.male)
                    wers.append(r["wer"])
                    fs.append(r["features"])
                sweep[k] = round(float(np.mean(wers)), 3)
                feats[k] = {f: round(float(np.mean([x[f] for x in fs if x[f] is not None])), 1)
                            for f in ("f0", "hnr", "centroid")}
                log(f"dsp tune {v.id} k={k}: WER {sweep[k]:.1%} (base {base_wer:.1%}) {feats[k]}")
            k = round2.tune_strength(sweep, base_wer)
            rec.update({"chain": base.describe(), "base_wer": round(base_wer, 3),
                        "sweep": {str(s): w for s, w in sweep.items()},
                        "sweep_features": {str(s): f for s, f in feats.items()},
                        "tune_files": [f"dsp_tune/{v.id}/k{s:g}/{keys[0].split('/')[1]}.wav"
                                       for s in round2.DSP_STRENGTHS],
                        "k": k, "final": base.scaled(k).describe()})
            save_results(out, results)
        chains[v.id] = base.scaled(rec["k"])
    return chains


def make_dsp_refs(out: Path, chains: dict, results: dict) -> dict[str, Path]:
    paths = {}
    for v in round2.VOICES:
        if chains[v.id].is_identity:
            continue
        p = out / f"refs/dsp/{v.id}.wav"
        audio, sr = _read(out / f"refs/vox/{v.id}.wav")
        _write(p, dsp.apply(audio, sr, chains[v.id]), sr)
        paths[v.id] = p
        results["dsp"][v.id]["ref_file"] = str(p.relative_to(out))
    return paths


# --- NPC variation -----------------------------------------------------------

def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1))


def variation(out: Path, results: dict, chains: dict, check: Checker, force: bool, log) -> None:
    """Three orc NPC Voices from one Archetype: each NPC's timbre reference is the Archetype's designed
    reference through the race chain jittered by NPC id (dsp.vary); accent/prosody stay the Archetype's."""
    from vo.bakeoff import engines2

    vid = round2.VARIATION_VOICE
    rec = results["variation"]
    if not force and rec.get("done"):
        return
    a = round2.approach("cb-vc")
    arche = out / f"refs/vox/{vid}.wav"
    audio, sr = _read(arche)
    npc_refs = {}
    rec["archetype"] = {"ref_file": str(arche.relative_to(out)), "chain": chains[vid].describe(),
                        "dsp_ref_file": f"refs/dsp/{vid}.wav"}
    for npc in round2.VARIATION_NPCS:
        chain = dsp.vary(chains[vid], npc)
        p = out / f"variation/{npc}/ref.wav"
        _write(p, dsp.apply(audio, sr, chain), sr)
        npc_refs[npc] = p
        rec.setdefault("npcs", {})[str(npc)] = {"chain": chain.describe(), "ref_file": str(p.relative_to(out)),
                                                "clips": {}}
    engine = engines2.load(a, ref_paths(out, "vox"), {})
    enc = engines2.SpeakerEncoder(engine.model)
    lines = {l.id: l for l in round2.lines() if l.voice == vid}
    embs = {"archetype": enc(*_read(out / f"refs/dsp/{vid}.wav"))}
    for npc, ref in npc_refs.items():
        conds = engine.split(arche, ref)
        clip_embs = []
        for lid in round2.VARIATION_LINES:
            line = lines[lid]
            import mlx.core as mx

            mx.random.seed(npc)
            t = time.perf_counter()
            y, ysr = _collect_cb(engine, line.text, conds)
            wall = time.perf_counter() - t
            wav = out / f"variation/{npc}/{lid}.wav"
            _write(wav, y, ysr)
            c = _clip(out, wav, len(y) / ysr, wall, line, check)
            rec["npcs"][str(npc)]["clips"][lid] = c
            clip_embs.append(enc(y, ysr))
            log(f"variation npc {npc} {lid}: WER {c['wer']:.0%} {c['features']}")
        embs[str(npc)] = np.mean(clip_embs, axis=0)
    names = list(embs)
    rec["similarity"] = {f"{x}|{y}": round(cosine(embs[x], embs[y]), 3)
                         for i, x in enumerate(names) for y in names[i + 1:]}
    # Reference points: the Archetype's own cb-vc clip, and other voices' cb-vc clips.
    other = {}
    for v in ("orc_m", "orc_f", "troll_m", "dwarf_f"):
        c = next((c for k, c in results["clips"].get("cb-vc", {}).items() if k.startswith(v + "/")), None)
        if c:
            other[v] = round(cosine(embs["archetype"], enc(*_read(out / c["file"]))), 3)
    rec["other_races_vs_orc_archetype"] = other
    rec["done"] = True
    save_results(out, results)
    del engine
    _free()


def _collect_cb(engine, text, conds):
    from vo.bakeoff.engines2 import _collect

    return _collect(engine.model.generate(text=text, conds=conds, verbose=False, **engine.spec.settings))


# --- benchmark ---------------------------------------------------------------

def benchmark(out: Path, results: dict, r1_out: Path) -> None:
    r1 = load_results(r1_out)
    for vid, label, lids in round2.BENCHMARK:
        rec = results["benchmark"].setdefault(vid, {"label": label, "clips": {}})
        for lid in lids:
            src = r1_out / f"audio/chatterbox/{lid}.wav"
            if not src.exists():
                continue
            dst = out / f"benchmark/{vid}/{lid}.wav"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            c = r1.get("clips", {}).get("chatterbox", {}).get(lid, {})
            rec["clips"][lid] = {"file": str(dst.relative_to(out)), "wer": c.get("wer"), "rtf": c.get("rtf"),
                                 "audio_s": c.get("audio_s")}
        ref = r1_out / f"refs/{vid}.wav"
        if ref.exists():
            dst = out / f"benchmark/{vid}/ref.wav"
            shutil.copyfile(ref, dst)
            rec["ref_file"] = str(dst.relative_to(out))


# --- entry point -------------------------------------------------------------

def bakeoff2(out: Path, approaches: list[str], force: bool = False, page_only: bool = False,
             variation_test: bool = True, r1_out: Path = R1_OUT, log=print) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    results = _defaults(load_results(out))
    if not page_only:
        results["meta"] = {"hardware": _hardware(),
                           "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                           "lines": len(round2.lines())}
        benchmark(out, results, r1_out)
        check = Checker()
        design_refs(out, results, check, log)
        save_results(out, results)
        chains: dict = {}
        order = [a for a in round2.APPROACHES if a.id in approaches]
        need_dsp = any(a.dsp for a in order) or variation_test
        if need_dsp and "cb-vox" not in approaches and not results["clips"].get("cb-vox"):
            order.insert(0, round2.approach("cb-vox"))  # DSP tuning listens to cb-vox clips
        dsp_refs: dict = {}
        for a in sorted(order, key=lambda a: (a.dsp, 0)):  # plain renders first, then DSP-dependent ones
            if a.dsp and not chains:
                chains = tune_dsp(out, results, check, log)
                dsp_refs = make_dsp_refs(out, chains, results)
                save_results(out, results)
            render(a, out, results, chains, dsp_refs, check, force, log)
        if variation_test:
            if not chains:
                chains = tune_dsp(out, results, check, log)
                dsp_refs = make_dsp_refs(out, chains, results)
            variation(out, results, chains, check, force, log)
        save_results(out, results)
    index = out / "index.html"
    index.write_text(page2.render(results))
    log(f"wrote {index}")
    return index

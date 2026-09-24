"""Batch driver for Seed-VC (github.com/Plachtaa/seed-vc, GPL-3.0), run with *that* checkout's Python:

    $SEEDVC_DIR/.venv/bin/python seedvc_driver.py jobs.json

jobs.json: {"diffusion_steps": 25, "jobs": [{"source": wav, "target": wav, "out": wav}, ...]}.
Loads the models once, converts every job, and prints one JSON line per job: {"out": ..., "wall_s": ...}.
It is kept outside the vo package's imports because Seed-VC needs its own torch/transformers pins.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time


def main(path: str) -> None:
    spec = json.load(open(path))
    sys.path.insert(0, os.getcwd())
    import soundfile as sf

    import inference  # the Seed-VC checkout's module (cwd must be the checkout)

    loaded = {}
    orig = inference.load_models

    def load_once(args):
        if "m" not in loaded:
            loaded["m"] = orig(args)
        return loaded["m"]

    inference.load_models = load_once
    # Recent torchaudio needs torchcodec to save; write with soundfile instead.
    inference.torchaudio.save = lambda p, wav, sr: sf.write(p, wav.cpu().numpy().reshape(-1), sr)
    tmp = tempfile.mkdtemp()
    for job in spec["jobs"]:
        args = argparse.Namespace(
            source=job["source"], target=job["target"], output=tmp, diffusion_steps=spec.get("diffusion_steps", 25),
            length_adjust=1.0, inference_cfg_rate=0.7, f0_condition=False, auto_f0_adjust=False,
            semi_tone_shift=0, checkpoint=None, config=None, fp16=False)
        t = time.perf_counter()
        inference.main(args)
        wall = time.perf_counter() - t
        produced = max((os.path.join(tmp, f) for f in os.listdir(tmp)), key=os.path.getmtime)
        os.makedirs(os.path.dirname(job["out"]), exist_ok=True)
        shutil.move(produced, job["out"])
        print(json.dumps({"out": job["out"], "wall_s": round(wall, 3)}), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])

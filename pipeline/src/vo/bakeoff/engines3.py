"""Round-3 backends: one VoxCPM2 model for anchors and every variant, and the speaker embedder."""
from __future__ import annotations

from math import gcd
from pathlib import Path

import numpy as np

from vo.bakeoff import round3
from vo.bakeoff.engines2 import _chunked, _collect, _load


class VoxCPM2:
    def __init__(self):
        self.model = _load(round3.REPO)

    def design(self, s: round3.Subject, seed: int) -> tuple[np.ndarray, int]:
        """The NPC's anchor: its description reading the race's anchor text."""
        import mlx.core as mx

        mx.random.seed(seed)
        return _collect(self.model.generate(text=s.anchor_text, instruct=s.description, **round3.SETTINGS))

    def synth(self, v: round3.Variant, s: round3.Subject, text: str, seed: int,
              anchor: Path | None) -> tuple[np.ndarray, int]:
        import mlx.core as mx

        kw = round3.generate_kwargs(v, s.description, str(anchor) if anchor else None, s.anchor_text)

        def one(t):
            mx.random.seed(seed)  # per chunk, so a fixed-seed NPC restarts from the same noise every time
            return _collect(self.model.generate(text=t, **kw, **round3.SETTINGS))

        return _chunked(one, text)


class Embedder:
    """WavLM-Base-Plus-SV x-vectors (PyTorch, CPU), L2-normalised."""

    SR = 16000

    def __init__(self):
        import torch
        from transformers import AutoFeatureExtractor, WavLMForXVector

        torch.set_grad_enabled(False)
        self.fe = AutoFeatureExtractor.from_pretrained(round3.EMBEDDER)
        self.model = WavLMForXVector.from_pretrained(round3.EMBEDDER).eval()

    def __call__(self, wav: Path) -> np.ndarray:
        import soundfile as sf
        from scipy.signal import resample_poly

        a, sr = sf.read(wav, dtype="float32")
        if a.ndim > 1:
            a = a.mean(axis=1)
        if sr != self.SR:
            g = gcd(sr, self.SR)
            a = resample_poly(a, self.SR // g, sr // g).astype(np.float32)
        x = self.fe(a, sampling_rate=self.SR, return_tensors="pt")
        e = self.model(**x).embeddings[0].numpy().astype(np.float32)
        return e / (np.linalg.norm(e) or 1)

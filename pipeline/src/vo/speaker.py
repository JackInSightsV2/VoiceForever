"""Speaker embeddings for NPC Voices: WavLM-Base-Plus-SV x-vectors (PyTorch, CPU), L2-normalised, so the cosine of two
embeddings is their dot product. Copied from the bake-off's embedder (#10 round 3), reading samples instead of files.

Scale seen in the bake-off: two lines of one VoxCPM2 voice (continuation from one anchor) 0.94-0.98; two NPCs
designed from one description with different seeds 0.89-0.93; different voices well below that.
"""
from __future__ import annotations

from functools import cache
from math import gcd

import numpy as np

MODEL = "microsoft/wavlm-base-plus-sv"
SR = 16000
DIM = 512


class Embedder:
    def __init__(self):
        import torch
        from transformers import AutoFeatureExtractor, WavLMForXVector

        torch.set_grad_enabled(False)
        # Apple GPU when there is one: ~10x faster than CPU (0.2 s vs 2 s per 12 s clip), same embedding.
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.fe = AutoFeatureExtractor.from_pretrained(MODEL)
        self.model = WavLMForXVector.from_pretrained(MODEL).eval().to(self.device)

    def __call__(self, samples: np.ndarray, rate: int) -> np.ndarray:
        from scipy.signal import resample_poly

        a = np.asarray(samples, dtype=np.float32).reshape(-1)
        if rate != SR:
            g = gcd(rate, SR)
            a = resample_poly(a, SR // g, rate // g).astype(np.float32)
        x = {k: v.to(self.device) for k, v in self.fe(a, sampling_rate=SR, return_tensors="pt").items()}
        return normalise(self.model(**x).embeddings[0].cpu().numpy())


@cache
def embedder() -> Embedder:
    return Embedder()


def normalise(e) -> np.ndarray:
    e = np.asarray(e, dtype=np.float32).reshape(-1)
    return e / (np.linalg.norm(e) or 1)


def cosine(a, b) -> float:
    return float(np.dot(normalise(a), normalise(b)))


def to_blob(e: np.ndarray) -> bytes:
    return np.asarray(e, dtype="<f4").tobytes()


def from_blob(b: bytes | None) -> np.ndarray | None:
    return None if b is None else np.frombuffer(b, dtype="<f4").copy()

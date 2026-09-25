"""Round-5 backend: Chatterbox voice conversion (S3Gen only; no text model).

Round 2's cb-vc was Chatterbox TTS with split conditioning. Here the source is audio: its S3 speech tokens
(what is said and how) go straight into S3Gen, conditioned on a target clip's mel/speaker embedding (the timbre)
- the same path as Chatterbox's own ChatterboxVC.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from vo.bakeoff import round2
from vo.bakeoff.engines2 import _load


class ChatterboxVC:
    def __init__(self):
        self.model = _load(round2.CB_REPO)
        self._targets: dict[str, dict] = {}

    def _target(self, wav: Path) -> dict:
        key = str(wav)
        if key not in self._targets:
            self._targets[key] = self.model.prepare_conditionals(key, 24000, 0.5).gen
        return self._targets[key]

    def __call__(self, source: Path, target: Path) -> tuple[np.ndarray, int]:
        import mlx.core as mx
        from mlx_audio.tts.models.chatterbox.chatterbox import S3_SR
        from mlx_audio.tts.models.chatterbox.s3tokenizer import log_mel_spectrogram
        from mlx_audio.utils import load_audio

        a = load_audio(str(source), sample_rate=S3_SR)
        if a.ndim == 2:
            a = a.squeeze(0)
        mel = mx.expand_dims(log_mel_spectrogram(a), 0)
        tokens, _ = self.model._s3_tokenizer(mel, mx.array([mel.shape[2]]))
        wav = self.model.s3gen(speech_tokens=tokens, ref_dict=self._target(target), finalize=True)
        return np.asarray(wav, dtype=np.float32).reshape(-1), self.model.sample_rate

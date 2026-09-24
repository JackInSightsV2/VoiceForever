"""TTS backends behind one interface. A voice id is `<backend>:<voice>`, e.g. `kokoro:am_michael`."""
from functools import cache
from typing import Protocol

import numpy as np

DEFAULT_VOICE = "am_michael"
DEFAULT_VOICE_ID = f"kokoro:{DEFAULT_VOICE}"


class TTSBackend(Protocol):
    def render(self, text: str, voice: str, seed: int) -> tuple[np.ndarray, int]:
        """Mono float32 samples and their sample rate. A different seed should give a different take."""


class Kokoro:
    """Stock Kokoro voice via mlx-audio. Each line of `text` is a segment, joined with its natural pause."""
    MODEL = "mlx-community/Kokoro-82M-bf16"

    @cache
    def _model(self):
        from mlx_audio.tts.utils import load
        return load(self.MODEL)

    def render(self, text: str, voice: str, seed: int) -> tuple[np.ndarray, int]:
        import mlx.core as mx
        model = self._model()
        mx.random.seed(seed)
        audio = np.concatenate([np.asarray(r.audio, dtype=np.float32) for r in model.generate(text, voice=voice)])
        return audio, model.sample_rate


BACKENDS: dict[str, type] = {"kokoro": Kokoro}


def split_voice_id(voice_id: str) -> tuple[str, str]:
    name, _, voice = voice_id.partition(":")
    if name not in BACKENDS or not voice:
        raise ValueError(f"unknown TTS voice id {voice_id!r}")
    return name, voice


@cache
def backend(name: str) -> TTSBackend:
    """One loaded backend per process."""
    return BACKENDS[name]()

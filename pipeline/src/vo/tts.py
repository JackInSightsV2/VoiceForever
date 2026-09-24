"""TTS backends behind one interface. A voice id is `<backend>:<voice>`, e.g. `kokoro:am_michael`.

Per-type delivery: every render gets the Delivery for its line type (DELIVERY below). A backend applies the knobs it
supports and ignores the rest. What is actually applied today:

- Kokoro: `speed` only (it has no emotion control). Completion is rendered slightly faster (more upbeat); progress and
  everything else at normal speed.
- `exaggeration` and `cfg_weight` are Chatterbox-style knobs held for later backends; no backend reads them yet.
"""
from dataclasses import asdict, dataclass
from functools import cache
from typing import Protocol

import numpy as np

DEFAULT_VOICE = "am_michael"
DEFAULT_VOICE_ID = f"kokoro:{DEFAULT_VOICE}"
# The Narrator: one fixed voice for Quest Text from objects and items (lines with no NPC). A stock Kokoro voice,
# distinct from the NPC default, until the Approval Gate approves a Narrator and replaces this.
NARRATOR_VOICE_ID = "kokoro:bm_george"


@dataclass(frozen=True)
class Delivery:
    """How a line is spoken, per line type. Defaults are the voice's own delivery."""
    speed: float = 1.0         # Kokoro: applied. Speaking rate multiplier.
    exaggeration: float = 0.5  # Chatterbox: emotion intensity (warmer, more upbeat above 0.5). Not applied yet.
    cfg_weight: float = 0.5    # Chatterbox: pacing/adherence (lower = more relaxed). Not applied yet.

    def key(self) -> str:
        """Stable text form, part of a job's hash so a delivery change requeues its lines."""
        return ",".join(f"{k}={v:g}" for k, v in sorted(asdict(self).items()))


DEFAULT_DELIVERY = Delivery()
# Quest completion: warmer and more upbeat. Quest progress: short and neutral. Detail, gossip, greeting: the default.
DELIVERY: dict[str, Delivery] = {
    "quest_complete": Delivery(speed=1.06, exaggeration=0.65, cfg_weight=0.5),
    "quest_progress": Delivery(speed=1.0, exaggeration=0.35, cfg_weight=0.5),
}


def delivery(line_type: str | None) -> Delivery:
    return DELIVERY.get(line_type or "", DEFAULT_DELIVERY)


class TTSBackend(Protocol):
    def render(self, text: str, voice: str, seed: int,
               delivery: Delivery = DEFAULT_DELIVERY) -> tuple[np.ndarray, int]:
        """Mono float32 samples and their sample rate. A different seed should give a different take.
        Apply what the backend can of `delivery`; ignore the rest."""


class Kokoro:
    """Stock Kokoro voice via mlx-audio. Each line of `text` is a segment, joined with its natural pause.
    The voice's first letter picks the accent (Kokoro convention: a = American, b = British)."""
    MODEL = "mlx-community/Kokoro-82M-bf16"

    @cache
    def _model(self):
        from mlx_audio.tts.utils import load
        return load(self.MODEL)

    def render(self, text: str, voice: str, seed: int,
               delivery: Delivery = DEFAULT_DELIVERY) -> tuple[np.ndarray, int]:
        import mlx.core as mx
        model = self._model()
        mx.random.seed(seed)
        lang = voice[0] if voice[:1] in ("a", "b") else "a"
        audio = np.concatenate([np.asarray(r.audio, dtype=np.float32) for r in model.generate(
            text, voice=voice, speed=delivery.speed, lang_code=lang)])
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

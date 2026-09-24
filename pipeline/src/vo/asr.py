"""ASR QA: transcribe a clip with Whisper and score it against the text it should say."""
import re
from functools import cache
from pathlib import Path
from typing import Protocol

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
# The mlx-community conversion ships weights only; the tokenizer comes from the original repo.
PROCESSOR = "openai/whisper-large-v3-turbo"


class ASRBackend(Protocol):
    def transcribe(self, wav: Path) -> str: ...


class Whisper:
    def __init__(self, model: str = DEFAULT_MODEL, processor: str = PROCESSOR):
        self.model_id, self.processor_id = model, processor

    @cache
    def _model(self):
        from mlx_audio.stt.utils import load
        from transformers import WhisperProcessor
        model = load(self.model_id)
        if getattr(model, "_processor", None) is None:
            model._processor = WhisperProcessor.from_pretrained(self.processor_id)
        return model

    def transcribe(self, wav: Path) -> str:
        return self._model().generate(str(wav), language="en", verbose=False).text


@cache
def whisper(model: str = DEFAULT_MODEL) -> Whisper:
    """One loaded Whisper per process."""
    return Whisper(model)


def normalise(text: str) -> list[str]:
    """Lower-case words with punctuation dropped; apostrophes join (Kel'Thuzad = kelthuzad), dashes split."""
    text = text.lower().replace("’", "'")
    text = re.sub(r"['`]", "", text)
    text = re.sub(r"[^\w\s]|_", " ", text)
    return text.split()


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate of `hypothesis` against `reference`: word-level edit distance / reference words."""
    ref, hyp = normalise(reference), normalise(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)

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


NAME_WINDOW = 4  # ASR may split a respelled name into up to this many words ("kel thoo zahd")


def _name_token(i: int) -> str:
    return f"lexname{i}x"


def collapse_names(reference: str, hypothesis: str, names) -> tuple[list[str], list[str], list[str]]:
    """Normalised (reference, hypothesis) words with each Lexicon name as one token on both sides, so a respelling
    costs nothing: the reference's respelling ("Kel-thoo-zahd", 3 words) and whatever ASR wrote for it (the written
    "Kel'Thuzad", "kelthuzad", "kel thoo zahd", ...) each become one token if they sound alike. `names` is
    [(spelling, written)]. Also returns the written names ASR missed."""
    from vo.lexicon import SOUNDS_LIKE, similarity
    names = [(s, w) for s, w in names if s]
    for i, (spelling, _) in sorted(enumerate(names), key=lambda x: -len(x[1][0])):
        reference = re.sub(rf"(?<![\w'’-]){re.escape(spelling)}(?![\w'’-])", f" {_name_token(i)} ", reference)
    ref = normalise(reference)
    wanted = sorted(i for i in range(len(names)) if _name_token(i) in ref)
    words, hyp, j = normalise(hypothesis), [], 0

    def best(at: int) -> tuple[float, int, int]:
        """(similarity, -size, name) of the best name match of 1..NAME_WINDOW words starting at `at`."""
        out = (0.0, 0, -1)
        for size in range(1, min(NAME_WINDOW, len(words) - at) + 1):
            for i in wanted:
                out = max(out, (similarity(" ".join(words[at:at + size]), *names[i]), -size, i))
        return out

    while j < len(words):
        score, neg_size, i = best(j)
        # Match here unless a match starting one word later is at least as good ("the kel thoo zahd").
        if score >= SOUNDS_LIKE and (j + 1 >= len(words) or best(j + 1)[0] < score):
            hyp.append(_name_token(i))
            j -= neg_size
        else:
            hyp.append(words[j])
            j += 1
    missed = [names[i][1] for i in sorted(wanted) if _name_token(i) not in hyp]
    return ref, hyp, missed


def wer(reference: str, hypothesis: str, names=()) -> float:
    """Word error rate of `hypothesis` against `reference`: word-level edit distance / reference words.
    With `names` [(spelling, written)], a Lexicon respelling in the reference counts as one word and matches ASR's
    rendering of either spelling (see collapse_names)."""
    if names:
        ref, hyp, _ = collapse_names(reference, hypothesis, names)
    else:
        ref, hyp = normalise(reference), normalise(hypothesis)
    return _edit_rate(ref, hyp)


def _edit_rate(ref: list[str], hyp: list[str]) -> float:
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)

"""VoxCPM2 (MLX): voice design for Archetype Candidates, and continuation from an anchor for every NPC line.

ADR-0004: a Candidate is VoxCPM2 designing a voice from its Archetype's description, reading the anchor line; a
human picks one as the Anchor; every line is then a continuation of that anchor (the anchor clip and its transcript
as the prompt). Two continuation modes, both validated in the bake-off (#10 round 4):
- "cont": prompt_audio + prompt_text (best for human_f).
- "ultimate": the anchor also as ref_audio (best for human_m, orc_f, troll_f).

Seeds: mx.random.seed(seed) before each generate call (per chunk for long lines), so a render is reproducible.
"""
from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import numpy as np

REPO = "mlx-community/VoxCPM2-bf16"
SETTINGS = {"inference_timesteps": 10, "cfg_value": 2.0}  # model defaults, as in the bake-off
MODES = ("cont", "ultimate")
MAX_CHARS = 250  # long lines render as sentence groups, each continuing from the anchor
GAP_S = 0.12


def split_sentences(text: str) -> list[str]:
    return [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p]


def chunk(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Whole sentences grouped into chunks of at most max_chars (a longer sentence stays whole)."""
    chunks: list[str] = []
    for sentence in split_sentences(text):
        if chunks and len(chunks[-1]) + 1 + len(sentence) <= max_chars:
            chunks[-1] += " " + sentence
        else:
            chunks.append(sentence)
    return chunks or [text]


def continuation_kwargs(anchor: Path | str, anchor_text: str, mode: str = "cont") -> dict:
    """mlx-audio VoxCPM2 generate() arguments (text excluded) to continue from an anchor."""
    if mode not in MODES:
        raise ValueError(f"unknown continuation mode {mode!r}; one of {MODES}")
    kw = {"prompt_audio": str(anchor), "prompt_text": anchor_text}
    if mode == "ultimate":
        kw["ref_audio"] = str(anchor)
    return kw


def _collect(results) -> tuple[np.ndarray, int]:
    results = list(results)
    audio = np.concatenate([np.asarray(r.audio, dtype=np.float32).reshape(-1) for r in results])
    return audio, results[0].sample_rate


class VoxCPM2:
    """One loaded model per process (loaded on first use)."""

    @cache
    def _model(self):
        from mlx_audio.tts.utils import load_model

        return load_model(REPO)

    def design(self, text: str, description: str, seed: int) -> tuple[np.ndarray, int]:
        """A Candidate anchor: a voice designed from `description`, reading `text`."""
        import mlx.core as mx

        mx.random.seed(seed)
        return _collect(self._model().generate(text=text, instruct=description, **SETTINGS))

    def continue_(self, text: str, anchor: Path | str, anchor_text: str, seed: int,
                  mode: str = "cont") -> tuple[np.ndarray, int]:
        """`text` in the anchor's voice: each chunk continues from the anchor clip and its transcript."""
        import mlx.core as mx

        kw = continuation_kwargs(anchor, anchor_text, mode)
        parts = []
        for c in chunk(text):
            mx.random.seed(seed)
            parts.append(_collect(self._model().generate(text=c, **kw, **SETTINGS)))
        sr = parts[0][1]
        gap = np.zeros(int(GAP_S * sr), dtype=np.float32)
        out = []
        for i, (a, _) in enumerate(parts):
            if i:
                out.append(gap)
            out.append(a)
        return np.concatenate(out), sr


@cache
def engine() -> VoxCPM2:
    return VoxCPM2()


class Backend:
    """`vo run`'s TTS backend for NPC lines. A voice is `<archetype>@<candidate>` (see lock.voice_id); the anchor,
    transcript, mode and effect chain come from approved_voices.json, so a changed anchor is a new voice id and
    requeues its lines. Delivery (per line type) isn't applied: continuation copies the anchor's delivery."""

    def __init__(self, engine_: VoxCPM2 | None = None, lock_data: dict | None = None):
        self._engine, self._lock = engine_, lock_data

    def _entry(self, voice: str) -> dict:
        from vo import lock

        data = self._lock if self._lock is not None else lock.load(lock.path())
        self._lock = data
        aid, _, cand = voice.partition("@")
        entry = data["archetypes"].get(aid)
        if entry is None or entry["candidate"] != cand:
            raise ValueError(f"voice {voice!r} is not an approved anchor in approved_voices.json")
        return entry

    def render(self, text: str, voice: str, seed: int, delivery=None) -> tuple[np.ndarray, int]:
        from vo import effects

        e = self._entry(voice)
        samples, rate = (self._engine or engine()).continue_(text, e["anchor"], e["transcript"], seed, e["mode"])
        return effects.apply(e.get("effect_chain"), samples, rate), rate

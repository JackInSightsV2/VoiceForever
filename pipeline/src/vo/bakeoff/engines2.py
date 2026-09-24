"""Round-2 model backends (MLX, Apple silicon; needs the `bakeoff` dependency group).

Each engine loads once; `synth(text, voice_id)` returns (mono float32 audio, sample rate).
`refs[voice_id]` is the reference clip for the approach's designer; `dsp_refs[voice_id]` is that clip
after the race DSP chain (used by the VC approach and the NPC variation test).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from vo.bakeoff import round2
from vo.bakeoff.text import chunk

MAX_CHARS = 250   # models without their own long-text handling render sentence groups
GAP_S = 0.12


def _collect(results) -> tuple[np.ndarray, int]:
    results = list(results)
    audio = np.concatenate([np.asarray(r.audio, dtype=np.float32).reshape(-1) for r in results])
    return audio, results[0].sample_rate


def _chunked(render, text: str) -> tuple[np.ndarray, int]:
    parts = [render(c) for c in chunk(text, MAX_CHARS)]
    sr = parts[0][1]
    gap = np.zeros(int(GAP_S * sr), dtype=np.float32)
    out = []
    for i, (a, _) in enumerate(parts):
        if i:
            out.append(gap)
        out.append(a)
    return np.concatenate(out), sr


def _load(repo):
    from mlx_audio.tts.utils import load_model

    return load_model(repo)


class Engine:
    def __init__(self, spec: round2.Approach, refs: dict[str, Path], dsp_refs: dict[str, Path]):
        self.spec, self.refs, self.dsp_refs = spec, refs, dsp_refs

    def synth(self, text: str, voice_id: str) -> tuple[np.ndarray, int]:
        raise NotImplementedError


class Chatterbox(Engine):
    def __init__(self, spec, refs, dsp_refs):
        super().__init__(spec, refs, dsp_refs)
        self.model = _load(spec.repo)
        self._conds = {}

    def conds(self, path: Path):
        key = str(path)
        if key not in self._conds:
            self._conds[key] = self.model.prepare_conditionals(key, 24000, self.spec.settings["exaggeration"])
        return self._conds[key]

    def voice_conds(self, voice_id: str):
        return self.conds(self.refs[voice_id])

    def synth(self, text, voice_id):
        return _collect(self.model.generate(text=text, conds=self.voice_conds(voice_id), verbose=False,
                                            **self.spec.settings))


class ChatterboxVC(Chatterbox):
    """T3 (what is said, accent, prosody) from the natural reference; S3Gen (timbre) from the DSP'd one."""

    def split(self, t3_ref: Path, gen_ref: Path):
        from mlx_audio.tts.models.chatterbox.chatterbox import Conditionals

        return Conditionals(self.conds(t3_ref).t3, self.conds(gen_ref).gen)

    def voice_conds(self, voice_id):
        return self.split(self.refs[voice_id], self.dsp_refs[voice_id])


class VoxCPM(Engine):
    def __init__(self, spec, refs, dsp_refs):
        super().__init__(spec, refs, dsp_refs)
        self.model = _load(spec.repo)

    def synth(self, text, voice_id):
        ref = str(self.refs[voice_id])
        s = self.spec.settings
        return _chunked(lambda t: _collect(self.model.generate(
            text=t, ref_audio=ref, prompt_audio=ref, prompt_text=round2.REF_TEXT,
            inference_timesteps=s["inference_timesteps"], cfg_value=s["cfg_value"])), text)


class VoxCPMDesign(Engine):
    def __init__(self, spec, refs, dsp_refs):
        super().__init__(spec, refs, dsp_refs)
        self.model = _load(spec.repo)

    def synth(self, text, voice_id):
        prompt = round2.voice(voice_id).prompt
        return _chunked(lambda t: _collect(self.model.generate(text=t, instruct=prompt)), text)


class QwenBase(Engine):
    def __init__(self, spec, refs, dsp_refs):
        super().__init__(spec, refs, dsp_refs)
        self.model = _load(spec.repo)

    def synth(self, text, voice_id):
        return _chunked(lambda t: _collect(self.model.generate(
            text=t, ref_audio=str(self.refs[voice_id]), ref_text=round2.REF_TEXT)), text)


class Fish(Engine):
    def __init__(self, spec, refs, dsp_refs):
        super().__init__(spec, refs, dsp_refs)
        self.model = _load(spec.repo)
        self._refs = {}

    def synth(self, text, voice_id):
        from mlx_audio.utils import load_audio

        if voice_id not in self._refs:  # this port wants an array, at the model's rate
            self._refs[voice_id] = load_audio(str(self.refs[voice_id]), sample_rate=self.model.sample_rate)
        return _chunked(lambda t: _collect(self.model.generate(
            text=t, ref_audio=self._refs[voice_id], ref_text=round2.REF_TEXT, verbose=False,
            **self.spec.settings)), text)


class OmniVoice(Engine):
    def __init__(self, spec, refs, dsp_refs):
        super().__init__(spec, refs, dsp_refs)
        self.model = _load(spec.repo)

    def synth(self, text, voice_id):
        return _chunked(lambda t: _collect(self.model.generate(
            text=t, ref_audio=str(self.refs[voice_id]), ref_text=round2.REF_TEXT, **self.spec.settings)), text)


ENGINES = {"chatterbox": Chatterbox, "chatterbox-vc": ChatterboxVC, "voxcpm": VoxCPM,
           "voxcpm-design": VoxCPMDesign, "qwen-base": QwenBase, "fish": Fish, "omnivoice": OmniVoice}


def load(a: round2.Approach, refs, dsp_refs) -> Engine:
    return ENGINES[a.engine](a, refs, dsp_refs)


class Designer:
    """Renders a reference Candidate from a voice's text prompt."""

    def __init__(self, d: round2.Designer):
        self.d = d
        self.model = _load(d.repo)

    def __call__(self, v: round2.Voice, seed: int) -> tuple[np.ndarray, int]:
        import mlx.core as mx

        mx.random.seed(seed)
        if self.d.id == "qwen":
            return _collect(self.model.generate_voice_design(text=round2.REF_TEXT, language="English",
                                                             instruct=v.prompt))
        return _collect(self.model.generate(text=round2.REF_TEXT, instruct=v.prompt))


class SpeakerEncoder:
    """Chatterbox's voice encoder (a speaker-verification embedding), for NPC Voice distances."""

    def __init__(self, model=None):
        self.model = model or _load(round2.CB_REPO)

    def __call__(self, audio: np.ndarray, sr: int) -> np.ndarray:
        from mlx_audio.tts.models.chatterbox.chatterbox import S3_SR
        from mlx_audio.utils import resample_audio

        import mlx.core as mx

        a = mx.array(np.asarray(audio, dtype=np.float32))
        if sr != S3_SR:
            a = resample_audio(a, sr, S3_SR)
        e = self.model.ve.embeds_from_wavs([a], sample_rate=S3_SR)
        e = np.asarray(mx.mean(e, axis=0), dtype=np.float32)
        return e / (np.linalg.norm(e) or 1)

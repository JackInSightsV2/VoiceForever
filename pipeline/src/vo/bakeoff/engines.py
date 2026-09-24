"""Model backends. Everything here needs the `bakeoff` dependency group (MLX, Apple silicon).

Each engine loads once, then `synth(text, voice_id)` returns (mono float32 audio, sample rate).
Conditioning on a reference clip is cached per voice, as the real pipeline would per NPC Voice.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from vo.bakeoff import catalog
from vo.bakeoff.text import chunk, split_sentences


def _mlx_generate(model, **kwargs) -> tuple[np.ndarray, int]:
    results = list(model.generate(verbose=False, **kwargs))
    audio = np.concatenate([np.asarray(r.audio, dtype=np.float32).reshape(-1) for r in results])
    return audio, results[0].sample_rate


class Engine:
    def __init__(self, spec: catalog.ModelSpec, refs: dict[str, Path]):
        self.spec = spec
        self.refs = refs

    def synth(self, text: str, voice_id: str) -> tuple[np.ndarray, int]:
        raise NotImplementedError


class Chatterbox(Engine):
    def __init__(self, spec, refs):
        super().__init__(spec, refs)
        from mlx_audio.tts.utils import load_model

        self.model = load_model(spec.repo)
        self._conds = {}

    def synth(self, text, voice_id):
        s = self.spec.settings
        if voice_id not in self._conds:
            self._conds[voice_id] = self.model.prepare_conditionals(
                str(self.refs[voice_id]), 24000, s["exaggeration"])
        return _mlx_generate(self.model, text=text, conds=self._conds[voice_id], **s)


class Orpheus(Engine):
    def __init__(self, spec, refs):
        super().__init__(spec, refs)
        from mlx_audio.tts.utils import load_model

        self.model = load_model(spec.repo)

    def synth(self, text, voice_id):
        # Orpheus stops at max_tokens (~14 s by default), so long passages are rendered in chunks.
        if self.spec.clones:
            kw = {"ref_audio": str(self.refs[voice_id]), "ref_text": catalog.REF_TEXT}
        else:
            kw = {"voice": catalog.ref_voice(voice_id).orpheus_stock}
        parts = [_mlx_generate(self.model, text=c, max_tokens=2400, **kw, **self.spec.settings)
                 for c in chunk(text, 200)]
        return np.concatenate([a for a, _ in parts]), parts[0][1]


class Kokoro(Engine):
    def __init__(self, spec, refs):
        super().__init__(spec, refs)
        from mlx_audio.tts.utils import load_model

        self.model = load_model(spec.repo)

    def synth(self, text, voice_id):
        # voice_id is ignored unless it names a Kokoro voice (the Narrator comparison).
        s = dict(self.spec.settings)
        if voice_id in catalog.KOKORO_NARRATOR_ALTS:
            s["voice"] = voice_id
            s["lang_code"] = voice_id[0]  # a = American, b = British
        return _mlx_generate(self.model, text=text, **s)


class F5(Engine):
    SR = 24000
    HOP = 256
    TARGET_RMS = 0.1

    def __init__(self, spec, refs):
        super().__init__(spec, refs)
        import mlx.core as mx

        # f5-tts-mlx 0.2.6 passes an mx.array inside a shape tuple, which current MLX rejects.
        orig = mx.random.normal
        if not getattr(orig, "_vo_shim", False):
            def normal(shape=(), *a, **k):
                return orig(tuple(int(x) for x in shape), *a, **k)
            normal._vo_shim = True
            mx.random.normal = normal

        from f5_tts_mlx.cfm import F5TTS
        from f5_tts_mlx.utils import convert_char_to_pinyin

        self.mx = mx
        self.pinyin = convert_char_to_pinyin
        self.model = F5TTS.from_pretrained(spec.repo)
        self._refs = {}

    def _ref(self, voice_id):
        if voice_id not in self._refs:
            import soundfile as sf

            audio, sr = sf.read(self.refs[voice_id], dtype="float32")
            assert sr == self.SR, f"F5 reference must be {self.SR} Hz"
            rms = float(np.sqrt(np.mean(audio**2)))
            if rms < self.TARGET_RMS:
                audio = audio * self.TARGET_RMS / rms
            self._refs[voice_id] = self.mx.array(audio)
        return self._refs[voice_id]

    def synth(self, text, voice_id):
        mx, s = self.mx, self.spec.settings
        ref = self._ref(voice_id)
        ref_frames = ref.shape[0] // self.HOP
        out = []
        for sentence in split_sentences(text):
            # Upstream F5 heuristic: generated length scales with text length at the reference's pace.
            gen_frames = int(ref_frames / len(catalog.REF_TEXT.encode()) * len(sentence.encode()))
            wave, _ = self.model.sample(
                mx.expand_dims(ref, 0),
                text=self.pinyin([catalog.REF_TEXT + " " + sentence]),
                duration=ref_frames + gen_frames,
                steps=s["steps"], method=s["method"], cfg_strength=s["cfg_strength"],
                sway_sampling_coef=s["sway_sampling_coef"],
            )
            wave = wave[ref.shape[0]:]
            mx.eval(wave)
            out.append(np.asarray(wave, dtype=np.float32))
        return np.concatenate(out), self.SR


ENGINES = {"chatterbox": Chatterbox, "f5": F5, "orpheus": Orpheus, "orpheus-clone": Orpheus, "kokoro": Kokoro}


def load(model_id: str, refs: dict[str, Path]) -> Engine:
    return ENGINES[model_id](catalog.model(model_id), refs)


class Designer:
    """Renders reference clips from text prompts with the voice-design model."""

    def __init__(self):
        from mlx_audio.tts.utils import load_model

        self.model = load_model(catalog.DESIGN_MODEL)

    def __call__(self, voice: catalog.RefVoice, out: Path, seed: int = 0) -> None:
        import mlx.core as mx
        import soundfile as sf

        mx.random.seed(seed)
        results = list(self.model.generate_voice_design(
            text=catalog.REF_TEXT, language="English", instruct=voice.prompt))
        audio = np.concatenate([np.asarray(r.audio, dtype=np.float32).reshape(-1) for r in results])
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(out, audio, results[0].sample_rate)


class Transcriber:
    def __init__(self):
        from mlx_audio.stt.utils import load_model

        self.model = load_model(catalog.ASR_MODEL)

    def __call__(self, wav: Path) -> str:
        return self.model.generate(str(wav)).text.strip()

"""Reference voices and the models under test, with the settings and licences recorded on the page."""
from dataclasses import dataclass, field

# Reference clips are rendered by a voice-design model from a text prompt (Build Spec, Voice design step 2).
DESIGN_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16"
DESIGN_LICENCE = "Apache-2.0 (Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign)"

# One neutral sentence, ~10 s, spoken by every reference voice. F5 and Orpheus-clone need its transcript.
REF_TEXT = (
    "The road north is long, and the weather has turned cold. "
    "Rest here tonight, eat something warm, and we will talk about the work in the morning."
)


@dataclass(frozen=True)
class RefVoice:
    id: str
    label: str
    prompt: str
    orpheus_stock: str  # closest Orpheus built-in voice, for the non-cloning Orpheus run


REF_VOICES: tuple[RefVoice, ...] = (
    RefVoice("dwarf_m", "Dwarf, male",
             "A deep, gravelly, barrel-chested middle-aged male voice with a broad Scottish accent. "
             "Hearty, gruff and warm, like a stout mountain blacksmith who laughs easily.", "leo"),
    RefVoice("nelf_f", "Night elf, female",
             "A calm, low and smooth adult female voice, serene and measured, slightly breathy, with an "
             "ancient, otherworldly dignity. Speaks slowly and softly, like a priestess in a moonlit forest.", "tara"),
    RefVoice("orc_m", "Orc, male",
             "A very deep, raspy, harsh male voice with a guttural growl. Blunt, commanding and battle-hardened, "
             "speaking in short forceful phrases like a veteran warrior.", "dan"),
    RefVoice("human_f", "Human, female",
             "A clear, warm young adult female voice with a gentle southern English accent. "
             "Friendly, earnest and a little anxious, like a townswoman in a medieval city.", "leah"),
    RefVoice("troll_m", "Troll, male",
             "A laid-back adult male voice with a strong Caribbean Jamaican accent, drawn-out vowels and a "
             "sly, playful, slightly raspy tone, like a mystical witch doctor.", "zac"),
)

KOKORO_NARRATOR = "bm_george"
KOKORO_NARRATOR_ALTS = ("bm_george", "bm_fable", "bm_lewis", "bf_emma", "am_michael")


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    repo: str
    clones: bool
    licence: str
    settings: dict = field(default_factory=dict)
    note: str = ""


MODELS: tuple[ModelSpec, ...] = (
    ModelSpec("chatterbox", "Chatterbox (mlx-audio, fp16)", "mlx-community/chatterbox-fp16", True,
              "MIT (ResembleAI/chatterbox). The official package adds a Perth watermark; this MLX port does not.",
              {"exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8}),
    ModelSpec("f5", "F5-TTS (f5-tts-mlx)", "lucasnewman/f5-tts-mlx", True,
              "Weights CC-BY-NC-4.0 (SWivid/F5-TTS, Emilia training data); the MLX repo card says MIT "
              "but covers the code and converted weights inherit NC. Code MIT.",
              {"steps": 8, "method": "rk4", "cfg_strength": 2.0, "sway_sampling_coef": -1.0,
               "duration": "char-ratio heuristic, per sentence"}),
    ModelSpec("orpheus", "Orpheus 3B ft (mlx-audio, 4-bit), stock voices", "mlx-community/orpheus-3b-0.1-ft-4bit", False,
              "Apache-2.0 per canopylabs card, but a Llama-3.2-3B fine-tune, so the Llama 3.2 Community "
              "Licence also applies (attribution, 700M MAU clause, acceptable-use policy).",
              {"temperature": 0.6, "top_p": 0.8},
              "Uses the closest of Orpheus' 8 built-in American voices per race; cloning needs a fine-tune."),
    ModelSpec("orpheus-clone", "Orpheus 3B ft (mlx-audio, 4-bit), zero-shot clone", "mlx-community/orpheus-3b-0.1-ft-4bit", True,
              "As Orpheus above.",
              {"temperature": 0.6, "top_p": 0.8},
              "Reference clip + transcript as a prompt prefix. Not an officially supported mode of the ft model."),
    ModelSpec("kokoro", "Kokoro 82M (mlx-audio, bf16), Narrator candidate", "mlx-community/Kokoro-82M-bf16", False,
              "Apache-2.0 (hexgrad/Kokoro-82M).",
              {"voice": KOKORO_NARRATOR, "lang_code": "b", "speed": 1.0},
              "No cloning: every line is read in the Narrator voice."),
)

MODEL_IDS = tuple(m.id for m in MODELS)

ASR_MODEL = "mlx-community/parakeet-tdt-0.6b-v2"
ASR_LICENCE = "CC-BY-4.0 (nvidia/parakeet-tdt-0.6b-v2)"


def model(model_id: str) -> ModelSpec:
    return next(m for m in MODELS if m.id == model_id)


def ref_voice(voice_id: str) -> RefVoice:
    return next(v for v in REF_VOICES if v.id == voice_id)

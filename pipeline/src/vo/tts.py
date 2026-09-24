"""Audio generation: stock Kokoro voice via mlx-audio, encoded to Ogg Vorbis."""
import sqlite3
import subprocess
import tempfile
from functools import cache
from pathlib import Path

import numpy as np

MODEL = "mlx-community/Kokoro-82M-bf16"
DEFAULT_VOICE = "am_michael"
SAMPLE_RATE = 48000
BITRATE_KBPS = 112


@cache
def _model():
    from mlx_audio.tts.utils import load
    return load(MODEL)


def render(text: str, voice: str) -> tuple[np.ndarray, int]:
    """Mono float32 samples; each line of `text` is a segment, joined with its natural pause."""
    model = _model()
    audio = np.concatenate([np.asarray(r.audio, dtype=np.float32) for r in model.generate(text, voice=voice)])
    return audio, model.sample_rate


def encode_ogg(samples: np.ndarray, rate: int, out: Path) -> None:
    """Resample to 48 kHz mono with ffmpeg, encode with libvorbis (oggenc): Homebrew ffmpeg lacks libvorbis."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "line.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(rate), "-ac", "1", "-i", "pipe:",
             "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
            input=samples.astype("<f4").tobytes(), check=True)
        subprocess.run(["oggenc", "-Q", "-b", str(BITRATE_KBPS), "-o", str(out), str(wav)], check=True)


def generate(conn: sqlite3.Connection, audio_dir: Path, voice: str = DEFAULT_VOICE) -> list[Path]:
    """Render every line with no audio yet for this voice; layout audio/<npcId|narrator>/<lineId>.ogg."""
    voice_id = f"kokoro:{voice}"
    todo = conn.execute(
        "SELECT id, npc_id, tts_text FROM lines WHERE id NOT IN"
        " (SELECT line_id FROM audio WHERE voice_id = ? AND status = 'done') ORDER BY id", (voice_id,)).fetchall()
    done = []
    for line in todo:
        samples, rate = render(line["tts_text"], voice)
        out = audio_dir / str(line["npc_id"] or "narrator") / f"{line['id']}.ogg"
        encode_ogg(samples, rate, out)
        conn.execute(
            "INSERT OR REPLACE INTO audio (line_id, voice_id, path, duration_s, status) VALUES (?, ?, ?, ?, 'done')",
            (line["id"], voice_id, str(out.resolve()), len(samples) / rate))
        conn.commit()
        done.append(out)
    return done

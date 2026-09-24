import json
import subprocess

import numpy as np
import pytest

from vo import audio

RATE = 24000


def tone(seconds, amp=0.3, hz=440):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amp * np.sin(2 * np.pi * hz * t) * (1 + 0.5 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * RATE), dtype=np.float32)


def test_trim_leaves_150ms_either_side():
    x = np.concatenate([silence(0.6), tone(1.0), silence(0.9)])
    assert len(audio.trim(x, RATE)) / RATE == pytest.approx(1.3, abs=0.011)


def test_trim_keeps_short_lead_in():
    x = np.concatenate([silence(0.05), tone(1.0), silence(0.05)])
    assert len(audio.trim(x, RATE)) / RATE == pytest.approx(1.1, abs=0.011)


def test_trim_rejects_silence():
    with pytest.raises(ValueError, match="silent"):
        audio.trim(silence(1.0), RATE)


@pytest.mark.parametrize("amp", [0.02, 0.3, 0.9])
def test_postprocess_normalises_and_encodes_per_spec(tmp_path, amp):
    x = np.concatenate([silence(0.5), tone(2.0, amp), silence(0.8)])
    wav, ogg = tmp_path / "line.wav", tmp_path / "line.ogg"
    assert audio.postprocess(x, RATE, wav) == pytest.approx(2.3, abs=0.011)
    audio.encode_ogg(wav, ogg)
    lufs, tp = audio.measure(ogg)
    assert lufs == pytest.approx(-16, abs=0.7)
    assert tp <= audio.TRUE_PEAK
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(ogg)],
        capture_output=True, check=True).stdout)["streams"][0]
    assert (probe["codec_name"], probe["sample_rate"], probe["channels"]) == ("vorbis", "48000", 1)
    assert 96_000 <= int(probe["bit_rate"]) <= 128_000
    assert float(probe["duration"]) == pytest.approx(2.3, abs=0.02)

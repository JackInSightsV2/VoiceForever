"""Drift hash: FNV-1a 32-bit over normalised, token-masked Quest Text.

Must stay byte-for-byte identical to addon/VoiceForever/Drift.lua; tests/drift_vectors.json checks both.
The pipeline masks the source tokens; the addon masks the player's real name, race and class.
"""
import re

from vo import text

PLACEHOLDER = {"n": "$N", "r": "$R", "c": "$C"}
_COLOUR = re.compile(r"\|c[0-9a-fA-F]{8}")
_SPACE = re.compile(r"[ \t\n\r\f\v]+")  # Lua's %s in the C locale, not Python's Unicode \s


def fnv1a32(data: bytes) -> str:
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def normalise(s: str) -> str:
    """Strip colour codes, collapse whitespace, trim."""
    s = _COLOUR.sub("", s).replace("|r", "")
    return _SPACE.sub(" ", s).strip(" ")


def mask(raw: str, gender: str | None = None) -> str:
    """Source text as the client shows it, with $N/$R/$C as fixed placeholders and $G resolved for `gender`."""
    variants = text.gender_variants(raw)
    if gender in variants:
        raw = variants[gender]
    elif None in variants:
        raw = variants[None]
    else:
        raise ValueError("text has $G choices; pass the player gender")
    raw = re.sub(r"\$[Bb]", "\n", raw)
    raw = re.sub(r"\$([NnRrCc])", lambda m: PLACEHOLDER[m[1].lower()], raw)
    return normalise(raw)


def text_hash(raw: str, gender: str | None = None) -> str:
    return fnv1a32(mask(raw, gender).encode("utf-8"))

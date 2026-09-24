"""Text prep: player gender variants and Neutral Address for spoken audio."""
import re

GENDER = re.compile(r"\$[Gg]\s*([^:;]*):([^;]*);")
NEUTRAL = {"n": "friend", "c": "adventurer", "r": "adventurer"}


def gender_variants(text: str) -> dict[str | None, str]:
    """{None: text} when the text has no $G, else one variant per player gender."""
    if not GENDER.search(text):
        return {None: text}
    return {"m": GENDER.sub(r"\1", text), "f": GENDER.sub(r"\2", text)}


def tts_text(text: str) -> str:
    """Spoken form: $B becomes a line break (a pause), $N/$C/$R a Neutral Address."""
    text = re.sub(r"\$[Bb]", "\n", text)
    text = re.sub(r"\$([NnCcRr])", lambda m: NEUTRAL[m[1].lower()], text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()

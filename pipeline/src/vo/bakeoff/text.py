"""Sentence splitting for models that render long passages piecewise."""
import re


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]


def chunk(text: str, max_chars: int) -> list[str]:
    """Group whole sentences into chunks of at most max_chars (a longer sentence stays whole)."""
    chunks: list[str] = []
    for sentence in split_sentences(text):
        if chunks and len(chunks[-1]) + 1 + len(sentence) <= max_chars:
            chunks[-1] += " " + sentence
        else:
            chunks.append(sentence)
    return chunks

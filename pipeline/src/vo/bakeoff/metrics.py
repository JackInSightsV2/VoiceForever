"""Pure helpers: word error rate and per-model speed summaries."""
import re
import statistics


def normalise(text: str) -> list[str]:
    # Apostrophes and hyphens are dropped so "Kel'Thuzad" and "Kelthuzad" compare equal.
    text = re.sub(r"['’\-]", "", text.lower())
    return re.findall(r"[a-z0-9]+", text)


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalise(reference), normalise(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def summarise(clips: dict[str, dict]) -> dict:
    """Speed/accuracy summary over one model's clips ({line_id: {audio_s, wall_s, rtf, wer?}})."""
    if not clips:
        return {"lines": 0}
    audio = sum(c["audio_s"] for c in clips.values())
    wall = sum(c["wall_s"] for c in clips.values())
    rtfs = [c["rtf"] for c in clips.values()]
    wers = [c["wer"] for c in clips.values() if c.get("wer") is not None]
    return {
        "lines": len(clips),
        "audio_s": audio,
        "wall_s": wall,
        "rtf": audio / wall if wall else 0.0,
        "rtf_median": statistics.median(rtfs),
        "rtf_min": min(rtfs),
        "rtf_max": max(rtfs),
        "wer_mean": statistics.fmean(wers) if wers else None,
        "wer_over_10pct": sum(w > 0.10 for w in wers),
    }

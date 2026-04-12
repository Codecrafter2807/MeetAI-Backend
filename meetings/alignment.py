"""Map Whisper timed segments onto pyannote speaker intervals (who said what)."""


def _seg_bounds(seg) -> tuple[float, float, str]:
    if isinstance(seg, dict):
        return (
            float(seg.get("start", 0)),
            float(seg.get("end", 0)),
            (seg.get("text") or "").strip(),
        )
    return (
        float(getattr(seg, "start", 0)),
        float(getattr(seg, "end", 0)),
        (getattr(seg, "text", None) or "").strip(),
    )


def whisper_text_for_interval(
    t0: float, t1: float, whisper_segments: list | None
) -> str:
    """Concatenate Whisper chunk texts that overlap [t0, t1] (seconds)."""
    if not whisper_segments:
        return ""
    parts: list[str] = []
    for seg in whisper_segments:
        ws, we, txt = _seg_bounds(seg)
        if we <= t0 or ws >= t1:
            continue
        if txt:
            parts.append(txt)
    return " ".join(parts)


def normalize_whisper_segments(whisper_segments: list | None) -> list[dict]:
    """Store-friendly list for JSONField."""
    if not whisper_segments:
        return []
    out: list[dict] = []
    for seg in whisper_segments:
        ws, we, txt = _seg_bounds(seg)
        out.append({"start": ws, "end": we, "text": txt})
    return out

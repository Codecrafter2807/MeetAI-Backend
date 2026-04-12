"""Reduce Whisper hallucination loops (same line repeated across many segments)."""

from __future__ import annotations


def _norm_text(s: str) -> str:
    return " ".join((s or "").lower().split())


def collapse_consecutive_duplicate_segments(segments: list | None) -> list[dict]:
    """
    Merge adjacent Whisper segments that carry the same text into one interval.
    This fixes common council/meeting artifacts where the model prints the same
    phrase dozens of times while clocks advance.
    """
    if not segments:
        return []

    out: list[dict] = []
    i = 0
    n = len(segments)
    while i < n:
        seg = segments[i]
        if isinstance(seg, dict):
            ws = float(seg.get("start", 0))
            we = float(seg.get("end", 0))
            txt = (seg.get("text") or "").strip()
        else:
            ws = float(getattr(seg, "start", 0))
            we = float(getattr(seg, "end", 0))
            txt = (getattr(seg, "text", None) or "").strip()

        key = _norm_text(txt)
        j = i + 1
        while j < n:
            sg2 = segments[j]
            if isinstance(sg2, dict):
                t2 = (sg2.get("text") or "").strip()
                we2 = float(sg2.get("end", we))
            else:
                t2 = (getattr(sg2, "text", None) or "").strip()
                we2 = float(getattr(sg2, "end", we))
            if _norm_text(t2) != key:
                break
            we = we2
            j += 1

        out.append({"start": ws, "end": we, "text": txt})
        i = j

    return out


def segments_to_full_text(segments: list[dict]) -> str:
    parts = [(s.get("text") or "").strip() for s in segments if (s.get("text") or "").strip()]
    return " ".join(parts)

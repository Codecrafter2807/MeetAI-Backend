"""Assign a diarization speaker label to each Whisper segment (max time overlap)."""


def _w_bounds(w):
    if isinstance(w, dict):
        return float(w["start"]), float(w["end"]), (w.get("text") or "").strip()
    return float(getattr(w, "start", 0)), float(getattr(w, "end", 0)), (
        getattr(w, "text", None) or ""
    ).strip()


def _d_bounds(d):
    if isinstance(d, dict):
        return float(d["start"]), float(d["end"]), d["speaker"]
    return float(getattr(d, "start", 0)), float(getattr(d, "end", 0)), getattr(
        d, "speaker", "Unknown"
    )


def align_speakers(whisper_segments, diarization_segments):
    aligned = []
    diar = list(diarization_segments or [])
    
    # Map raw labels (SPEAKER_00) to human-friendly ones (Speaker 1)
    speaker_map = {}
    next_speaker_num = 1

    for w in whisper_segments or []:
        w_start, w_end, w_text = _w_bounds(w)

        best_raw_speaker = "Unknown"
        max_overlap = 0.0

        for d in diar:
            d_start, d_end, speaker = _d_bounds(d)

            overlap = min(w_end, d_end) - max(w_start, d_start)
            if overlap < 0:
                overlap = 0.0

            if overlap > max_overlap:
                max_overlap = overlap
                best_raw_speaker = speaker

        # Convert raw label to human-friendly label
        if best_raw_speaker != "Unknown":
            if best_raw_speaker not in speaker_map:
                speaker_map[best_raw_speaker] = f"Speaker {next_speaker_num}"
                next_speaker_num += 1
            final_speaker = speaker_map[best_raw_speaker]
        else:
            final_speaker = "Unknown"

        aligned.append(
            {
                "speaker": final_speaker,
                "start": w_start,
                "end": w_end,
                "text": w_text,
            }
        )

    return aligned

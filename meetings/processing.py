"""Whisper + diarization + alignment + NLP for one meeting (runs synchronously on upload)."""

import logging

from .alignment import normalize_whisper_segments
from .alignment_service import align_speakers
from .models import ActionItem, Meeting, SpeakerSegment, Summary, Transcript
from .whisper_service import transcribe_audio

logger = logging.getLogger(__name__)


def run_meeting_pipeline(meeting_id: int) -> None:
    """
    Load audio for meeting_id, run Whisper + diarization, write Transcript + SpeakerSegments.
    Clears existing speaker segments first (safe for Celery retries).
    """
    meeting = Meeting.objects.select_related("audio").get(pk=meeting_id)
    path = meeting.audio.file.path

    SpeakerSegment.objects.filter(meeting=meeting).delete()
    ActionItem.objects.filter(meeting=meeting).delete()
    Summary.objects.filter(meeting=meeting).delete()

    try:
        result = transcribe_audio(path)

        from .diarization_service import diarize_audio

        whisper_raw_segments = None
        if result:
            whisper_raw_segments = result.get("segments") or []
            Transcript.objects.update_or_create(
                meeting=meeting,
                defaults={
                    "full_text": result["text"],
                    "whisper_segments": normalize_whisper_segments(whisper_raw_segments)
                    or None,
                },
            )
            meeting.status = "completed"
        else:
            meeting.status = "failed"

        diarization_result = diarize_audio(path) or []
        whisper_result = whisper_raw_segments or []

        if whisper_result:
            aligned_segments = align_speakers(whisper_result, diarization_result)
            for seg in aligned_segments:
                SpeakerSegment.objects.create(
                    meeting=meeting,
                    speaker=seg["speaker"],
                    start_time=seg["start"],
                    end_time=seg["end"],
                    text=seg["text"],
                )
        elif diarization_result:
            for seg in diarization_result:
                SpeakerSegment.objects.create(
                    meeting=meeting,
                    speaker=seg["speaker"],
                    start_time=seg["start"],
                    end_time=seg["end"],
                    text="",
                )

        try:
            from .nlp_service import run_meeting_nlp

            run_meeting_nlp(meeting)
        except Exception:
            logger.exception("NLP (Grok) step failed for meeting %s", meeting_id)

        meeting.save()
    except Exception:
        logger.exception("Pipeline failed for meeting %s", meeting_id)
        Meeting.objects.filter(pk=meeting_id).update(status="failed")
        raise

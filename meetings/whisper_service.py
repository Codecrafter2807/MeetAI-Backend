import whisper
import logging
from django.conf import settings

from .transcript_cleanup import collapse_consecutive_duplicate_segments, segments_to_full_text

logger = logging.getLogger(__name__)

def transcribe_audio(file_path: str):
    try:
        model_name = getattr(settings, "WHISPER_MODEL", "small") or "small"
        logger.info("Loading Whisper model %r", model_name)
        model = whisper.load_model(model_name)          

        result = model.transcribe(
            file_path,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.4,
            logprob_threshold=-1.0,
        )

        raw_segments = result.get("segments") or []
        segments = collapse_consecutive_duplicate_segments(raw_segments)
        
        if len(segments) < len(raw_segments):
            logger.info(
                "Collapsed %s Whisper segments -> %s (removed consecutive duplicates)",
                len(raw_segments),
                len(segments),
            )

        full_text = segments_to_full_text(segments).strip()

        return {
            "text": full_text,
            "segments": segments,
        }

    except Exception as e:
        logger.exception("Whisper error: %s", e)
        return None

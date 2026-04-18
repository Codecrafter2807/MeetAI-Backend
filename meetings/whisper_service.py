import logging
import os
from django.conf import settings
from deepgram import DeepgramClient, PrerecordedOptions, FileSource

from .transcript_cleanup import collapse_consecutive_duplicate_segments, segments_to_full_text

logger = logging.getLogger(__name__)

def transcribe_audio(file_path: str):
    """
    Transcribe audio using Deepgram API (High speed, High accuracy, Multi-language).
    """
    try:
        api_key = os.environ.get("DEEPGRAM_API_KEY")
        if not api_key:
            logger.error("DEEPGRAM_API_KEY not found in environment.")
            return None

        deepgram = DeepgramClient(api_key)

        with open(file_path, "rb") as file:
            buffer_data = file.read()

        payload: FileSource = {
            "buffer": buffer_data,
        }

        options = PrerecordedOptions(
            model="nova-2",
            smart_format=True,
            utterances=True,
            diarize=True,  # We get speaker info for free here
            language="hi", # Default to Hindi/English mixed (Nova-2 is great at this)
            punctuate=True,
        )

        logger.info(f"Sending audio to Deepgram for transcription: {file_path}")
        response = deepgram.listen.prerecorded.v("1").transcribe_file(payload, options)

        # Parse Deepgram response to match the existing Whisper format
        # So that the rest of your code doesn't break!
        
        full_text = response.results.channels[0].alternatives[0].transcript
        
        # Convert Deepgram utterances to whisper-style segments
        utterances = response.results.utterances or []
        segments = []
        for utt in utterances:
            segments.append({
                "start": utt.start,
                "end": utt.end,
                "text": utt.transcript,
                "speaker": f"Speaker {utt.speaker + 1}" # Deepgram uses 0,1,2...
            })

        logger.info(f"Deepgram transcription complete. Length: {len(full_text)} chars")

        return {
            "text": full_text,
            "segments": segments,
            "diarization": segments, # We provide this for diarization_service to use
        }

    except Exception as e:
        logger.exception("Deepgram transcription error: %s", e)
        return None

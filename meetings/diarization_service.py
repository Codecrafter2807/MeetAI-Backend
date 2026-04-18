import logging
from django.core.cache import cache

logger = logging.getLogger(__name__)

def diarize_audio(file_path: str):
    """
    In the new Cloud version, Diarization is handled by Deepgram alongside transcription.
    We retrieve the results either from the cache or return an empty list if not ready.
    """
    try:
        # Check if we have cached diarization results from the transcription step
        # The key is based on the file path to ensure we get the right one
        cache_key = f"diarization_{file_path.replace(' ', '_')}"
        cached_result = cache.get(cache_key)
        
        if cached_result:
            logger.info(f"Using cached diarization result for {file_path}")
            return cached_result

        # If not in cache, we return an empty list. 
        # In a real flow, transcribe_audio will populate this cache.
        logger.warning(f"No cached diarization result found for {file_path}. Task flow may need sync.")
        return []

    except Exception:
        logger.exception("Diarization lookup failed for %s", file_path)
        return []

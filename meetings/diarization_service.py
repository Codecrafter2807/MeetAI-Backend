"""
Speaker diarization via pyannote.audio.

Setup (mandatory):
  1. Hugging Face account + token: https://huggingface.co/settings/tokens
  2. Accept model terms for the pipeline you use, e.g.
     https://huggingface.co/pyannote/speaker-diarization-community-1
     or https://huggingface.co/pyannote/speaker-diarization-3.1
  3. Provide your Hugging Face token (pick one):
       • Create a file at the project root (next to manage.py):  .env
         HF_TOKEN=hf_your_token_here
       • Or set env vars in the shell / OS: HF_TOKEN or HUGGINGFACE_TOKEN
  4. ffmpeg must be on PATH (used to decode MP3/WAV for diarization; TorchCodec is not used).

Optional:
  PYANNOTE_PIPELINE   Hub id for Pipeline.from_pretrained.
                       Default: pyannote/speaker-diarization-community-1 (matches pyannote.audio 4.x).
                       Alternatives: pyannote/speaker-diarization-3.1 (accept terms on HF).
                       Revision: use PYANNOTE_REVISION or embed as model_id@revision in
                       PYANNOTE_PIPELINE (e.g. pyannote/foo@main).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import torch

logger = logging.getLogger(__name__)

# Token comes from the environment (see .env loaded in config/settings.py before imports).
_DEFAULT_PIPELINE = "pyannote/speaker-diarization-community-1"

# Pyannote is imported inside _get_pipeline() only so Django CLI (migrate, etc.) stays quiet:
# importing pyannote.audio triggers a long torchcodec warning on Windows when libtorchcodec is missing.
_pipeline: Any = None
_pipeline_key: tuple[str, str | None, str] | None = None


def _pipeline_spec() -> tuple[str, str | None]:
    """Return (hub_model_id, revision_or_none).

    pyannote/speaker-diarization (legacy card) pulls configs that use checkpoint@rev strings
    inside YAML; pyannote.audio 4 rejects those unless revision is passed separately — use
    community-1 or speaker-diarization-3.1 instead.
    """
    raw = (
        os.environ.get("PYANNOTE_PIPELINE", _DEFAULT_PIPELINE).strip()
    )
    revision = os.environ.get("PYANNOTE_REVISION", "").strip() or None
    if "@" in raw:
        model_id, _, rev = raw.partition("@")
        model_id = model_id.strip()
        rev = rev.strip()
        if rev:
            revision = rev
        return model_id, revision
    return raw, revision


def _hf_token() -> str:
    return (
        os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or ""
    ).strip()


def _get_pipeline() -> Any:
    global _pipeline, _pipeline_key
    from pyannote.audio import Pipeline

    token = _hf_token()
    if not token or token == "your_huggingface_token":
        logger.warning(
            "Diarization skipped: set HF_TOKEN or HUGGINGFACE_TOKEN in .env "
            "(Hugging Face token with access to the chosen pipeline)."
        )
        return None

    model_id, revision = _pipeline_spec()
    cache_key = (model_id, revision, token)

    if _pipeline is not None and _pipeline_key != cache_key:
        _pipeline = None

    if _pipeline is None:
        try:
            loaded = Pipeline.from_pretrained(
                model_id,
                revision=revision,
                token=token,
            )
        except Exception:
            logger.exception(
                "Diarization: Pipeline.from_pretrained failed for %r revision=%r",
                model_id,
                revision,
            )
            return None
        if loaded is None:
            logger.error(
                "Diarization: Pipeline.from_pretrained returned None for %r",
                model_id,
            )
            return None
        _pipeline = loaded
        _pipeline_key = cache_key
    return _pipeline


def _load_waveform_ffmpeg(file_path: str, sample_rate: int = 16000) -> tuple[torch.Tensor, int]:
    """Decode audio with ffmpeg CLI (same idea as Whisper). TorchAudio 2.9+ uses TorchCodec only, which
    often fails on Windows when libtorchcodec DLLs do not load."""
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-threads",
        "0",
        "-i",
        file_path,
        "-f",
        "f32le",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError as e:
        raise RuntimeError(
            "ffmpeg not found in PATH; install FFmpeg and add it to PATH (required for diarization)."
        ) from e
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode(errors="replace")[:800]
        raise RuntimeError(f"ffmpeg failed to decode audio: {err}") from e

    audio = np.frombuffer(proc.stdout, dtype=np.float32)
    waveform = torch.from_numpy(audio.copy()).unsqueeze(0)
    return waveform, sample_rate


def _audio_dict(file_path: str) -> dict:
    waveform, sample_rate = _load_waveform_ffmpeg(file_path)
    return {
        "waveform": waveform,
        "sample_rate": int(sample_rate),
        "uri": Path(file_path).stem,
    }


def diarize_audio(file_path: str):
    try:
        pipeline = _get_pipeline()
        if pipeline is None:
            return None

        audio_input = _audio_dict(file_path)
        output = pipeline(audio_input)

        if hasattr(output, "speaker_diarization"):
            diarization = output.speaker_diarization
        else:
            diarization = output

        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append(
                {
                    "speaker": speaker,
                    "start": float(turn.start),
                    "end": float(turn.end),
                }
            )

        return segments

    except Exception:
        logger.exception("Diarization failed for %s", file_path)
        return None

"""
Speech-to-text for video/audio when no sidecar transcript exists.
Uses faster-whisper; the RAG backend still only receives text (chunk → embed), same as other files.
Requires ``ffmpeg`` on PATH for most container formats.
"""
from __future__ import annotations

import threading
from pathlib import Path

from app import config

_lock = threading.Lock()
_model = None
_model_key: str | None = None

_INSTALL_MSG = (
    "Automatic captions are not available on this server. "
    "Add a text file with the same name as your video plus “.txt”, or ask the admin to install speech-to-text (faster-whisper and ffmpeg)."
)


def _get_model():
    global _model, _model_key
    key = f"{config.WHISPER_MODEL}|{config.WHISPER_DEVICE}|{config.WHISPER_COMPUTE_TYPE}"
    with _lock:
        if _model is not None and _model_key == key:
            return _model
        from faster_whisper import WhisperModel

        _model = WhisperModel(
            config.WHISPER_MODEL,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE_TYPE,
        )
        _model_key = key
        return _model


def transcribe_file(path: Path) -> tuple[str, str | None]:
    """
    Returns (text, error). Text is joined segment transcripts suitable for ingest.
    """
    if not path.is_file():
        return "", "File missing for transcription."

    try:
        model = _get_model()
        segments, _info = model.transcribe(
            str(path),
            beam_size=5,
            vad_filter=True,
        )
    except ImportError:
        return "", _INSTALL_MSG
    except OSError as exc:
        return "", f"Could not read or decode this media file: {exc}"
    except Exception as exc:  # noqa: BLE001
        return "", f"Transcription failed: {exc}"

    parts: list[str] = []
    for seg in segments:
        t = seg.text.strip()
        if t:
            parts.append(t)
    text = "\n\n".join(parts)
    if not text.strip():
        return "", "No speech was detected in this file."
    return text, None

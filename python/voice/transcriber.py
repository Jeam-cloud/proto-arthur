"""Local speech-to-text with faster-whisper.

Pipeline: the renderer records webm/opus with MediaRecorder (the format
browsers produce natively) -> POST bytes here -> faster-whisper decodes via
PyAV (handles webm directly, no ffmpeg subprocess) -> text back to the
composer. Audio never leaves the machine.

WHY lazy + threaded: the model is ~500MB of RAM once loaded, and plenty of
users never touch voice — loading at boot would tax everyone for a feature
some use. First call pays the load (UI shows "preparing voice…"); transcribe
runs in a worker thread because it's CPU-bound sync code on an async server.

WHY "small" + int8: the sweet spot for consumer CPUs — near-realtime on four
cores, fine accuracy for dictation. A Settings dropdown can offer "base" for
weak machines and "medium" for strong ones later; the constructor already
takes the name.
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading

from core.errors import VoiceError

log = logging.getLogger(__name__)


class Transcriber:
    def __init__(self, model_name: str = "small"):
        self._model_name = model_name
        self._model = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _ensure_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    try:
                        from faster_whisper import WhisperModel

                        log.info("loading faster-whisper '%s' (first use)…", self._model_name)
                        self._model = WhisperModel(self._model_name, device="auto", compute_type="int8")
                    except Exception as e:
                        raise VoiceError(
                            "Voice model could not be loaded. If this is the first use, "
                            "an internet connection is needed once to download it."
                        ) from e
        return self._model

    async def transcribe(self, audio_bytes: bytes) -> str:
        if len(audio_bytes) < 200:
            raise VoiceError("Recording was empty — check that the microphone picked anything up.")

        def _run() -> str:
            model = self._ensure_model()
            try:
                segments, _info = model.transcribe(
                    io.BytesIO(audio_bytes),
                    vad_filter=True,  # trims silence -> faster + fewer hallucinated words
                    beam_size=5,
                )
                return " ".join(s.text.strip() for s in segments).strip()
            except VoiceError:
                raise
            except Exception as e:
                raise VoiceError(f"Could not decode the recording: {e}") from e

        return await asyncio.to_thread(_run)

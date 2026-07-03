"""Local speech-to-text for Discord voice messages via faster-whisper.

faster-whisper is an optional dependency (requirements-voice.txt), imported
lazily. Model work is CPU-bound and runs in a worker thread behind a semaphore.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Wait after a transient model-load failure (e.g. a download blip) instead of
# disabling the feature until restart.
_LOAD_RETRY_BACKOFF_SECONDS = 300


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    language: str | None = None
    duration_seconds: float | None = None


class TranscriptionService:
    def __init__(
        self,
        *,
        enabled: bool = False,
        model: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = None,
        download_root: str | None = None,
        beam_size: int = 1,
        max_concurrency: int = 1,
    ) -> None:
        self.enabled = enabled
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.download_root = download_root
        self.beam_size = max(1, beam_size)
        self._model = None
        self._permanent_fail = False  # missing dependency — never retry
        self._retry_after = 0.0  # transient failure — retry after this monotonic time
        self._load_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))

    @property
    def available(self) -> bool:
        """Enabled, dependency present, and not in retry back-off."""
        if not self.enabled or self._permanent_fail:
            return False
        if self._model is not None:
            return True
        return time.monotonic() >= self._retry_after

    async def transcribe(
        self, audio_bytes: bytes, *, filename: str = "voice-message.ogg"
    ) -> TranscriptResult | None:
        if not self.available:
            return None
        model = await self._ensure_model()
        if model is None:
            return None
        async with self._semaphore:
            return await asyncio.to_thread(
                self._transcribe_blocking, model, audio_bytes, filename
            )

    async def _ensure_model(self):
        if self._model is not None:
            return self._model
        if self._permanent_fail or time.monotonic() < self._retry_after:
            return None
        async with self._load_lock:
            if self._model is not None:
                return self._model
            if self._permanent_fail or time.monotonic() < self._retry_after:
                return None
            try:
                model = await asyncio.to_thread(self._load_model_blocking)
            except Exception as exc:
                # Transient failure (download/IO): back off, retry on a later message.
                hint = ""
                if isinstance(exc, PermissionError):
                    hint = (
                        " The model cache directory is not writable by the bot "
                        "user — set VOICE_TRANSCRIBE_DOWNLOAD_ROOT to a directory "
                        "the bot owns (see docs/tldr-and-voice-transcription.md)."
                    )
                log.exception(
                    "Failed to load Whisper model %s; retrying in %ss.%s",
                    self.model_name,
                    _LOAD_RETRY_BACKOFF_SECONDS,
                    hint,
                )
                self._retry_after = time.monotonic() + _LOAD_RETRY_BACKOFF_SECONDS
                return None
            if model is None:
                # Missing dependency: never retry.
                self._permanent_fail = True
            self._model = model
            return model

    def _load_model_blocking(self):
        # huggingface's xet backend caches under HF_HOME (default ~/.cache, often
        # unwritable for the bot user), so point it inside download_root before
        # huggingface_hub is first imported, unless the operator already set it.
        if self.download_root:
            os.environ.setdefault(
                "HF_HOME", os.path.join(self.download_root, "huggingface")
            )
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            log.error(
                "Voice transcription is enabled but faster-whisper is not installed. "
                "Install it with `pip install -r requirements-voice.txt` (or "
                "`pip install faster-whisper`) and restart the bot."
            )
            return None
        # Other load errors propagate to _ensure_model and are treated as transient.
        log.info(
            "Loading Whisper model %s (device=%s, compute_type=%s) for voice transcription.",
            self.model_name,
            self.device,
            self.compute_type,
        )
        return WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            download_root=self.download_root,
        )

    def _transcribe_blocking(self, model, audio_bytes: bytes, filename: str):
        suffix = os.path.splitext(filename)[1] or ".ogg"
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
                handle.write(audio_bytes)
                tmp_path = handle.name
            segments, info = model.transcribe(
                tmp_path,
                beam_size=self.beam_size,
                language=self.language,
                vad_filter=True,
            )
            parts = [segment.text.strip() for segment in segments]
            text = " ".join(part for part in parts if part).strip()
            return TranscriptResult(
                text=text,
                language=getattr(info, "language", None),
                duration_seconds=getattr(info, "duration", None),
            )
        except Exception:
            log.exception("Voice transcription failed.")
            return None
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:  # pragma: no cover - best-effort cleanup
                    pass

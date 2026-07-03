from __future__ import annotations

import asyncio
from types import SimpleNamespace

from rob.services.transcription_service import TranscriptionService


class _FakeModel:
    def __init__(self, segments=None, info=None, error: Exception | None = None):
        self._segments = segments
        self._info = info
        self._error = error
        self.calls: list[str] = []

    def transcribe(self, path, beam_size=1, language=None, vad_filter=False):
        self.calls.append(path)
        if self._error is not None:
            raise self._error
        return self._segments, self._info


def test_disabled_service_is_unavailable_and_returns_none():
    svc = TranscriptionService(enabled=False)
    assert svc.available is False
    assert asyncio.run(svc.transcribe(b"data")) is None


def test_transcribe_joins_segment_text():
    svc = TranscriptionService(enabled=True)
    svc._model = _FakeModel(
        segments=[SimpleNamespace(text=" Hello there "), SimpleNamespace(text="world")],
        info=SimpleNamespace(language="en", duration=3.2),
    )
    result = asyncio.run(svc.transcribe(b"audio-bytes", filename="voice-message.ogg"))
    assert result is not None
    assert result.text == "Hello there world"
    assert result.language == "en"
    assert result.duration_seconds == 3.2


def test_transcribe_returns_none_on_model_error():
    svc = TranscriptionService(enabled=True)
    svc._model = _FakeModel(error=RuntimeError("decode failed"))
    result = asyncio.run(svc.transcribe(b"audio-bytes"))
    assert result is None


def test_empty_segments_yield_empty_text():
    svc = TranscriptionService(enabled=True)
    svc._model = _FakeModel(segments=[], info=SimpleNamespace(language="en", duration=0.0))
    result = asyncio.run(svc.transcribe(b"audio-bytes"))
    assert result is not None
    assert result.text == ""


def test_missing_faster_whisper_marks_unavailable():
    # faster-whisper isn't installed in the test env, so the model load fails
    # with ImportError and the service disables itself permanently.
    svc = TranscriptionService(enabled=True)
    assert svc.available is True  # before first use
    result = asyncio.run(svc.transcribe(b"audio-bytes"))
    assert result is None
    assert svc.available is False  # after a failed load it disables itself
    assert svc._permanent_fail is True


def test_download_root_redirects_hf_home(monkeypatch):
    # download_root must also cover huggingface's xet cache (HF_HOME), which
    # otherwise writes under the bot user's unwritable ~/.cache.
    monkeypatch.delenv("HF_HOME", raising=False)
    svc = TranscriptionService(enabled=True, download_root="/data/whisper")
    svc._load_model_blocking()  # returns None here (faster-whisper not installed)
    import os

    assert os.environ["HF_HOME"] == os.path.join("/data/whisper", "huggingface")


def test_download_root_respects_existing_hf_home(monkeypatch):
    monkeypatch.setenv("HF_HOME", "/var/cache/rob-bot/huggingface")
    svc = TranscriptionService(enabled=True, download_root="/data/whisper")
    svc._load_model_blocking()
    import os

    assert os.environ["HF_HOME"] == "/var/cache/rob-bot/huggingface"


def test_permission_error_load_failure_logs_actionable_hint(caplog):
    import logging

    svc = TranscriptionService(enabled=True)

    def _denied():
        raise PermissionError(13, "Permission denied", "/opt/rob-bot/.cache")

    svc._load_model_blocking = _denied  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR, logger="rob.services.transcription_service"):
        assert asyncio.run(svc.transcribe(b"audio-bytes")) is None

    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "VOICE_TRANSCRIBE_DOWNLOAD_ROOT" in messages
    # Still treated as transient: fixable on the host without a bot restart.
    assert svc._permanent_fail is False


def test_transient_load_failure_backs_off_and_retries():
    # A non-ImportError load failure (e.g. a download blip) must back off and
    # retry, not permanently disable the feature.
    svc = TranscriptionService(enabled=True)

    def _boom():
        raise RuntimeError("network blip during model download")

    svc._load_model_blocking = _boom  # type: ignore[method-assign]

    assert asyncio.run(svc.transcribe(b"audio-bytes")) is None
    assert svc._permanent_fail is False
    assert svc.available is False  # inside the back-off window

    svc._retry_after = 0.0  # simulate the back-off elapsing
    assert svc.available is True  # retryable again

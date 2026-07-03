from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from rob.services.tldr_service import (
    ChatMessage,
    TldrService,
    extract_urls,
    filter_by_topic,
)


def _msg(author: str, content: str) -> ChatMessage:
    return ChatMessage(author=author, content=content, created_at=datetime.now(timezone.utc))


SAMPLE = [
    _msg("Alice", "Did anyone see the new leaderboard update?"),
    _msg("Bob", "Yeah the leaderboard looks great, https://robthebot.com is live"),
    _msg("Cara", "lol nice"),
    _msg("Bob", "We should schedule the event for Friday evening I think."),
]


def _run(coro):
    return asyncio.run(coro)


def test_extract_urls():
    assert extract_urls("see https://a.test/x and http://b.test") == [
        "https://a.test/x",
        "http://b.test",
    ]
    assert extract_urls("no links here") == []


def test_filter_by_topic_matches_tokens():
    matched = filter_by_topic(SAMPLE, "leaderboard")
    assert len(matched) == 2
    assert all("leaderboard" in m.content.lower() for m in matched)


def test_digest_summary_without_ollama():
    svc = TldrService(enabled=True, ollama_url=None)
    result = _run(
        svc.summarize(SAMPLE, topic=None, timeframe_label="the last 24 hours", channel_name="general")
    )
    assert result.method == "digest"
    assert result.message_count == 4
    assert result.participant_count == 3
    assert "https://robthebot.com" in result.links
    assert "Most active" in result.summary
    assert "Bob (2)" in result.summary


def test_digest_topic_summary_reports_matches():
    svc = TldrService(enabled=True, ollama_url=None)
    result = _run(
        svc.summarize(
            SAMPLE, topic="leaderboard", timeframe_label="the last 24 hours", channel_name="general"
        )
    )
    assert result.topic == "leaderboard"
    assert result.matched_count == 2
    assert result.message_count == 2
    assert "leaderboard" in result.summary.lower()


def test_empty_scope_returns_no_messages_note():
    svc = TldrService(enabled=True, ollama_url=None)
    result = _run(
        svc.summarize([], topic=None, timeframe_label="the last hour", channel_name="general")
    )
    assert result.method == "digest"
    assert "No messages" in result.summary


def test_empty_topic_scope_mentions_topic():
    svc = TldrService(enabled=True, ollama_url=None)
    result = _run(
        svc.summarize(
            SAMPLE, topic="zzzznotfound", timeframe_label="the last hour", channel_name="general"
        )
    )
    assert result.matched_count == 0
    assert "zzzznotfound" in result.summary


class _FakeOllamaResponse:
    def __init__(self, status: int, payload, text: str = ""):
        self.status = status
        self._payload = payload
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return self._text or str(self._payload)


class _FakeSession:
    def __init__(self, response=None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, json=None):
        self.calls.append((url, json))
        if self._error is not None:
            raise self._error
        return self._response


def test_ollama_success_returns_ai_summary():
    session = _FakeSession(
        response=_FakeOllamaResponse(200, {"response": "- Talked about the leaderboard\n- Planned Friday event"})
    )
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        model="llama3.2:1b",
        session_factory=lambda: session,
    )
    result = _run(
        svc.summarize(SAMPLE, topic=None, timeframe_label="the last 24 hours", channel_name="general")
    )
    assert result.method == "ai"
    assert result.model == "llama3.2:1b"
    assert "Friday event" in result.summary
    # message/participant counts still populated from the real messages
    assert result.message_count == 4
    # All 4 sample messages fit the default budget, so the AI saw them all.
    assert result.ai_message_count == 4
    assert session.calls and session.calls[0][0].endswith("/api/generate")


def test_tldr_message_footer_is_honest_about_truncated_transcript():
    from rob.ui.cards.tldr import tldr_message

    content = tldr_message(
        channel_name="main-chat",
        timeframe_label="the last 7 days",
        summary="a recap",
        method="ai",
        message_count=390,
        participant_count=33,
        model="qwen2.5:0.5b",
        ai_message_count=72,
    )
    assert "latest 72 of 390 messages" in content
    assert "#main-chat" in content
    assert "a recap" in content


def test_tldr_message_fits_discord_content_limit():
    from rob.ui.cards.tldr import tldr_message

    content = tldr_message(
        channel_name="general",
        timeframe_label="the last 24 hours",
        summary="long text " * 500,
        method="ai",
        message_count=50,
        participant_count=5,
        model="qwen2.5:0.5b",
        ai_message_count=50,
    )
    assert len(content) <= 2000
    assert "trimmed" in content


def test_chunk_transcripts_splits_chronologically():
    from datetime import datetime, timezone
    from rob.services.tldr_service import ChatMessage

    msgs = [
        ChatMessage(f"User{i}", f"message number {i} " + "x" * 60, datetime.now(timezone.utc))
        for i in range(30)
    ]
    svc = TldrService(enabled=True, ollama_url="http://127.0.0.1:11434", transcript_char_budget=400)
    chunks = svc._chunk_transcripts(msgs)
    assert len(chunks) > 1
    assert sum(count for _, count in chunks) == 30
    # Chronological: the first chunk holds the oldest messages.
    assert "message number 0" in chunks[0][0]
    assert "message number 29" in chunks[-1][0]


def test_multi_chunk_window_is_map_reduced_and_covers_all_messages():
    from datetime import datetime, timezone
    from rob.services.tldr_service import ChatMessage

    msgs = [
        ChatMessage(f"User{i}", f"message number {i} " + "x" * 60, datetime.now(timezone.utc))
        for i in range(30)
    ]
    session = _FakeSession(response=_FakeOllamaResponse(200, {"response": "a partial or final summary"}))
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        transcript_char_budget=400,
        max_chunks=20,  # high enough that no chunk is dropped in this test
        session_factory=lambda: session,
    )
    result = _run(
        svc.summarize(msgs, topic=None, timeframe_label="the last 7 days", channel_name="general")
    )
    assert result.method == "ai"
    # Every message was covered, not just the newest budget's worth.
    assert result.ai_message_count == 30
    chunk_count = len(svc._chunk_transcripts(msgs))
    # One call per chunk plus the final combine call.
    assert len(session.calls) == chunk_count + 1
    assert "Partial summaries:" in session.calls[-1][1]["prompt"]


def test_max_chunks_caps_latency_and_keeps_most_recent():
    from datetime import datetime, timezone
    from rob.services.tldr_service import ChatMessage

    msgs = [
        ChatMessage(f"User{i}", f"message number {i} " + "x" * 60, datetime.now(timezone.utc))
        for i in range(30)
    ]
    session = _FakeSession(response=_FakeOllamaResponse(200, {"response": "summary"}))
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        transcript_char_budget=400,
        max_chunks=2,
        session_factory=lambda: session,
    )
    result = _run(
        svc.summarize(msgs, topic=None, timeframe_label="the last 7 days", channel_name="general")
    )
    assert len(session.calls) == 3  # 2 chunks + combine
    assert result.ai_message_count < 30  # honesty: only the most recent chunks
    assert "message number 29" in session.calls[1][1]["prompt"]


def test_ollama_connection_error_falls_back_to_digest_and_trips_breaker():
    import aiohttp

    session = _FakeSession(error=aiohttp.ClientError("boom"))
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        session_factory=lambda: session,
    )
    result = _run(
        svc.summarize(SAMPLE, topic=None, timeframe_label="the last 24 hours", channel_name="general")
    )
    assert result.method == "digest"
    # Breaker tripped: the next call should skip Ollama entirely.
    assert not svc._ollama_ready()


def test_ollama_payload_includes_keep_alive_and_num_predict():
    session = _FakeSession(response=_FakeOllamaResponse(200, {"response": "- ok"}))
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        keep_alive="15m",
        num_predict=250,
        session_factory=lambda: session,
    )
    _run(svc.summarize(SAMPLE, topic=None, timeframe_label="today", channel_name="general"))
    assert session.calls
    _, payload = session.calls[0]
    assert payload["keep_alive"] == "15m"
    assert payload["options"]["num_predict"] == 250


def test_transcript_char_budget_limits_prompt_and_keeps_recent_messages():
    from datetime import datetime, timezone
    from rob.services.tldr_service import ChatMessage

    msgs = [
        ChatMessage(f"User{i}", f"message number {i} " + "x" * 80, datetime.now(timezone.utc))
        for i in range(50)
    ]
    svc = TldrService(enabled=True, ollama_url="http://127.0.0.1:11434", transcript_char_budget=500)
    prompt, included = svc._build_prompt(
        msgs, topic=None, timeframe_label="today", channel_name="general"
    )
    # The budget keeps the most recent messages; included reports how many fit.
    assert "message number 49" in prompt
    assert "message number 0" not in prompt
    assert 0 < included < 50


def test_ollama_timeout_falls_back_and_trips_breaker():
    # A bare TimeoutError (aiohttp total-timeout) must fall back to the digest
    # and trip the breaker like any other connection failure.
    session = _FakeSession(error=TimeoutError())
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        session_factory=lambda: session,
    )
    result = _run(
        svc.summarize(SAMPLE, topic=None, timeframe_label="today", channel_name="general")
    )
    assert result.method == "digest"
    assert not svc._ollama_ready()


def test_ollama_timeout_logs_exception_type(caplog):
    import logging

    session = _FakeSession(error=TimeoutError())
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        session_factory=lambda: session,
    )
    with caplog.at_level(logging.INFO, logger="rob.services.tldr_service"):
        _run(svc.summarize(SAMPLE, topic=None, timeframe_label="today", channel_name="general"))
    # The log must name the exception type (never an empty "()") and show the
    # configured timeout.
    messages = [record.getMessage() for record in caplog.records]
    assert any("TimeoutError" in message for message in messages)
    assert any("timeout=" in message for message in messages)


def test_warm_up_sends_load_only_request():
    session = _FakeSession(response=_FakeOllamaResponse(200, {"done": True}))
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        keep_alive="10m",
        session_factory=lambda: session,
    )
    _run(svc.warm_up())
    assert session.calls
    url, payload = session.calls[0]
    assert url.endswith("/api/generate")
    assert payload["model"] == svc.model
    assert payload["keep_alive"] == "10m"
    # No prompt: Ollama treats a promptless generate as "load the model only".
    assert "prompt" not in payload
    assert svc._ollama_ready()


def test_warm_up_failure_never_trips_breaker():
    # Warm-up is advisory: a timeout there must not block the next real /tldr.
    session = _FakeSession(error=TimeoutError())
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        session_factory=lambda: session,
    )
    _run(svc.warm_up())
    assert svc._ollama_ready()


def test_summarize_serves_digest_while_warm_up_in_flight():
    from types import SimpleNamespace

    session = _FakeSession(response=_FakeOllamaResponse(200, {"response": "- ai"}))
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        session_factory=lambda: session,
    )
    # /tldr must not queue behind an in-flight model load (that would time out
    # and trip the breaker); it should serve the digest.
    svc._warmup_task = SimpleNamespace(done=lambda: False)
    result = _run(
        svc.summarize(SAMPLE, topic=None, timeframe_label="today", channel_name="general")
    )
    assert result.method == "digest"
    assert not session.calls
    assert svc._ollama_ready()  # breaker untouched


def test_warm_up_success_clears_tripped_breaker():
    # A user call racing the cold load may have tripped the breaker; a warm-up
    # success must clear it.
    session = _FakeSession(response=_FakeOllamaResponse(200, {"done": True}))
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        session_factory=lambda: session,
    )
    svc._trip_ollama_breaker()
    assert not svc._ollama_ready()
    assert _run(svc.warm_up()) is True
    assert svc._ollama_ready()


def test_warm_up_loop_retries_until_success():
    # Ollama may start after the bot on a reboot: retry with backoff until the
    # model loads.
    attempts = []

    class _FlakySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, json=None):
            attempts.append(url)
            if len(attempts) < 3:
                raise TimeoutError()
            return _FakeOllamaResponse(200, {"done": True})

    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        session_factory=_FlakySession,
    )
    svc._warmup_retry_seconds = 0  # no real waiting in tests
    _run(svc._warm_up_loop())
    assert len(attempts) == 3


def test_prompt_style_defaults_to_paragraphs_and_supports_bullets():
    svc = TldrService(enabled=True, ollama_url="http://127.0.0.1:11434")
    prompt, _ = svc._build_prompt(
        SAMPLE, topic=None, timeframe_label="today", channel_name="general"
    )
    assert "short paragraphs" in prompt
    assert "Do not use bullet points" in prompt
    # Anti-hallucination guard for small models.
    assert "clearly stated in the transcript" in prompt

    bullet_svc = TldrService(enabled=True, ollama_url="http://127.0.0.1:11434", style="bullets")
    bullet_prompt, _ = bullet_svc._build_prompt(
        SAMPLE, topic=None, timeframe_label="today", channel_name="general"
    )
    assert "bullet points, each starting with" in bullet_prompt

    # Unknown styles fall back to paragraphs rather than breaking the prompt.
    weird = TldrService(enabled=True, ollama_url="http://127.0.0.1:11434", style="haiku")
    assert weird.style == "paragraphs"


def test_begin_warm_up_noop_without_ollama_url():
    svc = TldrService(enabled=True, ollama_url=None)

    async def main():
        svc.begin_warm_up()
        assert svc._warmup_task is None

    asyncio.run(main())


def test_begin_warm_up_schedules_task_and_stop_awaits_it():
    session = _FakeSession(response=_FakeOllamaResponse(200, {"done": True}))
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        session_factory=lambda: session,
    )

    async def main():
        svc.begin_warm_up()
        assert svc._warmup_task is not None
        await svc.stop()
        assert svc._warmup_task.done()

    asyncio.run(main())


def test_ollama_non_200_falls_back():
    session = _FakeSession(response=_FakeOllamaResponse(500, {}))
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        session_factory=lambda: session,
    )
    result = _run(
        svc.summarize(SAMPLE, topic=None, timeframe_label="the last 24 hours", channel_name="general")
    )
    assert result.method == "digest"


def test_ollama_null_response_falls_back_to_digest():
    # A 200 with {"response": null} must not surface a literal "None"; it
    # should fall back to the digest.
    session = _FakeSession(response=_FakeOllamaResponse(200, {"response": None}))
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        session_factory=lambda: session,
    )
    result = _run(
        svc.summarize(SAMPLE, topic=None, timeframe_label="today", channel_name="general")
    )
    assert result.method == "digest"
    assert "None" not in result.summary.splitlines()[0]


def test_ollama_non_dict_body_falls_back_to_digest():
    # A 200 whose JSON body is a list/string must not raise out of summarize().
    session = _FakeSession(response=_FakeOllamaResponse(200, ["unexpected"]))
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        session_factory=lambda: session,
    )
    result = _run(
        svc.summarize(SAMPLE, topic=None, timeframe_label="today", channel_name="general")
    )
    assert result.method == "digest"


def test_ai_summary_neutralises_mentions():
    session = _FakeSession(
        response=_FakeOllamaResponse(200, {"response": "- <@123> pinged @everyone about the event"})
    )
    svc = TldrService(
        enabled=True,
        ollama_url="http://127.0.0.1:11434",
        session_factory=lambda: session,
    )
    result = _run(
        svc.summarize(SAMPLE, topic=None, timeframe_label="today", channel_name="general")
    )
    assert "<@123>" not in result.summary
    assert "@everyone" not in result.summary

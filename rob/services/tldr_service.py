"""Chat TL;DR summariser: an extractive digest that always works, plus an
optional summary from a local model served by Ollama. No chat data leaves the
host; when Ollama is unreachable we fall back to the digest."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>|]+", re.IGNORECASE)
# Skip Ollama for this long after a connection failure, so a down server
# doesn't add its connect timeout to every /tldr call.
_OLLAMA_BACKOFF_SECONDS = 120
# The startup warm-up may sit through a slow cold model load, so it gets a
# much larger window than user calls.
_WARMUP_TIMEOUT_SECONDS = 600
# Transcript budget for the local model's small context window.
_AI_TRANSCRIPT_CHAR_BUDGET = 8000


@dataclass(frozen=True)
class ChatMessage:
    """A single human chat message, decoupled from discord.py for testability."""

    author: str
    content: str
    created_at: datetime


@dataclass(frozen=True)
class TldrResult:
    summary: str
    method: str  # "ai" | "digest"
    message_count: int
    participant_count: int
    topic: str | None = None
    model: str | None = None
    matched_count: int | None = None
    # How many messages actually fit the model's transcript budget (AI path
    # only); < message_count means the summary covers the most recent slice.
    ai_message_count: int | None = None
    links: list[str] = field(default_factory=list)


async def _read_body(response, limit: int = 400) -> str:
    """Best-effort read of a small slice of a response body for diagnostics."""
    try:
        text = await response.text()
    except Exception:  # pragma: no cover - defensive; logging must never raise
        return "<unreadable body>"
    text = _clean(text)
    return text[:limit] + ("…" if len(text) > limit else "")


def _clean(text: str) -> str:
    return " ".join(text.split()).strip()


def _truncate(text: str, limit: int) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def extract_urls(text: str) -> list[str]:
    return _URL_RE.findall(text or "")


def filter_by_topic(messages: list[ChatMessage], topic: str) -> list[ChatMessage]:
    """Messages whose text contains any whitespace-separated token of ``topic``
    (case-insensitive). A loose match on purpose — the AI path narrows further."""

    stripped = topic.strip()
    if not stripped:
        return list(messages)
    tokens = [t.lower() for t in re.split(r"\s+", stripped) if len(t) >= 2]
    if not tokens:
        # Very short topic (e.g. one word under 2 chars): match it literally
        # rather than falling back to "everything".
        tokens = [stripped.lower()]
    matched: list[ChatMessage] = []
    for message in messages:
        lowered = message.content.lower()
        if any(token in lowered for token in tokens):
            matched.append(message)
    return matched


class TldrService:
    def __init__(
        self,
        *,
        enabled: bool = True,
        ollama_url: str | None = None,
        model: str = "llama3.2:1b",
        request_timeout_seconds: int = 120,
        keep_alive: str = "-1m",
        max_messages: int = 400,
        num_predict: int = 300,
        transcript_char_budget: int = _AI_TRANSCRIPT_CHAR_BUDGET,
        style: str = "paragraphs",
        max_chunks: int = 6,
        session_factory=None,
    ) -> None:
        self.enabled = enabled
        self.ollama_url = ollama_url.rstrip("/") if ollama_url else None
        self.model = model
        self.request_timeout_seconds = max(1, request_timeout_seconds)
        # How long Ollama keeps the model resident after a call. The default is
        # a negative duration ("never unload"): together with the startup
        # warm-up, that means no user call ever pays the cold-load cost.
        self.keep_alive = keep_alive
        self.max_messages = max(1, max_messages)
        # Generation-cost knobs: on a slow CPU host, output length and prompt
        # size are what decide whether a summary fits inside the timeout.
        self.num_predict = max(1, num_predict)
        self.transcript_char_budget = max(200, transcript_char_budget)
        # "paragraphs" (default): a short narrative run-through of the chat.
        # "bullets": the classic 3-6 bullet-point TL;DR.
        self.style = style if style in {"paragraphs", "bullets"} else "paragraphs"
        # When a window exceeds one transcript budget, it's summarised in chunks
        # and merged (map-reduce). Each chunk is one model call, so this caps
        # worst-case latency; beyond it the most recent chunks win.
        self.max_chunks = max(1, max_chunks)
        # Injectable for tests; when None, _make_session builds a real aiohttp
        # session per request.
        self._session_factory = session_factory
        self._ollama_disabled_until = 0.0
        self._warmup_task: asyncio.Task | None = None
        # Initial retry delay when Ollama isn't reachable at startup (doubles up
        # to the warm-up window). Overridable in tests.
        self._warmup_retry_seconds = 60

    def _make_session(self, *, total: int | None = None) -> aiohttp.ClientSession:
        if self._session_factory is not None:
            return self._session_factory()
        # Short connect timeout fails fast when Ollama is down; the (larger) total
        # covers a cold model load + generation on CPU.
        total = total or self.request_timeout_seconds
        timeout = aiohttp.ClientTimeout(total=total, sock_connect=min(5, total))
        return aiohttp.ClientSession(timeout=timeout)

    @property
    def ai_available(self) -> bool:
        return bool(self.ollama_url)

    @property
    def warming_up(self) -> bool:
        """True while the background warm-up hasn't succeeded yet. While warming,
        /tldr goes straight to the digest instead of queueing behind the model
        load inside Ollama (which would time out AND trip the breaker)."""
        return self._warmup_task is not None and not self._warmup_task.done()

    def begin_warm_up(self) -> None:
        """Schedule a background model warm-up so the first real /tldr doesn't
        pay the cold-load cost. Retries until Ollama is reachable (it may start
        after the bot on a reboot). Safe no-op when the AI path is disabled."""
        if not self.enabled or not self.ai_available:
            return
        if self._warmup_task is None or self._warmup_task.done():
            self._warmup_task = asyncio.create_task(self._warm_up_loop())

    async def stop(self) -> None:
        task = self._warmup_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _warm_up_loop(self) -> None:
        """Keep trying to load the model until it succeeds, backing off between
        attempts — covers Ollama coming up after the bot on a host reboot."""
        backoff = self._warmup_retry_seconds
        while True:
            if await self.warm_up():
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _WARMUP_TIMEOUT_SECONDS)

    async def warm_up(self) -> bool:
        """Ask Ollama to load the model into memory — an ``/api/generate`` call
        with no prompt is Ollama's documented load-only request. Failures are
        logged but never trip the breaker: this is purely advisory. Returns
        ``True`` once the model is confirmed loaded."""
        if not self.ai_available:
            return False
        payload = {"model": self.model, "keep_alive": self.keep_alive}
        url = f"{self.ollama_url}/api/generate"
        started = time.monotonic()
        try:
            async with self._make_session(total=_WARMUP_TIMEOUT_SECONDS) as session:
                async with session.post(url, json=payload) as response:
                    if response.status != 200:
                        body = await _read_body(response)
                        log.warning(
                            "Ollama warm-up failed HTTP %s (model=%s): %s",
                            response.status,
                            self.model,
                            body,
                        )
                        return False
                    await response.json()
            # A user /tldr that raced this load may have tripped the breaker;
            # the model is confirmed ready now, so clear it.
            self._ollama_disabled_until = 0.0
            log.info(
                "Ollama model %s loaded (warm-up took %.1fs); /tldr summaries are ready.",
                self.model,
                time.monotonic() - started,
            )
            return True
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.warning(
                "Ollama warm-up for model %s failed after %.1fs (%s: %s); retrying later.",
                self.model,
                time.monotonic() - started,
                type(exc).__name__,
                exc,
            )
            return False
        except Exception:  # pragma: no cover - defensive; warm-up must never crash startup
            log.exception("Unexpected error during Ollama warm-up.")
            return False

    async def summarize(
        self,
        messages: list[ChatMessage],
        *,
        topic: str | None = None,
        timeframe_label: str,
        channel_name: str,
    ) -> TldrResult:
        topic = (topic or "").strip() or None
        scope = messages if topic is None else filter_by_topic(messages, topic)
        matched_count = None if topic is None else len(scope)
        participants = sorted({m.author for m in scope})
        links: list[str] = []
        for message in scope:
            links.extend(extract_urls(message.content))

        base = TldrResult(
            summary="",
            method="digest",
            message_count=len(scope),
            participant_count=len(participants),
            topic=topic,
            matched_count=matched_count,
            links=_dedupe(links),
        )

        if not scope:
            return _replace(
                base,
                summary=self._empty_summary(topic, timeframe_label, channel_name),
            )

        # While the warm-up is still loading the model, don't queue behind it —
        # a user call would hit its (shorter) timeout and trip the breaker,
        # masking the model right as it becomes ready. Serve the digest instead.
        if self.ai_available and self._ollama_ready() and not self.warming_up:
            ai_result = await self._summarize_with_ollama(
                scope, topic=topic, timeframe_label=timeframe_label, channel_name=channel_name
            )
            if ai_result is not None:
                ai_summary, included = ai_result
                return _replace(
                    base,
                    summary=ai_summary,
                    method="ai",
                    model=self.model,
                    ai_message_count=included,
                )

        return _replace(base, summary=self._build_digest(scope, topic=topic))

    # -- Ollama path -------------------------------------------------------

    def _ollama_ready(self) -> bool:
        return time.monotonic() >= self._ollama_disabled_until

    def _trip_ollama_breaker(self) -> None:
        self._ollama_disabled_until = time.monotonic() + _OLLAMA_BACKOFF_SECONDS

    def _focus_instruction(self, topic: str | None) -> str:
        if not topic:
            return ""
        return (
            f'Focus ONLY on anything related to the topic "{topic}". '
            "If the chat does not discuss it, say so in one sentence.\n"
        )

    def _style_instruction(self) -> str:
        if self.style == "bullets":
            return (
                "Use 3-6 concise bullet points, each starting with '- '. Summarise "
                "what was discussed and any decisions or outcomes."
            )
        return (
            "Write 1-3 short paragraphs of plain prose — a quick run-through of "
            "what people talked about, any decisions or plans that came out of "
            "it, and how the conversation wrapped up. Do not use bullet points, "
            "numbered lists, or headings."
        )

    def _chunk_transcripts(self, messages: list[ChatMessage]) -> list[tuple[str, int]]:
        """Split the chat into chronological transcripts that each fit the
        model's budget. Returns ``[(transcript, message_count), …]``."""
        budget = self.transcript_char_budget
        chunks: list[tuple[str, int]] = []
        lines: list[str] = []
        used = 0
        count = 0
        for message in messages:
            author = _clean(message.author) or "someone"
            text = _clean(message.content)
            if not text:
                continue
            line = f"{author}: {text}"
            if len(line) > budget:
                line = line[:budget]
            if lines and used + len(line) + 1 > budget:
                chunks.append(("\n".join(lines), count))
                lines, used, count = [], 0, 0
            lines.append(line)
            used += len(line) + 1
            count += 1
        if lines:
            chunks.append(("\n".join(lines), count))
        return chunks

    def _final_prompt(
        self, transcript: str, *, topic: str | None, timeframe_label: str, channel_name: str
    ) -> str:
        return (
            "You are Rob, a Discord assistant. Write a short, neutral TL;DR of the "
            f"chat from #{channel_name} ({timeframe_label}).\n"
            f"{self._focus_instruction(topic)}"
            f"{self._style_instruction()} Only include things that are clearly stated in "
            "the transcript — if you are not sure about a detail or who said it, "
            "leave it out. Do not add a preamble, do not invent details, and never "
            "use @ mentions. If the chat is only small talk, say that briefly.\n\n"
            "Chat transcript:\n"
            f"{transcript}\n\n"
            "TL;DR:"
        )

    def _chunk_prompt(
        self, transcript: str, index: int, total: int, *, topic: str | None, channel_name: str
    ) -> str:
        return (
            f"You are Rob, a Discord assistant. Below is part {index} of {total} of a "
            f"chat from #{channel_name}.\n"
            f"{self._focus_instruction(topic)}"
            "Summarise this part in 2-4 short sentences of plain prose. Only include "
            "things that are clearly stated in the transcript — if you are not sure "
            "about a detail or who said it, leave it out. Do not add a preamble and "
            "never use @ mentions.\n\n"
            "Chat transcript:\n"
            f"{transcript}\n\n"
            "Summary:"
        )

    def _combine_prompt(
        self, partials: list[str], *, topic: str | None, timeframe_label: str, channel_name: str
    ) -> str:
        partial_block = "\n\n".join(
            f"Part {i}: {part}" for i, part in enumerate(partials, 1)
        )
        return (
            "You are Rob, a Discord assistant. Below are chronological partial "
            f"summaries of the chat from #{channel_name} ({timeframe_label}).\n"
            f"{self._focus_instruction(topic)}"
            f"Merge them into ONE final TL;DR. {self._style_instruction()} Do not "
            "add a preamble, do not invent details, and never use @ mentions.\n\n"
            "Partial summaries:\n"
            f"{partial_block}\n\n"
            "TL;DR:"
        )

    def _build_prompt(
        self,
        messages: list[ChatMessage],
        *,
        topic: str | None,
        timeframe_label: str,
        channel_name: str,
    ) -> tuple[str, int]:
        """Single-call prompt from the most recent messages that fit the budget.
        Returns ``(prompt, included_count)``."""
        lines: list[str] = []
        used = 0
        # Keep the most recent messages that fit the budget, then restore order.
        for message in reversed(messages):
            author = _clean(message.author) or "someone"
            text = _clean(message.content)
            if not text:
                continue
            line = f"{author}: {text}"
            if used + len(line) + 1 > self.transcript_char_budget:
                break
            lines.append(line)
            used += len(line) + 1
        lines.reverse()
        transcript = "\n".join(lines)
        prompt = self._final_prompt(
            transcript, topic=topic, timeframe_label=timeframe_label, channel_name=channel_name
        )
        return prompt, len(lines)

    async def _summarize_with_ollama(
        self,
        messages: list[ChatMessage],
        *,
        topic: str | None,
        timeframe_label: str,
        channel_name: str,
    ) -> tuple[str, int] | None:
        chunks = self._chunk_transcripts(messages)

        if len(chunks) <= 1:
            prompt, included = self._build_prompt(
                messages, topic=topic, timeframe_label=timeframe_label, channel_name=channel_name
            )
            summary = await self._generate(prompt)
            if summary is None:
                return None
            return summary, included

        # Map-reduce: the window doesn't fit one prompt, so summarise each chunk
        # and merge the partials — the TL;DR then covers the whole timeframe
        # instead of just its newest slice.
        if len(chunks) > self.max_chunks:
            log.info(
                "/tldr window has %s chunks; summarising the most recent %s.",
                len(chunks),
                self.max_chunks,
            )
            chunks = chunks[-self.max_chunks :]
        included = sum(count for _, count in chunks)
        partials: list[str] = []
        for index, (transcript, _count) in enumerate(chunks, 1):
            partial = await self._generate(
                self._chunk_prompt(
                    transcript, index, len(chunks), topic=topic, channel_name=channel_name
                )
            )
            if partial is None:
                return None
            partials.append(partial)
        summary = await self._generate(
            self._combine_prompt(
                partials, topic=topic, timeframe_label=timeframe_label, channel_name=channel_name
            )
        )
        if summary is None:
            return None
        return summary, included

    async def _generate(self, prompt: str) -> str | None:
        """One Ollama generate call. Returns the cleaned text, or ``None`` on any
        failure (breaker tripped, fallback expected)."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self.keep_alive,
            # A TL;DR is short; cap output so generation latency stays bounded.
            "options": {"temperature": 0.2, "num_predict": self.num_predict},
        }
        url = f"{self.ollama_url}/api/generate"
        started = time.monotonic()
        try:
            async with self._make_session() as session:
                async with session.post(url, json=payload) as response:
                    if response.status != 200:
                        # Include the body — Ollama explains *why* here (e.g.
                        # "model 'x' not found, try pulling it", or an OOM error).
                        body = await _read_body(response)
                        log.warning(
                            "Ollama returned HTTP %s for /tldr (model=%s): %s",
                            response.status,
                            self.model,
                            body,
                        )
                        self._trip_ollama_breaker()
                        return None
                    data = await response.json()
            # Parse inside the guard so a null/non-dict body can never escape.
            raw = data.get("response") if isinstance(data, dict) else None
            text = raw if isinstance(raw, str) else ""
        except (aiohttp.ClientError, TimeoutError) as exc:
            # TimeoutError covers aiohttp's total-timeout (asyncio.TimeoutError is
            # an alias on 3.11+); both mean "server slow/unreachable", not a bug.
            # Log the type name (a bare timeout stringifies to ""), how long we
            # actually waited, and the configured limit — so "I raised the
            # timeout" is verifiable straight from this line.
            log.info(
                "Ollama unavailable for /tldr (%s: %s) after %.1fs with timeout=%ss; "
                "using digest fallback.",
                type(exc).__name__,
                exc,
                time.monotonic() - started,
                self.request_timeout_seconds,
            )
            self._trip_ollama_breaker()
            return None
        except Exception:  # pragma: no cover - defensive; never break the command
            log.exception("Unexpected error calling Ollama for /tldr.")
            self._trip_ollama_breaker()
            return None

        log.info(
            "Ollama /tldr call finished in %.1fs (model=%s).",
            time.monotonic() - started,
            self.model,
        )
        summary = _clean_summary(text)
        return summary or None

    # -- Extractive digest -------------------------------------------------

    def _empty_summary(self, topic: str | None, timeframe_label: str, channel_name: str) -> str:
        if topic:
            return (
                f"No messages about **{topic}** were found in #{channel_name} "
                f"for {timeframe_label}."
            )
        return f"No messages were found in #{channel_name} for {timeframe_label}."

    def _build_digest(self, messages: list[ChatMessage], *, topic: str | None) -> str:
        counts = Counter(m.author for m in messages)
        top = counts.most_common(5)
        top_line = ", ".join(f"{author} ({n})" for author, n in top)

        links = _dedupe(url for m in messages for url in extract_urls(m.content))

        highlights = _pick_highlights(messages, limit=4)

        sections: list[str] = []
        if topic:
            sections.append(f'Here\'s what came up about **{topic}**:')
        sections.append(f"**Most active:** {top_line}")
        if links:
            shown = links[:3]
            more = f" (+{len(links) - len(shown)} more)" if len(links) > len(shown) else ""
            sections.append("**Links shared:** " + ", ".join(shown) + more)
        if highlights:
            bullet_lines = "\n".join(f"- {_truncate(h, 220)}" for h in highlights)
            sections.append("**Highlights:**\n" + bullet_lines)
        return "\n\n".join(sections)


def _pick_highlights(messages: list[ChatMessage], *, limit: int) -> list[str]:
    """A few representative lines: the longest, most substantive messages,
    kept in chronological order and de-duplicated."""

    candidates = [
        (m.author, _clean(m.content))
        for m in messages
        if len(_clean(m.content)) >= 20
    ]
    # Rank by length (proxy for substance) but keep chronological presentation.
    ranked = sorted(range(len(candidates)), key=lambda i: len(candidates[i][1]), reverse=True)
    chosen = sorted(ranked[:limit])
    seen: set[str] = set()
    out: list[str] = []
    for i in chosen:
        author, text = candidates[i]
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(f"{author}: {text}")
    return out


def _dedupe(items) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _clean_summary(text: str) -> str:
    text = text.strip()
    # Neutralise any stray mentions the model may have echoed from the chat.
    text = text.replace("@everyone", "everyone").replace("@here", "here")
    text = re.sub(r"<@!?(\d+)>", "someone", text)
    return text.strip()


def _replace(result: TldrResult, **changes) -> TldrResult:
    from dataclasses import replace

    return replace(result, **changes)

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from rob.discord.cogs.voice_transcription import VoiceTranscriptionCog, _voice_attachment
from rob.services.transcription_service import TranscriptResult


class _FakeAttachment:
    def __init__(
        self,
        *,
        content_type="audio/ogg",
        filename="voice-message.ogg",
        size=1000,
        duration=5.0,
        data=b"audio",
    ):
        self.content_type = content_type
        self.filename = filename
        self.size = size
        self.duration = duration
        self._data = data

    async def read(self):
        return self._data


class _FakeChannel:
    def __init__(self, fetch_result=None):
        self._fetch_result = fetch_result
        self.fetched_ids: list[int] = []

    async def fetch_message(self, message_id):
        self.fetched_ids.append(message_id)
        if self._fetch_result is None:
            import discord

            raise discord.NotFound(SimpleNamespace(status=404), "unknown message")
        return self._fetch_result


class _FakeMessage:
    def __init__(
        self,
        *,
        voice=True,
        attachments=None,
        bot=False,
        mentions=None,
        reference=None,
        channel=None,
    ):
        self.flags = SimpleNamespace(voice=voice)
        self.attachments = attachments if attachments is not None else [_FakeAttachment()]
        self.author = SimpleNamespace(bot=bot)
        self.id = 42
        self.mentions = mentions if mentions is not None else []
        self.reference = reference
        self.channel = channel if channel is not None else _FakeChannel()
        self.reply_calls: list[dict] = []

    async def reply(self, **kwargs):
        self.reply_calls.append(kwargs)


class _FakeService:
    def __init__(self, *, available=True, result=None):
        self._available = available
        self.result = result if result is not None else TranscriptResult("hello world", "en", 5.0)
        self.transcribe_calls: list[tuple] = []

    @property
    def available(self):
        return self._available

    async def transcribe(self, audio_bytes, *, filename="voice-message.ogg"):
        self.transcribe_calls.append((audio_bytes, filename))
        return self.result


_BOT_USER = SimpleNamespace(id=999, bot=True)


def _bot(service, *, max_duration=300, max_mb=25):
    return SimpleNamespace(
        transcription_service=service,
        user=_BOT_USER,
        settings=SimpleNamespace(
            voice_transcribe_max_duration_seconds=max_duration,
            voice_transcribe_max_file_mb=max_mb,
            voice_transcribe_max_concurrency=1,
        ),
    )


def _run(cog, message):
    asyncio.run(cog.on_message(message))


def test_voice_attachment_detection():
    voice_msg = _FakeMessage()
    assert _voice_attachment(voice_msg) is not None

    image = _FakeAttachment(content_type="image/png", filename="pic.png")
    non_voice = _FakeMessage(voice=False, attachments=[image])
    assert _voice_attachment(non_voice) is None

    # A plain audio upload (not flagged, not named like a voice note) must not
    # be treated as a voice message; Rob shouldn't transcribe arbitrary media.
    music = _FakeMessage(
        voice=False, attachments=[_FakeAttachment(content_type="audio/mpeg", filename="song.mp3")]
    )
    assert _voice_attachment(music) is None

    # Fallback: not flagged, but an audio file named like a voice note.
    fallback = _FakeMessage(
        voice=False, attachments=[_FakeAttachment(content_type=None, filename="voice-message.ogg")]
    )
    assert _voice_attachment(fallback) is not None


def test_successful_transcription_replies_without_ping():
    service = _FakeService()
    cog = VoiceTranscriptionCog(_bot(service))
    message = _FakeMessage()

    _run(cog, message)

    assert service.transcribe_calls
    assert message.reply_calls
    reply = message.reply_calls[0]
    assert reply["mention_author"] is False
    assert isinstance(reply["allowed_mentions"], discord.AllowedMentions)
    assert "view" in reply


def test_empty_transcript_still_replies():
    service = _FakeService(result=TranscriptResult("", "en", 2.0))
    cog = VoiceTranscriptionCog(_bot(service))
    message = _FakeMessage()
    _run(cog, message)
    assert message.reply_calls  # card shows "no speech detected"


def test_bot_author_is_ignored():
    service = _FakeService()
    cog = VoiceTranscriptionCog(_bot(service))
    message = _FakeMessage(bot=True)
    _run(cog, message)
    assert not service.transcribe_calls
    assert not message.reply_calls


def test_non_voice_message_ignored():
    service = _FakeService()
    cog = VoiceTranscriptionCog(_bot(service))
    message = _FakeMessage(voice=False, attachments=[])
    _run(cog, message)
    assert not service.transcribe_calls
    assert not message.reply_calls


def test_unavailable_service_is_noop():
    service = _FakeService(available=False)
    cog = VoiceTranscriptionCog(_bot(service))
    message = _FakeMessage()
    _run(cog, message)
    assert not service.transcribe_calls
    assert not message.reply_calls


def test_over_duration_is_skipped():
    service = _FakeService()
    cog = VoiceTranscriptionCog(_bot(service, max_duration=10))
    message = _FakeMessage(attachments=[_FakeAttachment(duration=60.0)])
    _run(cog, message)
    assert not service.transcribe_calls
    assert not message.reply_calls


def test_unknown_duration_is_skipped():
    # No duration means the length cap can't be applied, so skip it.
    service = _FakeService()
    cog = VoiceTranscriptionCog(_bot(service))
    message = _FakeMessage(attachments=[_FakeAttachment(duration=None)])
    _run(cog, message)
    assert not service.transcribe_calls
    assert not message.reply_calls


def test_over_size_is_skipped():
    service = _FakeService()
    cog = VoiceTranscriptionCog(_bot(service, max_mb=1))
    big = _FakeAttachment(size=2 * 1024 * 1024)
    message = _FakeMessage(attachments=[big])
    _run(cog, message)
    assert not service.transcribe_calls
    assert not message.reply_calls


def _ping_reply_message(target, *, resolved=True, mention=True):
    reference = SimpleNamespace(
        resolved=target if resolved else None,
        message_id=getattr(target, "id", None),
    )
    return _FakeMessage(
        voice=False,
        attachments=[],
        mentions=[_BOT_USER] if mention else [],
        reference=reference,
        channel=_FakeChannel(fetch_result=target),
    )


def test_ping_reply_transcribes_older_voice_message():
    import discord

    service = _FakeService()
    cog = VoiceTranscriptionCog(_bot(service))
    old_voice = _FakeMessage()  # the (older) voice message being replied to

    # Our fake isn't a real discord.Message, so patch the reference resolver.
    ping = _ping_reply_message(old_voice)

    async def _resolve(_message):
        return old_voice

    cog._resolve_reference = _resolve  # type: ignore[method-assign]
    _run(cog, ping)

    assert service.transcribe_calls
    # The transcript lands on the voice message itself, without pinging.
    assert old_voice.reply_calls
    assert old_voice.reply_calls[0]["mention_author"] is False
    assert isinstance(old_voice.reply_calls[0]["allowed_mentions"], discord.AllowedMentions)
    assert not ping.reply_calls


def test_ping_reply_to_non_voice_target_is_ignored():
    service = _FakeService()
    cog = VoiceTranscriptionCog(_bot(service))
    ordinary = _FakeMessage(voice=False, attachments=[])
    ping = _ping_reply_message(ordinary)

    async def _resolve(_message):
        return ordinary

    cog._resolve_reference = _resolve  # type: ignore[method-assign]
    _run(cog, ping)

    assert not service.transcribe_calls
    assert not ordinary.reply_calls


def test_reply_without_ping_is_ignored():
    service = _FakeService()
    cog = VoiceTranscriptionCog(_bot(service))
    old_voice = _FakeMessage()
    ping = _ping_reply_message(old_voice, mention=False)
    _run(cog, ping)
    assert not service.transcribe_calls
    assert not old_voice.reply_calls


def test_ping_reply_to_bot_voice_message_is_ignored():
    service = _FakeService()
    cog = VoiceTranscriptionCog(_bot(service))
    bot_voice = _FakeMessage(bot=True)
    ping = _ping_reply_message(bot_voice)

    async def _resolve(_message):
        return bot_voice

    cog._resolve_reference = _resolve  # type: ignore[method-assign]
    _run(cog, ping)
    assert not service.transcribe_calls
    assert not bot_voice.reply_calls


def test_transcribe_none_result_no_reply():
    service = _FakeService(result=None)
    service.result = None
    cog = VoiceTranscriptionCog(_bot(service))
    message = _FakeMessage()
    _run(cog, message)
    assert not message.reply_calls

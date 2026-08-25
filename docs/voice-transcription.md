# Voice-message transcription

When enabled, Rob watches for Discord **voice messages** and replies to each one
(**without pinging** the author) with a transcript, produced by a local
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) model.

**On-demand transcripts:** reply to any voice message and **@mention Rob** and
he'll transcribe that message — this covers voice messages sent before the
feature was enabled (the transcript is posted as a reply to the voice message
itself, no ping).

### Enabling it

`faster-whisper` is an **optional** dependency (kept out of the core install
because it pulls in CTranslate2/ONNX). On the bot host:

```bash
pip install -r requirements-voice.txt
```

```env
VOICE_TRANSCRIBE_ENABLED=true
VOICE_TRANSCRIBE_MODEL=base    # tiny | base | small | medium | large-v3
```

The model is downloaded from Hugging Face on first use and cached. Everything
runs on CPU by default; nothing leaves the host.

Until both steps are done the feature is a no-op — the bot logs a clear message
if it's enabled without `faster-whisper` installed, and stays running.

**`PermissionError: … '.cache'` on first use:** everything huggingface writes
(model files *and* the xet transfer backend's cache/logs under `HF_HOME`) must
be writable by the bot user. The shipped systemd units handle this (they set
`CacheDirectory=` + `HF_HOME` to a service-owned `/var/cache` dir — re-copy the
unit and `systemctl daemon-reload` after updating). Alternatively point the
cache somewhere the bot owns; setting `VOICE_TRANSCRIBE_DOWNLOAD_ROOT` also
redirects `HF_HOME` beneath it (unless one is already set):

```bash
sudo mkdir -p /opt/rob-bot/whisper-models
sudo chown rob:rob /opt/rob-bot/whisper-models
```

```env
VOICE_TRANSCRIBE_DOWNLOAD_ROOT=/opt/rob-bot/whisper-models
```

A failed load retries automatically every 5 minutes, so once the directory is
fixed the next voice message recovers without a restart.

### Behaviour & safety

- All model work runs in a worker thread (`asyncio.to_thread`) and is serialised
  by a semaphore (`VOICE_TRANSCRIBE_MAX_CONCURRENCY`), so the Discord event loop
  never stalls.
- Voice messages longer than `VOICE_TRANSCRIBE_MAX_DURATION_SECONDS` (default
  300s) or larger than `VOICE_TRANSCRIBE_MAX_FILE_MB` (default 25 MB) are skipped.
- Replies never ping (`mention_author=False` + no allowed mentions), and any
  mentions in the transcript text are neutralised.
- If transcription fails, Rob skips silently (logged) rather than spamming the
  channel.

### Config

| Env var | Default | Notes |
|---|---|---|
| `VOICE_TRANSCRIBE_ENABLED` | `false` | Master switch. |
| `VOICE_TRANSCRIBE_MODEL` | `base` | Whisper size; bigger = better + slower. |
| `VOICE_TRANSCRIBE_DEVICE` | `cpu` | `cpu` or `cuda`. |
| `VOICE_TRANSCRIBE_COMPUTE_TYPE` | `int8` | `int8` is fastest on CPU. |
| `VOICE_TRANSCRIBE_LANGUAGE` | _(blank)_ | Blank ⇒ auto-detect. |
| `VOICE_TRANSCRIBE_DOWNLOAD_ROOT` | _(blank)_ | Model cache dir; blank ⇒ HF default. |
| `VOICE_TRANSCRIBE_BEAM_SIZE` | `1` | Higher = more accurate + slower. |
| `VOICE_TRANSCRIBE_MAX_DURATION_SECONDS` | `300` | Skip longer clips. |
| `VOICE_TRANSCRIBE_MAX_FILE_MB` | `25` | Skip larger files. |
| `VOICE_TRANSCRIBE_MAX_CONCURRENCY` | `1` | Simultaneous transcriptions. |

# TL;DR summaries & voice-message transcription

Two AI-assisted features that both run **in-house** — chat content stays on the
bot host and is never sent to a third-party API.

---

## `/tldr` — chat summaries

Summarises recent chat in a channel, for everyone, on the main + test guilds.
The reply is a **public plain-text message** (no embed/card) posted in the
channel where the command was run; errors are shown only to the requester.

### Options

| Option      | Required | Description |
|-------------|----------|-------------|
| `timeframe` | no       | How far back to look: last hour / 6 hours / 24 hours / 3 days / 7 days. Defaults to the last 24 hours. |
| `topic`     | no       | Focus the summary on a topic. Non-matching chatter is filtered out (loosely) and the model is told to focus on it. |
| `channel`   | no       | Summarise a different text channel/thread. Defaults to the current one. |

A short per-user cooldown (`TLDR_COOLDOWN_SECONDS`, default 30s) prevents spam.
Rob only ever reads channels the requester can already see: it checks that both
the user and Rob have **View Channel** + **Read Message History** first.

### How the summary is produced

1. **Extractive digest (always available).** Pure-Python: most-active
   participants, links shared, and a few representative "highlight" messages,
   plus topic-match counts when a topic is given. No dependencies, nothing
   leaves the host.
2. **Natural-language summary (optional upgrade).** If a local
   [Ollama](https://ollama.com) server is reachable at `TLDR_OLLAMA_URL`, the
   selected messages are sent to a small local model (`TLDR_MODEL`) which writes
   the TL;DR. When the window holds more chat than fits one model prompt
   (`TLDR_TRANSCRIPT_CHAR_BUDGET`), Rob **summarises it in chronological chunks
   and merges the partial summaries** (up to `TLDR_MAX_CHUNKS` model calls +
   one merge call), so the summary covers the whole requested timeframe rather
   than just its newest slice. If even that cap is exceeded, the most recent
   chunks win and the footer says `latest X of Y messages`. If Ollama is
   unreachable, times out, or errors, Rob falls back to the digest silently
   (and backs off from retrying for a couple of minutes so a down server
   doesn't slow every call).

The footer of the reply shows which path produced it ("summarised by <model>
(on-server)" vs "quick digest").

### Enabling the natural-language path

On the **bot host** (loopback only — do not expose Ollama publicly):

```bash
curl -fsSL https://ollama.com/install.sh | sh   # or your platform's installer
ollama pull llama3.2:1b                          # small, CPU-friendly model
```

Then point Rob at it (defaults already match a local install):

```env
TLDR_OLLAMA_URL=http://127.0.0.1:11434
TLDR_MODEL=llama3.2:1b
```

Any Ollama model works — `qwen2.5:1.5b`, `phi3:mini`, `gemma2:2b`, etc. Pick one
that fits the host's RAM. Set `TLDR_OLLAMA_URL=` (blank) to force digest-only.

### Right-sizing the model to the host's RAM

The model has to fit in **free** RAM (CPU-only inference), and it's loaded on top
of everything else already running on the host. `llama3.2:1b` needs roughly
**1.3–1.6 GB free**. If the host is short on memory, Ollama returns HTTP 500 and
Rob falls back to the digest; the bot log then shows the reason, e.g.:

```
Ollama returned HTTP 500 for /tldr (model=llama3.2:1b): {"error":"llama-server
process has terminated ... ggml_aligned_malloc: insufficient memory ..."}
```

Check free memory with `free -h`, then pick a model that fits:

| Model | `ollama pull` | Approx. RAM | Quality |
|---|---|---|---|
| `llama3.2:1b` | `llama3.2:1b` | ~1.3–1.6 GB | good |
| `qwen2.5:0.5b` | `qwen2.5:0.5b` | ~0.7–1.0 GB | decent |
| `smollm2:360m` | `smollm2:360m` | ~0.4–0.6 GB | basic |
| `smollm2:135m` | `smollm2:135m` | ~0.2–0.3 GB | minimal |

Then set `TLDR_MODEL` to the one you pulled and restart the bot. After a failed
call Rob backs off from Ollama for ~2 minutes, so wait (or restart the bot)
before retrying — the footer flips to "summarised by <model> (on-server)" once
it works. If nothing fits, leave `TLDR_OLLAMA_URL` blank and rely on the digest,
or add swap / more RAM.

### Config

| Env var | Default | Notes |
|---|---|---|
| `TLDR_ENABLED` | `true` | Master switch for `/tldr`. |
| `TLDR_OLLAMA_URL` | `http://127.0.0.1:11434` | Blank ⇒ digest only. |
| `TLDR_MODEL` | `llama3.2:1b` | Must be pulled in Ollama. |
| `TLDR_REQUEST_TIMEOUT_SECONDS` | `120` | Total per-summary timeout (covers a cold model load on CPU). |
| `TLDR_KEEP_ALIVE` | `-1m` | Model residency after a call; negative = never unload, finite (e.g. `10m`) = unload when idle. |
| `TLDR_MAX_MESSAGES` | `400` | Most-recent messages scanned in the window. |
| `TLDR_NUM_PREDICT` | `300` | Max tokens the model may generate per summary. |
| `TLDR_TRANSCRIPT_CHAR_BUDGET` | `8000` | Max chars of chat handed to the model. |
| `TLDR_STYLE` | `paragraphs` | `paragraphs` = short narrative run-through; `bullets` = 3-6 bullet points. |
| `TLDR_MAX_CHUNKS` | `6` | Max chunk-summary model calls for windows bigger than one prompt. |
| `TLDR_COOLDOWN_SECONDS` | `30` | Per-user cooldown. |

**Slow hosts:** on a small CPU VPS the wall-clock cost of a summary is roughly
*(transcript length ÷ prompt-eval speed) + (output tokens ÷ generation speed)*.
If the timed curl above shows the *warm* run is close to (or over) your timeout,
switch to a smaller model first (`qwen2.5:0.5b`, `smollm2:360m`); lowering
`TLDR_NUM_PREDICT` (e.g. 200) and `TLDR_TRANSCRIPT_CHAR_BUDGET` (e.g. 4000)
trims the remaining cost.

**Cold starts:** loading the model into RAM is slow on CPU, so Rob **pre-warms
it at startup** — a background load request with a generous (10-minute) window,
retried with backoff until Ollama is reachable (Ollama may start after the bot
on a reboot). Success is logged as `Ollama model <model> loaded (warm-up took
Xs)`. While the warm-up is still loading, `/tldr` serves the digest immediately
instead of queueing behind the load. The default `TLDR_KEEP_ALIVE=-1m` (negative
= never unload) then keeps the model resident **forever**, so no call is ever
cold; on a RAM-tight host set a finite duration (e.g. `10m`) to let it unload
when idle, accepting that the first call after idle is slow or falls back.

### Diagnosing timeouts

A timed-out call logs how long it waited **and the timeout the bot is actually
running with**:

```
Ollama unavailable for /tldr (TimeoutError: ) after 120.0s with timeout=120s; …
```

If `timeout=` doesn't match what you set in `.env`, the bot wasn't restarted
after the edit (`sudo systemctl restart rob-bot`). If it does match, measure
what the host can really do — run this **twice** on the bot host:

```bash
time curl -s http://127.0.0.1:11434/api/generate \
  -d '{"model":"llama3.2:1b","prompt":"Write three short bullet points about pizza.","stream":false}' >/dev/null
```

The first run includes the cold model load; the second is the warm speed. If
even the *warm* run is slower than your timeout, the CPU can't run that model —
switch to a smaller one (`qwen2.5:0.5b`) rather than raising the timeout further.
After any failure Rob backs off from Ollama for ~2 minutes; a successful call
logs `Ollama /tldr call finished in Xs`.

---

## Voice-message transcription

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

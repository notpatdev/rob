#!/usr/bin/env bash
# Remove the TL;DR AI stack from the bot host: uninstall Ollama (service,
# binary, user, and all downloaded models) and strip the TLDR_* settings from
# the bot's .env. Run as root (sudo) on the bot host AFTER deploying the code
# that removes the /tldr feature, then restart the bot.
#
#   sudo bash deploy/scripts/remove-tldr-ai.sh
#   sudo systemctl restart rob-bot
#
# Voice-message transcription (faster-whisper) is NOT touched by default —
# see the optional section at the bottom.

set -euo pipefail

ENV_FILE="${ENV_FILE:-/opt/rob-bot/app/.env}"

if [[ $EUID -ne 0 ]]; then
    echo "Run this with sudo/root." >&2
    exit 1
fi

echo "== Stopping and removing the Ollama service =="
if systemctl list-unit-files ollama.service >/dev/null 2>&1; then
    systemctl stop ollama 2>/dev/null || true
    systemctl disable ollama 2>/dev/null || true
fi
rm -f /etc/systemd/system/ollama.service /usr/lib/systemd/system/ollama.service
systemctl daemon-reload

echo "== Removing the Ollama binary =="
for bin in /usr/local/bin/ollama /usr/bin/ollama /bin/ollama; do
    [[ -e "$bin" ]] && rm -f "$bin" && echo "removed $bin"
done

echo "== Removing downloaded models and Ollama data =="
# Default model store for the systemd install; ~1.3 GB+ per pulled model.
rm -rf /usr/share/ollama
# Per-user stores, in case models were ever pulled outside the service.
for home in /root /home/*; do
    [[ -d "$home/.ollama" ]] && rm -rf "$home/.ollama" && echo "removed $home/.ollama"
done

echo "== Removing the ollama system user/group =="
id ollama >/dev/null 2>&1 && userdel ollama && echo "removed user ollama" || true
getent group ollama >/dev/null 2>&1 && groupdel ollama && echo "removed group ollama" || true

echo "== Stripping TLDR_* settings from $ENV_FILE =="
if [[ -f "$ENV_FILE" ]]; then
    cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"
    sed -i '/^TLDR_/d' "$ENV_FILE"
    echo "done (backup kept next to it)"
else
    echo "no $ENV_FILE found; skipped"
fi

echo
echo "Done. Now deploy the code that removes /tldr (if you haven't) and restart:"
echo "  sudo -u rob git -C /opt/rob-bot/app pull origin main"
echo "  sudo systemctl restart rob-bot"
echo
echo "Discord removes the /tldr command from guilds shortly after the bot"
echo "restarts and re-syncs its commands."

# ---------------------------------------------------------------------------
# OPTIONAL: also remove voice-message transcription (the other on-server AI).
# Uncomment everything below ONLY if you want that feature gone too.
# ---------------------------------------------------------------------------
# echo "== Removing voice transcription (faster-whisper + models) =="
# sed -i 's/^VOICE_TRANSCRIBE_ENABLED=.*/VOICE_TRANSCRIBE_ENABLED=false/' "$ENV_FILE"
# sudo -u rob /opt/rob-bot/app/.venv/bin/pip uninstall -y faster-whisper
# rm -rf /opt/rob-bot/whisper-models        # VOICE_TRANSCRIBE_DOWNLOAD_ROOT
# rm -rf /var/cache/rob-bot/huggingface     # HF_HOME set by the systemd unit
# echo "Voice transcription disabled and its models removed."

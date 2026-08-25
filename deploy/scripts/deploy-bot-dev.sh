#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export APP_DIR="${APP_DIR:-/opt/rob-bot/app}"
export SERVICE_NAME="${SERVICE_NAME:-rob-bot-dev.service}"

exec "${SCRIPT_DIR}/deploy-bot.sh" "$@"

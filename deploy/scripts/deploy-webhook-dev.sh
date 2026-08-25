#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export APP_DIR="${APP_DIR:-/opt/rob-webhook/app}"
export SERVICE_NAME="${SERVICE_NAME:-rob-webhook-dev.service}"
export HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/health}"

exec "${SCRIPT_DIR}/deploy-webhook.sh" "$@"

#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/rob-webhook/app}"
SERVICE_NAME="${SERVICE_NAME:-rob-webhook.service}"
PYTHON_BIN="${PYTHON_BIN:-${APP_DIR}/.venv/bin/python}"
HEALTH_URL="${HEALTH_URL:-}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: Required command not found: $1"; exit 1; }
}

load_env_file() {
  local env_file="$1"
  local line=""
  local line_no=0

  while IFS= read -r line || [[ -n "${line}" ]]; do
    line_no=$((line_no + 1))
    line="${line%$'\r'}"

    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "${line}" =~ ^[[:space:]]*# ]] && continue

    if [[ "${line}" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      local key="${BASH_REMATCH[2]}"
      local value="${BASH_REMATCH[3]}"
      value="${value#"${value%%[![:space:]]*}"}"

      if [[ ! "${value}" =~ ^\".*\"$ && ! "${value}" =~ ^\'.*\'$ ]]; then
        value="$(printf '%s' "${value}" | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//')"
      fi

      export "${key}=${value}"
      continue
    fi

    echo "WARNING: Invalid .env syntax on line ${line_no}; ignoring: ${line}" >&2
    echo "Hint: Use KEY=value format and prefix comments with #." >&2
  done < "${env_file}"
}

local_http_host() {
  local host="$1"
  case "${host}" in
    ""|"0.0.0.0"|"::"|"[::]")
      printf '127.0.0.1'
      ;;
    *)
      printf '%s' "${host}"
      ;;
  esac
}

check_public_health() {
  local base_url="$1"
  local label="$2"
  local health_url="${base_url%/}/health"

  curl -fsS "${health_url}" >/dev/null
  echo "${label} is reachable: ${health_url}"
}

echo "[1/10] Checking required commands"
require_cmd git
require_cmd python3
require_cmd curl
require_cmd systemctl

echo "[2/10] Checking app directory"
[[ -d "${APP_DIR}" ]] || { echo "ERROR: APP_DIR does not exist: ${APP_DIR}"; exit 1; }
cd "${APP_DIR}"
[[ -d .git ]] || { echo "ERROR: ${APP_DIR} is not a git repository."; exit 1; }
[[ -f .env ]] || { echo "ERROR: ${APP_DIR}/.env does not exist."; exit 1; }

echo "[3/10] Checking virtual environment"
[[ -x "${PYTHON_BIN}" ]] || { echo "ERROR: Python executable missing: ${PYTHON_BIN}"; exit 1; }

echo "[4/10] Loading environment"
load_env_file ".env"

echo "[5/10] Validating webhook runtime settings"
PYTHON_DOTENV_DISABLED=1 PYTHONPATH=. "${PYTHON_BIN}" ops/checks/check_webhook_runtime.py

echo "[6/10] Running database check"
PYTHON_DOTENV_DISABLED=1 ROB_CHECK_DB_PROFILE=webhook PYTHONPATH=. "${PYTHON_BIN}" ops/checks/check_db.py

echo "[7/10] Checking webhook service"
systemctl is-active --quiet "${SERVICE_NAME}" || {
  echo "ERROR: ${SERVICE_NAME} is not active."
  systemctl status "${SERVICE_NAME}" --no-pager || true
  exit 1
}

echo "[8/10] Checking local webhook health"
if [[ -z "${HEALTH_URL}" ]]; then
  HEALTH_URL="http://$(local_http_host "${THRONE_WEBHOOK_HOST:-127.0.0.1}"):${THRONE_WEBHOOK_PORT:-8080}/health"
fi
curl -fsS "${HEALTH_URL}" >/dev/null
echo "Local webhook health is reachable: ${HEALTH_URL}"

echo "[9/10] Checking public webhook host"
check_public_health "${THRONE_WEBHOOK_BASE_URL:-https://throne.robthebot.com}" "Webhook public host"

echo "[10/10] Checking webhook-to-bot notify route if configured"
if [[ -n "${ROB_BOT_NOTIFY_URL:-}" ]]; then
  notify_status="$(curl -sS -o /dev/null -w '%{http_code}' "${ROB_BOT_NOTIFY_URL}")"
  case "${notify_status}" in
    200|401|403|405)
      echo "Webhook-to-bot notify route responded with HTTP ${notify_status}: ${ROB_BOT_NOTIFY_URL}"
      ;;
    *)
      echo "ERROR: Unexpected webhook-to-bot notify response HTTP ${notify_status}: ${ROB_BOT_NOTIFY_URL}"
      exit 1
      ;;
  esac
else
  echo "Skipping ROB_BOT_NOTIFY_URL check because the variable is unset."
fi

echo
printf 'Webhook runtime check passed.\n'

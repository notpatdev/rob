#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/rob-bot/app}"
SERVICE_NAME="${SERVICE_NAME:-rob-bot.service}"
PYTHON_BIN="${PYTHON_BIN:-${APP_DIR}/.venv/bin/python}"

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

echo "[1/8] Checking required commands"
require_cmd git
require_cmd python3
require_cmd curl
require_cmd systemctl

echo "[2/8] Checking app directory"
[[ -d "${APP_DIR}" ]] || { echo "ERROR: APP_DIR does not exist: ${APP_DIR}"; exit 1; }
cd "${APP_DIR}"
[[ -d .git ]] || { echo "ERROR: ${APP_DIR} is not a git repository."; exit 1; }
[[ -f .env ]] || { echo "ERROR: ${APP_DIR}/.env does not exist."; exit 1; }

echo "[3/8] Checking virtual environment"
[[ -x "${PYTHON_BIN}" ]] || { echo "ERROR: Python executable missing: ${PYTHON_BIN}"; exit 1; }

echo "[4/8] Loading environment"
load_env_file ".env"

echo "[5/8] Validating bot runtime settings"
PYTHON_DOTENV_DISABLED=1 PYTHONPATH=. "${PYTHON_BIN}" ops/checks/check_bot_runtime.py

echo "[6/8] Running database check"
PYTHON_DOTENV_DISABLED=1 ROB_CHECK_DB_PROFILE=bot PYTHONPATH=. "${PYTHON_BIN}" ops/checks/check_db.py

echo "[7/8] Checking bot service and local ops health"
systemctl is-active --quiet "${SERVICE_NAME}" || {
  echo "ERROR: ${SERVICE_NAME} is not active."
  systemctl status "${SERVICE_NAME}" --no-pager || true
  exit 1
}
BOT_HEALTH_URL="http://$(local_http_host "${ROB_OPS_HOST:-127.0.0.1}"):${ROB_OPS_PORT:-8811}/health"
health_args=()
if [[ -n "${ROB_OPS_SECRET:-}" ]]; then
  health_args+=(-H "X-Rob-Ops-Secret: ${ROB_OPS_SECRET}")
fi
BOT_HEALTH_JSON="$(curl -fsS "${health_args[@]}" "${BOT_HEALTH_URL}")"
printf '%s' "${BOT_HEALTH_JSON}" | "${PYTHON_BIN}" -c 'import json,sys; data=json.load(sys.stdin); assert data.get("ok") is True, data'
echo "Bot ops health is reachable: ${BOT_HEALTH_URL}"

echo "[8/8] Checking webhook-to-bot notify route config"
if [[ -n "${ROB_BOT_NOTIFY_URL:-}" ]]; then
  echo "Bot is configured to receive webhook notifications at: ${ROB_BOT_NOTIFY_URL}"
else
  echo "WARNING: ROB_BOT_NOTIFY_URL is unset; webhook send notifications will not reach the bot."
fi

echo
printf 'Bot runtime check passed.\n'

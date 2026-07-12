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

require_env() {
  local key="$1"
  if [[ -z "${!key:-}" ]]; then
    echo "ERROR: Required environment variable is missing: ${key}"
    exit 1
  fi
}

echo "[1/7] Checking required commands"
require_cmd git
require_cmd python3
require_cmd sudo

echo "[2/7] Checking app directory"
[[ -d "${APP_DIR}" ]] || { echo "ERROR: APP_DIR does not exist: ${APP_DIR}"; exit 1; }
cd "${APP_DIR}"
[[ -d .git ]] || { echo "ERROR: ${APP_DIR} is not a git repository."; exit 1; }
[[ -f .env ]] || { echo "ERROR: ${APP_DIR}/.env does not exist."; exit 1; }

echo "[3/7] Checking virtual environment"
[[ -x "${PYTHON_BIN}" ]] || { echo "ERROR: Python executable missing: ${PYTHON_BIN}"; exit 1; }

echo "[4/7] Checking systemd service"
if ! systemctl cat "${SERVICE_NAME}" >/dev/null 2>&1; then
  echo "ERROR: systemd service not found: ${SERVICE_NAME}"
  exit 1
fi

echo "[5/7] Loading environment"
load_env_file ".env"
require_env DATABASE_URL
require_env DISCORD_TOKEN
require_env BOT_NAME

echo "[6/7] Running database check"
if ! PYTHON_DOTENV_DISABLED=1 ROB_CHECK_DB_PROFILE=bot PYTHONPATH=. "$PYTHON_BIN" ops/checks/check_db.py; then
  echo "Database check failed."
  echo "Manual fix:"
  echo "1. Open pgAdmin4 / psql as doadmin."
  echo "2. Select the target database."
  echo "3. Run db/build/001_core_schema.sql."
  echo "4. Run db/build/002_indexes.sql."
  echo "5. Run db/build/003_achievements.sql."
  echo "6. Run db/build/004_sub_send_names.sql."
  echo "7. Run db/build/005_count_recovery.sql."
  echo "8. Run db/build/006_send_change_requests.sql."
  echo "9. Run the correct grants file from db/grants/."
  exit 1
fi

echo "[7/7] Checking current service state"
systemctl is-active "$SERVICE_NAME" || true

echo
printf 'Bot server pre-check passed.\n'

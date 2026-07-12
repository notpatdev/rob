#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/rob-webhook/app}"
SERVICE_NAME="${SERVICE_NAME:-rob-webhook.service}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
DEPLOY_REF="${DEPLOY_REF:-${DEPLOY_BRANCH}}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/health}"
HEALTH_ATTEMPTS="${HEALTH_ATTEMPTS:-20}"
HEALTH_SLEEP_SECONDS="${HEALTH_SLEEP_SECONDS:-2}"
GIT_CLEAN="${GIT_CLEAN:-true}"
INSTALL_ROB_GLOBAL="${INSTALL_ROB_GLOBAL:-true}"
PYTHON_BIN="${PYTHON_BIN:-${APP_DIR}/.venv/bin/python}"

trap 'echo "Deploy failed. Showing service diagnostics:"; sudo systemctl status "$SERVICE_NAME" --no-pager || true; sudo journalctl -u "$SERVICE_NAME" -n 120 --no-pager || true' ERR

run_git() {
  if git "$@"; then
    return 0
  fi

  if command -v sudo >/dev/null 2>&1; then
    echo "Git command failed as $(id -un); retrying with sudo."
    sudo -n git "$@"
    return $?
  fi

  return 1
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

echo "[1/13] Pre-flight checks"
command -v git >/dev/null
command -v python3 >/dev/null
command -v sudo >/dev/null
command -v curl >/dev/null

echo "[2/13] Enter app directory"
[[ -d "$APP_DIR" ]] || { echo "ERROR: APP_DIR does not exist: $APP_DIR"; exit 1; }
cd "$APP_DIR"

echo "[3/13] Verify repository and .env"
[[ -d .git ]] || { echo "ERROR: ${APP_DIR} is not a git repository."; exit 1; }
[[ -f .env ]] || { echo "ERROR: ${APP_DIR}/.env does not exist."; exit 1; }

echo "[4/13] Sync git ref"
run_git fetch origin --prune "$DEPLOY_BRANCH"
run_git fetch origin "$DEPLOY_REF" || true
if [[ "$DEPLOY_REF" == "$DEPLOY_BRANCH" ]]; then
  run_git checkout "$DEPLOY_BRANCH" || run_git checkout -B "$DEPLOY_BRANCH" "origin/$DEPLOY_BRANCH"
  run_git reset --hard "origin/$DEPLOY_BRANCH"
else
  if run_git rev-parse --verify --quiet "$DEPLOY_REF" >/dev/null; then
    run_git checkout --detach "$DEPLOY_REF"
  else
    run_git checkout --detach FETCH_HEAD
  fi
fi
if [[ "$GIT_CLEAN" == "true" ]]; then
  run_git clean -fd --exclude=.env --exclude=.venv
fi

echo "[5/13] Prepare virtual environment"
if [[ ! -x "$PYTHON_BIN" ]]; then
  python3 -m venv "$APP_DIR/.venv"
fi

echo "[6/13] Install dependencies"
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r requirements.txt

echo "[7/13] Run compile checks"
PYTHONPATH=. "$PYTHON_BIN" -m compileall apps rob scripts

echo "[8/13] Install global rob command if enabled"
if [[ "$INSTALL_ROB_GLOBAL" == "true" ]]; then
  ./ops/install-rob-global.sh
else
  echo "Skipping global rob command installation."
fi

echo "[9/13] Load and validate environment"
load_env_file ".env"
for key in DATABASE_URL THRONE_WEBHOOK_HOST THRONE_WEBHOOK_PORT; do
  [[ -n "${!key:-}" ]] || { echo "ERROR: Missing required environment variable: $key"; exit 1; }
done
if [[ -z "${THRONE_WEBHOOK_BASE_URL:-}" ]]; then
  echo "WARNING: THRONE_WEBHOOK_BASE_URL is not set."
fi

echo "[10/13] Run database checks"
if ! PYTHON_DOTENV_DISABLED=1 ROB_CHECK_DB_PROFILE=webhook PYTHONPATH=. "$PYTHON_BIN" ops/checks/check_db.py; then
  echo "Database check failed."
  echo "This database has not been built for Rob v2 yet, or runtime grants are incomplete."
  echo
  echo "Manual fix:"
  echo "1. Open pgAdmin4 / psql as doadmin."
  echo "2. Select the target database."
  echo "3. Run db/build/001_core_schema.sql."
  echo "4. Run db/build/002_indexes.sql."
  echo "5. Run db/build/004_sub_send_names.sql."
  echo "6. Run db/build/005_count_recovery.sql."
  echo "7. Run db/build/006_send_change_requests.sql."
  echo "8. Run db/build/007_send_update_requests.sql."
  echo "9. Run db/build/008_dm_preferences.sql."
  echo "10. Run db/build/009_terms_acceptance.sql."
  echo "11. Run the correct grants file from db/grants/."
  echo "12. Rerun deploy."
  exit 1
fi

echo "[11/13] Restart webhook service"
sudo systemctl restart "$SERVICE_NAME"

echo "[12/13] Wait for health check"
for ((i=1; i<=HEALTH_ATTEMPTS; i++)); do
  if curl -fsS "$HEALTH_URL" >/dev/null; then
    echo "Health check passed."
    break
  fi
  if [[ "$i" -eq "$HEALTH_ATTEMPTS" ]]; then
    echo "ERROR: Health check failed after ${HEALTH_ATTEMPTS} attempts."
    sudo systemctl status "$SERVICE_NAME" --no-pager || true
    sudo journalctl -u "$SERVICE_NAME" -n 120 --no-pager || true
    exit 1
  fi
  sleep "$HEALTH_SLEEP_SECONDS"
done

echo "[13/13] Show final service status"
sudo systemctl --no-pager --full status "$SERVICE_NAME" | sed -n '1,14p'

echo "Webhook deploy complete."

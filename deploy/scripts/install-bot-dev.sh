#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/patdadev/rob.git}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
APP_ROOT="${APP_ROOT:-/opt/rob-bot}"
APP_DIR="${APP_DIR:-${APP_ROOT}/app}"
SERVICE_NAME="${SERVICE_NAME:-rob-bot-dev.service}"
SERVICE_SOURCE_REL="${SERVICE_SOURCE_REL:-deploy/systemd/rob-bot-dev.service}"
DEPLOY_SCRIPT_SOURCE_REL="${DEPLOY_SCRIPT_SOURCE_REL:-deploy/scripts/deploy-bot-dev.sh}"
DEPLOY_SCRIPT_LINK="${DEPLOY_SCRIPT_LINK:-${APP_ROOT}/deploy-bot-dev.sh}"
RUNTIME_USER="${RUNTIME_USER:-rob}"
RUNTIME_GROUP="${RUNTIME_GROUP:-rob}"
DEPLOY_USER="${DEPLOY_USER:-${SUDO_USER:-}}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SUDOERS_PATH="${SUDOERS_PATH:-/etc/sudoers.d/rob-bot-deploy}"

log() {
  printf '[install-bot-dev] %s\n' "$*"
}

die() {
  printf '[install-bot-dev] error: %s\n' "$*" >&2
  exit 1
}

run_as_deploy() {
  runuser -u "${DEPLOY_USER}" -- "$@"
}

ensure_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "Run this script with sudo or as root."
  fi
}

ensure_deploy_user() {
  if [[ -z "${DEPLOY_USER}" ]]; then
    die "DEPLOY_USER is empty. Run via sudo or set DEPLOY_USER explicitly."
  fi

  if ! id "${DEPLOY_USER}" >/dev/null 2>&1; then
    log "Creating deploy user ${DEPLOY_USER}"
    useradd --create-home --shell /bin/bash "${DEPLOY_USER}"
  fi
}

ensure_runtime_user() {
  if ! getent group "${RUNTIME_GROUP}" >/dev/null 2>&1; then
    log "Creating runtime group ${RUNTIME_GROUP}"
    groupadd --system "${RUNTIME_GROUP}"
  fi

  if ! id "${RUNTIME_USER}" >/dev/null 2>&1; then
    log "Creating runtime user ${RUNTIME_USER}"
    useradd \
      --system \
      --gid "${RUNTIME_GROUP}" \
      --home-dir "${APP_ROOT}" \
      --shell /usr/sbin/nologin \
      "${RUNTIME_USER}"
  fi
}

install_packages() {
  if ! command -v apt-get >/dev/null 2>&1; then
    die "This installer currently supports Debian/Ubuntu hosts with apt-get."
  fi

  log "Installing system packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y git python3 python3-venv python3-pip ca-certificates sudo
}

clone_or_update_repo() {
  local deploy_group
  deploy_group="$(id -gn "${DEPLOY_USER}")"

  install -d -m 0755 -o "${DEPLOY_USER}" -g "${deploy_group}" "${APP_ROOT}"

  if [[ -d "${APP_DIR}/.git" ]]; then
    log "Updating existing checkout in ${APP_DIR}"
    chown -R "${DEPLOY_USER}:${deploy_group}" "${APP_DIR}"
    run_as_deploy git -C "${APP_DIR}" fetch origin
    run_as_deploy git -C "${APP_DIR}" checkout "${DEPLOY_BRANCH}"
    run_as_deploy git -C "${APP_DIR}" pull --ff-only origin "${DEPLOY_BRANCH}"
  else
    log "Cloning ${DEPLOY_BRANCH} into ${APP_DIR}"
    rm -rf "${APP_DIR}"
    run_as_deploy git clone --branch "${DEPLOY_BRANCH}" "${REPO_URL}" "${APP_DIR}"
  fi
}

install_python_environment() {
  log "Creating or updating virtual environment"
  run_as_deploy "${PYTHON_BIN}" -m venv "${APP_DIR}/.venv"
  run_as_deploy "${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
  run_as_deploy "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

  log "Running compile checks"
  run_as_deploy bash -lc "cd '${APP_DIR}' && PYTHONPATH=. .venv/bin/python -m compileall apps rob ops"
}

write_env_template_if_missing() {
  local env_file
  env_file="${APP_DIR}/.env"

  if [[ -f "${env_file}" ]]; then
    log "Keeping existing ${env_file}"
    return
  fi

  log "Writing bot .env template to ${env_file}"
  cat > "${env_file}" <<'EOF'
APP_ENV=prod
LOG_LEVEL=INFO
DATABASE_URL=postgresql://prod_rob_bot:replace@replace:25060/rob_dev_v2?sslmode=require
DISCORD_TOKEN=replace
DISCORD_GUILD_ID=replace
BOT_NAME=Rob
ROB_OPS_HOST=127.0.0.1
ROB_OPS_PORT=8811
ROB_OPS_SECRET=replace
# Optional: useful when the bot needs to generate webhook URLs.
THRONE_WEBHOOK_BASE_URL=https://throne.robthebot.com
EOF
  chown "${DEPLOY_USER}:${RUNTIME_GROUP}" "${env_file}"
  chmod 0640 "${env_file}"
}

warn_if_stale_env_values() {
  local env_file="${APP_DIR}/.env"
  [[ -f "${env_file}" ]] || return

  local stale=()
  for token in "dev_rob_bot" "dev_rob_webhook" "rob-dev.barecoding.com"; do
    if grep -Fq "${token}" "${env_file}"; then
      stale+=("${token}")
    fi
  done

  if [[ "${#stale[@]}" -gt 0 ]]; then
    log "WARNING: ${env_file} still contains stale values (${stale[*]})."
    log "WARNING: Update DATABASE_URL to prod_rob_bot on rob_dev_v2 before restarting."
  fi
}

install_service_files() {
  log "Installing systemd unit and deploy symlink"
  install -m 0644 \
    "${APP_DIR}/${SERVICE_SOURCE_REL}" \
    "/etc/systemd/system/${SERVICE_NAME}"
  ln -sfn "${APP_DIR}/${DEPLOY_SCRIPT_SOURCE_REL}" "${DEPLOY_SCRIPT_LINK}"
}

install_sudoers() {
  log "Installing sudoers entry for deploy user ${DEPLOY_USER}"
  cat > "${SUDOERS_PATH}" <<EOF
Cmnd_Alias ROB_BOT_DEPLOY = /bin/systemctl restart ${SERVICE_NAME}, /usr/bin/systemctl restart ${SERVICE_NAME}, /bin/systemctl --no-pager --full status ${SERVICE_NAME}, /usr/bin/systemctl --no-pager --full status ${SERVICE_NAME}
${DEPLOY_USER} ALL=(root) NOPASSWD: ROB_BOT_DEPLOY
EOF
  chmod 0440 "${SUDOERS_PATH}"
  if command -v visudo >/dev/null 2>&1; then
    visudo -cf "${SUDOERS_PATH}" >/dev/null
  fi
}

env_value() {
  local name="$1"
  local env_file="${APP_DIR}/.env"
  local line=""

  if [[ -f "${env_file}" ]]; then
    line="$(grep -E "^${name}=" "${env_file}" | tail -n 1 || true)"
  fi
  line="${line#*=}"
  line="${line%$'\r'}"
  line="${line#\"}"
  line="${line%\"}"
  printf '%s' "${line}"
}

is_real_value() {
  local value="$1"
  [[ -n "${value}" && "${value}" != "replace" && "${value}" != *"replace"* ]]
}

maybe_enable_and_start() {
  local database_url
  local discord_token
  database_url="$(env_value DATABASE_URL)"
  discord_token="$(env_value DISCORD_TOKEN)"

  log "Reloading systemd"
  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}"

  if ! is_real_value "${database_url}" || ! is_real_value "${discord_token}"; then
    log "Skipping service start because DATABASE_URL or DISCORD_TOKEN is still a placeholder."
    return
  fi

  log "Running database check"
  run_as_deploy bash -lc "cd '${APP_DIR}' && set -a && source .env && set +a && PYTHONPATH=. .venv/bin/python -m ops.checks.check_db"

  log "Starting ${SERVICE_NAME}"
  systemctl restart "${SERVICE_NAME}"
  systemctl --no-pager --full status "${SERVICE_NAME}" | sed -n '1,12p'
}

print_summary() {
  cat <<EOF

Bot prod-role rehearsal bootstrap complete.

App root:       ${APP_ROOT}
App dir:        ${APP_DIR}
Deploy user:    ${DEPLOY_USER}
Runtime user:   ${RUNTIME_USER}
Service:        ${SERVICE_NAME}
Deploy script:  ${DEPLOY_SCRIPT_LINK}

Next steps:
  1. Edit ${APP_DIR}/.env with the real PostgreSQL and Discord values.
  2. Keep DATABASE_URL on prod_rob_bot against rob_dev_v2 for rehearsal.
  3. Confirm the bot can connect with the configured token.
  4. Run ${DEPLOY_SCRIPT_LINK} after pushing updates, or restart the service manually once the .env file is ready.
EOF
}

main() {
  ensure_root
  ensure_deploy_user
  ensure_runtime_user
  install_packages
  clone_or_update_repo
  install_python_environment
  write_env_template_if_missing
  warn_if_stale_env_values
  install_service_files
  install_sudoers
  maybe_enable_and_start
  print_summary
}

main "$@"

#!/usr/bin/env bash
set -Eeuo pipefail

# Bootstraps the Rob public read-only API service (api.robthebot.com) on a host.
# Safe to run alongside the webhook service on the same box: it installs a
# separate service, user-owned .env, and port. The public API MUST use the
# SELECT-only prod_rob_public database role — never the webhook/bot writer role.

REPO_URL="${REPO_URL:-https://github.com/foolishbuilder/rob.git}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
APP_ROOT="${APP_ROOT:-/opt/rob-public}"
APP_DIR="${APP_DIR:-${APP_ROOT}/app}"
SERVICE_NAME="${SERVICE_NAME:-rob-public.service}"
SERVICE_SOURCE_REL="${SERVICE_SOURCE_REL:-deploy/systemd/rob-public.service}"
RUNTIME_USER="${RUNTIME_USER:-rob}"
RUNTIME_GROUP="${RUNTIME_GROUP:-rob}"
DEPLOY_USER="${DEPLOY_USER:-${SUDO_USER:-}}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SUDOERS_PATH="${SUDOERS_PATH:-/etc/sudoers.d/rob-public-deploy}"

log() {
  printf '[install-public] %s\n' "$*"
}

warn() {
  printf '[install-public] WARNING: %s\n' "$*" >&2
}

die() {
  printf '[install-public] error: %s\n' "$*" >&2
  exit 1
}

ensure_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
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
  local packages=(
    git
    python3
    python3-venv
    python3-pip
    curl
    ca-certificates
    sudo
  )
  local missing=()
  local package=""

  if ! command -v apt-get >/dev/null 2>&1; then
    die "This installer currently supports Debian/Ubuntu hosts with apt-get."
  fi

  for package in "${packages[@]}"; do
    if ! dpkg -s "${package}" >/dev/null 2>&1; then
      missing+=("${package}")
    fi
  done

  if [[ "${#missing[@]}" -eq 0 ]]; then
    log "Required system packages already installed."
    return
  fi

  log "Installing missing system packages: ${missing[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y "${missing[@]}"
}

clone_or_update_repo() {
  local deploy_group
  deploy_group="$(id -gn "${DEPLOY_USER}")"

  install -d -m 0755 -o "${DEPLOY_USER}" -g "${deploy_group}" "${APP_ROOT}"

  if [[ -d "${APP_DIR}/.git" ]]; then
    log "Updating existing checkout in ${APP_DIR}"
    chown -R "${DEPLOY_USER}:${deploy_group}" "${APP_DIR}"
    run_as_deploy git -C "${APP_DIR}" remote set-url origin "${REPO_URL}"
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
  run_as_deploy "${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
  run_as_deploy "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

  log "Running compile checks"
  run_as_deploy bash -lc "cd '${APP_DIR}' && PYTHONPATH=. .venv/bin/python -m compileall apps rob"
}

write_env_template_if_missing() {
  local env_file
  env_file="${APP_DIR}/.env"

  if [[ -f "${env_file}" ]]; then
    log "Keeping existing ${env_file}"
    return
  fi

  log "Writing public API .env template to ${env_file}"
  cat > "${env_file}" <<'EOF'
APP_ENV=prod
LOG_LEVEL=INFO

# SELECT-only role. NEVER the webhook (prod_rob_webhook) or bot (prod_rob_bot)
# writer role. See db/grants/prod_rob_public.sql.
DATABASE_URL=postgresql://prod_rob_public:replace@replace:25060/rob_prod?sslmode=require

# Public read-only API only. Do not add DISCORD_TOKEN on this host.
PUBLIC_API_HOST=127.0.0.1
PUBLIC_API_PORT=8090
# Use https://robthebot.com in prod; set to * only while testing.
PUBLIC_API_ALLOWED_ORIGIN=https://robthebot.com
EOF
  chown "${DEPLOY_USER}:${RUNTIME_GROUP}" "${env_file}"
  chmod 0640 "${env_file}"
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

warn_if_wrong_role() {
  local env_file="${APP_DIR}/.env"
  [[ -f "${env_file}" ]] || return
  local dsn
  dsn="$(env_value DATABASE_URL)"

  # The whole point of this service is a SELECT-only role. Loudly flag a writer.
  if [[ "${dsn}" == *rob_webhook* || "${dsn}" == *rob_bot* ]]; then
    warn "DATABASE_URL looks like a WRITER role (contains rob_webhook/rob_bot)."
    warn "The public API must connect as the SELECT-only prod_rob_public role."
    warn "Fix ${env_file} before this service is exposed publicly."
  fi
  if grep -Eq 'rob-dev\.barecoding\.com|rob_dev_v2' "${env_file}"; then
    warn "Existing .env appears to contain old rehearsal values; update to rob_prod."
  fi
}

install_service_files() {
  log "Installing systemd unit"
  install -m 0644 \
    "${APP_DIR}/${SERVICE_SOURCE_REL}" \
    "/etc/systemd/system/${SERVICE_NAME}"
}

install_sudoers() {
  log "Installing sudoers entry so ${DEPLOY_USER} can restart ${SERVICE_NAME}"
  cat > "${SUDOERS_PATH}" <<EOF
Cmnd_Alias ROB_PUBLIC_DEPLOY = /bin/systemctl restart ${SERVICE_NAME}, /usr/bin/systemctl restart ${SERVICE_NAME}
${DEPLOY_USER} ALL=(root) NOPASSWD: ROB_PUBLIC_DEPLOY
EOF
  chmod 0440 "${SUDOERS_PATH}"
  if command -v visudo >/dev/null 2>&1; then
    visudo -cf "${SUDOERS_PATH}" >/dev/null
  fi
}

verify_service() {
  local port base code i attempts
  port="$(env_value PUBLIC_API_PORT)"
  [[ -n "${port}" ]] || port="8090"
  base="http://127.0.0.1:${port}"

  log "Waiting for ${SERVICE_NAME} to answer on ${base}/health"
  attempts=10
  i=0
  code=""
  while (( i < attempts )); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "${base}/health" || true)"
    [[ "${code}" == "200" ]] && break
    i=$((i + 1))
    sleep 1
  done
  if [[ "${code}" != "200" ]]; then
    die "Health check failed (last HTTP ${code:-none}). Inspect: journalctl -u ${SERVICE_NAME} -n 50"
  fi
  log "Health OK (${base}/health)"

  # Missing username must be a clean 400 (routing + handler are live).
  code="$(curl -s -o /dev/null -w '%{http_code}' "${base}/public/sends" || true)"
  [[ "${code}" == "400" ]] || warn "Expected HTTP 400 for missing username, got ${code}."

  # A random username should be a 404 — which proves the SELECT query actually
  # runs under the read-only role. A 500 here means the grants are wrong.
  code="$(curl -s -o /dev/null -w '%{http_code}' "${base}/public/sends?username=__install_smoke_test__" || true)"
  case "${code}" in
    404) log "Read query path OK (404 for unknown username)." ;;
    200) log "Read query path OK (a real send happened to match the smoke username)." ;;
    *) warn "Unexpected HTTP ${code} from /public/sends. The SELECT may be failing — check db/grants/prod_rob_public.sql and journalctl -u ${SERVICE_NAME}." ;;
  esac

  # guild-summary always returns 200; a 500 here means the dommes/subs grants
  # are missing.
  code="$(curl -s -o /dev/null -w '%{http_code}' "${base}/public/guild-summary" || true)"
  [[ "${code}" == "200" ]] || warn "Unexpected HTTP ${code} from /public/guild-summary. Check the sends/dommes/subs SELECT grants."
}

maybe_enable_and_start() {
  local database_url
  database_url="$(env_value DATABASE_URL)"

  log "Reloading systemd"
  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}"

  if ! is_real_value "${database_url}"; then
    log "Skipping service start because DATABASE_URL is still a placeholder."
    return
  fi

  log "Starting ${SERVICE_NAME}"
  systemctl restart "${SERVICE_NAME}"
  verify_service
}

print_summary() {
  local port
  port="$(env_value PUBLIC_API_PORT)"
  [[ -n "${port}" ]] || port="8090"
  cat <<EOF

Public API bootstrap complete.

App root:      ${APP_ROOT}
App dir:       ${APP_DIR}
Deploy user:   ${DEPLOY_USER}
Runtime user:  ${RUNTIME_USER}
Service:       ${SERVICE_NAME}

Before this service can serve traffic:
  1. Create the SELECT-only DB role (once, as doadmin):
       CREATE ROLE prod_rob_public LOGIN PASSWORD '<strong-password>';
       psql "<admin-dsn-to-rob_prod>" -f ${APP_DIR}/db/grants/prod_rob_public.sql
  2. Edit ${APP_DIR}/.env: set DATABASE_URL to the prod_rob_public role and
     PUBLIC_API_ALLOWED_ORIGIN to https://robthebot.com (or * while testing).
  3. Re-run this installer (or: sudo systemctl restart ${SERVICE_NAME}) once
     the .env is real — it will start the service and verify /health.
  4. Route api.robthebot.com -> http://127.0.0.1:${port}
     via Cloudflare Tunnel. Keep the port local; do not expose it directly.

See docs/public-api.md for the full contract and guarantees.
EOF
}

main() {
  ensure_root
  ensure_cmd systemctl
  ensure_cmd curl
  ensure_deploy_user
  ensure_runtime_user
  install_packages
  clone_or_update_repo
  install_python_environment
  write_env_template_if_missing
  warn_if_wrong_role
  install_service_files
  install_sudoers
  maybe_enable_and_start
  print_summary
}

main "$@"

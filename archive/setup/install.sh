#!/usr/bin/env bash
# install.sh — Rob the Bot server installer
# Usage: sudo bash install.sh
set -euo pipefail

REPO_URL="https://github.com/notpatdev/rob.git"
APP_ROOT="/opt/rob"
APP_DIR="${APP_ROOT}/app"
DATA_DIR="${APP_ROOT}/data"
SERVICE_NAME="rob-bot"
SERVICE_FILE="rob-bot.service"
RUNTIME_USER="robbot"
DEPLOY_USER="robdeploy"
PYTHON_BIN=""
DEPLOY_HOME=""
DEPLOY_GROUP=""
SYSTEMCTL_BIN="$(command -v systemctl)"

if [[ -t 1 ]]; then
  BOLD="$(printf '\033[1m')"
  GREEN="$(printf '\033[32m')"
  YELLOW="$(printf '\033[33m')"
  CYAN="$(printf '\033[36m')"
  RESET="$(printf '\033[0m')"
else
  BOLD="" GREEN="" YELLOW="" CYAN="" RESET=""
fi

step()    { printf '%s ▶%s %s\n' "${BOLD}" "${RESET}" "$*"; }
success() { printf '%s ✔%s %s\n' "${GREEN}" "${RESET}" "$*"; }
warn()    { printf '%s !%s %s\n' "${YELLOW}" "${RESET}" "$*"; }
die()     { printf '%s ✖ error:%s %s\n' "${YELLOW}" "${RESET}" "$*" >&2; exit 1; }
section() { printf '\n%s══ %s ══%s\n' "${CYAN}" "$*" "${RESET}"; }

prompt_secret() {
  local label="$1" value=""
  while true; do
    read -r -s -p "${label}: " value; echo
    if [[ -n "${value}" && "${value}" =~ ^[A-Za-z0-9._/-]+$ ]]; then
      printf '%s' "${value}"; return
    fi
    warn "Value must not be empty or contain spaces / special characters."
  done
}

write_env_line() {
  local name="$1" value="$2"
  local escaped="${value//\\/\\\\}"
  escaped="${escaped//\"/\\\"}"
  printf '%s="%s"\n' "${name}" "${escaped}"
}

run_as_deploy() { runuser -u "${DEPLOY_USER}" -- "$@"; }

# ── Pre-flight ────────────────────────────────────────────────────────────────
if [[ "${EUID}" -ne 0 || -z "${SUDO_USER:-}" || "${SUDO_USER}" == "root" ]]; then
  die "Run with sudo from your normal admin user:  sudo bash install.sh"
fi
if ! command -v apt-get >/dev/null 2>&1; then
  die "This installer only supports Debian/Ubuntu (apt-get required)."
fi

section "Rob the Bot — installer"
warn "Installing to ${APP_ROOT} — running as ${RUNTIME_USER}."
warn "Discord IDs now come from bot/channels.py, and event windows come from config/events.json."

# ── 1. System packages ────────────────────────────────────────────────────────
step "1/7  System packages"
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip openssh-client rsync software-properties-common >/dev/null 2>&1

if ! command -v python3.11 >/dev/null 2>&1; then
  if [[ -r /etc/os-release ]]; then
    # shellcheck source=/dev/null
    . /etc/os-release
    if [[ "${ID:-}" == "ubuntu" ]]; then
      add-apt-repository -y ppa:deadsnakes/ppa >/dev/null 2>&1
      apt-get update -qq
    fi
  fi
fi

apt-get install -y -qq python3.11 python3.11-venv >/dev/null 2>&1
PYTHON_BIN="$(command -v python3.11)"

if ! "${PYTHON_BIN}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)'; then
  die "Python 3.11 or newer is required."
fi
success "System packages ready"

# ── 2. Users & directories ────────────────────────────────────────────────────
step "2/7  Users and directories"
if ! getent group "${RUNTIME_USER}" >/dev/null 2>&1; then
  groupadd --system "${RUNTIME_USER}"
fi
if ! id "${RUNTIME_USER}" >/dev/null 2>&1; then
  useradd --system --gid "${RUNTIME_USER}" --home-dir "${APP_ROOT}" --shell /usr/sbin/nologin "${RUNTIME_USER}"
fi
if ! id "${DEPLOY_USER}" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "${DEPLOY_USER}"
fi

DEPLOY_HOME="$(getent passwd "${DEPLOY_USER}" | cut -d: -f6)"
DEPLOY_GROUP="$(id -gn "${DEPLOY_USER}")"
mkdir -p "${APP_ROOT}" "${DATA_DIR}"
chown "${DEPLOY_USER}:${DEPLOY_GROUP}" "${APP_ROOT}"
chown "${RUNTIME_USER}:${RUNTIME_USER}" "${DATA_DIR}"
chmod 755 "${APP_ROOT}" "${DATA_DIR}"
success "Users and directories ready"

# ── 3. Repository ─────────────────────────────────────────────────────────────
step "3/7  Cloning / updating repository"
if [[ -d "${APP_DIR}/.git" ]]; then
  chown -R "${DEPLOY_USER}:${DEPLOY_GROUP}" "${APP_DIR}"
  run_as_deploy git -C "${APP_DIR}" remote set-url origin "${REPO_URL}" 2>/dev/null
  run_as_deploy git -C "${APP_DIR}" fetch -q origin main
  run_as_deploy git -C "${APP_DIR}" switch -q main 2>/dev/null || true
  run_as_deploy git -C "${APP_DIR}" pull -q --ff-only origin main
else
  rm -rf "${APP_DIR}"
  run_as_deploy git clone -q "${REPO_URL}" "${APP_DIR}"
fi
success "Repository ready at ${APP_DIR}"

# ── 4. Virtual environment ────────────────────────────────────────────────────
step "4/7  Python virtual environment"
chown -R "${DEPLOY_USER}:${DEPLOY_GROUP}" "${APP_DIR}"
run_as_deploy "${PYTHON_BIN}" -m venv "${APP_DIR}/.venv"
run_as_deploy "${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip -q --disable-pip-version-check
run_as_deploy "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt" -q --disable-pip-version-check
success "Virtual environment ready"

# ── 5. Environment file ───────────────────────────────────────────────────────
step "5/7  Bot configuration"
echo
DISCORD_TOKEN="$(prompt_secret "DISCORD_TOKEN (paste, hidden)")"
echo

{
  write_env_line "DISCORD_TOKEN"               "${DISCORD_TOKEN}"
  write_env_line "DATABASE_PATH"               "${DATA_DIR}/rob_the_bot.sqlite3"
  write_env_line "THRONE_POLL_INTERVAL_SECONDS" "60"
} > "${APP_DIR}/.env"

chmod 600 "${APP_DIR}/.env"
chown "${RUNTIME_USER}:${RUNTIME_USER}" "${APP_DIR}/.env"
chown -R "${DEPLOY_USER}:${DEPLOY_GROUP}" "${APP_DIR}"
chown -R "${RUNTIME_USER}:${RUNTIME_USER}" "${DATA_DIR}"
success "Environment file written"

# ── 6. Deploy user + SSH key ─────────────────────────────────────────────────
step "6/7  Deploy user SSH key and sudo"
install -d -m 700 -o "${DEPLOY_USER}" -g "${DEPLOY_GROUP}" "${DEPLOY_HOME}/.ssh"

# Generate a dedicated Ed25519 deploy key (no passphrase — intended for
# automated CI/CD use only; the private key must be kept secret).
DEPLOY_KEY_FILE="${DEPLOY_HOME}/.ssh/rob_deploy_ed25519"
if [[ ! -f "${DEPLOY_KEY_FILE}" ]]; then
  ssh-keygen -q -t ed25519 -f "${DEPLOY_KEY_FILE}" -N "" -C "rob-the-bot-deploy"
  chown "${DEPLOY_USER}:${DEPLOY_GROUP}" "${DEPLOY_KEY_FILE}" "${DEPLOY_KEY_FILE}.pub"
  chmod 600 "${DEPLOY_KEY_FILE}"
  chmod 644 "${DEPLOY_KEY_FILE}.pub"
fi

# Add the public key to the deploy user's authorised_keys
touch "${DEPLOY_HOME}/.ssh/authorized_keys"
chown "${DEPLOY_USER}:${DEPLOY_GROUP}" "${DEPLOY_HOME}/.ssh/authorized_keys"
chmod 600 "${DEPLOY_HOME}/.ssh/authorized_keys"
if ! grep -qF "$(cat "${DEPLOY_KEY_FILE}.pub")" "${DEPLOY_HOME}/.ssh/authorized_keys" 2>/dev/null; then
  cat "${DEPLOY_KEY_FILE}.pub" >> "${DEPLOY_HOME}/.ssh/authorized_keys"
fi

# Sudo permissions for the deploy user
cat > "/etc/sudoers.d/${SERVICE_NAME}-deploy" <<SUDOERS
${DEPLOY_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL_BIN} stop ${SERVICE_NAME}, ${SYSTEMCTL_BIN} start ${SERVICE_NAME}, ${SYSTEMCTL_BIN} restart ${SERVICE_NAME}, ${SYSTEMCTL_BIN} status ${SERVICE_NAME}, ${SYSTEMCTL_BIN} daemon-reload
SUDOERS
chmod 440 "/etc/sudoers.d/${SERVICE_NAME}-deploy"
visudo -cf "/etc/sudoers.d/${SERVICE_NAME}-deploy" >/dev/null
success "Deploy user and SSH key ready"

# ── 7. Systemd service ────────────────────────────────────────────────────────
step "7/7  Systemd service"
install -m 0644 "${APP_DIR}/${SERVICE_FILE}" "/etc/systemd/system/${SERVICE_NAME}.service"
"${SYSTEMCTL_BIN}" daemon-reload -q
"${SYSTEMCTL_BIN}" enable -q --now "${SERVICE_NAME}"
success "Service installed and started"

# ── Done — show GitHub Actions secrets ────────────────────────────────────────
SERVER_IP="$(hostname -I | awk '{print $1}')"
KNOWN_HOSTS_LINE="$(ssh-keyscan -H "${SERVER_IP}" 2>/dev/null || true)"

section "Install complete"
echo
printf '%sService commands%s\n' "${BOLD}" "${RESET}"
echo "  sudo systemctl status ${SERVICE_NAME}"
echo "  sudo systemctl restart ${SERVICE_NAME}"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
echo
printf '%sNext steps%s\n' "${BOLD}" "${RESET}"
echo "  1. Put your Discord IDs in bot/channels.py."
echo "  2. Set event windows and themes in config/events.json."
echo "  3. Add the GitHub Actions secrets below to your repository."
echo "  4. Push to main or run the Deploy workflow manually."
echo
section "GitHub Actions secrets"
printf '%sGo to:%s  https://github.com/notpatdev/rob-the-bot/settings/secrets/actions\n' "${CYAN}" "${RESET}"
echo
printf '%s%s%s\n' "${BOLD}" "DEPLOY_SSH_KEY" "${RESET}"
echo "  The deploy private key is at: ${DEPLOY_KEY_FILE}"
echo "  Upload it WITHOUT printing it to this terminal (it must stay secret):"
echo "    gh secret set DEPLOY_SSH_KEY < ${DEPLOY_KEY_FILE}"
echo "  Avoid 'cat'-ing the private key into shared terminals, logs, or screen shares."
echo
printf '%s%s%s\n' "${BOLD}" "DEPLOY_KNOWN_HOSTS" "${RESET}"
printf '%s\n' "${KNOWN_HOSTS_LINE}"
echo
printf '%s%s%s\n' "${BOLD}" "DEPLOY_HOST" "${RESET}"
printf '  %s\n' "${SERVER_IP}"
echo
printf '%s%s%s\n' "${BOLD}" "DEPLOY_PORT" "${RESET}"
printf '  22\n'
echo
printf '%s%s%s\n' "${BOLD}" "DEPLOY_USER" "${RESET}"
printf '  %s\n' "${DEPLOY_USER}"
echo
warn "Keep the private key secret. Deploys now sync files over SSH and restart the service on the box."

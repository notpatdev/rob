#!/usr/bin/env bash
set -Eeuo pipefail

# ---------------------------------------------------------------------------
# rob fix-me — prepare ONE host to run both the bot and the webhook backend.
#
# Rob is two services that share a remote PostgreSQL DB: the Discord bot (with
# the ops bridge on :8811) and the Throne webhook receiver (:8080, which
# notifies the bot over ROB_BOT_NOTIFY_URL). This script reconciles both .env
# files so the two services talk over loopback on a single host, installs both
# systemd units, and verifies the result. The database stays remote — fix-me
# never touches DATABASE_URL.
#
# Safe to re-run. Each run backs up the .env files it edits and prints a diff.
#
# Usage:
#   sudo bash deploy/scripts/fix-me.sh [options]
#
# Options:
#   --bot-dir DIR        Bot app dir       (default: /opt/rob-bot/app)
#   --webhook-dir DIR    Webhook app dir   (default: /opt/rob-webhook/app)
#   --rotate-secret      Generate a fresh shared ROB_OPS_SECRET
#   --dry-run            Show planned changes, write nothing
#   --yes                Don't prompt for confirmation
#   -h, --help           Show this help
# ---------------------------------------------------------------------------

BOT_DIR="${BOT_DIR:-/opt/rob-bot/app}"
WEBHOOK_DIR="${WEBHOOK_DIR:-/opt/rob-webhook/app}"
BOT_SERVICE="${BOT_SERVICE:-rob-bot.service}"
WEBHOOK_SERVICE="${WEBHOOK_SERVICE:-rob-webhook.service}"
OPS_HOST="127.0.0.1"
OPS_PORT="8811"
WEBHOOK_HOST="127.0.0.1"
DEFAULT_WEBHOOK_PORT="8080"

DRY_RUN="false"
ASSUME_YES="false"
ROTATE_SECRET="false"

log() {
  printf '[fix-me] %s\n' "$*"
}

warn() {
  printf '[fix-me] WARNING: %s\n' "$*" >&2
}

die() {
  printf '[fix-me] error: %s\n' "$*" >&2
  exit 1
}

usage() {
  sed -n '4,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

parse_args() {
  while (($#)); do
    case "$1" in
      --bot-dir) shift; [[ $# -gt 0 ]] || die "--bot-dir needs a value"; BOT_DIR="$1" ;;
      --bot-dir=*) BOT_DIR="${1#*=}" ;;
      --webhook-dir) shift; [[ $# -gt 0 ]] || die "--webhook-dir needs a value"; WEBHOOK_DIR="$1" ;;
      --webhook-dir=*) WEBHOOK_DIR="${1#*=}" ;;
      --rotate-secret) ROTATE_SECRET="true" ;;
      --dry-run) DRY_RUN="true" ;;
      --yes|-y) ASSUME_YES="true" ;;
      -h|--help) usage; exit 0 ;;
      *) die "unknown option: $1 (try --help)" ;;
    esac
    shift || true
  done
}

# -- env file helpers -------------------------------------------------------

read_env_var() {
  local file="$1" key="$2" line=""
  [[ -f "${file}" ]] || { printf ''; return; }
  line="$(grep -E "^${key}=" "${file}" | tail -n 1 || true)"
  line="${line#*=}"
  line="${line%$'\r'}"
  line="${line#\"}"; line="${line%\"}"
  line="${line#\'}"; line="${line%\'}"
  printf '%s' "${line}"
}

# upsert_env_var FILE KEY VALUE — replace the first KEY= line in place, or
# append KEY=VALUE if absent. Comment lines (#KEY=) are left untouched.
upsert_env_var() {
  local file="$1" key="$2" value="$3" tmp
  tmp="$(mktemp)"
  KEY="${key}" VALUE="${value}" awk '
    BEGIN { key = ENVIRON["KEY"]; value = ENVIRON["VALUE"]; done = 0 }
    {
      if (!done && $0 ~ "^" key "=") { print key "=" value; done = 1; next }
      print
    }
    END { if (!done) print key "=" value }
  ' "${file}" > "${tmp}"
  mv "${tmp}" "${file}"
}

generate_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets; print(secrets.token_hex(32))'
  else
    die "Need openssl or python3 to generate ROB_OPS_SECRET."
  fi
}

is_placeholder() {
  local value="$1"
  [[ -z "${value}" || "${value}" == "replace" || "${value}" == *replace* ]]
}

# -- planning ---------------------------------------------------------------

resolve_shared_secret() {
  local bot_secret webhook_secret
  bot_secret="$(read_env_var "${BOT_DIR}/.env" ROB_OPS_SECRET)"
  webhook_secret="$(read_env_var "${WEBHOOK_DIR}/.env" ROB_OPS_SECRET)"

  # NOTE: this function's stdout is captured via command substitution, so all
  # human-facing logging here must go to stderr — only the secret prints to stdout.
  if [[ "${ROTATE_SECRET}" == "true" ]]; then
    log "Rotating ROB_OPS_SECRET (new shared value for both services)." >&2
    generate_secret
    return
  fi
  if ! is_placeholder "${bot_secret}"; then
    printf '%s' "${bot_secret}"
    return
  fi
  if ! is_placeholder "${webhook_secret}"; then
    printf '%s' "${webhook_secret}"
    return
  fi
  log "No usable ROB_OPS_SECRET found; generating a shared one." >&2
  generate_secret
}

apply_env_changes() {
  local file="$1"; shift
  # remaining args are KEY=VALUE pairs
  local backup
  if [[ "${DRY_RUN}" == "true" ]]; then
    log "DRY-RUN ${file} would set:"
    local pair
    for pair in "$@"; do
      printf '          %s\n' "${pair%%=*}=${pair#*=}"
    done
    return
  fi
  backup="${file}.bak.$(date +%Y%m%d-%H%M%S)"
  cp "${file}" "${backup}"
  local pair key value
  for pair in "$@"; do
    key="${pair%%=*}"
    value="${pair#*=}"
    upsert_env_var "${file}" "${key}" "${value}"
  done
  log "Updated ${file} (backup: ${backup})"
  if command -v diff >/dev/null 2>&1; then
    diff -u "${backup}" "${file}" || true
  fi
}

warn_about_database_urls() {
  local bot_db webhook_db
  bot_db="$(read_env_var "${BOT_DIR}/.env" DATABASE_URL)"
  webhook_db="$(read_env_var "${WEBHOOK_DIR}/.env" DATABASE_URL)"
  if is_placeholder "${bot_db}"; then
    warn "Bot DATABASE_URL is empty/placeholder — set it before starting the bot."
  elif [[ "${bot_db}" != *_bot:* && "${bot_db}" != *rob_bot* ]]; then
    warn "Bot DATABASE_URL does not look like the prod_rob_bot user."
  fi
  if is_placeholder "${webhook_db}"; then
    warn "Webhook DATABASE_URL is empty/placeholder — set it before starting the webhook."
  elif [[ "${webhook_db}" != *_webhook:* && "${webhook_db}" != *rob_webhook* ]]; then
    warn "Webhook DATABASE_URL does not look like the prod_rob_webhook user."
  fi
}

# -- systemd + verification (root only) -------------------------------------

install_units() {
  if [[ "${EUID}" -ne 0 ]] || ! command -v systemctl >/dev/null 2>&1; then
    log "Skipping systemd install (need root + systemctl). Install units manually:"
    log "  sudo cp ${BOT_DIR}/deploy/systemd/${BOT_SERVICE} /etc/systemd/system/"
    log "  sudo cp ${WEBHOOK_DIR}/deploy/systemd/${WEBHOOK_SERVICE} /etc/systemd/system/"
    log "  sudo systemctl daemon-reload && sudo systemctl enable ${BOT_SERVICE} ${WEBHOOK_SERVICE}"
    return
  fi
  local bot_unit="${BOT_DIR}/deploy/systemd/${BOT_SERVICE}"
  local webhook_unit="${WEBHOOK_DIR}/deploy/systemd/${WEBHOOK_SERVICE}"
  if [[ "${DRY_RUN}" == "true" ]]; then
    log "DRY-RUN would install + enable ${BOT_SERVICE} and ${WEBHOOK_SERVICE}."
    return
  fi
  [[ -f "${bot_unit}" ]] && install -m 0644 "${bot_unit}" "/etc/systemd/system/${BOT_SERVICE}" || warn "Bot unit not found at ${bot_unit}"
  [[ -f "${webhook_unit}" ]] && install -m 0644 "${webhook_unit}" "/etc/systemd/system/${WEBHOOK_SERVICE}" || warn "Webhook unit not found at ${webhook_unit}"
  systemctl daemon-reload
  systemctl enable "${BOT_SERVICE}" "${WEBHOOK_SERVICE}" >/dev/null 2>&1 || true
  log "Installed and enabled ${BOT_SERVICE} + ${WEBHOOK_SERVICE}."
}

run_db_checks() {
  [[ "${DRY_RUN}" == "true" ]] && return
  local dir profile
  for entry in "${BOT_DIR}:bot" "${WEBHOOK_DIR}:webhook"; do
    dir="${entry%:*}"; profile="${entry#*:}"
    if [[ -x "${dir}/.venv/bin/python" && -f "${dir}/.env" ]]; then
      log "Running DB check (${profile}) in ${dir}"
      ( cd "${dir}" && set -a && . ./.env && set +a \
        && ROB_CHECK_DB_PROFILE="${profile}" PYTHONPATH=. .venv/bin/python -m ops.checks.check_db ) \
        || warn "DB check (${profile}) failed — review the output above."
    else
      log "Skipping DB check (${profile}); no venv/.env in ${dir}."
    fi
  done
}

restart_and_health_check() {
  if [[ "${EUID}" -ne 0 ]] || ! command -v systemctl >/dev/null 2>&1 || [[ "${DRY_RUN}" == "true" ]]; then
    return
  fi
  log "Restarting services"
  systemctl restart "${BOT_SERVICE}" "${WEBHOOK_SERVICE}" || warn "Service restart reported an error."
  systemctl is-active "${BOT_SERVICE}" "${WEBHOOK_SERVICE}" || true

  if command -v curl >/dev/null 2>&1; then
    local secret
    secret="$(read_env_var "${BOT_DIR}/.env" ROB_OPS_SECRET)"
    log "Checking bot ops health (http://${OPS_HOST}:${OPS_PORT}/health)"
    curl -fsS --max-time 5 -H "X-Rob-Ops-Secret: ${secret}" \
      "http://${OPS_HOST}:${OPS_PORT}/health" && printf '\n' || warn "Bot ops health check failed."
    log "Checking webhook health (http://${WEBHOOK_HOST}:$(read_env_var "${WEBHOOK_DIR}/.env" THRONE_WEBHOOK_PORT)/health)"
    curl -fsS --max-time 5 \
      "http://${WEBHOOK_HOST}:$(read_env_var "${WEBHOOK_DIR}/.env" THRONE_WEBHOOK_PORT)/health" && printf '\n' \
      || warn "Webhook health check failed."
  fi
}

confirm() {
  [[ "${ASSUME_YES}" == "true" || "${DRY_RUN}" == "true" ]] && return 0
  if [[ ! -t 0 ]]; then
    die "Refusing to edit files non-interactively without --yes (or use --dry-run)."
  fi
  local reply
  read -r -p "[fix-me] Apply these changes to the .env files and services? [y/N] " reply
  [[ "${reply}" =~ ^[Yy]$ ]] || die "Aborted by user."
}

print_summary() {
  local dry_suffix=""
  [[ "${DRY_RUN}" == "true" ]] && dry_suffix=" (dry-run — nothing written)"
  cat <<EOF

[fix-me] Single-host consolidation complete${dry_suffix}.

  Bot app dir:      ${BOT_DIR}      (ops bridge ${OPS_HOST}:${OPS_PORT})
  Webhook app dir:  ${WEBHOOK_DIR}  (receiver ${WEBHOOK_HOST}:$(read_env_var "${WEBHOOK_DIR}/.env" THRONE_WEBHOOK_PORT))
  Webhook → bot:    http://${OPS_HOST}:${OPS_PORT}/ops/sends/process (shared ROB_OPS_SECRET)
  Database:         left untouched (stays remote / separate)

Next steps:
  1. Confirm DATABASE_URL in each .env (bot → prod_rob_bot, webhook → prod_rob_webhook).
  2. Keep the public Throne URL reaching the webhook (e.g. cloudflared → 127.0.0.1:${DEFAULT_WEBHOOK_PORT}).
  3. Leaderboard access role (test guild): create the role, set the #leaderboard
     channel to be visible only to it, and make sure Rob has "Manage Roles" and
     ranks ABOVE that role so it can assign it. Then run:
         rob scan && rob auto-apply roles
  4. Verify any time with:  rob status
EOF
}

main() {
  parse_args "$@"

  [[ -d "${BOT_DIR}" ]] || die "Bot app dir not found: ${BOT_DIR} (run install-bot.sh first, or pass --bot-dir)."
  [[ -d "${WEBHOOK_DIR}" ]] || die "Webhook app dir not found: ${WEBHOOK_DIR} (run install-webhook.sh first, or pass --webhook-dir)."
  [[ -f "${BOT_DIR}/.env" ]] || die "Missing ${BOT_DIR}/.env — run install-bot.sh first."
  [[ -f "${WEBHOOK_DIR}/.env" ]] || die "Missing ${WEBHOOK_DIR}/.env — run install-webhook.sh first."

  local secret webhook_port
  secret="$(resolve_shared_secret)"
  webhook_port="$(read_env_var "${WEBHOOK_DIR}/.env" THRONE_WEBHOOK_PORT)"
  [[ -n "${webhook_port}" ]] || webhook_port="${DEFAULT_WEBHOOK_PORT}"

  log "Plan: bot and webhook on one host, talking over loopback; DB untouched."
  confirm

  apply_env_changes "${BOT_DIR}/.env" \
    "ROB_OPS_HOST=${OPS_HOST}" \
    "ROB_OPS_PORT=${OPS_PORT}" \
    "ROB_OPS_SECRET=${secret}"

  apply_env_changes "${WEBHOOK_DIR}/.env" \
    "ROB_OPS_SECRET=${secret}" \
    "ROB_BOT_NOTIFY_URL=http://${OPS_HOST}:${OPS_PORT}/ops/sends/process" \
    "THRONE_WEBHOOK_HOST=${WEBHOOK_HOST}" \
    "THRONE_WEBHOOK_PORT=${webhook_port}"

  warn_about_database_urls
  install_units
  run_db_checks
  restart_and_health_check
  print_summary
}

main "$@"

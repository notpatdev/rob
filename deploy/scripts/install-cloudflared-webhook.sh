#!/usr/bin/env bash
set -Eeuo pipefail

WEBHOOK_ENV_FILE="${WEBHOOK_ENV_FILE:-/opt/rob-webhook/app/.env}"
PUBLIC_HOSTNAME="${PUBLIC_HOSTNAME:-throne.robthebot.com}"
ORIGIN_URL="${ORIGIN_URL:-http://127.0.0.1:8080}"
CONFIG_FILE="${CONFIG_FILE:-/etc/cloudflared/config.yml}"
CLOUDFLARED_CONFIG="${CONFIG_FILE}"
TUNNEL_NAME="${TUNNEL_NAME:-rob-webhook}"
TUNNEL_ID="${TUNNEL_ID:-}"
CREDENTIALS_FILE="${CREDENTIALS_FILE:-/etc/cloudflared/rob-webhook.json}"
SOURCE_CREDENTIALS_FILE="${SOURCE_CREDENTIALS_FILE:-}"
LOGIN_USER="${LOGIN_USER:-${SUDO_USER:-root}}"
SKIP_DNS_ROUTE="${SKIP_DNS_ROUTE:-}"

log() {
  printf '[install-cloudflared-webhook] %s\n' "$*"
}

warn() {
  printf '[install-cloudflared-webhook] WARNING: %s\n' "$*" >&2
}

die() {
  printf '[install-cloudflared-webhook] error: %s\n' "$*" >&2
  exit 1
}

backup_existing_config() {
  if [[ -f "${CLOUDFLARED_CONFIG}" ]]; then
    local backup_path
    backup_path="${CLOUDFLARED_CONFIG}.bak-$(date +%Y%m%d-%H%M%S)"
    log "Backing up existing ${CLOUDFLARED_CONFIG} to ${backup_path}"
    cp -a "${CLOUDFLARED_CONFIG}" "${backup_path}"
  fi
}

ensure_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "Run this script with sudo or as root."
  fi
}

install_cloudflared() {
  if command -v cloudflared >/dev/null 2>&1; then
    log "cloudflared is already installed."
    return
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    die "cloudflared is not installed and apt-get is unavailable. Install cloudflared manually first."
  fi

  log "Installing cloudflared from the official Cloudflare apt repository."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y curl ca-certificates gnupg lsb-release
  install -d -m 0755 /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
    | gpg --dearmor \
    | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
  local codename
  codename="$(lsb_release -cs)"
  cat > /etc/apt/sources.list.d/cloudflared.list <<EOF
deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared ${codename} main
EOF
  apt-get update -y
  apt-get install -y cloudflared
}

warn_webhook_env_values() {
  if [[ ! -f "${WEBHOOK_ENV_FILE}" ]]; then
    warn "${WEBHOOK_ENV_FILE} not found; skipping webhook env checks."
    return
  fi

  local base_url
  base_url="$(grep -E '^THRONE_WEBHOOK_BASE_URL=' "${WEBHOOK_ENV_FILE}" | tail -n 1 | cut -d= -f2- || true)"
  base_url="${base_url#\"}"
  base_url="${base_url%\"}"

  if [[ -n "${base_url}" && "${base_url}" != "https://throne.robthebot.com" ]]; then
    warn "THRONE_WEBHOOK_BASE_URL is '${base_url}' but should be https://throne.robthebot.com."
  fi

  if grep -E '^DISCORD_TOKEN=' "${WEBHOOK_ENV_FILE}" >/dev/null 2>&1; then
    warn "DISCORD_TOKEN is present in webhook .env; this host should stay webhook-only."
  fi
}

login_home() {
  getent passwd "${LOGIN_USER}" | cut -d: -f6
}

cloudflared_run() {
  local user_home
  user_home="$(login_home)"
  HOME="${user_home}" cloudflared "$@"
}

origin_cert_path() {
  local user_home cert_file

  if [[ -n "${TUNNEL_ORIGIN_CERT:-}" ]]; then
    if [[ -f "${TUNNEL_ORIGIN_CERT}" ]]; then
      printf '%s\n' "${TUNNEL_ORIGIN_CERT}"
      return
    fi
    warn "TUNNEL_ORIGIN_CERT is set but the file does not exist: ${TUNNEL_ORIGIN_CERT}"
    return
  fi

  user_home="$(login_home)"
  cert_file="${user_home}/.cloudflared/cert.pem"
  if [[ -f "${cert_file}" ]]; then
    printf '%s\n' "${cert_file}"
    return
  fi

  if [[ -f "/etc/cloudflared/cert.pem" ]]; then
    printf '%s\n' "/etc/cloudflared/cert.pem"
  fi
}

read_tunnel_id_from_credentials() {
  local credentials_file="$1"
  python3 - <<'PY' "${credentials_file}"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    print("")
    raise SystemExit(0)

try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)

value = str(payload.get("TunnelID", "")).strip()
print(value)
PY
}

ensure_login_certificate() {
  local user_home cert_file
  user_home="$(login_home)"
  cert_file="${user_home}/.cloudflared/cert.pem"

  if [[ -f "${cert_file}" ]]; then
    log "Found existing Cloudflare login certificate for ${LOGIN_USER}."
    return
  fi

  log "Starting Cloudflare named-tunnel login for ${LOGIN_USER}."
  log "Complete the browser-based login when cloudflared prints the authorization URL."
  cloudflared_run tunnel login

  if [[ ! -f "${cert_file}" ]]; then
    die "cloudflared tunnel login did not create ${cert_file}."
  fi
}

discover_existing_tunnel_id() {
  local json_output
  json_output="$(cloudflared_run tunnel list --output json 2>/dev/null || true)"
  if [[ -z "${json_output}" ]]; then
    return 0
  fi
  python3 - <<'PY' "${TUNNEL_NAME}" "${json_output}"
import json
import sys

name = sys.argv[1]
payload = sys.argv[2]
try:
    tunnels = json.loads(payload)
except json.JSONDecodeError:
    print("")
    raise SystemExit(0)

for tunnel in tunnels:
    if str(tunnel.get("name", "")).strip() == name:
        print(str(tunnel.get("id", "")).strip())
        break
else:
    print("")
PY
}

route_dns_if_possible() {
  local cert_path lowered_skip
  lowered_skip="$(printf '%s' "${SKIP_DNS_ROUTE}" | tr '[:upper:]' '[:lower:]')"
  if [[ "${lowered_skip}" == "1" || "${lowered_skip}" == "true" || "${lowered_skip}" == "yes" || "${lowered_skip}" == "on" ]]; then
    log "Skipping DNS routing because SKIP_DNS_ROUTE=${SKIP_DNS_ROUTE}."
    return
  fi

  cert_path="$(origin_cert_path)"
  if [[ -z "${cert_path}" ]]; then
    warn "Skipping DNS routing because no Cloudflare origin certificate is available for API auth on this host."
    warn "This is expected when reusing an existing tunnel credentials JSON on a new server."
    warn "If ${PUBLIC_HOSTNAME} already points at tunnel ${TUNNEL_NAME}, no action is required."
    return
  fi

  if ! TUNNEL_ORIGIN_CERT="${cert_path}" cloudflared_run tunnel route dns "${TUNNEL_NAME}" "${PUBLIC_HOSTNAME}"; then
    warn "DNS routing command did not succeed even though an origin certificate was available."
    warn "Confirm ${PUBLIC_HOSTNAME} already points at tunnel ${TUNNEL_NAME}, or rerun:"
    warn "TUNNEL_ORIGIN_CERT=${cert_path} cloudflared tunnel route dns ${TUNNEL_NAME} ${PUBLIC_HOSTNAME}"
  fi
}

ensure_named_tunnel() {
  local tunnel_id source_credentials
  install -d -m 0750 /etc/cloudflared

  tunnel_id="${TUNNEL_ID}"
  if [[ -n "${SOURCE_CREDENTIALS_FILE}" ]]; then
    [[ -f "${SOURCE_CREDENTIALS_FILE}" ]] || die "SOURCE_CREDENTIALS_FILE does not exist: ${SOURCE_CREDENTIALS_FILE}"
    source_credentials="${SOURCE_CREDENTIALS_FILE}"
    if [[ -z "${tunnel_id}" ]]; then
      tunnel_id="$(read_tunnel_id_from_credentials "${source_credentials}")"
    fi
    log "Using imported tunnel credentials from ${source_credentials}."
  elif [[ -f "${CREDENTIALS_FILE}" ]]; then
    source_credentials="${CREDENTIALS_FILE}"
    if [[ -z "${tunnel_id}" ]]; then
      tunnel_id="$(read_tunnel_id_from_credentials "${source_credentials}")"
    fi
    log "Reusing installed tunnel credentials at ${source_credentials}."
  else
    ensure_login_certificate

    tunnel_id="$(discover_existing_tunnel_id)"
    if [[ -z "${tunnel_id}" ]]; then
      log "Creating named tunnel ${TUNNEL_NAME}."
      local create_output
      create_output="$(cloudflared_run tunnel create "${TUNNEL_NAME}" 2>&1)"
      printf '%s\n' "${create_output}"
      tunnel_id="$(printf '%s\n' "${create_output}" | grep -Eo '[0-9a-fA-F-]{36}' | head -n 1 || true)"
      [[ -n "${tunnel_id}" ]] || die "Could not determine tunnel UUID after creation."
    else
      log "Using existing named tunnel ${TUNNEL_NAME} (${tunnel_id})."
    fi

    source_credentials="$(login_home)/.cloudflared/${tunnel_id}.json"
    if [[ ! -f "${source_credentials}" ]]; then
      die "Expected named tunnel credentials were not found at:
${source_credentials}

Run cloudflared tunnel login and cloudflared tunnel create ${TUNNEL_NAME} as ${LOGIN_USER}, then rerun this script."
    fi
  fi

  [[ -n "${tunnel_id}" ]] || die "Could not determine tunnel UUID from the named tunnel credentials."

  if [[ "${source_credentials}" != "${CREDENTIALS_FILE}" ]]; then
    install -m 0640 "${source_credentials}" "${CREDENTIALS_FILE}"
  else
    chmod 0640 "${CREDENTIALS_FILE}"
  fi
  log "Installed tunnel credentials to ${CREDENTIALS_FILE}."

  route_dns_if_possible

  backup_existing_config
  cat > "${CLOUDFLARED_CONFIG}" <<EOF
tunnel: ${tunnel_id}
credentials-file: ${CREDENTIALS_FILE}
ingress:
  - hostname: ${PUBLIC_HOSTNAME}
    service: ${ORIGIN_URL}
  - service: http_status:404
EOF
  chmod 0640 "${CLOUDFLARED_CONFIG}"
  log "Wrote ${CLOUDFLARED_CONFIG} for named tunnel ingress."
}

install_or_refresh_service() {
  if ! systemctl list-unit-files cloudflared.service >/dev/null 2>&1; then
    log "Installing cloudflared service unit."
    cloudflared service install
    return
  fi
  log "cloudflared service unit already exists."
}

restart_and_verify() {
  log "Restarting cloudflared service"
  systemctl daemon-reload
  systemctl enable cloudflared
  systemctl restart cloudflared
  systemctl --no-pager --full status cloudflared | sed -n '1,16p'
}

print_summary() {
  cat <<EOF

Cloudflared webhook setup complete.

Routing target:
  https://${PUBLIC_HOSTNAME} -> ${ORIGIN_URL}

Validation commands:
  curl -fsS http://127.0.0.1:8080/health
  curl -I https://${PUBLIC_HOSTNAME}/health
  sudo systemctl status cloudflared --no-pager
  sudo journalctl -u cloudflared -n 100 --no-pager

Notes:
  - This installer uses cloudflared named-tunnel login flow, not token mode.
  - To migrate without changing tracking links, reuse the same tunnel credentials JSON on the new host.
  - DNS routing changes require a Cloudflare login cert; imported tunnel credentials alone are enough to run the tunnel.
  - Do not expose port 8080 publicly.
  - Do not commit tunnel credentials into the repository.
  - The installed credentials file is ${CREDENTIALS_FILE}.
  - Host-level routing keeps /health reachable for validation checks.
EOF
}

main() {
  ensure_root
  install_cloudflared
  warn_webhook_env_values
  ensure_named_tunnel
  install_or_refresh_service
  restart_and_verify
  print_summary
}

main "$@"

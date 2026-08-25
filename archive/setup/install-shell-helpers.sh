#!/usr/bin/env bash
# scripts/install-shell-helpers.sh — Rob the Bot shell helper installer
#
# Installs the rob() and throne() bash functions into ~/.bashrc safely.
#
# Usage:
#   bash scripts/install-shell-helpers.sh
#   source ~/.bashrc
#
# Features:
#   - Backs up ~/.bashrc before touching anything.
#   - Strips existing rob()/throne() blocks (sentinel-aware on re-install,
#     brace-aware Python stripping for legacy .bashrc files without sentinels).
#   - Syntax-checks the result; auto-restores backup on failure.
#   - Safe to re-run idempotently.
#
set -euo pipefail

BASHRC="${HOME}/.bashrc"
SENTINEL_START="# >> rob-the-bot shell helpers >>"
SENTINEL_END="# << rob-the-bot shell helpers <<"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP="${BASHRC}.bak.${TIMESTAMP}"

# ── colour helpers (gracefully degraded when not in a terminal) ──────────────
if [[ -t 1 ]]; then
  BOLD="$(printf '\033[1m')"
  GREEN="$(printf '\033[0;32m')"
  YELLOW="$(printf '\033[0;33m')"
  CYAN="$(printf '\033[0;36m')"
  RED="$(printf '\033[0;31m')"
  RESET="$(printf '\033[0m')"
else
  BOLD="" GREEN="" YELLOW="" CYAN="" RED="" RESET=""
fi

step()    { printf '%s ▶%s %s\n' "${BOLD}" "${RESET}" "$*"; }
success() { printf '%s ✔%s %s\n' "${GREEN}" "${RESET}" "$*"; }
warn()    { printf '%s !%s %s\n' "${YELLOW}" "${RESET}" "$*"; }
die()     { printf '%s ✖ error:%s %s\n' "${RED}" "${RESET}" "$*" >&2; exit 1; }

# ── 1. Backup ────────────────────────────────────────────────────────────────
step "Backing up ${BASHRC} → ${BACKUP}"
if [[ ! -f "${BASHRC}" ]]; then
  touch "${BASHRC}"
fi
cp "${BASHRC}" "${BACKUP}"
success "Backup written."

# ── 2. Strip old blocks (sentinel + legacy brace-aware) ─────────────────────
step "Stripping existing rob()/throne() blocks from ${BASHRC}"
python3 - "${BASHRC}" "${SENTINEL_START}" "${SENTINEL_END}" <<'PYEOF'
from __future__ import annotations
import sys
import re

path, sentinel_start, sentinel_end = sys.argv[1], sys.argv[2], sys.argv[3]

with open(path, "r", encoding="utf-8", errors="replace") as fh:
    lines = fh.readlines()

# ── Pass 1: strip sentinel-wrapped block ────────────────────────────────────
out: list[str] = []
in_sentinel = False
for line in lines:
    stripped = line.rstrip("\n").rstrip()
    if stripped == sentinel_start:
        in_sentinel = True
        continue
    if stripped == sentinel_end:
        in_sentinel = False
        continue
    if not in_sentinel:
        out.append(line)
lines = out

# ── Pass 2: brace-aware removal of legacy rob()/throne() blocks ─────────────
FUNC_NAMES = ("rob", "throne")

# Matches: rob() {  /  rob () {  /  function rob {  /  function rob() {
def make_func_re(name: str) -> re.Pattern:
    esc = re.escape(name)
    return re.compile(
        r"^\s*(?:function\s+)?" + esc + r"\s*\(\s*\)\s*\{|"
        r"^\s*function\s+" + esc + r"\s*\{"
    )

FUNC_RES = [make_func_re(n) for n in FUNC_NAMES]


def remove_function_blocks(lines: list[str], patterns: list[re.Pattern]) -> list[str]:
    """Remove bash function blocks for the given patterns, handling heredocs."""
    result: list[str] = []
    i = 0
    while i < len(lines):
        # Check if this line opens one of the target functions.
        if any(p.match(lines[i]) for p in patterns):
            # Count opening braces on this line to establish initial depth.
            depth = lines[i].count("{") - lines[i].count("}")
            if depth <= 0:
                depth = 1  # function body always opens with net +1
            i += 1
            heredoc_end: str | None = None
            while i < len(lines) and depth > 0:
                raw = lines[i]
                stripped_line = raw.rstrip("\n")
                if heredoc_end is not None:
                    # Inside a heredoc: only watch for the closing marker.
                    # Strip all trailing whitespace to handle \r\n line endings.
                    if stripped_line.rstrip() == heredoc_end:
                        heredoc_end = None
                else:
                    # Detect heredoc opening: <<WORD  <<'WORD'  <<"WORD"  <<-WORD
                    hd_match = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", stripped_line)
                    if hd_match:
                        heredoc_end = hd_match.group(1)
                    # Count braces only outside heredocs.
                    depth += stripped_line.count("{") - stripped_line.count("}")
                i += 1
            # Optionally consume a trailing blank line after the function.
            if i < len(lines) and lines[i].strip() == "":
                i += 1
        else:
            result.append(lines[i])
            i += 1
    return result


lines = remove_function_blocks(lines, FUNC_RES)

with open(path, "w", encoding="utf-8") as fh:
    fh.writelines(lines)

print("Strip pass complete.")
PYEOF
success "Old blocks removed."

# ── 3. Append the new helpers wrapped in sentinels ──────────────────────────
step "Appending rob() and throne() to ${BASHRC}"

# Ensure there is a trailing newline before the sentinel block.
if [[ -s "${BASHRC}" ]]; then
  tail_char="$(tail -c1 "${BASHRC}")"
  if [[ "${tail_char}" != $'\n' ]]; then
    printf '\n' >> "${BASHRC}"
  fi
fi

# Write sentinel + functions in one atomic heredoc append.
cat >> "${BASHRC}" <<'SHELL_HELPERS'

# >> rob-the-bot shell helpers >>

# ── Input validators (used by rob() and throne()) ────────────────────────────
# Discord snowflake IDs are 17-19 digit integers; enforce that strictly.
_rob_valid_uid() {
  [[ "${1:-}" =~ ^[0-9]{17,19}$ ]]
}
# Throne handles: alphanumeric, hyphens, underscores; no shell meta-chars.
_rob_valid_handle() {
  [[ "${1:-}" =~ ^[A-Za-z0-9_-]{1,64}$ ]]
}
# Strictly positive integers (send IDs, cent amounts).
_rob_valid_posint() {
  [[ "${1:-}" =~ ^[0-9]+$ ]] && [ "${1}" -gt 0 ]
}
# Escape single quotes for safe use in inline SQL literals.
_rob_sql_escape() {
  printf '%s' "${1}" | sed "s/'/''/g"
}
_rob_db_path() {
  local default_db="/opt/rob-the-bot/data/rob_the_bot.sqlite3"
  local env_file="/opt/rob-the-bot/app/.env"
  local env_db=""
  if [ -r "${env_file}" ]; then
    env_db="$(grep -E '^[[:space:]]*(export[[:space:]]+)?DATABASE_PATH[[:space:]]*=' "${env_file}" \
      | tail -n1 \
      | sed -E "s/^[[:space:]]*(export[[:space:]]+)?DATABASE_PATH[[:space:]]*=[[:space:]]*//; s/[[:space:]]+$//; s/^['\"]//; s/['\"]$//")"
  fi
  if [ -n "${env_db}" ]; then
    printf '%s' "${env_db}"
    return
  fi
  printf '%s' "${default_db}"
}

rob() {
  local DB
  DB="$(_rob_db_path)"
  local RED GREEN YELLOW CYAN BOLD RESET
  RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'
  CYAN=$'\033[0;36m'; BOLD=$'\033[1m'; RESET=$'\033[0m'

  case "${1:-}" in
    blacklist)
      local uid="${2:-}" reason="${3:-manual}"
      if [ -z "$uid" ]; then
        echo "${RED}usage: rob blacklist <discord_user_id> [reason]${RESET}" >&2
        return 1
      fi
      if ! _rob_valid_uid "$uid"; then
        echo "${RED}Error: discord_user_id must be a 17-19 digit integer.${RESET}" >&2
        return 1
      fi
      local safe_reason
      safe_reason="$(_rob_sql_escape "$reason")"
      sudo sqlite3 "$DB" \
        "INSERT INTO rob_blacklist (discord_user_id, reason, created_at, created_by)
         VALUES ('${uid}', '${safe_reason}', datetime('now'), 'shell')
         ON CONFLICT(discord_user_id) DO UPDATE SET
           reason = excluded.reason,
           created_by = excluded.created_by;"
      echo "${GREEN}Blacklisted ${uid} (silent).${RESET}"
      ;;

    unblacklist)
      local uid="${2:-}"
      if [ -z "$uid" ]; then
        echo "${RED}usage: rob unblacklist <discord_user_id>${RESET}" >&2
        return 1
      fi
      if ! _rob_valid_uid "$uid"; then
        echo "${RED}Error: discord_user_id must be a 17-19 digit integer.${RESET}" >&2
        return 1
      fi
      sudo sqlite3 "$DB" "DELETE FROM rob_blacklist WHERE discord_user_id = '${uid}';"
      echo "${GREEN}Removed ${uid} from blacklist.${RESET}"
      ;;

    blacklisted)
      sudo sqlite3 -separator '|' "$DB" \
        "SELECT discord_user_id, COALESCE(reason,''), created_at, COALESCE(created_by,'')
         FROM rob_blacklist ORDER BY created_at DESC;" \
      | while IFS='|' read -r uid reason at by; do
          [ -z "$uid" ] && continue
          echo "${CYAN}• ${uid}${RESET}"
          printf "  ${BOLD}%-12s${RESET} %s\n" "Reason:" "${reason:-none}"
          printf "  ${BOLD}%-12s${RESET} %s\n" "At:"     "$at"
          printf "  ${BOLD}%-12s${RESET} %s\n" "By:"     "${by:-shell}"
          echo
        done
      ;;

    count)
      local action="${2:-}" start_from="${3:-0}"
      case "$action" in
        start)
          if ! [[ "$start_from" =~ ^[0-9]+$ ]]; then
            echo "${RED}usage: rob count start [number>=0]${RESET}" >&2
            return 1
          fi
          sudo sqlite3 "$DB" \
            "INSERT INTO bot_config (key, value) VALUES ('count.current', '${start_from}')
             ON CONFLICT(key) DO UPDATE SET value = excluded.value;
             INSERT INTO bot_config (key, value) VALUES ('count.active', '1')
             ON CONFLICT(key) DO UPDATE SET value = excluded.value;
             INSERT INTO bot_config (key, value) VALUES ('count.pending_restore', '0')
             ON CONFLICT(key) DO UPDATE SET value = excluded.value;
             DELETE FROM bot_config WHERE key IN ('count.restore_mode', 'count.restore_until', 'count.restore_value', 'count.failed_user_id');"
          echo "${GREEN}Counting started from ${start_from}. Next number is $((start_from + 1)).${RESET}"
          ;;
        end)
          sudo sqlite3 "$DB" \
            "INSERT INTO bot_config (key, value) VALUES ('count.active', '0')
             ON CONFLICT(key) DO UPDATE SET value = excluded.value;
             INSERT INTO bot_config (key, value) VALUES ('count.pending_restore', '0')
             ON CONFLICT(key) DO UPDATE SET value = excluded.value;
             DELETE FROM bot_config WHERE key IN ('count.restore_mode', 'count.restore_until', 'count.restore_value', 'count.failed_user_id');"
          echo "${YELLOW}Counting ended.${RESET}"
          ;;
        *)
          echo "${RED}usage: rob count <start [number>=0]|end>${RESET}" >&2
          return 1
          ;;
      esac
      ;;

    restart|refresh)
      sudo systemctl restart rob-the-bot
      echo "${GREEN}Rob restarted.${RESET}"
      ;;

    *)
      printf '%s\n' \
        "${BOLD}Usage:${RESET}" \
        "  rob blacklist <discord_user_id> [reason]" \
        "  rob unblacklist <discord_user_id>" \
        "  rob blacklisted" \
        "  rob count <start [number>=0]|end>" \
        "  rob restart"
      return 1
      ;;
  esac
}

throne() {
  local DB
  DB="$(_rob_db_path)"
  local BASE_URL="https://rob.barecoding.com"
  local RED GREEN YELLOW CYAN BOLD RESET
  RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'
  CYAN=$'\033[0;36m'; BOLD=$'\033[1m'; RESET=$'\033[0m'

  _throne_error()  { echo "${RED}Error: $*${RESET}" >&2; }
  _throne_ok()     { echo "${GREEN}✔ $*${RESET}"; }
  _throne_warn()   { echo "${YELLOW}! $*${RESET}"; }
  _throne_kv()     { printf "  ${BOLD}%-22s${RESET} %s\n" "$1" "$2"; }
  _throne_header() { echo; echo "${CYAN}${BOLD}═══ $* ═══${RESET}"; echo; }
  _throne_sync_leaderboard() {
    local admin_url="http://127.0.0.1:8080/admin/leaderboard/sync"
    local response status_code body
    response=$(curl -sS -w '\n%{http_code}' \
      -X POST "${admin_url}" \
      -H "Content-Type: application/json" \
      --data '{}' ) || {
        _throne_warn "Could not reach ${admin_url}."
        _throne_warn "Run 'rob restart' to re-render leaderboard."
        return 1
      }
    status_code="${response##*$'\n'}"
    body="${response%$'\n'*}"
    if [ "$status_code" = "200" ]; then
      _throne_ok "Leaderboard sync requested."
      return 0
    fi
    _throne_warn "Leaderboard sync endpoint returned HTTP ${status_code}."
    [ -n "$body" ] && echo "$body" >&2
    _throne_warn "Run 'rob restart' to re-render leaderboard."
    return 1
  }

  local cmd="${1:-}"
  shift || true

  case "$cmd" in
    sends)
      local handle="${1:-}"
      if [ -z "$handle" ]; then
        _throne_error "usage: throne sends <handle>"; return 1
      fi
      if ! _rob_valid_handle "$handle"; then
        _throne_error "handle must contain only letters, digits, hyphens, or underscores."
        return 1
      fi
      _throne_header "Sends for @${handle}"
      local safe_handle
      safe_handle="$(_rob_sql_escape "${handle}")"
      sudo sqlite3 -separator '|' "$DB" \
        "SELECT es.id, es.sub_name, es.amount_usd, es.item_name, es.sent_at
         FROM event_sends es
         JOIN throne_creators tc ON tc.discord_user_id = CAST(es.domme_user_id AS TEXT)
         WHERE LOWER(tc.throne_handle) = LOWER('${safe_handle}')
         ORDER BY es.sent_at DESC LIMIT 25;" \
      | while IFS='|' read -r id sub usd item at; do
          [ -z "$id" ] && continue
          printf "  ${BOLD}#%-6s${RESET} \$%-8s  %-30s  %s  %s\n" \
            "$id" "$usd" "${item:-(no item)}" "${sub:-(anon)}" "$at"
        done
      ;;

    wishlist)
      local handle="${1:-}"
      if [ -z "$handle" ]; then
        _throne_error "usage: throne wishlist <handle>"; return 1
      fi
      if ! _rob_valid_handle "$handle"; then
        _throne_error "handle must contain only letters, digits, hyphens, or underscores."
        return 1
      fi
      _throne_header "Wishlist for @${handle}"
      local safe_handle
      safe_handle="$(_rob_sql_escape "${handle}")"
      sudo sqlite3 -separator '|' "$DB" \
        "SELECT twi.item_name, twi.amount_usd, twi.currency, twi.is_available
         FROM throne_wishlist_items twi
         JOIN throne_creators tc ON tc.throne_creator_id = twi.creator_id
         WHERE LOWER(tc.throne_handle) = LOWER('${safe_handle}')
         ORDER BY twi.amount_usd DESC;" \
      | while IFS='|' read -r name usd cur avail; do
          [ -z "$name" ] && [ -z "$usd" ] && continue
          local av_label
          [ "${avail:-0}" = "1" ] \
            && av_label="${GREEN}available${RESET}" \
            || av_label="${YELLOW}unavailable${RESET}"
          printf "  %-32s  \$%-8s  %-4s  %b\n" \
            "${name:-(unnamed)}" "$usd" "${cur:-USD}" "$av_label"
        done
      ;;

    fix-send)
      local handle="${1:-}" send_id="${2:-}" amount_cents="${3:-}"
      if [ -z "$handle" ] || [ -z "$send_id" ] || [ -z "$amount_cents" ]; then
        _throne_error "usage: throne fix-send <handle> <send_id> <amount_cents>"
        return 1
      fi
      if ! _rob_valid_handle "$handle"; then
        _throne_error "handle must contain only letters, digits, hyphens, or underscores."
        return 1
      fi
      if ! _rob_valid_posint "$send_id"; then
        _throne_error "send_id must be a positive integer."; return 1
      fi
      if ! _rob_valid_posint "$amount_cents"; then
        _throne_error "amount_cents must be a positive integer."; return 1
      fi
      local amount_usd
      amount_usd=$(python3 -c "import sys; print(round(int(sys.argv[1]) / 100, 2))" "${amount_cents}")
      if ! [[ "${amount_usd}" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        _throne_error "Failed to compute amount_usd from ${amount_cents} cents."; return 1
      fi
      local rows_changed
      rows_changed=$(sudo sqlite3 "$DB" \
        "UPDATE event_sends SET amount_usd = ${amount_usd} WHERE id = ${send_id};
         SELECT changes();")
      rows_changed="${rows_changed//$'\n'/}"
      if [ "${rows_changed:-0}" -eq 0 ]; then
        _throne_error "No send found with id ${send_id}; nothing was updated."
        return 1
      fi
      _throne_ok "Send #${send_id} → \$${amount_usd} USD (${amount_cents} cents)."
      _throne_sync_leaderboard || true
      ;;

    refresh)
      sudo systemctl restart rob-the-bot
      _throne_ok "Throne refresh triggered (bot restarted)."
      ;;

    dommes)
      _throne_header "Registered Dommes"
      sudo sqlite3 -separator '|' "$DB" \
        "SELECT throne_handle, throne_creator_id, tracking_mode,
                COALESCE(webhook_connected_at,'—'), discord_user_id
         FROM throne_creators ORDER BY throne_handle;" \
      | while IFS='|' read -r handle cid mode wcat uid; do
          [ -z "$handle" ] && continue
          echo "  ${CYAN}${BOLD}@${handle}${RESET}"
          _throne_kv "Creator ID:" "$cid"
          _throne_kv "Mode:"       "$mode"
          _throne_kv "Webhook at:" "$wcat"
          _throne_kv "Discord UID:" "$uid"
          echo
        done
      ;;

    subs)
      _throne_header "Registered Subs"
      sudo sqlite3 -separator '|' "$DB" \
        "SELECT user_id, sub_name, registered_at FROM event_subs ORDER BY sub_name;" \
      | while IFS='|' read -r uid name at; do
          [ -z "$uid" ] && continue
          printf "  ${BOLD}%-30s${RESET}  uid=%-20s  %s\n" "$name" "$uid" "$at"
        done
      ;;

    status)
      local handle="${1:-}"
      if [ -z "$handle" ]; then
        _throne_error "usage: throne status <handle>"; return 1
      fi
      if ! _rob_valid_handle "$handle"; then
        _throne_error "handle must contain only letters, digits, hyphens, or underscores."
        return 1
      fi
      _throne_header "Status: @${handle}"
      local safe_handle
      safe_handle="$(_rob_sql_escape "${handle}")"
      sudo sqlite3 -separator '|' "$DB" \
        "SELECT throne_handle, throne_creator_id, tracking_mode,
                COALESCE(webhook_connected_at,'—'), discord_user_id,
                COALESCE(last_successful_event_at,'—'), overlay_detected
         FROM throne_creators
         WHERE LOWER(throne_handle) = LOWER('${safe_handle}')
         LIMIT 1;" \
      | while IFS='|' read -r h cid mode wcat uid last_ev overlay; do
          _throne_kv "Handle:"     "@$h"
          _throne_kv "Creator ID:" "$cid"
          _throne_kv "Discord UID:" "$uid"
          _throne_kv "Mode:"       "$mode"
          _throne_kv "Webhook at:" "$wcat"
          _throne_kv "Last event:" "$last_ev"
          _throne_kv "Overlay:"    "$overlay"
        done
      ;;

    url)
      local handle="${1:-}"
      if [ -z "$handle" ]; then
        _throne_error "usage: throne url <handle>"; return 1
      fi
      if ! _rob_valid_handle "$handle"; then
        _throne_error "handle must contain only letters, digits, hyphens, or underscores."
        return 1
      fi
      local row
      local safe_handle
      safe_handle="$(_rob_sql_escape "${handle}")"
      row=$(sudo sqlite3 -separator '|' "$DB" \
        "SELECT throne_creator_id, webhook_secret
         FROM throne_creators
         WHERE LOWER(throne_handle) = LOWER('${safe_handle}')
         LIMIT 1;")
      if [ -z "$row" ]; then
        _throne_error "No creator found for handle: $handle"
        return 1
      fi
      local cid secret
      cid="${row%%|*}"
      secret="${row##*|}"
      echo "${BASE_URL}/throne/webhook/${cid}/${secret}"
      ;;

    blacklist)
      local uid="${1:-}"
      if [ -z "$uid" ]; then
        _throne_error "usage: throne blacklist <discord_user_id>"
        return 1
      fi
      if ! _rob_valid_uid "$uid"; then
        _throne_error "discord_user_id must be a 17-19 digit integer."
        return 1
      fi
      # creator_id comes from our own database, not user input.
      local creator_id
      creator_id=$(sudo sqlite3 "$DB" \
        "SELECT throne_creator_id FROM throne_creators
         WHERE discord_user_id = '${uid}' LIMIT 1;")
      if [ -n "$creator_id" ]; then
        local safe_cid
        safe_cid="$(_rob_sql_escape "$creator_id")"
        sudo sqlite3 "$DB" "DELETE FROM throne_wishlist_items WHERE creator_id = '${safe_cid}';"
        sudo sqlite3 "$DB" "DELETE FROM throne_creators WHERE discord_user_id = '${uid}';"
        sudo sqlite3 "$DB" "DELETE FROM event_dommes WHERE user_id = '${uid}';"
      fi
      sudo sqlite3 "$DB" \
        "INSERT INTO rob_blacklist (discord_user_id, reason, created_at, created_by)
         VALUES ('${uid}', 'throne blacklist', datetime('now'), 'shell')
         ON CONFLICT(discord_user_id) DO UPDATE SET
           reason = excluded.reason,
           created_by = excluded.created_by;"
      _throne_header "Throne Blacklist"
      _throne_ok "Done (silent)."
      if [ -n "$creator_id" ]; then
        _throne_kv "Removed creator:" "$creator_id"
      else
        _throne_warn "No throne registration found — global blacklist applied anyway."
      fi
      _throne_kv "Discord user ID:" "$uid"
      _throne_warn "Historical sends in event_sends were NOT deleted (intentional)."
      ;;

    addsend)
      local uid="${1:-}" amount="${2:-}" sub="${3:-}"
      if [ -z "$uid" ] || [ -z "$amount" ]; then
        _throne_error "usage: throne addsend <discord_user_id> <amount_usd> [sub_name]"
        return 1
      fi
      if ! _rob_valid_uid "$uid"; then
        _throne_error "discord_user_id must be a 17-19 digit integer."
        return 1
      fi
      if ! python3 -c "
import sys, re
a = sys.argv[1]
if not re.match(r'^[0-9]+(\.[0-9]+)?$', a):
    sys.exit(1)
if float(a) <= 0:
    sys.exit(1)
" "${amount}" 2>/dev/null; then
        _throne_error "amount_usd must be a positive number (e.g. 25 or 9.99)."
        return 1
      fi
      if python3 -c "
import sys
a = sys.argv[1].strip()
if '.' in a:
    sys.exit(1)
sys.exit(0 if int(a) >= 1000 else 1)
" "${amount}" 2>/dev/null; then
        _throne_error "amount_usd looks like cents (${amount}). Use USD with decimals (example: 466.00)."
        return 1
      fi
      local safe_sub=""
      if [ -n "$sub" ]; then
        safe_sub="$(_rob_sql_escape "${sub}")"
      fi
      local claimed_sub_sql="NULL"
      if [ -n "$safe_sub" ]; then
        local claimed_uid
        claimed_uid=$(sudo sqlite3 "$DB" \
          "SELECT user_id FROM event_subs
           WHERE sub_name = '${safe_sub}' COLLATE NOCASE LIMIT 1;" 2>/dev/null || true)
        if [ -n "$claimed_uid" ] && _rob_valid_uid "$claimed_uid"; then
          claimed_sub_sql="${claimed_uid}"
        fi
      fi
      local claimed_sub_display="(unclaimed)"
      if [ "$claimed_sub_sql" != "NULL" ]; then
        claimed_sub_display="$claimed_sub_sql"
      fi
      # Resolve the active event key (NULL when no event is running).
      local event_key
      event_key=$(sudo sqlite3 "$DB" \
        "SELECT event_key FROM event_state WHERE is_active = 1 LIMIT 1;" 2>/dev/null || true)
      local ek_sql="NULL"
      if [ -n "$event_key" ]; then
        local safe_ek
        safe_ek="$(_rob_sql_escape "${event_key}")"
        ek_sql="'${safe_ek}'"
      fi
      local sub_sql="NULL"
      if [ -n "$safe_sub" ]; then
        sub_sql="'${safe_sub}'"
      fi
      local send_id
      send_id=$(sudo sqlite3 "$DB" \
        "INSERT INTO event_sends
           (domme_user_id, sub_name, claimed_sub_user_id, amount_usd, item_name,
             item_image_url, logged_by, sent_at, source,
             is_private, seeded, event_key)
          VALUES
           (${uid}, ${sub_sql}, ${claimed_sub_sql}, ${amount}, 'Admin-logged external send',
             NULL, 0, datetime('now'), 'manual:admin',
             0, 0, ${ek_sql});
          SELECT last_insert_rowid();")
      if [ -z "$send_id" ]; then
        _throne_error "Insert failed — check the database path and permissions."
        return 1
      fi
      _throne_header "Send Added"
      _throne_ok "Send logged (ID #${send_id})."
      _throne_kv "Discord UID:"  "${uid}"
      _throne_kv "Amount:"       "\$${amount}"
      _throne_kv "Sub:"          "${sub:-(none)}"
      _throne_kv "Claimed sub UID:" "${claimed_sub_display}"
      _throne_kv "Event key:"    "${event_key:-(none)}"
      echo
      _throne_sync_leaderboard || true
      _throne_warn "Shell addsend writes directly to DB; it does not post a send-tracker card."
      ;;

    register-domme)
      local uid="${1:-}" handle="${2:-}"
      if [ -z "$uid" ] || [ -z "$handle" ]; then
        _throne_error "usage: throne register-domme <discord_user_id> <throne_handle_or_url>"
        return 1
      fi
      if ! _rob_valid_uid "$uid"; then
        _throne_error "discord_user_id must be a 17-19 digit integer."
        return 1
      fi
      # Normalise handle/URL using the same logic as the bot.
      local throne_url
      throne_url=$(python3 -c "
import sys
from urllib.parse import urlparse, urlunparse, quote
val = sys.argv[1].strip()
if not val: sys.exit(1)
if '://' not in val and not val.startswith('www.'):
    un = val.lstrip('@').strip()
    if not un or any(c.isspace() for c in un): sys.exit(1)
    val = 'https://throne.com/' + quote(un, safe='._-')
if '://' not in val: val = 'https://' + val
p = urlparse(val)
h = (p.hostname or '').lower().lstrip('.')
if h.startswith('www.'): h = h[4:]
if h not in {'throne.com', 'throne.gifts'}: sys.exit(1)
path = p.path.rstrip('/')
if not path or path == '/': sys.exit(1)
print(urlunparse(('https', h, path, '', '', '')))
" "${handle}" 2>/dev/null)
      if [ -z "$throne_url" ]; then
        _throne_error "Invalid Throne handle or URL: ${handle}"
        return 1
      fi
      local safe_url
      safe_url="$(_rob_sql_escape "${throne_url}")"
      sudo sqlite3 "$DB" \
        "INSERT INTO event_dommes (user_id, throne_url, registered_at)
         VALUES (${uid}, '${safe_url}', datetime('now'))
         ON CONFLICT(user_id) DO UPDATE SET
           throne_url = excluded.throne_url;"
      _throne_header "Domme Registered"
      _throne_ok "Registered."
      _throne_kv "Discord UID:" "${uid}"
      _throne_kv "Throne URL:"  "${throne_url}"
      _throne_warn "For full webhook tracking, also run /register-domme on Discord."
      _throne_warn "Run 'rob restart' so the bot picks up the new registration."
      ;;

    register-sub)
      local uid="${1:-}" sub_name="${2:-}"
      if [ -z "$uid" ] || [ -z "$sub_name" ]; then
        _throne_error "usage: throne register-sub <discord_user_id> <sub_name>"
        return 1
      fi
      if ! _rob_valid_uid "$uid"; then
        _throne_error "discord_user_id must be a 17-19 digit integer."
        return 1
      fi
      # Normalise whitespace (collapse runs), matching what the bot does.
      local norm_name
      norm_name=$(python3 -c "import sys; print(' '.join(sys.argv[1].split()))" "${sub_name}")
      if [ -z "$norm_name" ]; then
        _throne_error "sub_name must not be empty."
        return 1
      fi
      local safe_name
      safe_name="$(_rob_sql_escape "${norm_name}")"
      # Reject if name is taken by a different user.
      local existing_uid
      existing_uid=$(sudo sqlite3 "$DB" \
        "SELECT user_id FROM event_subs
         WHERE sub_name = '${safe_name}' COLLATE NOCASE LIMIT 1;" 2>/dev/null || true)
      if [ -n "$existing_uid" ] && [ "$existing_uid" != "$uid" ]; then
        _throne_error "Name '${norm_name}' is already taken by user ${existing_uid}."
        return 1
      fi
      sudo sqlite3 "$DB" \
        "INSERT INTO event_subs (user_id, sub_name, registered_at)
         VALUES (${uid}, '${safe_name}', datetime('now'))
         ON CONFLICT(user_id) DO UPDATE SET
           sub_name = excluded.sub_name;"
      # Claim any historical unclaimed sends matching this sub_name (mirrors bot logic).
      local claimed
      claimed=$(sudo sqlite3 "$DB" \
        "UPDATE event_sends
         SET claimed_sub_user_id = ${uid}
         WHERE sub_name = '${safe_name}' COLLATE NOCASE
           AND claimed_sub_user_id IS NULL;
         SELECT changes();")
      _throne_header "Sub Registered"
      _throne_ok "Registered."
      _throne_kv "Discord UID:" "${uid}"
      _throne_kv "Sub name:"    "${norm_name}"
      _throne_kv "Sends claimed:" "${claimed:-0}"
      _throne_warn "Run 'rob restart' so the bot picks up the new registration."
      ;;

    webhook-rebuild)
      local handle="${1:-}"
      if [ -z "$handle" ]; then
        _throne_error "usage: throne webhook-rebuild <handle>"
        return 1
      fi
      if ! _rob_valid_handle "$handle"; then
        _throne_error "handle must contain only letters, digits, hyphens, or underscores."
        return 1
      fi
      local safe_handle
      safe_handle="$(_rob_sql_escape "${handle}")"
      # Look up the creator_id — comes from our own DB, safe to use.
      local creator_id
      creator_id=$(sudo sqlite3 "$DB" \
        "SELECT throne_creator_id FROM throne_creators
         WHERE LOWER(throne_handle) = LOWER('${safe_handle}') LIMIT 1;")
      if [ -z "$creator_id" ]; then
        _throne_error "No throne creator found for handle: ${handle}"
        return 1
      fi
      # Generate a new secret (same method used by the bot itself).
      local new_secret
      new_secret=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
      if [ -z "$new_secret" ]; then
        _throne_error "Failed to generate new webhook secret."
        return 1
      fi
      local safe_secret
      safe_secret="$(_rob_sql_escape "$new_secret")"
      local safe_cid
      safe_cid="$(_rob_sql_escape "$creator_id")"
      # Rotate the secret and clear webhook_connected_at so the bot treats
      # this as a brand-new webhook registration.
      sudo sqlite3 "$DB" \
        "UPDATE throne_creators
         SET webhook_secret = '${safe_secret}',
             webhook_connected_at = NULL
         WHERE throne_creator_id = '${safe_cid}';"
      local new_url="${BASE_URL}/throne/webhook/${creator_id}/${new_secret}"
      _throne_header "Webhook Rebuilt: @${handle}"
      _throne_ok "New secret generated and saved."
      _throne_kv "Handle:"     "@${handle}"
      _throne_kv "Creator ID:" "$creator_id"
      echo
      _throne_kv "New URL:" "$new_url"
      echo
      _throne_warn "You MUST update this URL in Throne's webhook settings."
      _throne_warn "The old webhook URL will no longer work."
      _throne_warn "Run 'rob restart' after updating Throne to reload the bot."
      ;;

    maintenance)
      local mode="${1:-}"
      if [ -z "$mode" ] || { [ "$mode" != "on" ] && [ "$mode" != "off" ]; }; then
        _throne_error "usage: throne maintenance <on|off>"
        return 1
      fi
      local admin_url="http://127.0.0.1:8080"
      local active="true"
      [ "$mode" = "off" ] && active="false"
      local response
      response=$(curl -sS -w '\n%{http_code}' \
        -X POST "${admin_url}/admin/maintenance" \
        -H "Content-Type: application/json" \
        --data "{\"active\": ${active}}") || {
          _throne_error "curl failed to reach ${admin_url}/admin/maintenance"
          return 1
        }
      local status_code body
      status_code="${response##*$'\n'}"
      body="${response%$'\n'*}"
      if [ "$status_code" = "200" ]; then
        _throne_ok "Maintenance mode → ${mode}"
        [ -n "$body" ] && echo "$body"
      else
        _throne_error "Server returned HTTP ${status_code}"
        [ -n "$body" ] && echo "$body" >&2
        return 1
      fi
      ;;

    broadcast)
      local target="${1:-}"
      local message="${2:-}"
      local _target_valid=0
      case "$target" in
        owner|all|dommes|subs) _target_valid=1 ;;
        user:*) [ -n "${target#user:}" ] && _target_valid=1 ;;
        channel:*) [ -n "${target#channel:}" ] && _target_valid=1 ;;
      esac
      if [ -z "$target" ] || [ "$_target_valid" -eq 0 ]; then
        _throne_error "usage: throne broadcast <owner|all|dommes|subs|user:<discord_user_id>|channel:<discord_channel_id>> \"<message>\" [url] [--plain]"
        return 1
      fi
      if [ -z "$message" ]; then
        _throne_error "usage: throne broadcast <owner|all|dommes|subs|user:<discord_user_id>|channel:<discord_channel_id>> \"<message>\" [url] [--plain]"
        return 1
      fi
      # Parse remaining optional args: [url] [--plain] (order-independent after message)
      local url="" plain=0
      shift 2
      while [ $# -gt 0 ]; do
        case "$1" in
          --plain) plain=1 ;;
          http://*|https://*) url="$1" ;;
          *) _throne_error "unknown broadcast option: $1"; return 1 ;;
        esac
        shift
      done
      local admin_url="http://127.0.0.1:8080"
      # Use python3 to safely build JSON so quotes/newlines in $message can't break it.
      local payload
      payload=$(TARGET="$target" MESSAGE="$message" URL="$url" PLAIN="$plain" python3 -c '
import json, os
data = {"target": os.environ.get("TARGET", ""), "message": os.environ.get("MESSAGE", "")}
u = os.environ.get("URL", "")
if u:
    data["url"] = u
if os.environ.get("PLAIN") == "1":
    data["plain"] = True
print(json.dumps(data))
') || {
        _throne_error "Failed to build JSON payload."
        return 1
      }
      local response
      response=$(curl -sS -w '\n%{http_code}' \
        -X POST "${admin_url}/admin/broadcast" \
        -H "Content-Type: application/json" \
        --data "$payload") || {
          _throne_error "curl failed to reach ${admin_url}/admin/broadcast"
          return 1
        }
      local status_code body
      status_code="${response##*$'\n'}"
      body="${response%$'\n'*}"
      if [ "$status_code" = "200" ]; then
        _throne_ok "Broadcast delivered."
        [ -n "$body" ] && echo "$body"
      else
        _throne_error "Server returned HTTP ${status_code}"
        [ -n "$body" ] && echo "$body" >&2
        return 1
      fi
      ;;

    *)
      printf '%s\n' \
        "${BOLD}throne — Rob the Bot shell helper${RESET}" \
        "" \
        "Usage:" \
        "  throne addsend       <discord_user_id> <amount_usd> [sub_name]" \
        "  throne register-domme <discord_user_id> <throne_handle_or_url>" \
        "  throne register-sub  <discord_user_id> <sub_name>" \
        "  throne sends    <handle>" \
        "  throne wishlist <handle>" \
        "  throne fix-send <handle> <send_id> <amount_cents>" \
        "  throne refresh" \
        "  throne dommes" \
        "  throne subs" \
        "  throne status         <handle>" \
        "  throne url            <handle>" \
        "  throne webhook-rebuild <handle>" \
        "  throne blacklist <discord_user_id>" \
        "  throne maintenance <on|off>" \
        "  throne broadcast <owner|all|dommes|subs|user:<discord_user_id>|channel:<discord_channel_id>> \"<message>\" [url] [--plain]"
      [ -n "$cmd" ] && return 1 || return 0
      ;;
  esac
}

# << rob-the-bot shell helpers <<
SHELL_HELPERS

success "Functions appended."

# ── 4. Syntax-check ──────────────────────────────────────────────────────────
step "Syntax-checking ${BASHRC} with bash -n"
if ! bash -n "${BASHRC}" 2>&1; then
  die "Syntax check failed.  Restoring backup ${BACKUP} …
$(cp "${BACKUP}" "${BASHRC}" && echo "Restored OK.")"
fi
success "Syntax check passed."

# ── 5. Done ──────────────────────────────────────────────────────────────────
printf '\n%s✔ Done.%s Reload your shell:\n\n    %ssource ~/.bashrc%s\n\n' \
  "${GREEN}" "${RESET}" "${CYAN}" "${RESET}"

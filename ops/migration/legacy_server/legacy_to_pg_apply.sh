#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
REPORT_DIR="${REPORT_DIR:-/tmp/rob-legacy-migration}"
SQLITE_PATH="${SQLITE_PATH:-}"
DATABASE_URL="${DATABASE_URL:-}"
DEFAULT_GUILD_ID="${DEFAULT_GUILD_ID:-}"
SCAN_ROOTS="${SCAN_ROOTS:-}"
CONFIRM_APPLY="${CONFIRM_APPLY:-}"
EXCLUDE_DOMME_HANDLES=()

usage() {
  cat <<'EOF'
Usage:
  legacy_to_pg_apply.sh --database-url <url> [--default-guild-id <guild_id>] --confirm-apply yes [--sqlite <path>] [--report-dir <dir>] [--roots "/opt /srv"] [--exclude-domme-handle <handle>]

Notes:
  - This script writes imported data into the target PostgreSQL database.
  - Schema/build SQL and runtime grants must already be applied manually.
  - If --default-guild-id is omitted, the importer will try to infer a single guild id.
  - It does not create schema or database roles.
EOF
}

while (($#)); do
  case "$1" in
    --sqlite)
      shift
      SQLITE_PATH="${1:-}"
      ;;
    --database-url)
      shift
      DATABASE_URL="${1:-}"
      ;;
    --default-guild-id)
      shift
      DEFAULT_GUILD_ID="${1:-}"
      ;;
    --report-dir)
      shift
      REPORT_DIR="${1:-}"
      ;;
    --roots)
      shift
      SCAN_ROOTS="${1:-}"
      ;;
    --confirm-apply)
      shift
      CONFIRM_APPLY="${1:-}"
      ;;
    --exclude-domme-handle)
      shift
      EXCLUDE_DOMME_HANDLES+=("${1:-}")
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift || true
done

command -v python3 >/dev/null 2>&1 || {
  echo "python3 is required." >&2
  exit 1
}

[[ "${CONFIRM_APPLY}" == "yes" ]] || {
  echo "--confirm-apply yes is required." >&2
  exit 1
}
[[ -n "${DATABASE_URL}" ]] || {
  echo "--database-url is required." >&2
  exit 1
}
mkdir -p "${REPORT_DIR}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
INSPECT_JSON="${REPORT_DIR}/sqlite-report-${STAMP}.json"
APPLY_JSON="${REPORT_DIR}/pg-apply-${STAMP}.json"

cd "${APP_ROOT}"

ROOT_ARGS=()
if [[ -n "${SCAN_ROOTS}" ]]; then
  # shellcheck disable=SC2206
  ROOT_PARTS=(${SCAN_ROOTS})
  ROOT_ARGS=(--roots "${ROOT_PARTS[@]}")
fi

EXCLUDE_ARGS=()
for handle in "${EXCLUDE_DOMME_HANDLES[@]}"; do
  EXCLUDE_ARGS+=(--exclude-domme-handle "${handle}")
done

DEFAULT_GUILD_ARGS=()
if [[ -n "${DEFAULT_GUILD_ID}" ]]; then
  DEFAULT_GUILD_ARGS=(--default-guild-id "${DEFAULT_GUILD_ID}")
fi

if [[ -z "${SQLITE_PATH}" ]]; then
  SQLITE_PATH="$(
    PYTHONPATH=. python3 -m scripts.data_migration.legacy_server.legacy_sqlite_report \
      "${ROOT_ARGS[@]}" \
      --report-json "${INSPECT_JSON}" \
      | awk -F': ' '/selected_sqlite:/ {print $2; exit}'
  )"
else
  PYTHONPATH=. python3 -m scripts.data_migration.legacy_server.legacy_sqlite_report \
    --sqlite "${SQLITE_PATH}" \
    "${ROOT_ARGS[@]}" \
    --report-json "${INSPECT_JSON}"
fi

[[ -n "${SQLITE_PATH}" ]] || {
  echo "Could not determine a SQLite source path." >&2
  exit 1
}

PYTHONPATH=. python3 -m scripts.data_migration.import_sqlite_to_postgres \
  --sqlite "${SQLITE_PATH}" \
  --database-url "${DATABASE_URL}" \
  "${DEFAULT_GUILD_ARGS[@]}" \
  "${EXCLUDE_ARGS[@]}" \
  --no-dry-run \
  --report-json "${APPLY_JSON}"

echo
echo "Legacy migration apply complete."
echo "- sqlite_report: ${INSPECT_JSON}"
echo "- apply_report: ${APPLY_JSON}"
echo "- source_sqlite: ${SQLITE_PATH}"

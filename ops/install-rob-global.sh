#!/usr/bin/env bash
set -euo pipefail

resolve_script_path() {
  local source_path="${BASH_SOURCE[0]}"
  while [[ -L "${source_path}" ]]; do
    local source_dir
    source_dir="$(cd -P "$(dirname "${source_path}")" && pwd)"
    source_path="$(readlink "${source_path}")"
    [[ "${source_path}" != /* ]] && source_path="${source_dir}/${source_path}"
  done
  printf '%s\n' "${source_path}"
}

SCRIPT_PATH="$(resolve_script_path)"
SCRIPT_DIR="$(cd -P "$(dirname "${SCRIPT_PATH}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOURCE_ROB="${APP_ROOT}/ops/cli/rob"
SOURCE_ROBCTL="${APP_ROOT}/ops/cli/robctl"
MARKER_START="# >>> rob global command >>>"
MARKER_END="# <<< rob global command <<<"

resolve_target_bin_dir() {
  if [[ -n "${TARGET_BIN_DIR:-}" ]]; then
    printf '%s\n' "${TARGET_BIN_DIR}"
    return
  fi

  if [[ -d "/usr/local/bin" && -w "/usr/local/bin" ]]; then
    printf '%s\n' "/usr/local/bin"
    return
  fi

  if command -v sudo >/dev/null 2>&1 \
    && sudo -n test -d /usr/local/bin 2>/dev/null \
    && sudo -n test -w /usr/local/bin 2>/dev/null; then
    printf '%s\n' "/usr/local/bin"
    return
  fi

  printf '%s\n' "${HOME}/.local/bin"
}

TARGET_BIN_DIR="$(resolve_target_bin_dir)"
TARGET_ROB="${TARGET_BIN_DIR}/rob"
TARGET_ROBCTL="${TARGET_BIN_DIR}/robctl"

ensure_executable() {
  local file="$1"
  if [[ ! -x "${file}" ]]; then
    chmod +x "${file}"
  fi
}

ensure_executable "${SOURCE_ROB}"
ensure_executable "${SOURCE_ROBCTL}"

ensure_target_dir() {
  if [[ -d "${TARGET_BIN_DIR}" ]]; then
    return
  fi
  if [[ "${TARGET_BIN_DIR}" == "/usr/local/bin" ]] && command -v sudo >/dev/null 2>&1; then
    sudo mkdir -p "${TARGET_BIN_DIR}"
    return
  fi
  mkdir -p "${TARGET_BIN_DIR}"
}

install_link() {
  local source_file="$1"
  local target_file="$2"
  if [[ "${TARGET_BIN_DIR}" == "/usr/local/bin" ]] && command -v sudo >/dev/null 2>&1 && [[ ! -w "${TARGET_BIN_DIR}" ]]; then
    sudo ln -sf "${source_file}" "${target_file}"
    return
  fi
  ln -sf "${source_file}" "${target_file}"
}

ensure_target_dir
install_link "${SOURCE_ROB}" "${TARGET_ROB}"
install_link "${SOURCE_ROBCTL}" "${TARGET_ROBCTL}"

append_shell_block() {
  local rc_file="$1"
  [[ -f "${rc_file}" ]] || touch "${rc_file}"
  local tmp_file
  tmp_file="$(mktemp)"
  awk -v start="${MARKER_START}" -v end="${MARKER_END}" '
    $0 == start {skip=1; next}
    $0 == end {skip=0; next}
    skip != 1 {print}
  ' "${rc_file}" > "${tmp_file}"
  mv "${tmp_file}" "${rc_file}"

  cat >> "${rc_file}" <<EOF
${MARKER_START}
ROB_GLOBAL_BIN="${TARGET_BIN_DIR}"
if [ -d "\${ROB_GLOBAL_BIN}" ]; then
  case ":\$PATH:" in
    *":\${ROB_GLOBAL_BIN}:"*) ;;
    *) export PATH="\${ROB_GLOBAL_BIN}:\$PATH" ;;
  esac
fi

rob() {
  "\${ROB_GLOBAL_BIN}/rob" "\$@"
}

robctl() {
  "\${ROB_GLOBAL_BIN}/robctl" "\$@"
}
${MARKER_END}
EOF
}

append_shell_block "${HOME}/.bashrc"
append_shell_block "${HOME}/.bash_profile"
append_shell_block "${HOME}/.zshrc"
append_shell_block "${HOME}/.zprofile"
append_shell_block "${HOME}/.profile"

cat <<EOF
Installed Rob globally:
  ${TARGET_ROB} -> ${SOURCE_ROB}
  ${TARGET_ROBCTL} -> ${SOURCE_ROBCTL}

Added shell functions to:
  ${HOME}/.bashrc
  ${HOME}/.bash_profile
  ${HOME}/.zshrc
  ${HOME}/.zprofile
  ${HOME}/.profile

Primary install target:
  ${TARGET_BIN_DIR}

Restart your shell or run:
  export PATH="${TARGET_BIN_DIR}:\$PATH"
  source "${HOME}/.bashrc" 2>/dev/null || true
  source "${HOME}/.bash_profile" 2>/dev/null || true
  source "${HOME}/.zshrc" 2>/dev/null || true
  source "${HOME}/.zprofile" 2>/dev/null || true

Then verify:
  rob
EOF

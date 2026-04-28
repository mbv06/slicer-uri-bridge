#!/usr/bin/env bash
set -euo pipefail

# Slicer URI Bridge macOS installer
#
# What it does:
#   1. Finds Python 3.11+
#   2. If Python 3.11+ is missing, installs Homebrew Python 3.12
#   3. Creates a private virtual environment
#   4. Installs / upgrades Slicer URI Bridge into that environment
#   5. Creates a wrapper in ~/.local/bin/slicer-uri-bridge
#   6. Creates config if missing
#   7. Registers URI handlers
#   8. Shows how to test the registered handler
#
# To update later, run this installer again.

PROJECT_SPEC="https://github.com/mbv06/slicer-uri-bridge/archive/refs/heads/main.zip"

APP_HOME="${HOME}/.local/share/slicer-uri-bridge"
VENV="${APP_HOME}/venv"
LOCAL_BIN="${HOME}/.local/bin"
WRAPPER="${LOCAL_BIN}/slicer-uri-bridge"
BIN="${VENV}/bin/slicer-uri-bridge"

MIN_MAJOR=3
MIN_MINOR=11
BREW_PYTHON_FORMULA="python@3.12"

log() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

python_is_compatible() {
  local python="$1"

  "$python" - <<PY >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (${MIN_MAJOR}, ${MIN_MINOR}) else 1)
PY
}

find_python311() {
  local candidate path

  for candidate in \
    python3.14 python3.13 python3.12 python3.11 \
    /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
    /usr/local/bin/python3.14 /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 \
    python3
  do
    path=""

    if command -v "$candidate" >/dev/null 2>&1; then
      path="$(command -v "$candidate")"
    elif [ -x "$candidate" ]; then
      path="$candidate"
    fi

    if [ -n "$path" ] && python_is_compatible "$path"; then
      printf '%s\n' "$path"
      return 0
    fi
  done

  return 1
}

install_homebrew_python() {
  if ! command -v brew >/dev/null 2>&1; then
    cat >&2 <<'EOF'
Python 3.11+ was not found.

Homebrew is required to install Python automatically.
Install Homebrew first:

  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

Then run this installer again.
EOF
    exit 1
  fi

  log "Installing Python with Homebrew"
  brew install "${BREW_PYTHON_FORMULA}"

  local brew_python
  brew_python="$(brew --prefix "${BREW_PYTHON_FORMULA}")/bin/python3.12"

  if [ ! -x "$brew_python" ]; then
    die "Homebrew Python was installed, but this executable was not found: ${brew_python}"
  fi

  if ! python_is_compatible "$brew_python"; then
    "$brew_python" --version >&2 || true
    die "Homebrew Python is not Python ${MIN_MAJOR}.${MIN_MINOR}+."
  fi

  printf '%s\n' "$brew_python"
}

ensure_zprofile_path() {
  if [[ ":${PATH}:" == *":${LOCAL_BIN}:"* ]]; then
    return 0
  fi

  touch "${HOME}/.zprofile"

  if ! grep -Fq 'export PATH="$HOME/.local/bin:$PATH"' "${HOME}/.zprofile"; then
    {
      printf '\n'
      printf '# Slicer URI Bridge\n'
      printf 'export PATH="$HOME/.local/bin:$PATH"\n'
    } >> "${HOME}/.zprofile"
  fi
}

create_wrapper() {
  mkdir -p "$LOCAL_BIN"

  cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
exec "${BIN}" "\$@"
EOF

  chmod +x "$WRAPPER"
}

main() {
  if [ "$(uname -s)" != "Darwin" ]; then
    die "This installer is for macOS only."
  fi

  log "Checking Python ${MIN_MAJOR}.${MIN_MINOR}+"

  local python
  if python="$(find_python311)"; then
    printf 'Using Python: %s\n' "$python"
  else
    python="$(install_homebrew_python)"
    printf 'Using Python: %s\n' "$python"
  fi

  log "Checking built-in venv support"
  if ! "$python" -m venv --help >/dev/null 2>&1; then
    die "This Python does not support the built-in venv module. Install a full Python ${MIN_MAJOR}.${MIN_MINOR}+ distribution and run this installer again."
  fi

  log "Creating private Python environment"
  mkdir -p "$APP_HOME"
  "$python" -m venv "$VENV"

  log "Installing / upgrading Slicer URI Bridge"
  "${VENV}/bin/python" -m pip install --upgrade pip
  "${VENV}/bin/python" -m pip install --upgrade "$PROJECT_SPEC"

  log "Creating command wrapper"
  create_wrapper
  ensure_zprofile_path

  log "Creating config if needed"
  "$BIN" init-config || true

  log "Registering URI handlers"
  "$BIN" register --auto

  cat <<EOF

✅ Done! Slicer URI Bridge is installed.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  📌 Command:     slicer-uri-bridge
  ⚙️  Config:      ~/.config/slicer-uri-bridge/config.toml
  📂 Logs:        ~/.config/slicer-uri-bridge/launcher.log
                  ~/.config/slicer-uri-bridge/bridge.log
  🧪 Test:        slicer-uri-bridge test
  📦 Environment:  ${VENV}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ⚠️  If "slicer-uri-bridge" is not found, open a new Terminal window.
  🔄 To update later, just run this installer again.

EOF
}

main "$@"

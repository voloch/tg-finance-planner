#!/usr/bin/env bash
# Single entrypoint: creates/updates the venv as needed, then runs the bot.
# Works on any Linux box: ./run.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR=".venv"
REQS_HASH_FILE="$VENV_DIR/.reqs.sha256"

if [ ! -d "$VENV_DIR" ]; then
    echo "[run.sh] creating virtualenv at $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

CURRENT_HASH="$(sha256sum requirements.txt | cut -d' ' -f1)"
STORED_HASH="$(cat "$REQS_HASH_FILE" 2>/dev/null || true)"

if [ "$CURRENT_HASH" != "$STORED_HASH" ]; then
    echo "[run.sh] installing/updating dependencies"
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install -r requirements.txt -q
    echo "$CURRENT_HASH" > "$REQS_HASH_FILE"
fi

exec "$VENV_DIR/bin/python" -m bot "$@"

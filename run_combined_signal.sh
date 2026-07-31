#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN="/Users/khosoya/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
"$PYTHON_BIN" "$SCRIPT_DIR/signal_system/signal_engine.py"
"$PYTHON_BIN" "$SCRIPT_DIR/generate_combined_signal.py"
"$PYTHON_BIN" "$SCRIPT_DIR/generate_market_history.py"

#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
cd "$SCRIPT_DIR"

mkdir -p output logs
/usr/bin/env python3 signal_engine.py --output-dir output \
  >> logs/signal.log 2>> logs/signal-error.log

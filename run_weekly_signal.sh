#!/bin/zsh
set -euo pipefail

project_dir="/Users/khosoya/20260731_Investment"
python_bin="/Users/khosoya/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"

cd "$project_dir"
export REFRESH_DATA=1
"$python_bin" generate_weekly_signal.py
"$python_bin" signal_system/signal_engine.py
"$python_bin" generate_combined_signal.py
"$python_bin" generate_market_history.py

#!/usr/bin/env bash
# TaskOps negative-path battery runner (thin wrapper around run_battery.py).
#
# Deterministic, no LLM / no API cost. Generates self-contained fixtures under
# ./_build, runs the real `taskops` CLI (validate / classify-runnable / summary /
# audit / queue), and writes results.json next to this script.
#
#   TASKOPS_BIN=~/.npm-global/bin/taskops ./run_battery.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${TASKOPS_BIN:=$HOME/.npm-global/bin/taskops}"
export TASKOPS_BIN
exec python3 "$HERE/run_battery.py"

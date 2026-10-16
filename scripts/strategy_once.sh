#!/usr/bin/env bash
# Strategy tier, after the analysis tier — steps live in
# scripts/orchestration/strategy_once.json; run_steps.sh runs them one by
# one.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bash "$SCRIPT_DIR/run_steps.sh" "$SCRIPT_DIR/orchestration/strategy_once.json"

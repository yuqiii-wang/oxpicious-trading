#!/usr/bin/env bash
# Semi-annual builds tier — steps live in
# scripts/orchestration/builds_semiannually.json; run_steps.sh runs them
# one by one.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bash "$SCRIPT_DIR/run_steps.sh" "$SCRIPT_DIR/orchestration/builds_semiannually.json"

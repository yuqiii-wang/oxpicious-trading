#!/usr/bin/env bash
# One-time builds tier — steps live in
# scripts/orchestration/builds_once.json; run_steps.sh runs them one by
# one (sec_info consumes the classification output).
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bash "$SCRIPT_DIR/run_steps.sh" "$SCRIPT_DIR/orchestration/builds_once.json"

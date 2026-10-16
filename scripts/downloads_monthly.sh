#!/usr/bin/env bash
# Monthly downloads tier — steps live in
# scripts/orchestration/downloads_monthly.json; run_steps.sh runs them
# one by one.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bash "$SCRIPT_DIR/run_steps.sh" "$SCRIPT_DIR/orchestration/downloads_monthly.json"

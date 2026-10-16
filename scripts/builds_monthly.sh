#!/usr/bin/env bash
# Monthly builds tier — steps live in
# scripts/orchestration/builds_monthly.json; run_steps.sh runs them one
# by one. Invoked nightly from main_run.sh after the daily/once builds
# (importance needs their turnover outputs); the importance build's own
# monthly guard makes every non-first run of the month a no-op.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bash "$SCRIPT_DIR/run_steps.sh" "$SCRIPT_DIR/orchestration/builds_monthly.json"

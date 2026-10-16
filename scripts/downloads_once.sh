#!/usr/bin/env bash
# One-time downloads tier (full-history archive seeds) — steps live in
# scripts/orchestration/downloads_once.json; run_steps.sh runs them one
# by one (serial: heavy concurrent bulk DB writes).
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bash "$SCRIPT_DIR/run_steps.sh" "$SCRIPT_DIR/orchestration/downloads_once.json"

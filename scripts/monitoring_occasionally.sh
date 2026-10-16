#!/usr/bin/env bash
# Monitoring, run before heavy writes so replica lag, WAL slot pinning, or
# a pg_wal dir growing past its max_wal_size recycle horizon are visible
# first. Steps live in scripts/orchestration/monitoring_occasionally.json;
# alert-only: monitoring failures do not prevent the scheduled work.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bash "$SCRIPT_DIR/run_steps.sh" "$SCRIPT_DIR/orchestration/monitoring_occasionally.json"

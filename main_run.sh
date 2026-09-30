#!/usr/bin/env bash

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# Monitoring must run before scheduled writes.
bash "$SCRIPT_DIR/scripts/monitoring_occasionally.sh"

# Daily and monthly downloads.
bash "$SCRIPT_DIR/scripts/downloads_daily.sh"
bash "$SCRIPT_DIR/scripts/downloads_monthly.sh"
bash "$SCRIPT_DIR/scripts/downloads_semiannually.sh"
bash "$SCRIPT_DIR/scripts/downloads_once.sh"

# Builds consume the downloaded data.
bash "$SCRIPT_DIR/scripts/builds_daily.sh"
bash "$SCRIPT_DIR/scripts/builds_semiannually.sh"
bash "$SCRIPT_DIR/scripts/builds_once.sh"

# Analysis consumes the daily build outputs; strategy runs after analysis.
bash "$SCRIPT_DIR/scripts/analysis_daily.sh"
bash "$SCRIPT_DIR/scripts/strategy_once.sh"
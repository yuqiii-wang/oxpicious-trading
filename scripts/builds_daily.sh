#!/usr/bin/env bash
# Daily builds tier, after downloads_daily.sh. Steps + per-step ordering
# notes live in scripts/orchestration/builds_daily.json — strict sequence:
# feeder_nav right after etf (CASCADE truncate), industry after index,
# market_regimes after the basic_stats builds and before the analysis
# tier. Same guard as the daily downloads (trading day AND time >= 18:00);
# FORCE_DOWNLOADS=1 bypasses it for manual/test runs.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
_is_biz_date=$(python -c "from _common._holidays_and_weekdays import is_trading_day; from datetime import date; print(int(is_trading_day(date.today())))")
_cur_hm=$(date +%H%M)
if [ "${FORCE_DOWNLOADS:-0}" = "1" ] || { [ "$_is_biz_date" = "1" ] && [ "$_cur_hm" -ge 1800 ]; }; then
  bash "$SCRIPT_DIR/run_steps.sh" "$SCRIPT_DIR/orchestration/builds_daily.json"
fi

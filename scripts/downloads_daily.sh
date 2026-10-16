#!/usr/bin/env bash
# Daily downloads tier. Steps live in
# scripts/orchestration/downloads_daily.json; run_steps.sh runs them one
# by one. Guard: A-share trading day
# AND time >= 18:00 (automation starts at 18:00). FORCE_DOWNLOADS=1
# bypasses the guard for manual/test runs.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
_is_biz_date=$(python -c "from _common._holidays_and_weekdays import is_trading_day; from datetime import date; print(int(is_trading_day(date.today())))")
_cur_hm=$(date +%H%M)
if [ "${FORCE_DOWNLOADS:-0}" = "1" ] || { [ "$_is_biz_date" = "1" ] && [ "$_cur_hm" -ge 1800 ]; }; then
  bash "$SCRIPT_DIR/run_steps.sh" "$SCRIPT_DIR/orchestration/downloads_daily.json"
fi

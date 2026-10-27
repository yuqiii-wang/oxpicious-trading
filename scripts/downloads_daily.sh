#!/usr/bin/env bash
# Daily downloads tier. Steps live in
# scripts/orchestration/downloads_daily.json; run_steps.sh runs them one
# by one. Guard: A-share trading day
# AND time >= 18:00 (automation starts at 18:00). FORCE_DOWNLOADS=1
# bypasses the guard for manual/test runs.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# Single-instance guard + await: hold an exclusive flock while this tier
# runs. A second entry (automation overlap, the user's main_run.sh racing
# the scheduled chain, a manual run colliding with a scheduled one) WAITS
# for the lock instead of colliding — heartbeat auto-exits, duplicate
# CSIndex walks and GPU contention all stem from that overlap. Bounded
# wait (AWAIT_LOCK_SEC, default 4h); on timeout exit 1 rather than run
# two concurrent tiers.
_TIER=downloads
_LOCK=/tmp/oxpicious_${_TIER}_daily.lock
_AWAIT_SEC=${AWAIT_LOCK_SEC:-14400}
exec 9>"$_LOCK"
if ! flock -w "$_AWAIT_SEC" 9; then
  echo "[$_TIER] lock wait timed out after ${_AWAIT_SEC}s — another "
  echo "[$_TIER] instance still holds it; refusing to run concurrently." >&2
  exit 1
fi
_is_biz_date=$(python -c "from _common._holidays_and_weekdays import is_trading_day; from datetime import date; print(int(is_trading_day(date.today())))")
_cur_hm=$(date +%H%M)
if [ "${FORCE_DOWNLOADS:-0}" = "1" ] || { [ "$_is_biz_date" = "1" ] && [ "$_cur_hm" -ge 1800 ]; }; then
  bash "$SCRIPT_DIR/run_steps.sh" "$SCRIPT_DIR/orchestration/downloads_daily.json"
fi

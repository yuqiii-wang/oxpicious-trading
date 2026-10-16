#!/usr/bin/env bash
# Always-on supervisor for the downloads intraday stream.
#
# Idempotent: starts `python -m database.stream_watchdog` only if no
# watchdog process is already running. The watchdog restarts dead or
# wedged downloads.stream.* pollers (see database/stream_watchdog.py);
# stream_always.sh stays the manual one-shot starter, but running it
# while the watchdog is active can duplicate pollers.
#
# Requires the conda env on PATH (run via main_run.sh / an activated
# WSL shell), like scripts/stream_always.sh.
if pgrep -f "python.* -m database\.stream_watchdog" > /dev/null 2>&1; then
  echo "stream_watchdog already running"
  exit 0
fi

_log_dir="logs/$(date +%F)"
mkdir -p "$_log_dir"
# One date one service one log file: stdout/stderr append to the SAME
# logs/<date>/database.stream_watchdog.log the service's _common/log_setup
# handler writes (both writers append).
nohup python -u -m database.stream_watchdog \
  >> "$_log_dir/database.stream_watchdog.log" 2>&1 &
echo "stream_watchdog started (pid $!)"

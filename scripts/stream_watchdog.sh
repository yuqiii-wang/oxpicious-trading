#!/usr/bin/env bash
# Always-on supervisor for the downloads intraday stream.
#
# Idempotent: starts `python -m database.stream_watchdog` only if no
# watchdog process is already running. Since the completed 2026-11
# resident migration the watchdog's ONE scan charge is the monitor
# server (every other always-on process — the pollers included — is an
# area server's resident, and the watchdog itself is the monitor's
# resident; see database/stream_watchdog.py). The monitor's supervisor
# revives this watchdog when it dies; the pollers come up with the
# fleet (POST /residents/ensure-up — the maintainer's daily 07:00 pass
# or the UI's "Start stream always" button).
#
# Requires the conda env on PATH (run via the WSL boot hook or an
# activated WSL shell).
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

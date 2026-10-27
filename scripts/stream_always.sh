# Manual one-shot starter for the intraday pollers. In normal operation
# the stream_watchdog supervisor (python -m database.stream_watchdog,
# started by scripts/stream_watchdog.sh) keeps these running and restarts
# them when they die or wedge. Idempotent: a poller already running is
# skipped (the pgrep guard), so a redundant start — the UI's stream-always
# session, main_run.sh, a second click — is a no-op.
_log_dir="logs/$(date +%F)"
mkdir -p "$_log_dir"

# One date one service one log file: stdout/stderr append to the SAME
# logs/<date>/<service>.log the service's _common/log_setup handler
# writes (both writers append), so prints and log records share a file.
for m in \
  downloads.stream.sse.price \
  downloads.stream.szse.price \
  downloads.stream.bse.price \
  downloads.stream.csindex.price \
  downloads.stream.cnindex.price
do
  if pgrep -f "python.* -m ${m//./\.}" > /dev/null 2>&1; then
    echo "$m already running"
  else
    nohup python -u -m "$m" >> "$_log_dir/$m.log" 2>&1 &
    sleep 1
  fi
done

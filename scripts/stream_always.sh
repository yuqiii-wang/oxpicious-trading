# Manual one-shot starter for the intraday pollers. In normal operation
# scripts/stream_watchdog.sh (wired into main_run.sh) keeps these running
# and restarts them when they die or wedge; running this script while a
# watchdog is active can duplicate pollers.
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
  nohup python -u -m "$m" >> "$_log_dir/$m.log" 2>&1 &
  sleep 1
done

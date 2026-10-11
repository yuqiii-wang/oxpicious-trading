# Manual one-shot starter for the intraday pollers. In normal operation
# scripts/stream_watchdog.sh (wired into main_run.sh) keeps these running
# and restarts them when they die or wedge; running this script while a
# watchdog is active can duplicate pollers.
_log_dir="logs/$(date +%F)"
mkdir -p "$_log_dir"

for m in \
  downloads.stream.sse.price \
  downloads.stream.szse.price \
  downloads.stream.bse.price \
  downloads.stream.csindex.price \
  downloads.stream.cnindex.price
do
  _log_name="${m//./_}.log"
  nohup python -m "$m" > "$_log_dir/$_log_name" 2>&1 &
  sleep 1
done

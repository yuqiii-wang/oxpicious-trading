# always run
_log_dir="logs/$(date +%F)"
mkdir -p "$_log_dir"

for m in \
  downloads.stream.sse.price \
  downloads.stream.szse.price \
  downloads.stream.csindex.price \
  downloads.stream.cnindex.price
do
  _log_name="${m//./_}.log"
  nohup python -m "$m" > "$_log_dir/$_log_name" 2>&1 &
  sleep 1
done

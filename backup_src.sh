#!/usr/bin/env bash
# backup_src.sh
# Incremental backup of temps/* -> D:\backup_temps\
#
# Coarse cutoff approach:
#   - A marker file (.last_backup_ts) records the last successful run's start time.
#   - On each run, find files modified after the marker (with a 60s safety buffer).
#   - If no marker exists (first run), back up everything.
#   - After successful backup, update the marker to this run's start time.
#   - New files coming in during/after a run are caught on the next run.
set -euo pipefail

SRC="/e/oxpicious-trading/temps"
DST="/d/backup_temps"
STAGING_E="/e/oxpicious-trading/.backup_staging"
STAGING_D="/d/backup_temps/.staging"
MARKER_FILE="$DST/.last_backup_ts"

TS="$(date +%Y%m%d_%H%M%S)"
START_TS=$(date +%s)
ZIP_NAME="temps_backup_${TS}.zip"

if [ ! -d "$SRC" ]; then
  echo "ERROR: source dir not found: $SRC" >&2
  exit 1
fi

mkdir -p "$DST" "$STAGING_E" "$STAGING_D"

# Cleanup trap — always remove staging artifacts, even on error
cleanup() {
  rm -f "$STAGING_E"/*.zip "$STAGING_E"/backup_list_*.txt "$STAGING_D"/*.zip 2>/dev/null || true
}
trap cleanup EXIT

# 1. Determine cutoff and find files to back up
LIST_FILE="$STAGING_E/backup_list_${TS}.txt"
: > "$LIST_FILE"

if [ -f "$MARKER_FILE" ]; then
  LAST_TS=$(cat "$MARKER_FILE")
  # Safety buffer: 60s before last run to avoid edge-case misses
  CUTOFF=$((LAST_TS - 60))
  echo "Last backup: $(date -d "@$LAST_TS" '+%Y-%m-%d %H:%M:%S') | cutoff: $(date -d "@$CUTOFF" '+%Y-%m-%d %H:%M:%S')"
  find "$SRC" -type f -newermt "@$CUTOFF" -printf '%P\n' > "$LIST_FILE"
else
  echo "No marker file — first run, backing up everything."
  find "$SRC" -type f -printf '%P\n' > "$LIST_FILE"
fi

count=$(wc -l < "$LIST_FILE")
echo "Files to back up: $count"

if [ "$count" -eq 0 ]; then
  echo "All up to date. Nothing to do."
  rm -f "$LIST_FILE"
  exit 0
fi

echo "--- File list (first 50) ---"
head -50 "$LIST_FILE"
[ "$count" -gt 50 ] && echo "... ($count total)"
echo "----------------------------"

# 2. Zip (python zipfile — handles Unicode filenames; `zip` binary not in gitbash)
ZIP_E="$STAGING_E/$ZIP_NAME"
SRC_WIN="$(cygpath -w "$SRC")"
LIST_WIN="$(cygpath -w "$LIST_FILE")"
ZIP_E_WIN="$(cygpath -w "$ZIP_E")"
python - "$SRC_WIN" "$LIST_WIN" "$ZIP_E_WIN" <<'PYEOF'
import os, sys, zipfile
src_win, list_win, out_zip = sys.argv[1], sys.argv[2], sys.argv[3]
# Stream paths lazily — avoids loading the entire file list into memory
# (matters when temps/ has hundreds of thousands of files).
count = 0
with open(list_win, encoding='utf-8') as f, \
     zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    for ln in f:
        rel = ln.strip()
        if rel:
            zf.write(os.path.join(src_win, rel), rel)
            count += 1
print(f"Zipped {count} files -> {out_zip}")
PYEOF
echo "Created zip: $ZIP_E ($(du -h "$ZIP_E" | cut -f1))"

# 3. Move zip to D: disk
ZIP_D="$STAGING_D/$ZIP_NAME"
mv "$ZIP_E" "$ZIP_D"
echo "Moved zip to: $ZIP_D"

# 4. Unzip into DST (single sync dir, overwrite in place, preserve paths)
#    Suppress stderr: unzip warns about Unicode "local" filename mismatches but
#    correctly falls back to the "central" (UTF-8) filename — extraction is correct.
(cd "$DST" && unzip -oq "$ZIP_D" 2>/dev/null)
echo "Unzipped into: $DST"

# 5. Update marker — record this run's start time
echo "$START_TS" > "$MARKER_FILE"
echo "Marker updated: $(date -d "@$START_TS" '+%Y-%m-%d %H:%M:%S')"

# 6. Cleanup (also handled by trap)
rm -f "$ZIP_D" "$LIST_FILE"
echo "Done. Backed up $count files to $DST"

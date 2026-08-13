#!/usr/bin/env bash
# backup_all.sh
# Orchestrate both backups in sequence:
#   1. backup_src.sh            (gitbash)  — incremental zip of temps/* -> D:\backup_temps
#   2. database/backup_postgres.sh (WSL)   — logical backup of oxpicious-stats DB
#
# Any args after a `--` separator are forwarded to backup_postgres.sh
# (e.g. `./backup_all.sh -- --base` or `./backup_all.sh -- --list`).
#
# Usage
# -----
#   ./backup_all.sh                     run src backup, then auto pg backup (base or inc)
#   ./backup_all.sh -- --base           force a fresh full pg base backup
#   ./backup_all.sh -- --incremental    force a pg incremental backup
#   ./backup_all.sh -- --list           only list existing pg backups (src backup still runs)
#   ./backup_all.sh --skip-src          skip the src backup, run pg backup only
#   ./backup_all.sh --skip-pg           run src backup only, skip pg backup
#
# Run from gitbash:  ./backup_all.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_SCRIPT="$HERE/backup_src.sh"
PG_SCRIPT_WIN="$HERE/database/backup_postgres.sh"
# WSL view of the same file
PG_SCRIPT_WSL="/mnt/e/oxpicious-trading/database/backup_postgres.sh"
WSL_DISTRO="Ubuntu-22.04"

log()  { printf '[%s] %s\n' "$(date +'%Y-%m-%d %H:%M:%S')" "$*"; }
die()  { log "ERROR: $*"; exit 1; }

# Parse own flags; everything after `--` is forwarded to the pg script.
SKIP_SRC=0
SKIP_PG=0
PG_ARGS=()
saw_sep=0
for arg in "$@"; do
  if [[ $saw_sep -eq 1 ]]; then
    PG_ARGS+=("$arg")
    continue
  fi
  case "$arg" in
    --)         saw_sep=1 ;;
    --skip-src) SKIP_SRC=1 ;;
    --skip-pg)  SKIP_PG=1 ;;
    *)          die "Unknown option: $arg (use --skip-src, --skip-pg, or -- to forward args to pg backup)" ;;
  esac
done

OVERALL_START=$(date +%s)
SRC_RC=0
PG_RC=0

# ----------------------------------------------------------------------------
# 1. Source backup (gitbash native)
# ----------------------------------------------------------------------------
if [[ $SKIP_SRC -eq 1 ]]; then
  log "Skipping src backup (--skip-src)"
else
  [[ -x "$SRC_SCRIPT" ]] || [[ -f "$SRC_SCRIPT" ]] || die "src script not found: $SRC_SCRIPT"
  log "=== [1/2] Source backup: $SRC_SCRIPT ==="
  START=$(date +%s)
  # shellcheck disable=SC1090
  bash "$SRC_SCRIPT" || SRC_RC=$?
  ELAPSED=$(( $(date +%s) - START ))
  if [[ $SRC_RC -ne 0 ]]; then
    log "Source backup FAILED (exit=$SRC_RC) after ${ELAPSED}s"
  else
    log "Source backup OK (${ELAPSED}s)"
  fi
fi

# ----------------------------------------------------------------------------
# 2. PostgreSQL backup (must run in WSL — uses docker exec + /mnt paths)
# ----------------------------------------------------------------------------
if [[ $SKIP_PG -eq 1 ]]; then
  log "Skipping pg backup (--skip-pg)"
else
  [[ -f "$PG_SCRIPT_WIN" ]] || die "pg script not found: $PG_SCRIPT_WIN"
  log "=== [2/2] PostgreSQL backup via WSL ($WSL_DISTRO) ==="
  START=$(date +%s)
  # Quote each forwarded arg safely for the inner bash -lc
  ESCAPED_ARGS=()
  for a in "${PG_ARGS[@]:-}"; do
    ESCAPED_ARGS+=("$(printf '%q' "$a")")
  done
  INNER_CMD="cd /mnt/e/oxpicious-trading && bash '$PG_SCRIPT_WSL' ${ESCAPED_ARGS[*]}"
  wsl -d "$WSL_DISTRO" -- bash -lc "$INNER_CMD" || PG_RC=$?
  ELAPSED=$(( $(date +%s) - START ))
  if [[ $PG_RC -ne 0 ]]; then
    log "PostgreSQL backup FAILED (exit=$PG_RC) after ${ELAPSED}s"
  else
    log "PostgreSQL backup OK (${ELAPSED}s)"
  fi
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------
OVERALL_ELAPSED=$(( $(date +%s) - OVERALL_START ))
log "=== Summary ==="
[[ $SKIP_SRC -eq 1 ]] && log "  src : skipped" || log "  src : $([ $SRC_RC -eq 0 ] && echo OK || echo "FAILED ($SRC_RC)")"
[[ $SKIP_PG  -eq 1 ]] && log "  pg  : skipped" || log "  pg  : $([ $PG_RC  -eq 0 ] && echo OK || echo "FAILED ($PG_RC)")"
log "  total: ${OVERALL_ELAPSED}s"

# Exit non-zero only if a backup that ran actually failed
if [[ $SKIP_SRC -eq 0 && $SRC_RC -ne 0 ]]; then exit $SRC_RC; fi
if [[ $SKIP_PG  -eq 0 && $PG_RC  -ne 0 ]]; then exit $PG_RC;  fi
exit 0

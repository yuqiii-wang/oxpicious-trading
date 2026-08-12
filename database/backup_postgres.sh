#!/usr/bin/env bash
# ============================================================================
# backup_postgres.sh — Incremental logical backup for the oxpicious-stats DB
# ============================================================================
#
# Strategy
# -------
#   First run (no state.json):       full pg_dump  →  base/BASE_TS.sql.gz
#   Subsequent runs (state.json):    per-table inc →  incremental/INC_TS.sql.gz
#
#   Per-table behavior on each incremental:
#     * Tables WITH a date column (date / trade_date / exec_date / as_of_date):
#       dump only rows where date_col > state.tables[t].max_date  (true delta)
#     * Tables WITHOUT a date column (reference & strategy tables):
#       full-table dump with INSERT ... ON CONFLICT (pk) DO UPDATE — safe to
#       replay every run, only changed rows actually write to disk on restore.
#
#   All incremental INSERTs are emitted as
#       INSERT INTO "s"."t" (...) VALUES (...) ON CONFLICT (pk) DO UPDATE SET ...;
#   so applying incrementals is idempotent and order-tolerant.
#
# Restore
# -------
#   1. Apply the most recent base backup to a FRESH database:
#        gunzip -c base/BASE_*.sql.gz | psql -d oxpicious-stats
#   2. Apply every incremental in chronological order:
#        for f in $(ls incremental/INC_*.sql.gz | sort); do
#            gunzip -c "$f" | psql -d oxpicious-stats
#        done
#
#   NOTE: schema changes (DDL) since the last --base are NOT captured by
#   incrementals. After a schema migration, run `./backup_postgres.sh --base`
#   to take a fresh full backup.
#
# Layout (on D: drive)
# --------------------
#   D:\pg_backup\oxpicious-stats\
#   ├── base\BASE_TS.sql.gz          full logical dumps (one per --base run)
#   ├── incremental\INC_TS.sql.gz    incremental dumps (one per run)
#   └── state.json                   per-table max(date_col) marker
#
# Usage
# -----
#   ./backup_postgres.sh              auto: base if no state, else incremental
#   ./backup_postgres.sh --base       force a new full base backup (resets state)
#   ./backup_postgres.sh --incremental  force incremental (errors if no state)
#   ./backup_postgres.sh --list       list existing backups + state summary
#
# Requirements
# ------------
#   * Docker Desktop with WSL integration (uses `docker exec oxpicious-db` for
#     pg_dump/psql)  — OR — host-installed psql/pg_dump (auto-detected fallback)
#   * jq   (state.json manipulation)
#   * gzip
#
#   Run from WSL:  wsl -d Ubuntu-22.04 -- bash -lc "/mnt/e/oxpicious-trading/database/backup_postgres.sh"
# ============================================================================

set -euo pipefail

# ===== Config (override via env) =====
DB_NAME="${SUPABASE_DB:-oxpicious-stats}"
DB_USER="${SUPABASE_USER:-postgres}"
export PGPASSWORD="${SUPABASE_PASSWORD:-postgres}"
DB_HOST="${SUPABASE_HOST:-127.0.0.1}"
DB_PORT="${SUPABASE_PORT:-9876}"
CONTAINER="${DB_CONTAINER:-oxpicious-db}"

# WSL path for D:\pg_backup\oxpicious-stats\
BACKUP_ROOT="${BACKUP_ROOT:-/mnt/d/pg_backup/oxpicious-stats}"
BASE_DIR="${BACKUP_ROOT}/base"
INC_DIR="${BACKUP_ROOT}/incremental"
STATE_FILE="${BACKUP_ROOT}/state.json"

# Schemas to back up (dependency order: stats first, then analysis, then strategy)
SCHEMAS=("stats" "analysis" "strategy")

# Date column candidates, in priority order (used for incremental WHERE filter)
DATE_COL_CANDIDATES=("date" "trade_date" "exec_date" "as_of_date")

# ===== Helpers =====
log()  { printf '[%s] %s\n' "$(date +'%Y-%m-%d %H:%M:%S')" "$*" >&2; }
die()  { log "ERROR: $*"; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"; }

# Detect pg tooling backend (docker exec preferred, host psql fallback)
BACKEND=""
detect_backend() {
  if docker exec "$CONTAINER" pg_isready -U "$DB_USER" >/dev/null 2>&1; then
    BACKEND="docker"
  elif command -v psql >/dev/null 2>&1 && command -v pg_dump >/dev/null 2>&1; then
    BACKEND="host"
  else
    die "Cannot reach pg tools: 'docker exec $CONTAINER' failed and host psql/pg_dump not on PATH."
  fi
}

psql_exec() {
  if [[ "$BACKEND" == "docker" ]]; then
    # NOTE: no -i flag — all calls use -c (no stdin needed). Without this,
    # docker exec -i inside a `while read` loop would consume the loop's stdin.
    docker exec -e PGPASSWORD="$PGPASSWORD" "$CONTAINER" \
      psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 "$@" < /dev/null
  else
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 "$@" < /dev/null
  fi
}

pgdump_exec() {
  if [[ "$BACKEND" == "docker" ]]; then
    docker exec -e PGPASSWORD="$PGPASSWORD" "$CONTAINER" \
      pg_dump -U "$DB_USER" -d "$DB_NAME" "$@" < /dev/null
  else
    pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" "$@" < /dev/null
  fi
}

# List all base tables in target schemas (TSV: schema<TAB>table)
get_tables() {
  local schema_list
  schema_list=$(IFS=,; echo "${SCHEMAS[*]}")
  psql_exec -t -A -F $'\t' -c "
    SELECT table_schema, table_name
    FROM information_schema.tables
    WHERE table_schema = ANY (STRING_TO_ARRAY('${schema_list}', ','))
      AND table_type = 'BASE TABLE'
    ORDER BY table_schema, table_name;"
}

# Best-priority date column for a table (empty string if none)
get_date_col() {
  local schema="$1" table="$2"
  psql_exec -t -A -c "
    SELECT column_name FROM (
      SELECT column_name,
             CASE column_name
               WHEN 'date'       THEN 1
               WHEN 'trade_date' THEN 2
               WHEN 'exec_date'  THEN 3
               WHEN 'as_of_date' THEN 4
               ELSE 99 END AS prio
      FROM information_schema.columns
      WHERE table_schema='${schema}' AND table_name='${table}'
        AND column_name IN ('date','trade_date','exec_date','as_of_date')
    ) s ORDER BY prio LIMIT 1;" | tr -d '[:space:]'
}

# Comma-separated PK column names (in index order); empty if no PK
get_pk_cols() {
  local schema="$1" table="$2"
  psql_exec -t -A -c "
    SELECT string_agg(a.attname, ',' ORDER BY array_position(i.indkey, a.attnum))
    FROM pg_index i
    JOIN pg_attribute a
      ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
    WHERE i.indrelid = '\"${schema}\".\"${table}\"'::regclass AND i.indisprimary;" | tr -d '[:space:]'
}

# Comma-separated column names in ordinal order
get_all_cols() {
  local schema="$1" table="$2"
  psql_exec -t -A -c "
    SELECT string_agg(column_name, ',' ORDER BY ordinal_position)
    FROM information_schema.columns
    WHERE table_schema='${schema}' AND table_name='${table}';" | tr -d '[:space:]'
}

# Quote a comma-separated identifier list: a,b -> "a","b"
quote_ident_list() {
  local csv="$1"
  [[ -z "$csv" ]] && { echo ""; return; }
  echo "$csv" | awk -F, '{for(i=1;i<=NF;i++){printf "\"%s\"%s", $i, (i<NF?",":"")}}'
}

# Build "col"=EXCLUDED."col" pairs for non-PK columns: a,b (pk=a) -> "b"=EXCLUDED."b"
build_set_clause() {
  local all_cols="$1" pk_cols="$2"
  local pk_arr=(${pk_cols//,/ })
  local result=""
  IFS=, read -ra cols <<< "$all_cols"
  for c in "${cols[@]}"; do
    local is_pk=0
    for p in "${pk_arr[@]}"; do [[ "$p" == "$c" ]] && { is_pk=1; break; }; done
    [[ $is_pk -eq 1 ]] && continue
    [[ -n "$result" ]] && result+=","
    result+="\"${c}\"=EXCLUDED.\"${c}\""
  done
  echo "$result"
}

# Build the per-column SELECT expression that emits quoted literal values:
#   COALESCE(quote_literal("c1"::text),'NULL')||','||COALESCE(quote_literal("c2"::text),'NULL')
build_values_expr() {
  local cols_csv="$1"
  local result=""
  IFS=, read -ra cols <<< "$cols_csv"
  for i in "${!cols[@]}"; do
    [[ $i -gt 0 ]] && result+="||','||"
    result+="COALESCE(quote_literal(\"${cols[$i]}\"::text),'NULL')"
  done
  echo "$result"
}

# ===== Actions =====

do_base_backup() {
  local ts base_file
  ts=$(date +%Y%m%d_%H%M%S)
  base_file="${BASE_DIR}/BASE_${ts}.sql.gz"

  log "Starting full base backup → ${base_file}"
  mkdir -p "$BASE_DIR" "$INC_DIR"

  # Base backup uses pg_dump's default COPY format (10x faster than
  # --column-inserts on a 25GB DB). Base is applied to an EMPTY database so
  # ON CONFLICT is not needed. Comments (table/column docs) are kept.
  pgdump_exec \
    --schema=stats --schema=analysis --schema=strategy \
    --no-owner --no-privileges \
    | gzip -9 > "$base_file"

  local size; size=$(stat -c %s "$base_file" 2>/dev/null || stat -f %z "$base_file")
  log "Base backup complete: ${base_file} ($(numfmt --to=iec $size 2>/dev/null || echo "${size}B"))"

  # Initialize state.json with current max(date_col) per table
  init_state "$base_file"
  log "State initialized at ${STATE_FILE}"
}

init_state() {
  local base_file="$1"
  local ts; ts=$(date -Iseconds)
  local tmp; tmp=$(mktemp)

  {
    echo "{"
    echo "  \"base_backup\": \"$(basename "$base_file")\","
    echo "  \"base_backup_at\": \"${ts}\","
    echo "  \"last_incremental\": null,"
    echo "  \"last_incremental_at\": null,"
    echo "  \"tables\": {"

    local rows line schema table date_col max_date first=1
    mapfile -t rows < <(get_tables)
    for line in "${rows[@]}"; do
      [[ -z "$line" ]] && continue
      IFS=$'\t' read -r schema table <<< "$line"
      date_col=$(get_date_col "$schema" "$table")
      if [[ -n "$date_col" ]]; then
        max_date=$(psql_exec -t -A -c "SELECT max(${date_col})::text FROM \"${schema}\".\"${table}\";" | tr -d '[:space:]')
      else
        max_date=""
      fi
      [[ $first -eq 0 ]] && echo ","
      printf '    "%s.%s": {"date_col": %s, "max_date": %s}' \
        "$schema" "$table" \
        "$( [[ -n "$date_col" ]] && echo "\"$date_col\"" || echo "null" )" \
        "$( [[ -n "$max_date" ]] && echo "\"$max_date\"" || echo "null" )"
      first=0
    done
    echo ""
    echo "  }"
    echo "}"
  } > "$tmp"

  mv "$tmp" "$STATE_FILE"
}

do_incremental_backup() {
  [[ -f "$STATE_FILE" ]] || die "No state file at ${STATE_FILE}. Run with --base first."

  local ts inc_file
  ts=$(date +%Y%m%d_%H%M%S)
  inc_file="${INC_DIR}/INC_${ts}.sql.gz"
  mkdir -p "$INC_DIR"

  log "Starting incremental backup → ${inc_file}"

  local tmp; tmp=$(mktemp)
  {
    echo "-- ============================================================================"
    echo "-- Incremental backup ${ts}"
    echo "-- Source DB : ${DB_NAME}"
    echo "-- Backend   : ${BACKEND}"
    echo "-- Apply AFTER the most recent BASE_*.sql.gz, in chronological order."
    echo "-- Idempotent: every statement is INSERT ... ON CONFLICT (pk) DO UPDATE."
    echo "-- ============================================================================"
    echo "BEGIN;"
    echo ""

    local rows line schema table date_col pk_cols all_cols
    local last_max where_clause values_expr set_clause insert_prefix
    local row_count
    local total_rows=0
    local tables_touched=0

    mapfile -t rows < <(get_tables)
    for line in "${rows[@]}"; do
      [[ -z "$line" ]] && continue
      IFS=$'\t' read -r schema table <<< "$line"

      date_col=$(jq -r --arg k "${schema}.${table}" '.tables[$k].date_col // empty' "$STATE_FILE")
      last_max=$(jq -r --arg k "${schema}.${table}" '.tables[$k].max_date // empty' "$STATE_FILE")

      pk_cols=$(get_pk_cols "$schema" "$table")
      all_cols=$(get_all_cols "$schema" "$table")

      [[ -z "$all_cols" ]] && { log "  skip ${schema}.${table} (no columns)"; continue; }

      # Build WHERE clause for date-keyed tables
      where_clause=""
      if [[ -n "$date_col" && -n "$last_max" && "$last_max" != "null" ]]; then
        where_clause="WHERE \"${date_col}\" > '${last_max}'::date"
      fi

      # Build INSERT prefix and ON CONFLICT clause
      local quoted_cols quoted_pk
      quoted_cols=$(quote_ident_list "$all_cols")
      quoted_pk=$(quote_ident_list "$pk_cols")
      insert_prefix="INSERT INTO \"${schema}\".\"${table}\" (${quoted_cols}) VALUES "

      local conflict_clause=""
      if [[ -n "$pk_cols" ]]; then
        local set_clause
        set_clause=$(build_set_clause "$all_cols" "$pk_cols")
        if [[ -n "$set_clause" ]]; then
          conflict_clause=" ON CONFLICT (${quoted_pk}) DO UPDATE SET ${set_clause}"
        else
          conflict_clause=" ON CONFLICT (${quoted_pk}) DO NOTHING"
        fi
      fi

      # Per-row VALUES expression (quoted literals joined by commas)
      values_expr=$(build_values_expr "$all_cols")

      # Emit per-row INSERTs via a single SQL query that builds the statement
      # text. Cache the output so we only query once per table.
      local sql
      sql="SELECT '${insert_prefix}' || (${values_expr}) || ')' || '${conflict_clause};' FROM \"${schema}\".\"${table}\" ${where_clause};"

      local section_out
      section_out=$(psql_exec -t -A -c "$sql" || true)
      local section_rows=0
      if [[ -n "$section_out" ]]; then
        section_rows=$(printf '%s\n' "$section_out" | grep -c '^INSERT INTO ' || true)
      fi
      total_rows=$((total_rows + section_rows))
      [[ $section_rows -gt 0 ]] && tables_touched=$((tables_touched + 1))

      # Emit a section header then the INSERTs
      echo "-- ----------------------------------------------------------------------------"
      echo "-- Table: ${schema}.${table}  (date_col=${date_col:-none}, last_max=${last_max:-none}, rows=${section_rows})"
      echo "-- ----------------------------------------------------------------------------"
      if [[ $section_rows -gt 0 ]]; then
        printf '%s\n' "$section_out"
      else
        echo "-- (no new rows)"
      fi
      echo ""
    done

    echo "COMMIT;"
    echo ""
    echo "-- End of incremental ${ts}: ${total_rows} row(s) across ${tables_touched} table(s)"
  } > "$tmp"

  gzip -9 < "$tmp" > "$inc_file"
  rm -f "$tmp"

  local size; size=$(stat -c %s "$inc_file" 2>/dev/null || stat -f %z "$inc_file")
  log "Incremental backup complete: ${inc_file} ($(numfmt --to=iec $size 2>/dev/null || echo "${size}B"))"

  # Refresh state.json with new max(date_col) per table
  update_state "$inc_file"
  log "State updated at ${STATE_FILE}"
}

update_state() {
  local inc_file="$1"
  local ts; ts=$(date -Iseconds)
  local tmp; tmp=$(mktemp)

  # Bump last_incremental fields
  jq --arg inc "$(basename "$inc_file")" --arg ts "$ts" \
     '.last_incremental=$inc | .last_incremental_at=$ts' \
     "$STATE_FILE" > "$tmp"

  # Re-query max(date_col) for every table that has one
  local rows line schema table date_col max_date
  mapfile -t rows < <(get_tables)
  for line in "${rows[@]}"; do
    [[ -z "$line" ]] && continue
    IFS=$'\t' read -r schema table <<< "$line"
    date_col=$(jq -r --arg k "${schema}.${table}" '.tables[$k].date_col // empty' "$tmp")
    [[ -z "$date_col" ]] && continue
    max_date=$(psql_exec -t -A -c "SELECT max(${date_col})::text FROM \"${schema}\".\"${table}\";" | tr -d '[:space:]')
    jq --arg k "${schema}.${table}" --arg md "${max_date}" \
       '.tables[$k].max_date = $md' "$tmp" > "${tmp}.2" && mv "${tmp}.2" "$tmp"
  done

  mv "$tmp" "$STATE_FILE"
}

do_list() {
  log "Backup root: ${BACKUP_ROOT}"
  echo
  if [[ -d "$BASE_DIR" ]]; then
    echo "Base backups:"
    ls -lh "$BASE_DIR"/BASE_*.sql.gz 2>/dev/null | awk '{printf "  %s  %s\n", $5, $9}' || echo "  (none)"
  else
    echo "Base backups: (directory missing)"
  fi
  echo
  if [[ -d "$INC_DIR" ]]; then
    echo "Incremental backups:"
    ls -lh "$INC_DIR"/INC_*.sql.gz 2>/dev/null | awk '{printf "  %s  %s\n", $5, $9}' || echo "  (none)"
  else
    echo "Incremental backups: (directory missing)"
  fi
  echo
  if [[ -f "$STATE_FILE" ]]; then
    echo "State:"
    jq '{base_backup, base_backup_at, last_incremental, last_incremental_at,
         tables_with_date: [.tables | to_entries[] | select(.value.date_col != null) | .key] | length,
         tables_no_date:   [.tables | to_entries[] | select(.value.date_col == null) | .key] | length}' \
       "$STATE_FILE"
  else
    echo "State: (none — run with --base to initialize)"
  fi
}

# ===== Main =====

need_cmd jq
need_cmd gzip
detect_backend
log "Backend: ${BACKEND}  DB: ${DB_NAME}  Container: ${CONTAINER}"

MODE="auto"
case "${1:-}" in
  --base)         MODE="base" ;;
  --incremental)  MODE="incremental" ;;
  --list)         MODE="list" ;;
  --help|-h|"")   grep '^#' "$0" | head -n 50; exit 0 ;;
  *)              die "Unknown option: $1 (use --base, --incremental, --list, or --help)" ;;
esac

case "$MODE" in
  list)         do_list ;;
  base)         do_base_backup ;;
  incremental)  do_incremental_backup ;;
  auto)
    if [[ -f "$STATE_FILE" ]]; then
      do_incremental_backup
    else
      do_base_backup
    fi
    ;;
esac

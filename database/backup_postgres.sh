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
#       dump only rows where date_col > state.tables[t].max_date  (true delta),
#       streamed in MONTHLY BATCHES so each query is bounded (a single query
#       over a 72M-row / 15GB table would otherwise blow up memory).
#     * Tables WITHOUT a date column (reference & strategy tables):
#       SKIPPED if row_count is unchanged since last run (avoids re-dumping
#       1.6M-row reference tables every run). When changed, full-table dump
#       with INSERT ... ON CONFLICT (pk) DO UPDATE.
#
#   Memory safety
#   -------------
#   All table dumps STREAM psql output directly to a temp file via a server-side
#   cursor (FETCH_SIZE=10000) — no shell variable capture. Memory stays bounded
#   at ~10k rows regardless of table size. Previously the entire INSERT text for
#   each table was captured in a bash variable, which would OOM on multi-GB
#   tables (sec_alloc_perf_attribution alone produces ~15-20GB of INSERT text).
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
#   ./backup_postgres.sh --init-state  rebuild state.json from latest base (no dump)
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
    # FETCH_SIZE=10000 makes psql use a server-side cursor for SELECT queries,
    # streaming rows in batches of 10k instead of materializing the ENTIRE
    # result set in client memory (critical for multi-GB table dumps).
    docker exec -e PGPASSWORD="$PGPASSWORD" "$CONTAINER" \
      psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -v FETCH_SIZE=10000 "$@" < /dev/null
  else
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -v FETCH_SIZE=10000 "$@" < /dev/null
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

# ===== Streaming dump helpers (memory-bounded) =====
#
# The original implementation captured each table's entire INSERT output in a
# shell variable:  section_out=$(psql_exec -t -A -c "$sql")
# For multi-GB tables (sec_alloc_perf_attribution=72M rows / 6.9GB) this would
# load 10-20GB of text into a single bash variable → OOM.
#
# These helpers instead STREAM psql output directly to a per-table temp file.
# Combined with FETCH_SIZE=10000 (server-side cursor in psql_exec), memory stays
# bounded at ~10k rows regardless of table size.

# dump_table_streamed — stream INSERT statements for one WHERE clause to out_file.
# Args: schema table where_clause all_cols pk_cols out_file
# Output: appends INSERT lines to out_file. Does NOT capture in shell variables.
dump_table_streamed() {
  local schema="$1" table="$2" where_clause="$3" all_cols="$4" pk_cols="$5" out_file="$6"

  local quoted_cols quoted_pk insert_prefix conflict_clause values_expr sql
  quoted_cols=$(quote_ident_list "$all_cols")
  quoted_pk=$(quote_ident_list "$pk_cols")
  insert_prefix="INSERT INTO \"${schema}\".\"${table}\" (${quoted_cols}) VALUES "

  conflict_clause=""
  if [[ -n "$pk_cols" ]]; then
    local set_clause
    set_clause=$(build_set_clause "$all_cols" "$pk_cols")
    if [[ -n "$set_clause" ]]; then
      conflict_clause=" ON CONFLICT (${quoted_pk}) DO UPDATE SET ${set_clause}"
    else
      conflict_clause=" ON CONFLICT (${quoted_pk}) DO NOTHING"
    fi
  fi

  values_expr=$(build_values_expr "$all_cols")
  sql="SELECT '${insert_prefix}' || (${values_expr}) || ')' || '${conflict_clause};' FROM \"${schema}\".\"${table}\" ${where_clause};"

  # STREAM directly to file via FETCH_SIZE cursor. No shell variable capture.
  psql_exec -t -A -c "$sql" >> "$out_file" 2>/dev/null || true
}

# dump_date_table_batched — dump a date-keyed table in monthly batches.
# Each batch is a separate streaming query bounded to one month of data, so
# both server-side work and client-side memory stay small per batch.
# Args: schema table date_col last_max all_cols pk_cols out_file
dump_date_table_batched() {
  local schema="$1" table="$2" date_col="$3" last_max="$4" all_cols="$5" pk_cols="$6" out_file="$7"

  # Current max(date) in the table
  local current_max
  current_max=$(psql_exec -t -A -c "SELECT max(${date_col})::text FROM \"${schema}\".\"${table}\";" | tr -d '[:space:]')

  if [[ -z "$current_max" || "$current_max" == "null" ]]; then
    log "  ${schema}.${table}: empty table, skipping"
    return
  fi

  # Exclusive lower bound for the first batch (last_max was already backed up).
  local cursor="${last_max:-0001-01-01}"

  # ISO date strings compare correctly lexicographically.
  if [[ "$cursor" > "$current_max" || "$cursor" == "$current_max" ]]; then
    log "  ${schema}.${table}: up to date (last_max=$cursor, current_max=$current_max)"
    return
  fi

  log "  ${schema}.${table}: batching ${date_col} ${cursor} → ${current_max}"

  # Generate month-end boundaries from the cursor's month to current_max's month.
  local batch_end where_clause before after batch_rows
  mapfile -t batches < <(psql_exec -t -A -c "
    SELECT (d + interval '1 month' - interval '1 day')::date::text
    FROM generate_series(
      date_trunc('month', '${cursor}'::date)::date,
      date_trunc('month', '${current_max}'::date)::date,
      '1 month'::interval
    ) AS d
    ORDER BY d;
  ")

  for batch_end in "${batches[@]}"; do
    [[ -z "$batch_end" ]] && continue
    # Clamp the batch upper bound to current_max
    [[ "$batch_end" > "$current_max" ]] && batch_end="$current_max"
    # Skip if cursor already past this batch's end
    [[ "$cursor" > "$batch_end" ]] && continue

    where_clause="WHERE \"${date_col}\" > '${cursor}'::date AND \"${date_col}\" <= '${batch_end}'::date"
    before=$(wc -l < "$out_file" 2>/dev/null | tr -d '[:space:]' || echo 0)
    dump_table_streamed "$schema" "$table" "$where_clause" "$all_cols" "$pk_cols" "$out_file"
    after=$(wc -l < "$out_file" 2>/dev/null | tr -d '[:space:]' || echo 0)
    batch_rows=$((after - before))
    log "    batch ${date_col} > ${cursor} and <= ${batch_end}: ${batch_rows} rows"
    cursor="$batch_end"
  done
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

    local rows line schema table date_col max_date row_count first=1
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
      # row_count is tracked for ALL tables; it's the change-detection key for
      # NO-DATE tables (date_col=null) so they aren't full-dumped every run.
      row_count=$(psql_exec -t -A -c "SELECT count(*)::text FROM \"${schema}\".\"${table}\";" | tr -d '[:space:]')
      [[ $first -eq 0 ]] && echo ","
      printf '    "%s.%s": {"date_col": %s, "max_date": %s, "row_count": %s}' \
        "$schema" "$table" \
        "$( [[ -n "$date_col" ]] && echo "\"$date_col\"" || echo "null" )" \
        "$( [[ -n "$max_date" ]] && echo "\"$max_date\"" || echo "null" )" \
        "${row_count:-0}"
      first=0
    done
    echo ""
    echo "  }"
    echo "}"
  } > "$tmp"

  mv "$tmp" "$STATE_FILE"
}

do_incremental_backup() {
  [[ -f "$STATE_FILE" ]] || die "No state file at ${STATE_FILE}. Run with --base (or --init-state) first."

  local ts inc_file
  ts=$(date +%Y%m%d_%H%M%S)
  inc_file="${INC_DIR}/INC_${ts}.sql.gz"
  mkdir -p "$INC_DIR"

  log "Starting incremental backup → ${inc_file}"

  # Main output file (assembled on disk — never held in shell variables).
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
  } > "$tmp"

  local total_rows=0 tables_touched=0
  local rows line schema table date_col last_max pk_cols all_cols
  local stored_row_count table_tmp section_rows

  mapfile -t rows < <(get_tables)
  for line in "${rows[@]}"; do
    [[ -z "$line" ]] && continue
    IFS=$'\t' read -r schema table <<< "$line"

    date_col=$(jq -r --arg k "${schema}.${table}" '.tables[$k].date_col // empty' "$STATE_FILE")
    last_max=$(jq -r --arg k "${schema}.${table}" '.tables[$k].max_date // empty' "$STATE_FILE")
    stored_row_count=$(jq -r --arg k "${schema}.${table}" '.tables[$k].row_count // empty' "$STATE_FILE")

    pk_cols=$(get_pk_cols "$schema" "$table")
    all_cols=$(get_all_cols "$schema" "$table")
    [[ -z "$all_cols" ]] && { log "  skip ${schema}.${table} (no columns)"; continue; }

    # Per-table temp file — streamed output lands here, never in a shell var.
    table_tmp=$(mktemp)
    : > "$table_tmp"

    if [[ -n "$date_col" ]]; then
      # DATE-keyed table: monthly batches, each streamed to table_tmp.
      dump_date_table_batched "$schema" "$table" "$date_col" "$last_max" \
        "$all_cols" "$pk_cols" "$table_tmp"
    else
      # NO-DATE table: skip if row_count unchanged (avoids re-dumping 1.6M-row
      # reference tables like sec_composition every run).
      local current_row_count
      current_row_count=$(psql_exec -t -A -c "SELECT count(*)::text FROM \"${schema}\".\"${table}\";" | tr -d '[:space:]')
      if [[ -n "$stored_row_count" && "$stored_row_count" != "null" \
            && "$stored_row_count" == "$current_row_count" ]]; then
        log "  skip ${schema}.${table} (row_count=${current_row_count} unchanged)"
        rm -f "$table_tmp"
        continue
      fi
      log "  ${schema}.${table}: full dump (row_count ${stored_row_count:-0} → ${current_row_count})"
      # Streamed full dump — FETCH_SIZE cursor bounds memory.
      dump_table_streamed "$schema" "$table" "" "$all_cols" "$pk_cols" "$table_tmp"
    fi

    section_rows=$(wc -l < "$table_tmp" 2>/dev/null | tr -d '[:space:]' || echo 0)
    total_rows=$((total_rows + section_rows))
    [[ $section_rows -gt 0 ]] && tables_touched=$((tables_touched + 1))

    # Append section header + streamed content to main output.
    {
      echo "-- ----------------------------------------------------------------------------"
      echo "-- Table: ${schema}.${table}  (date_col=${date_col:-none}, last_max=${last_max:-none}, rows=${section_rows})"
      echo "-- ----------------------------------------------------------------------------"
      if [[ $section_rows -gt 0 ]]; then
        cat "$table_tmp"
      else
        echo "-- (no new rows)"
      fi
      echo ""
    } >> "$tmp"
    rm -f "$table_tmp"
  done

  {
    echo "COMMIT;"
    echo ""
    echo "-- End of incremental ${ts}: ${total_rows} row(s) across ${tables_touched} table(s)"
  } >> "$tmp"

  gzip -9 < "$tmp" > "$inc_file"
  rm -f "$tmp"

  local size; size=$(stat -c %s "$inc_file" 2>/dev/null || stat -f %z "$inc_file")
  log "Incremental backup complete: ${inc_file} ($(numfmt --to=iec $size 2>/dev/null || echo "${size}B"))"

  # Refresh state.json with new max(date_col) + row_count per table
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

  # Re-query max(date_col) for date-keyed tables AND row_count for all tables.
  # row_count is the change-detection signal for NO-DATE tables.
  local rows line schema table date_col max_date row_count
  mapfile -t rows < <(get_tables)
  for line in "${rows[@]}"; do
    [[ -z "$line" ]] && continue
    IFS=$'\t' read -r schema table <<< "$line"
    date_col=$(jq -r --arg k "${schema}.${table}" '.tables[$k].date_col // empty' "$tmp")
    if [[ -n "$date_col" ]]; then
      max_date=$(psql_exec -t -A -c "SELECT max(${date_col})::text FROM \"${schema}\".\"${table}\";" | tr -d '[:space:]')
      jq --arg k "${schema}.${table}" --arg md "${max_date}" \
         '.tables[$k].max_date = $md' "$tmp" > "${tmp}.2" && mv "${tmp}.2" "$tmp"
    fi
    row_count=$(psql_exec -t -A -c "SELECT count(*)::text FROM \"${schema}\".\"${table}\";" | tr -d '[:space:]')
    jq --arg k "${schema}.${table}" --arg rc "${row_count}" \
       '.tables[$k].row_count = $rc' "$tmp" > "${tmp}.2" && mv "${tmp}.2" "$tmp"
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
    echo "State: (none — run with --base or --init-state to initialize)"
  fi
}

# Recreate state.json from the latest existing base backup WITHOUT re-running
# pg_dump. Use this when state.json is missing/stale but a base backup exists.
do_init_state() {
  local latest_base
  latest_base=$(ls -t "$BASE_DIR"/BASE_*.sql.gz 2>/dev/null | head -1)
  if [[ -z "$latest_base" ]]; then
    die "No base backup found in ${BASE_DIR}. Run with --base first."
  fi
  log "Re-initializing state from existing base: $(basename "$latest_base")"
  mkdir -p "$BASE_DIR" "$INC_DIR"
  init_state "$latest_base"
  log "State initialized at ${STATE_FILE}"
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
  --init-state)   MODE="init-state" ;;
  --list)         MODE="list" ;;
  --help|-h|"")   grep '^#' "$0" | head -n 50; exit 0 ;;
  *)              die "Unknown option: $1 (use --base, --incremental, --init-state, --list, or --help)" ;;
esac

case "$MODE" in
  list)         do_list ;;
  base)         do_base_backup ;;
  incremental)  do_incremental_backup ;;
  init-state)   do_init_state ;;
  auto)
    if [[ -f "$STATE_FILE" ]]; then
      do_incremental_backup
    elif ls "$BASE_DIR"/BASE_*.sql.gz >/dev/null 2>&1; then
      # state.json missing but a base backup exists — recover state then inc.
      log "state.json missing; recovering from latest base backup."
      do_init_state
      do_incremental_backup
    else
      do_base_backup
    fi
    ;;
esac

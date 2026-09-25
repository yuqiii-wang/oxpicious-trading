"""
_async_ops.py — Asynchronous database operations.

Provides:
  - get_existing_keys_async() — query existing key tuples (async)
  - bulk_upsert_async() — efficient async bulk insert/update with conflict handling
  - copy_insert_async() — bulk-insert via PostgreSQL COPY (fastest path)
  - csv_copy_from_frame_async() — bulk-insert a DataFrame via COPY csv
    (vectorized client encoding — no per-row dicts)
  - ensure_table_exists_async() — create table if not exists (async)
  - truncate_table_async() — clear all data from table (async)
"""
from __future__ import annotations

import math

from ._helpers import (
    _parse_table_name,
    _resolve_partition_key,
    _build_commit_chunks,
    _chunk_keys_by_weight,
    DEFAULT_COMMIT_CHUNK_ROWS,
    PARTITION_KEY_AUTO,
)

import asyncio
import logging
logger = logging.getLogger(__name__)

# Standby replay-lag cap for the bulk-write helpers: when the streaming
# replica falls further behind than this, writers pause between commit
# chunks until it drains. The cap must sit well below the primary's
# max_slot_wal_keep_size (64GB since 2026-09-24 — it was 512MB and the
# cap then 256MB, i.e. half the ceiling): 4096MB = 1/16 of the new
# ceiling and ≈ 15 minutes of replay backlog at the observed ~4.5 MB/s
# bind-mount replay rate, so a bulk load streams through normal lag
# instead of pausing per chunk while the slot stays far from
# invalidation. No-op when no standby is streaming (or on permission
# errors).
DEFAULT_MAX_REPLICA_LAG_MB = 4096.0


async def _wait_for_replica_lag_async(
    conn, max_lag_mb: float, poll_s: float = 2.0,
) -> None:
    """Pause until the streaming standby's replay lag is within cap.

    Lag = distance between the primary's current WAL position and the
    farthest-behind streaming standby's replayed position
    (``pg_stat_replication`` → ``replay_lsn``). Called BETWEEN commit
    chunks (never inside a transaction), so pausing holds no locks —
    the primary just stops generating WAL for a moment while the
    replica catches up.

    Returns immediately when no standby is streaming (lag is NULL) —
    local/no-replica setups are unaffected. A permission error reading
    ``pg_stat_replication`` disables the wait (log once, never fail a
    write pipeline over monitoring).
    """
    max_lag_bytes = int(max_lag_mb * 1024 * 1024)
    warned = False
    while True:
        try:
            # 'streaming' + 'catchup': connected standbys reporting a
            # replay position. A base backup's WAL streamer (pg_basebackup
            # -X stream) never reports one — it contributes NULL and is
            # ignored, so a running base backup does not stall writers.
            lag = await conn.fetchval(
                "SELECT max(pg_wal_lsn_diff(pg_current_wal_lsn(), "
                "replay_lsn)) FROM pg_stat_replication "
                "WHERE state IN ('streaming', 'catchup')"
            )
        except Exception as e:
            logger.warning(
                f"      replica-lag check unavailable "
                f"({type(e).__name__}: {e}); continuing without throttle")
            return
        if lag is None or lag <= max_lag_bytes:
            return
        if not warned:
            logger.info(
                f"      replica replay lag "
                f"{lag / 1024 / 1024:,.0f} MB exceeds "
                f"{max_lag_mb:,.0f} MB cap — pausing between commit "
                f"chunks until it drains")
            warned = True
        await asyncio.sleep(poll_s)


def _copy_clean_value(v):
    """None-out NaN/NaT sentinels for the COPY binary protocol.

    Pure host logic — importing pandas / building frames here fires cudf
    fallbacks (DataFrame init + astype + where + itertuples) on EVERY
    copy_insert_async call.
    """
    if v is None:
        return None
    # isinstance covers np.float64 too (subclass of float)
    if isinstance(v, float) and math.isnan(v):
        return None
    # NaT (pd.NaT) — duck-check without importing pandas
    if "NaT" in type(v).__name__:
        return None
    return v


async def get_existing_keys_async(conn, table_name: str, key_columns: list) -> set:
    """Get set of existing key tuples from a table (async)."""
    if not key_columns:
        return set()

    schema, table = _parse_table_name(table_name)

    columns_sql = ", ".join([f'"{c}"' for c in key_columns])

    if schema:
        query = f'SELECT {columns_sql} FROM "{schema}"."{table}"'
    else:
        query = f'SELECT {columns_sql} FROM "{table}"'

    rows = await conn.fetch(query)

    return set(tuple(row[c] for c in key_columns) for row in rows)


async def get_latest_dates_async(
    conn,
    table_name: str,
    key_columns: list,
    date_column: str = "date",
) -> dict:
    """Get the latest (MAX) date per key from a table (async).

    Cheap latest-missing-date detection: instead of loading every
    (date, key) pair (millions of rows), a single GROUP BY returns one row
    per key. Safe as a completeness check when a key's rows are inserted
    in ascending date order (the pipelines' contract): a key's max date
    >= d implies the row at d exists, so only dates AFTER the max can be
    missing. Commit-chunked bulk writes do not change this — a crash
    mid-run leaves each key's committed prefix intact, and the
    uncommitted tail is re-detected as missing dates on rerun.

    Args:
        conn: asyncpg connection.
        table_name: schema-qualified table name (e.g. "stats.index_tech_stats").
        key_columns: key column(s) to group by (e.g. ["code"]).
        date_column: date column name (default "date").

    Returns:
        dict mapping key -> max date. With a single key column the key is
        the bare value; with multiple key columns it is a tuple.
    """
    if not key_columns:
        return {}

    schema, table = _parse_table_name(table_name)
    from_clause = f'"{schema}"."{table}"' if schema else f'"{table}"'
    keys_sql = ", ".join([f'"{c}"' for c in key_columns])
    rows = await conn.fetch(
        f'SELECT {keys_sql}, MAX("{date_column}") AS max_date '
        f'FROM {from_clause} GROUP BY {keys_sql}'
    )

    if len(key_columns) == 1:
        k = key_columns[0]
        return {r[k]: r["max_date"] for r in rows if r["max_date"] is not None}
    return {
        tuple(r[c] for c in key_columns): r["max_date"]
        for r in rows if r["max_date"] is not None
    }


async def bulk_upsert_async(
    conn,
    table_name: str,
    rows: list,
    key_columns: list,
    batch_size: int = 5000,
    *,
    commit_chunk_rows: int = DEFAULT_COMMIT_CHUNK_ROWS,
    partition_key: str | None = PARTITION_KEY_AUTO,
    max_replica_lag_mb: float | None = DEFAULT_MAX_REPLICA_LAG_MB,
) -> int:
    """Perform bulk upsert (INSERT ... ON CONFLICT DO UPDATE/NOTHING) (async).

    Uses asyncpg's executemany (pipelined extended-query protocol: statement
    prepared once, then Bind+Execute pipelined without per-row round-trips),
    committed in bounded chunks of ~``commit_chunk_rows`` rows.

    THE BULK-WRITE RULE (chunk by partition key → one COMMIT per chunk →
    replica-lag throttle between chunks) is the DEFAULT here:

    WHY BOUNDED COMMITS (not one transaction for everything): a
    replication slot cannot advance past a transaction until it commits,
    so a multi-million-row single transaction freezes the replica's
    confirmed position for the whole load and the primary retains every
    byte of WAL generated meanwhile (~29 GB observed behind one mega
    upsert). Committing every ~100K rows lets the slot — and thus WAL
    recycling — advance continuously while the primary keeps ingesting.
    Crash-safety is preserved because ON CONFLICT upserts are idempotent:
    a rerun re-upserts whatever did not commit.

    WHY KEY-ALIGNED CHUNKS (partition_key, default "auto"): commit
    boundaries never split one key's rows, so the replica consumes whole
    industries / codes / dates per commit. "auto" resolves to "date"
    when the rows carry a date column (the repo-wide partitioning
    convention), else the first usable key column; an explicit column
    name wins; None opts out to flat row-count chunks. Between commit
    chunks, ``max_replica_lag_mb`` pauses the writer while a streaming
    standby is further behind than the cap (no-op without a replica), so
    WAL cannot pile up faster than the replica replays.

    Args:
        conn: asyncpg connection
        table_name: target table (may include schema prefix like "stats.debt_identity")
        rows: list of dictionaries, each representing a row
        key_columns: list of column names forming the primary key
        batch_size: chunk size for executemany calls (default 5000, capped at
                    10000).
        commit_chunk_rows: rows per COMMIT (default 100K). Commits — and
                    therefore replication-slot advances — happen at this
                    granularity.
        partition_key: column name whose values must never be split
                    across commits ("code", "industry_id", "date"...).
                    Default "auto": "date" if the rows carry one, else the
                    first usable key column. None = flat row-count chunks.
        max_replica_lag_mb: pause between commit chunks while the streaming
                    standby's replay lag exceeds this (default 4096 MB —
                    1/16 of the primary's 64GB max_slot_wal_keep_size;
                    None disables the throttle).

    Returns:
        Number of rows processed (len(rows)).
    """
    if not rows:
        return 0

    columns = list(rows[0].keys())
    if not columns:
        return 0

    # Cap batch_size at 10000 to bound memory and avoid too-large pipeline
    # batches.
    batch_size = min(batch_size, 10000)
    commit_chunk_rows = max(commit_chunk_rows, batch_size)

    schema, table = _parse_table_name(table_name)

    columns_sql = ", ".join([f'"{c}"' for c in columns])
    # Single-row placeholders ($1, $2, ...) — executemany reuses them
    # for each row in the batch, unlike multi-row INSERT which needs
    # unique $N for every row×column.
    placeholders = ", ".join([f"${i+1}" for i in range(len(columns))])
    conflict_columns = ", ".join([f'"{c}"' for c in key_columns])

    update_clause = ", ".join([
        f'"{c}" = EXCLUDED."{c}"'
        for c in columns if c not in key_columns
    ])

    if update_clause:
        conflict_action = f"ON CONFLICT ({conflict_columns}) DO UPDATE SET {update_clause}"
    else:
        # PK-only tables (e.g. debt_identity with just `date`): no columns
        # to update, so use DO NOTHING.
        conflict_action = f"ON CONFLICT ({conflict_columns}) DO NOTHING"

    if schema:
        table_ref = f'"{schema}"."{table}"'
    else:
        table_ref = f'"{table}"'

    query = (
        f'INSERT INTO {table_ref} ({columns_sql}) '
        f'VALUES ({placeholders}) {conflict_action}'
    )

    # Commit-bounded chunks aligned to the partition key (the bulk-write
    # rule): "auto" resolves to "date" / first usable key column, None
    # keeps legacy flat row-count slices.
    effective_key = _resolve_partition_key(rows[0], key_columns, partition_key)
    commit_chunks = _build_commit_chunks(rows, effective_key, commit_chunk_rows)

    try:
        for chunk in commit_chunks:
            # Between commit chunks (no transaction open, no locks held):
            # wait out standby replay lag so the primary does not pile WAL
            # up faster than the replica can replay it.
            if max_replica_lag_mb is not None:
                await _wait_for_replica_lag_async(conn, max_replica_lag_mb)
            # Dedup by PK within the chunk (last occurrence wins). Rows
            # sharing a PK inside one executemany batch would otherwise
            # abort the whole chunk with "ON CONFLICT DO UPDATE command
            # cannot affect row a second time" — and partition-key
            # grouping deliberately co-locates a key's rows in the SAME
            # chunk, so this is what keeps every chunk internally
            # conflict-free. Cross-chunk duplicates (flat mode) remain
            # last-chunk-wins, matching the former multi-batch
            # single-transaction behavior.
            by_pk: dict[tuple, dict] = {}
            for row in chunk:
                by_pk[tuple(row[c] for c in key_columns)] = row
            if len(by_pk) != len(chunk):
                logger.warning(
                    f"    [WARN] {table_name}: dropped "
                    f"{len(chunk) - len(by_pk):,} duplicate-PK row(s) "
                    f"within a commit chunk (last occurrence wins)")
            # Tuples are built per commit chunk only — the full input's
            # tuple list is never materialized at once.
            chunk_values = [
                tuple(row[c] for c in columns) for row in by_pk.values()
            ]
            # One COMMIT per chunk: asyncpg operates in implicit autocommit
            # by default, so without this each executemany call would be
            # its own transaction (per-batch COMMITs — the old checkpoint
            # storms). Batching executemany calls inside one transaction
            # per commit chunk bounds both COMMIT frequency and the
            # replication slot's lag between advances.
            async with conn.transaction():
                for i in range(0, len(chunk_values), batch_size):
                    # executemany uses the pipelined extended-query protocol:
                    # Parse once → (Bind + Execute) × N pipelined without
                    # waiting for individual results.
                    await conn.executemany(query, chunk_values[i:i + batch_size])

        return len(rows)
    except Exception as e:
        logger.error(
            f"    [ERROR] Bulk upsert failed for {table_name}: "
            f"{type(e).__name__}: {e}",
        )
        raise


async def copy_insert_async(conn, table_name: str, rows: list, columns: list | None = None) -> int:
    """Bulk-insert rows via PostgreSQL COPY (fastest path).

    Uses asyncpg's ``copy_records_to_table``, which streams rows through the
    COPY protocol — bypassing the extended-query parsing/planning that
    ``executemany`` incurs per batch. On multi-million-row inserts (e.g. the
    14M-row industry_correlations table) COPY is typically 5-10× faster than
    ``INSERT ... ON CONFLICT`` because:

      1. No per-row ON CONFLICT arbiter check (the table is pre-truncated,
         so there are never conflicts).
      2. No prepared-statement Bind/Execute overhead — rows are streamed as
         a single binary COPY stream.
      3. WAL is written in bulk via the COPY's internal buffer.

    SAFE ONLY when the target table has been truncated (or is otherwise
    guaranteed conflict-free). For upsert (conflict-possible) scenarios, use
    ``bulk_upsert_async`` instead.

    Args:
        conn: asyncpg connection.
        table_name: target table (schema-qualified, e.g.
            "analysis.industry_correlations").
        rows: list of row dicts (same shape as ``bulk_upsert_async``).
        columns: optional explicit column order. When None, inferred from
            the first row's keys (same as ``bulk_upsert_async``).

    Returns:
        Number of rows inserted (``len(rows)``).
    """
    if not rows:
        return 0

    # Bulk-write rule guard: this primitive commits EVERYTHING in one
    # transaction, so a slot cannot advance until it finishes. Callers
    # pushing more than the commit-chunk target should chunk by partition
    # key instead (the throttled batched writers).
    if len(rows) > DEFAULT_COMMIT_CHUNK_ROWS:
        logger.warning(
            f"    [WARN] {table_name}: single-transaction COPY of "
            f"{len(rows):,} rows exceeds the {DEFAULT_COMMIT_CHUNK_ROWS:,}-row "
            f"commit-chunk target — the replication slot cannot advance "
            f"until it commits. Use batched_copy_by_key_async / "
            f"copy_frame_chunked_async to chunk by partition key.")

    if columns is None:
        columns = list(rows[0].keys())
    if not columns:
        return 0

    schema, table = _parse_table_name(table_name)

    # Pure-Python record assembly — no pandas. The previous
    # DataFrame(rows).astype(object).where(notna, None).itertuples() flow
    # triggered one cudf.pandas fallback chain per call and mis-handles
    # object-dtype date columns ("Cannot convert a date of object type").
    # Callers emit rows via records_from_frame which already swept NaN→None;
    # _copy_clean_value guards any stragglers (NaN float / pd.NaT) since
    # COPY's binary protocol cannot encode them.
    records = [
        tuple(_copy_clean_value(r.get(c)) for c in columns)
        for r in rows
    ]
    async with conn.transaction():
        await conn.copy_records_to_table(
            table,
            records=records,
            schema_name=schema if schema else None,
            columns=columns,
        )
    return len(rows)


async def csv_copy_from_frame_async(
    conn, table_name: str, df, columns: list | None = None,
) -> int:
    """Bulk-insert a DataFrame via PostgreSQL COPY ... FORMAT csv.

    DataFrame-flavored alternative to :func:`copy_insert_async`. The
    client renders the frame to CSV text with whole-column numpy/pandas
    C code instead of encoding one binary value at a time in Python
    (``copy_records_to_table`` costs ~6s of per-value Python encoding per
    100k x 81-col chunk; the same rows stream to the server in ~0.5s as
    CSV — the server was never the bottleneck, the client was).

    Deterministic, cudf.pandas-safe rendering:
      - date columns are pre-rendered to ISO "YYYY-MM-DD" strings with
        ``np.datetime_as_string`` (host numpy) so neither pandas nor cudf
        datetime formatting can reach to_csv and diverge on the wire
        format; NaT renders as an empty field;
      - other columns keep their native dtype; NaN/None render as empty
        fields, which PostgreSQL COPY csv reads as NULL;
      - the frame passed to to_csv is rebuilt from host numpy arrays, so
        host pandas does the encoding regardless of whether the input
        frame was GPU-backed.

    SAFE ONLY when the target is guaranteed conflict-free (truncated
    table / PK-filtered missing dates) — same contract as
    ``copy_insert_async``. For upsert scenarios use ``bulk_upsert_async``.

    Args:
        conn: asyncpg connection.
        table_name: target table (schema-qualified, e.g.
            "analysis.mov_ave_spreads_detail_ohlc").
        df: DataFrame whose columns (after optional reordering via
            ``columns``) match the target table.
        columns: optional explicit column order; defaults to df.columns.

    Returns:
        Number of rows inserted (``len(df)``).
    """
    if df is None or len(df) == 0:
        return 0

    # Bulk-write rule guard (same as copy_insert_async): one transaction
    # for the whole frame — chunk by partition key above this size.
    if len(df) > DEFAULT_COMMIT_CHUNK_ROWS:
        logger.warning(
            f"    [WARN] {table_name}: single-transaction CSV COPY of "
            f"{len(df):,} rows exceeds the {DEFAULT_COMMIT_CHUNK_ROWS:,}-row "
            f"commit-chunk target — the replication slot cannot advance "
            f"until it commits. Use copy_frame_chunked_async to chunk by "
            f"partition key.")

    schema, table = _parse_table_name(table_name)

    import io

    import numpy as np
    import pandas as pd

    from _common.df_utils import host_array, safe_columns

    # Under cudf.pandas the `pandas` module is a proxy: every ctor call
    # first attempts cuDF (NotImplementedError on copy=False → fallback)
    # and every to_csv first uploads the whole frame to VRAM before
    # failing back to pandas. This function is pure host-CPU CSV
    # rendering, so unwrap the REAL pandas module once and build the
    # frame + to_csv entirely outside the proxy dispatcher (zero
    # fallbacks, zero GPU transfers).
    real_pd = getattr(pd, "_fsproxy_slow", pd)

    cols = list(columns) if columns is not None else safe_columns(df)
    if not cols:
        return 0

    data = {}
    for c in cols:
        arr = host_array(df[c].to_numpy())
        if arr.dtype.kind == "M":  # datetime64 — ISO date text, NaT -> NULL
            s = np.datetime_as_string(arr, unit="D").astype(object)
            s[np.isnat(arr)] = None
            data[c] = s
        else:
            data[c] = arr
    clean = real_pd.DataFrame(data, columns=cols)

    buf = io.BytesIO()
    clean.to_csv(buf, index=False, header=False, na_rep="")
    buf.seek(0)

    async with conn.transaction():
        await conn.copy_to_table(
            table,
            schema_name=schema if schema else None,
            columns=cols,
            source=buf,
            format="csv",
        )
    return len(df)


async def chunked_dml_by_key_async(
    conn,
    *,
    statements: list,
    params: tuple = (),
    weighted_keys: list,
    weight_target: int = DEFAULT_COMMIT_CHUNK_ROWS,
    max_replica_lag_mb: float | None = DEFAULT_MAX_REPLICA_LAG_MB,
) -> int:
    """Run DML statements once per partition-key chunk — the bulk-write
    rule applied to DELETEs.

    Row versions written by a DELETE are WAL too: a multi-million-row
    single-statement DELETE retains every byte behind its ONE
    transaction — the replication slot cannot advance until it commits
    (the same failure mode the COPY chunking fixed; the forecasts
    refresh deletes alone reach ~12M rows per stock snapshot). This
    helper re-runs ``statements`` once per whole-key chunk — chunks
    accumulate keys by ROW WEIGHT (the caller counts the doomed rows
    per key) so every COMMIT's WAL stays ~``weight_target`` rows — all
    statements of one chunk inside ONE transaction (a grouped delete —
    e.g. results + motivation + registry rows — vanishes atomically per
    key set), with the replica-lag cap paused between chunks.

    Args:
        statements: (sql, key_clause) pairs or (sql, key_clause,
            stmt_params) triples. ``sql`` is the DML WITHOUT any key
            filter; ``key_clause`` is appended per chunk and contains a
            ``{ph}`` placeholder for the parameter index — e.g.
            ``" AND i.code = ANY({ph}::text[])"`` (aliased joins) or
            ``" AND code = ANY({ph}::text[])"`` (plain deletes).
        params: the statements' DEFAULT positional parameters ($1..$N;
            each key clause's placeholder is appended after them). A
            statement carrying its own ``stmt_params`` (different
            placeholder subset — asyncpg requires an exact
            placeholder/argument match per statement) uses those
            instead.
        weighted_keys: (key, row_count) pairs of the scope — the
            caller counts the doomed rows per key (one GROUP BY over
            the rows the DELETE itself would scan; cheap next to the
            delete's WAL). Use the BIGGEST table in the group as the
            weight.
        weight_target: rows per chunk (~100K, the commit-chunk
            target). A key's rows are never split across chunks.
        max_replica_lag_mb: pause between chunks while the streaming
            standby's replay lag exceeds this (no-op without a
            replica).

    Returns the total affected rows across all statements and chunks
    (parsed from the status tags).
    """
    if not weighted_keys:
        return 0
    chunks = _chunk_keys_by_weight(weighted_keys, weight_target)
    total = 0
    for chunk in chunks:
        if max_replica_lag_mb is not None:
            await _wait_for_replica_lag_async(conn, max_replica_lag_mb)
        async with conn.transaction():
            for entry in statements:
                sql, key_clause = entry[0], entry[1]
                stmt_params = entry[2] if len(entry) > 2 else params
                status = await conn.execute(
                    sql + key_clause.format(ph=len(stmt_params) + 1),
                    *stmt_params, chunk,
                )
                # asyncpg's status tag: "DELETE 12345" / "DELETE 0"
                try:
                    total += int(status.rsplit(" ", 1)[-1])
                except ValueError:
                    pass
    return total


async def chunked_purge_async(
    conn,
    table_name: str,
    *,
    where_sql: str,
    params: tuple = (),
    key_column: str = "code",
    weight_target: int = DEFAULT_COMMIT_CHUNK_ROWS,
    max_replica_lag_mb: float | None = DEFAULT_MAX_REPLICA_LAG_MB,
) -> int:
    """Delete rows matching ``where_sql`` in partition-key chunks — the
    ONE-CALL form of the bulk-write rule for the repo's recurring purge
    idiom (``DELETE FROM <table> WHERE sec_type = $1`` and friends).

    A whole-sec_type / whole-table DELETE of a daily-grain stats table
    reaches millions of rows; one statement retains all its row-version
    WAL behind ONE transaction and the replication slot cannot advance
    until it commits. This helper:

      1. counts the doomed rows per ``key_column`` (one GROUP BY over
         the same scan the DELETE itself performs — cheap next to the
         delete's WAL),
      2. accumulates keys into ~``weight_target``-row chunks (whole
         keys, never split — ``_chunk_keys_by_weight``),
      3. runs ``DELETE FROM <table> WHERE <scope> AND <key> = ANY($n)``
         once per chunk — the key filter walks the table's code-leading
         PK index (these tables are code-clustered by design), so each
         chunk is an index-scan delete of ~``weight_target`` rows in
         its own COMMIT —
      4. pausing for the replica-lag cap between chunks.

    Args:
        table_name: schema-qualified target table.
        where_sql: the scope WITHOUT any key-column filter (the key
            clause is appended per chunk). May reference an alias —
            pass ``key_column`` alias-qualified to match (e.g.
            ``"t.code"`` for ``DELETE FROM tbl t WHERE t.sec_type = $1``).
        params: ``where_sql``'s positional parameters.
        key_column: the partition-key column (default "code"); the
            COUNT/GROUP BY and the per-chunk filter both use it.
        weight_target: rows per chunk (~100K, the commit-chunk target).
        max_replica_lag_mb: pause between chunks while the streaming
            standby's replay lag exceeds this (no-op without a
            replica).

    Returns the total deleted rows (parsed from the status tags).
    """
    schema, table = _parse_table_name(table_name)
    from_clause = f'"{schema}"."{table}"' if schema else f'"{table}"'
    weight_rows = await conn.fetch(
        f"SELECT {key_column} AS k, count(*) AS n FROM {from_clause} "
        f"WHERE {where_sql} GROUP BY {key_column}",
        *params,
    )
    if not weight_rows:
        return 0
    weighted = [(r["k"], r["n"]) for r in weight_rows]
    total = 0
    for chunk in _chunk_keys_by_weight(weighted, weight_target):
        if max_replica_lag_mb is not None:
            await _wait_for_replica_lag_async(conn, max_replica_lag_mb)
        status = await conn.execute(
            f"DELETE FROM {from_clause} WHERE {where_sql} "
            f"AND {key_column} = ANY(${len(params) + 1}::text[])",
            *params, chunk,
        )
        try:
            total += int(status.rsplit(" ", 1)[-1])
        except ValueError:
            pass
    return total


async def ensure_table_exists_async(conn, table_name: str, create_sql: str) -> None:
    """Ensure a table exists, create it if not (async)."""
    schema, table = _parse_table_name(table_name)

    if schema:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = $1 AND table_name = $2)",
            schema, table,
        )
    else:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = $1)",
            table,
        )

    if not exists:
        logger.info(f"    [INFO] Creating table {table_name}")
        await conn.execute(create_sql)
        logger.info(f"    [INFO] Table {table_name} created")


async def truncate_table_async(conn, table_name: str) -> None:
    """Truncate a table (clear all data) (async). Skips if table doesn't exist."""
    schema, table = _parse_table_name(table_name)

    if schema:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = $1 AND table_name = $2)",
            schema, table,
        )
    else:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = $1)",
            table,
        )

    if not exists:
        logger.info(f"    [INFO] Table {table_name} does not exist, skipping truncate")
        return

    if schema:
        await conn.execute(f'TRUNCATE TABLE "{schema}"."{table}" CASCADE')
    else:
        await conn.execute(f'TRUNCATE TABLE "{table}" CASCADE')
    logger.info(f"    [INFO] Truncated table {table_name}")
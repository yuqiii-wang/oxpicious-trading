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

from ._helpers import _parse_table_name


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
    per key. Safe as a completeness check when rows are inserted per table
    in one transaction (bulk_upsert_async) — a key's max date >= d implies
    the row at d exists, so only dates AFTER the max can be missing.

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
) -> int:
    """Perform bulk upsert (INSERT ... ON CONFLICT DO UPDATE/NOTHING) (async).

    Uses asyncpg's executemany (pipelined extended-query protocol: statement
    prepared once, then Bind+Execute pipelined without per-row round-trips)
    wrapped in a single transaction for atomicity and WAL efficiency.

    This is asyncpg's fastest bulk-insert method and the best defense against
    checkpoint storms: a 233k-row insert goes from 233 COMMITs/WAL flushes
    (autocommit-per-batch under the old multi-row-INSERT approach) to 1 COMMIT.

    Args:
        conn: asyncpg connection
        table_name: target table (may include schema prefix like "stats.debt_identity")
        rows: list of dictionaries, each representing a row
        key_columns: list of column names forming the primary key
        batch_size: chunk size for executemany calls (default 5000, capped at
                    10000).

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

    all_values = [tuple(row[c] for c in columns) for row in rows]

    try:
        # Wrap ALL batches in a single transaction. asyncpg operates in
        # implicit autocommit by default — without this, each executemany
        # call would be its own transaction with its own COMMIT + WAL flush.
        async with conn.transaction():
            for i in range(0, len(all_values), batch_size):
                chunk = all_values[i:i + batch_size]
                # executemany uses the pipelined extended-query protocol:
                # Parse once → (Bind + Execute) × N pipelined without
                # waiting for individual results.
                await conn.executemany(query, chunk)

        return len(rows)
    except Exception as e:
        print(
            f"    [ERROR] Bulk upsert failed for {table_name}: "
            f"{type(e).__name__}: {e}",
            flush=True,
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
        print(f"    [INFO] Creating table {table_name}", flush=True)
        await conn.execute(create_sql)
        print(f"    [INFO] Table {table_name} created", flush=True)


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
        print(f"    [INFO] Table {table_name} does not exist, skipping truncate", flush=True)
        return

    if schema:
        await conn.execute(f'TRUNCATE TABLE "{schema}"."{table}" CASCADE')
    else:
        await conn.execute(f'TRUNCATE TABLE "{table}" CASCADE')
    print(f"    [INFO] Truncated table {table_name}", flush=True)
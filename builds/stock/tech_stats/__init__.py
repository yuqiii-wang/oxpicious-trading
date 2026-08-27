"""builds.stock.tech_stats — Stock technical indicator computation.

Computes MA5/20/60/120/255 + MA5 ratio + EMA6/10/20/60/120/255 from
stats.stock_basic_stats.close, storing results in stats.stock_tech_stats.

OPTIMIZED: Only loads the minimal lookback window from source and
computes indicators only for NEW dates (date > MAX(date) in the target
table). Full history is only loaded on --force.

Usage:
    # As standalone module:
    python -m builds.stock.tech_stats

    # Integrated into builds.stock:
    from builds.stock.tech_stats import run_tech_stats_chunked
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta
from typing import Optional

from _common.build_commons import (
    copy_or_upsert_split_async,
    truncate_table_async,
    get_max_table_date_async,
    rec_col,
    rec_cols,
)

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

from _common.df_utils import compute_moving_averages, compute_emas
import pandas as pd

TABLE = "stats.stock_tech_stats"
SOURCE_TABLE = "stats.stock_basic_stats"
DEFAULT_CHUNK_CODES = 500

# Lookback: enough calendar days to cover max MA/EMA period × convergence
# factor. Max period = 255; EMA needs ~3× span for stable convergence.
# 255 × 3 = 765 trading days → ~1115 calendar days.
_MAX_INDICATOR_PERIOD = 255
_LOOKBACK_TRADING_DAYS = _MAX_INDICATOR_PERIOD * 3  # 765
_LOOKBACK_CALENDAR_DAYS = round(_LOOKBACK_TRADING_DAYS * 365 / 250)  # 1115


async def _load_all_codes(conn) -> list:
    rows = await conn.fetch(
        f'SELECT DISTINCT code FROM {SOURCE_TABLE} WHERE close IS NOT NULL ORDER BY code'
    )
    return rec_col(rows, "code")


async def _load_close_window(conn, codes: list, start_date: date) -> pd.DataFrame:
    """Load close data for given codes from start_date onward.

    Only loads the minimal window needed for indicator computation
    (lookback + new dates), not the full history.
    """
    rows = await conn.fetch(
        f'SELECT date, code, close FROM {SOURCE_TABLE} '
        f'WHERE code = ANY($1::text[]) '
        f'  AND close IS NOT NULL '
        f'  AND date >= $2 '
        f'ORDER BY code, date ASC',
        sorted(codes), start_date,
    )
    if not rows:
        return pd.DataFrame(columns=["date", "code", "close"])
    df = pd.DataFrame(rec_cols(rows))
    # Keep date as datetime64 for GPU operations; convert to .dt.date
    # only at DB write time (avoids cudf fallback on .dt.date accessor).
    df["date"] = pd.to_datetime(df["date"])
    # DB data is clean — no errors='coerce' needed
    df["close"] = df["close"].astype(float)
    df = df.dropna(subset=["close"]).sort_values(["code", "date"]).reset_index(drop=True)
    return df


async def _load_full_close_history(conn, codes: list) -> pd.DataFrame:
    """Load ALL close history for given codes (force mode only)."""
    rows = await conn.fetch(
        f'SELECT date, code, close FROM {SOURCE_TABLE} '
        f'WHERE code = ANY($1::text[]) AND close IS NOT NULL '
        f'ORDER BY code, date ASC',
        sorted(codes),
    )
    if not rows:
        return pd.DataFrame(columns=["date", "code", "close"])
    df = pd.DataFrame(rec_cols(rows))
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = df["close"].astype(float)
    df = df.dropna(subset=["close"]).sort_values(["code", "date"]).reset_index(drop=True)
    return df


def _compute_tech_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = compute_moving_averages(
        df, group_key="code", value_col="close",
        windows=[5, 20, 60, 120, 255],
    )
    df = compute_emas(
        df, group_key="code", value_col="close",
        spans=[6, 10, 20, 60, 120, 255],
    )
    return df


async def run_tech_stats_chunked(
    conn,
    force: bool = False,
    chunk_size: int = DEFAULT_CHUNK_CODES,
    verbose: bool = True,
) -> int:
    """Compute tech stats (MA/EMA) for all stocks and upsert missing rows.

    Incremental mode (default):
      1. Query MAX(date) from TABLE (existing tech_stats).
      2. Load only the lookback window + new dates from SOURCE_TABLE.
      3. Compute indicators on this window.
      4. Filter to rows with date > MAX(date).
      5. Insert via COPY/upsert.

    Force mode:
      1. Truncate TABLE.
      2. Load full history from SOURCE_TABLE.
      3. Compute indicators on full history.
      4. Insert all rows.

    Args:
        conn: asyncpg connection (must remain open)
        force: if True, truncate TABLE and recompute all rows
        chunk_size: number of codes per chunk
        verbose: print progress messages

    Returns:
        Total number of rows upserted.
    """
    t0 = time.time()

    # ------------------------------------------------------------------
    # 1. Determine max existing date and compute lookback window
    # ------------------------------------------------------------------
    if force:
        if verbose:
            print(f"    [TECH-STATS] Force mode: truncating {TABLE}…", flush=True)
        await truncate_table_async(conn, TABLE)
        max_existing_date: Optional[date] = None
    else:
        max_existing_date = await get_max_table_date_async(conn, TABLE)
        if max_existing_date is not None and verbose:
            lookback_start = max_existing_date - timedelta(days=_LOOKBACK_CALENDAR_DAYS)
            print(f"    [TECH-STATS] Existing max date: {max_existing_date}…",
                  flush=True)
            print(f"    [TECH-STATS] Loading lookback window: "
                  f"{lookback_start} → today "
                  f"({_LOOKBACK_CALENDAR_DAYS} calendar days ≈ "
                  f"{_LOOKBACK_TRADING_DAYS} trading days)…",
                  flush=True)

    # ------------------------------------------------------------------
    # 2. Load all codes
    # ------------------------------------------------------------------
    if verbose:
        print(f"    [TECH-STATS] Loading distinct codes from {SOURCE_TABLE}…", flush=True)
    all_codes = await _load_all_codes(conn)
    if verbose:
        print(f"    [TECH-STATS] {len(all_codes):,} codes with non-null close", flush=True)
    if not all_codes:
        return 0

    # ------------------------------------------------------------------
    # 3. Compute indicators per chunk
    # ------------------------------------------------------------------
    total_upserted = 0
    n_chunks = (len(all_codes) + chunk_size - 1) // chunk_size
    for i in range(0, len(all_codes), chunk_size):
        chunk = all_codes[i:i + chunk_size]
        chunk_idx = i // chunk_size + 1

        # Load data: minimal lookback window for incremental, full history for force
        if force or max_existing_date is None:
            df = await _load_full_close_history(conn, chunk)
        else:
            lookback_start = max_existing_date - timedelta(days=_LOOKBACK_CALENDAR_DAYS)
            df = await _load_close_window(conn, chunk, lookback_start)

        if df.empty:
            if verbose:
                print(f"    [TECH-STATS] [{chunk_idx}/{n_chunks}] codes "
                      f"{chunk[0]}..{chunk[-1]}: no close data, skipping",
                      flush=True)
            continue

        # Compute indicators
        df = _compute_tech_indicators(df)

        # Filter to new dates only (incremental mode)
        if not force and max_existing_date is not None:
            # pd.Timestamp: comparing datetime64[s] (unit inferred from DB
            # date objects) against a raw datetime.date raises
            # InvalidComparison on this pandas version.
            _cutoff = pd.Timestamp(max_existing_date)
            df = df[df["date"] > _cutoff].reset_index(drop=True)
            if df.empty:
                if verbose:
                    print(f"    [TECH-STATS] [{chunk_idx}/{n_chunks}] codes "
                          f"{chunk[0]}..{chunk[-1]}: 0 new rows "
                          f"(all dates ≤ {max_existing_date})",
                          flush=True)
                continue

        # Build rows dict — vectorized: column-wise NaN→None conversion,
        # then single to_dict(records) call.
        _numeric_cols = [
            "ma5", "ma5_ratio", "ma20", "ma60", "ma120", "ma255",
            "ema6", "ema10", "ema20", "ema60", "ema120", "ema255",
        ]
        _out_cols = ["date", "code"] + _numeric_cols
        # Host transfer at the DB boundary: .dt.date on a cudf-backed
        # frame falls back per element (no GPU Timestamp.date fast
        # path); on host pandas it is a plain vectorized conversion.
        out_df = df[_out_cols].to_pandas()
        out_df["code"] = out_df["code"].astype(str)
        # Convert datetime64 → Python date for DB insertion (avoids
        # carrying pandas Timestamp into the asyncpg boundary)
        out_df["date"] = out_df["date"].dt.date
        for _c in _numeric_cols:
            out_df[_c] = out_df[_c].where(out_df[_c].notna(), None)
        rows = out_df.to_dict(orient="records")

        n_copied, n_upserted = await copy_or_upsert_split_async(
            conn, TABLE, rows, ["date", "code"],
        )
        n = n_copied + n_upserted
        total_upserted += n
        if verbose:
            # Show lookback row count to demonstrate the optimization
            n_source_rows = len(df)
            if not force and max_existing_date is not None:
                print(f"    [TECH-STATS] [{chunk_idx}/{n_chunks}] codes "
                      f"{chunk[0]}..{chunk[-1]}: "
                      f"{len(rows):,} new rows "
                      f"(computed from {n_source_rows:,} lookback rows) "
                      f"-> {n_copied:,} copied + {n_upserted:,} upserted "
                      f"(cumulative {total_upserted:,})",
                      flush=True)
            else:
                print(f"    [TECH-STATS] [{chunk_idx}/{n_chunks}] codes "
                      f"{chunk[0]}..{chunk[-1]}: "
                      f"{len(rows):,} rows -> upserted {n:,} "
                      f"(cumulative {total_upserted:,})",
                      flush=True)

    elapsed = int(time.time() - t0)
    if verbose:
        print(f"    [TECH-STATS] Done in {elapsed}s. Total upserted: {total_upserted:,}",
              flush=True)
    return total_upserted


__all__ = ["run_tech_stats_chunked", "TABLE", "SOURCE_TABLE"]
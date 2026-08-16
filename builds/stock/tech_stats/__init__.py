"""builds.stock.tech_stats — Stock technical indicator computation.

Computes MA5/20/60/120/255 + MA5 ratio + EMA6/10/20/60/120/255 from
stats.stock_basic_stats.close, storing results in stats.stock_tech_stats.

Usage:
    # As standalone module:
    python -m builds.stock.tech_stats

    # Integrated into builds.stock:
    from builds.stock.tech_stats import run_tech_stats_chunked
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from _common.build_commons import (
    bulk_upsert_async,
    truncate_table_async,
    get_existing_keys_async,
)
from _common.df_utils import compute_moving_averages, compute_emas
import pandas as pd

TABLE = "stats.stock_tech_stats"
SOURCE_TABLE = "stats.stock_basic_stats"
DEFAULT_CHUNK_CODES = 500


async def _load_all_codes(conn) -> list:
    rows = await conn.fetch(
        f'SELECT DISTINCT code FROM {SOURCE_TABLE} WHERE close IS NOT NULL ORDER BY code'
    )
    return [r["code"] for r in rows]


async def _load_close_history(conn, codes: list) -> pd.DataFrame:
    rows = await conn.fetch(
        f'SELECT date, code, close FROM {SOURCE_TABLE} '
        f'WHERE code = ANY($1::text[]) AND close IS NOT NULL '
        f'ORDER BY code, date ASC',
        sorted(codes),
    )
    if not rows:
        return pd.DataFrame(columns=["date", "code", "close"])
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
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

    Args:
        conn: asyncpg connection (must remain open)
        force: if True, truncate TABLE first and recompute all rows
        chunk_size: number of codes per chunk
        verbose: print progress messages

    Returns:
        Total number of rows upserted.
    """
    t0 = time.time()

    if force:
        if verbose:
            print(f"    [TECH-STATS] Force mode: truncating {TABLE}...", flush=True)
        await truncate_table_async(conn, TABLE)
        existing_keys: set = set()
    else:
        if verbose:
            print(f"    [TECH-STATS] Querying existing (date, code) keys in {TABLE}...", flush=True)
        existing_keys = await get_existing_keys_async(conn, TABLE, ["date", "code"])
        if verbose:
            print(f"    [TECH-STATS] {len(existing_keys):,} existing (date, code) pairs", flush=True)

    if verbose:
        print(f"    [TECH-STATS] Loading distinct codes from {SOURCE_TABLE}...", flush=True)
    all_codes = await _load_all_codes(conn)
    if verbose:
        print(f"    [TECH-STATS] {len(all_codes):,} codes with non-null close", flush=True)
    if not all_codes:
        return 0

    total_upserted = 0
    n_chunks = (len(all_codes) + chunk_size - 1) // chunk_size
    for i in range(0, len(all_codes), chunk_size):
        chunk = all_codes[i:i + chunk_size]
        chunk_idx = i // chunk_size + 1
        df = await _load_close_history(conn, chunk)
        if df.empty:
            continue
        df = _compute_tech_indicators(df)
        if existing_keys:
            mask = df.apply(
                lambda r: (r["date"], r["code"]) not in existing_keys, axis=1
            )
            df = df[mask].reset_index(drop=True)
        if df.empty:
            if verbose:
                print(f"    [TECH-STATS] [{chunk_idx}/{n_chunks}] codes {chunk[0]}..{chunk[-1]}: "
                      f"0 new rows (all in DB)", flush=True)
            continue

        rows = []
        for _, r in df.iterrows():
            rows.append({
                "date": r["date"],
                "code": str(r["code"]),
                "ma5": None if pd.isna(r["ma5"]) else float(r["ma5"]),
                "ma5_ratio": None if pd.isna(r["ma5_ratio"]) else float(r["ma5_ratio"]),
                "ma20": None if pd.isna(r["ma20"]) else float(r["ma20"]),
                "ma60": None if pd.isna(r["ma60"]) else float(r["ma60"]),
                "ma120": None if pd.isna(r["ma120"]) else float(r["ma120"]),
                "ma255": None if pd.isna(r["ma255"]) else float(r["ma255"]),
                "ema6": None if pd.isna(r["ema6"]) else float(r["ema6"]),
                "ema10": None if pd.isna(r["ema10"]) else float(r["ema10"]),
                "ema20": None if pd.isna(r["ema20"]) else float(r["ema20"]),
                "ema60": None if pd.isna(r["ema60"]) else float(r["ema60"]),
                "ema120": None if pd.isna(r["ema120"]) else float(r["ema120"]),
                "ema255": None if pd.isna(r["ema255"]) else float(r["ema255"]),
            })
        n = await bulk_upsert_async(conn, TABLE, rows, ["date", "code"], batch_size=1000)
        total_upserted += n
        if verbose:
            print(f"    [TECH-STATS] [{chunk_idx}/{n_chunks}] codes {chunk[0]}..{chunk[-1]}: "
                  f"{len(rows):,} rows -> upserted {n:,} (cumulative {total_upserted:,})",
                  flush=True)

    elapsed = int(time.time() - t0)
    if verbose:
        print(f"    [TECH-STATS] Done in {elapsed}s. Total upserted: {total_upserted:,}", flush=True)
    return total_upserted


__all__ = ["run_tech_stats_chunked", "TABLE", "SOURCE_TABLE"]

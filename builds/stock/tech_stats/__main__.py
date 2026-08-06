"""Entry point for builds.stock.tech_stats.

Run via ``python -m builds.stock.tech_stats``.

Builds ``stats.stock_tech_stats`` (ma5 / ma20 / ma60 / ma120 / ma255 + ma5_ratio)
from ``stats.stock_basic_stats.close``. Mirrors the MA computation in
``builds/index/baseline/__main__.py`` and ``builds/etf/__main__.py``.

DB-first incremental mode (default):
  1. Query existing (date, code) keys in stats.stock_tech_stats.
  2. Load full per-code close history from stats.stock_basic_stats (only codes
     that have at least one missing tech_stats row), in code-batched chunks to
     bound memory.
  3. Compute MAs per code over the FULL history (rolling windows need prior
     rows for correctness).
  4. Filter to (date, code) pairs NOT already in stock_tech_stats.
  5. Bulk upsert only the missing rows.

--force mode:
  Truncate stats.stock_tech_stats first, then recompute and insert all rows.

This script is the stock counterpart of the inline MA computation that
``builds/index/baseline/__main__.py`` performs for indices. It is separate
from ``builds/stock/__main__.py`` (which only handles OHLCV+PE for missing
dates) because MA computation requires the full per-code history (not just
missing dates) for correct rolling windows.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# Ensure project root is on sys.path so ``utils`` is importable when run
# directly via ``python -m builds.stock.tech_stats`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ),
)

from utils.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_or_exit,
    bulk_upsert_async,
    truncate_table_async,
    get_existing_keys_async,
    print_build_header,
    print_wall_time,
    add_force_arg,
)

setup_utf8_stdout()

import pandas as pd  # noqa: E402

TABLE = "stats.stock_tech_stats"
SOURCE_TABLE = "stats.stock_basic_stats"

# Number of codes per chunk. Each code's full history is loaded, MAs
# computed, and missing rows upserted before moving to the next chunk.
# Bounds peak memory: ~600 rows/code × CHUNK_CODES codes × ~80 bytes/row ≈
# 50-150 MB per chunk for typical A-share stocks.
CHUNK_CODES = 500


async def _load_all_codes(conn) -> list[str]:
    """Return all distinct codes in stock_basic_stats with non-null close,
    sorted ascending.
    """
    rows = await conn.fetch(
        f'SELECT DISTINCT code FROM {SOURCE_TABLE} WHERE close IS NOT NULL ORDER BY code'
    )
    return [r["code"] for r in rows]


async def _load_close_history(conn, codes: list[str]) -> pd.DataFrame:
    """Load full per-code (date, code, close) history for the given codes,
    sorted by code, date ASC.
    """
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


def _compute_mas(df: pd.DataFrame) -> pd.DataFrame:
    """Compute ma5 / ma20 / ma60 / ma120 / ma255 + ma5_ratio per code.

    Uses rolling(window=W, min_periods=1).mean() so the first W-1 rows of
    each code get a partial MA (matching the index/etf build convention).
    """
    if df.empty:
        for col in ("ma5", "ma20", "ma60", "ma120", "ma255", "ma5_ratio"):
            df[col] = pd.Series(dtype="float64")
        return df
    g = df.groupby("code", sort=False)["close"]
    df["ma5"] = g.transform(lambda x: x.rolling(window=5, min_periods=1).mean()).round(6)
    df["ma20"] = g.transform(lambda x: x.rolling(window=20, min_periods=1).mean()).round(6)
    df["ma60"] = g.transform(lambda x: x.rolling(window=60, min_periods=1).mean()).round(6)
    df["ma120"] = g.transform(lambda x: x.rolling(window=120, min_periods=1).mean()).round(6)
    df["ma255"] = g.transform(lambda x: x.rolling(window=255, min_periods=1).mean()).round(6)
    df["ma5_ratio"] = ((df["close"] / df["ma5"]) - 1.0).round(6)
    return df


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build stats.stock_tech_stats from stock_basic_stats.close."
    )
    ap.add_argument(
        "--chunk-size", type=int, default=CHUNK_CODES,
        help=f"Number of codes per chunk (default {CHUNK_CODES}).",
    )
    add_force_arg(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "BUILD STOCK TECH_STATS  ·  MA5/20/60/120/255 from close",
        table=TABLE,
        source=SOURCE_TABLE,
        mode="FORCE (full recompute)" if args.force else "incremental (missing pairs only)",
    )

    conn = await get_db_or_exit()
    try:
        # ---- Step 0: existing keys / force ------------------------------
        if args.force:
            print(f"\n[0/3] Force mode: truncating {TABLE}...", flush=True)
            await truncate_table_async(conn, TABLE)
            existing_keys: set = set()
            print("    -> truncated; will recompute all rows", flush=True)
        else:
            print(f"\n[0/3] Querying existing (date, code) keys in {TABLE}...",
                  flush=True)
            existing_keys = await get_existing_keys_async(
                conn, TABLE, ["date", "code"]
            )
            print(f"    -> {len(existing_keys):,} existing (date, code) pairs",
                  flush=True)

        # ---- Step 1: load all codes -------------------------------------
        print(f"\n[1/3] Loading distinct codes from {SOURCE_TABLE} "
              f"(close IS NOT NULL)...", flush=True)
        all_codes = await _load_all_codes(conn)
        print(f"    -> {len(all_codes):,} codes", flush=True)
        if not all_codes:
            print("    -> no source data; exiting.", flush=True)
            return

        # ---- Step 2: chunked load + MA + filter + upsert ----------------
        print(f"\n[2/3] Processing in chunks of {args.chunk_size} codes...",
              flush=True)
        total_upserted = 0
        n_chunks = (len(all_codes) + args.chunk_size - 1) // args.chunk_size
        for i in range(0, len(all_codes), args.chunk_size):
            chunk = all_codes[i:i + args.chunk_size]
            chunk_idx = i // args.chunk_size + 1
            df = await _load_close_history(conn, chunk)
            if df.empty:
                continue
            df = _compute_mas(df)
            # Filter to missing (date, code) pairs.
            if existing_keys:
                mask = df.apply(
                    lambda r: (r["date"], r["code"]) not in existing_keys, axis=1
                )
                df = df[mask].reset_index(drop=True)
            if df.empty:
                print(f"    [{chunk_idx}/{n_chunks}] codes {chunk[0]}..{chunk[-1]}: "
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
                })
            n = await bulk_upsert_async(
                conn, TABLE, rows, ["date", "code"], batch_size=1000
            )
            total_upserted += n
            print(f"    [{chunk_idx}/{n_chunks}] codes {chunk[0]}..{chunk[-1]}: "
                  f"{len(rows):,} rows -> upserted {n:,} (cumulative {total_upserted:,})",
                  flush=True)

        # ---- Step 3: summary --------------------------------------------
        print(f"\n[3/3] Done. Total rows upserted into {TABLE}: "
              f"{total_upserted:,}", flush=True)
        print_wall_time(t0)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())

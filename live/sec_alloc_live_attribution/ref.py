"""HEAVY once-per-date reference builder → live.sec_alloc_live_prev_ref.

This module owns ALL the prev-date computation (the "expensive" part):
prev-day closes, prev-day trading amounts, and the normalized
trading-amount market-share weight. It runs ONLY for (benchmark, date)
pairs that have NO ref rows yet — typically once at the first 5-min run
of a new trading day. Every subsequent 5-min run of the same date skips
ref building entirely (see fetch.find_missing_ref_pairs).

Weight semantics:
  code_trading_amount_weight = member prev-day amount / Σ amounts across
  the benchmark's eligible universe (all fetched members have non-NULL
  amounts by construction — see fetch.fetch_ref_members). Σ = 1 per
  (benchmark, date). Constant across all ticks of the date.

Loaded table: live.sec_alloc_live_prev_ref
  PK (benchmark_code, date, code, sec_type) — upsert via bulk_upsert_async.
"""
from __future__ import annotations

import datetime
import time

import pandas as pd
from _common.build_commons import bulk_upsert_async

from .config import REF_TABLE
from .fetch import fetch_ref_members

# Columns of the ref rows handed to bulk_upsert_async (must match the table).
_REF_ROW_COLUMNS = (
    "benchmark_code",
    "date",
    "code",
    "sec_type",
    "industry_id",
    "is_industry_not_strategy",
    "prev_date",
    "code_prev_date_close",
    "benchmark_prev_date_close",
    "code_prev_date_trading_amount",
    "code_trading_amount_weight",
    "code_sec_shared_weight",
)


def compute_ref_rows(
    members: list[dict],
    benchmark_code: str,
    live_date: datetime.date,
) -> list[dict]:
    """Normalize prev-day amounts into market-share weights (pure pandas).

    Weight = member amount / Σ VALID amounts (NULL/NaN amounts excluded —
    those members keep a ref row for pct/equal-weight purposes but carry a
    NULL weight and are ignored by the weighted aggregate; Σ of non-NULL
    weights = 1). Row sec_type = the member's own sap sec_type (stocks are
    kept for share weights). Returns ref rows ready for bulk_upsert_async
    into REF_TABLE. Empty input → empty output.
    """
    if not members:
        return []

    df = pd.DataFrame(members)
    df["prev_trading_amount"] = pd.to_numeric(
        df["prev_trading_amount"], errors="coerce"
    )
    valid_amt = df["prev_trading_amount"].notna() & (df["prev_trading_amount"] > 0)

    total_amount = df.loc[valid_amt, "prev_trading_amount"].sum()
    df["code_trading_amount_weight"] = None
    if total_amount and total_amount > 0:
        df.loc[valid_amt, "code_trading_amount_weight"] = (
            df.loc[valid_amt, "prev_trading_amount"] / total_amount
        )

    bench_prev_close = (
        df["bench_prev_close"].dropna().iloc[0]
        if df["bench_prev_close"].notna().any()
        else None
    )

    # Build ref rows — vectorized: add constant columns, pre-convert
    # types, then single to_dict(records) call.
    df["benchmark_code"] = benchmark_code
    df["date"] = live_date
    df["benchmark_prev_date_close"] = bench_prev_close

    # Convert boolean column
    df["is_industry_not_strategy"] = df["is_industry_not_strategy"].astype(bool)

    # Convert prev_trading_amount: NaN → None, else float
    amt_col = "prev_trading_amount"
    df[amt_col] = pd.to_numeric(df[amt_col], errors="coerce")
    df[amt_col] = df[amt_col].where(df[amt_col].notna(), None)
    # Keep non-None values as float (to_numeric already produced float)

    # Convert code_trading_amount_weight: None/NaN → None, float values as-is
    w_col = "code_trading_amount_weight"
    df[w_col] = pd.to_numeric(df[w_col], errors="coerce")
    # Replace all NaN (incl. those from pd.to_numeric(None)) with None
    df.loc[df[w_col].isna(), w_col] = None

    # Convert code_sec_shared_weight: None/NaN → None, float values as-is
    # COALESCE'd to 0 in SQL so missing SAP rows get 0 (not NULL)
    sw_col = "code_sec_shared_weight"
    df[sw_col] = pd.to_numeric(df[sw_col], errors="coerce")
    # Replace all NaN with None
    df.loc[df[sw_col].isna(), sw_col] = None

    # Select and rename columns to match _REF_ROW_COLUMNS order
    _col_map = {
        "member_code": "code",
        "member_sec_type": "sec_type",
        "prev_close": "code_prev_date_close",
        amt_col: "code_prev_date_trading_amount",
    }
    out_df = df.rename(columns=_col_map)

    out_cols = [
        "benchmark_code", "date", "code", "sec_type",
        "industry_id", "is_industry_not_strategy", "prev_date",
        "code_prev_date_close", "benchmark_prev_date_close",
        "code_prev_date_trading_amount", "code_trading_amount_weight",
        "code_sec_shared_weight",
    ]
    out_df = out_df[out_cols].copy()
    rows = out_df.to_dict(orient="records")
    return rows


async def build_ref_pair(
    conn,
    benchmark_code: str,
    live_date: datetime.date,
) -> int:
    """Compute + upsert the heavy ref for ONE (benchmark, date) pair.

    Returns the number of ref rows upserted (0 when the pair has no
    eligible members with prev-day data).
    """
    members = await fetch_ref_members(conn, benchmark_code, live_date)
    if not members:
        return 0

    rows = compute_ref_rows(members, benchmark_code, live_date)
    if not rows:
        return 0

    n = await bulk_upsert_async(
        conn,
        REF_TABLE,
        rows,
        key_columns=["benchmark_code", "date", "code", "sec_type"],
        batch_size=2000,
    )
    return n


async def ensure_ref(
    conn,
    missing_pairs: list[tuple[str, datetime.date]],
) -> tuple[int, list[tuple[str, datetime.date]]]:
    """Build the heavy ref for every missing pair (once-per-day semantics).

    ``missing_pairs`` comes from fetch.find_missing_ref_pairs — pairs whose
    (benchmark, date) already has ref rows are NOT in the list, so 5-min
    re-runs of the same date skip the heavy pass entirely.

    Returns (total ref rows upserted, zero-ref pairs). Zero-ref pairs
    (e.g. prev-day basic_stats entirely missing for the universe) remain
    ref-less — the caller sends them to the FALLBACK tick pass so live
    equal-weighted data still flows (is_without_trading_amt = TRUE).
    """
    total = 0
    zero_ref_pairs: list[tuple[str, datetime.date]] = []
    for idx, (bench, dt) in enumerate(missing_pairs, start=1):
        t_pair = time.time()
        n = await build_ref_pair(conn, bench, dt)
        total += n
        if n == 0:
            zero_ref_pairs.append((bench, dt))
        print(
            f"    [ref {idx}/{len(missing_pairs)}] {bench} @ {dt}: "
            f"{n:,} ref rows ({time.time() - t_pair:.1f}s)",
            flush=True,
        )
    return total, zero_ref_pairs


def _delete_count(status: str) -> int:
    """Parse an asyncpg execute() status tag ('DELETE n') into an int."""
    try:
        return int(status.split()[-1])
    except (ValueError, IndexError):
        return 0


async def invalidate_ref_for_date(
    conn,
    live_date: datetime.date,
) -> tuple[int, int]:
    """Delete this date's ref + tick rows so the heavy pass rebuilds them.

    Used by the "Build Yday Ref" chain (--rebuild-latest-date): the chain
    refreshes the CSVs and rebuilds estimated daily rows BEFORE the ref
    runs, so existing refs for the date (potentially built from stale
    estimated closes) are invalidated to force a rebuild from the fresh
    data. Ticks are deleted first (FK child); fallback ticks are
    re-created by the 5-min LIVE process, weighted ticks by the heavy
    pass that follows in the same run.

    Returns (ref rows deleted, tick rows deleted).
    """
    n_tick = await conn.execute(
        "DELETE FROM live.sec_alloc_live_attribution WHERE date = $1::date",
        live_date,
    )
    n_ref = await conn.execute(
        "DELETE FROM live.sec_alloc_live_prev_ref WHERE date = $1::date",
        live_date,
    )
    return _delete_count(n_ref), _delete_count(n_tick)

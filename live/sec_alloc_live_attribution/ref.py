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

    rows: list[dict] = []
    for _, r in df.iterrows():
        weight = r["code_trading_amount_weight"]
        rows.append(
            {
                "benchmark_code": benchmark_code,
                "date": live_date,
                "code": r["member_code"],
                "sec_type": r["member_sec_type"],
                "industry_id": r["industry_id"],
                "is_industry_not_strategy": bool(r["is_industry_not_strategy"]),
                "prev_date": r["prev_date"],
                "code_prev_date_close": r["prev_close"],
                "benchmark_prev_date_close": bench_prev_close,
                "code_prev_date_trading_amount": (
                    float(r["prev_trading_amount"]) if pd.notna(r["prev_trading_amount"]) else None
                ),
                "code_trading_amount_weight": (
                    float(weight) if weight is not None and pd.notna(weight) else None
                ),
            }
        )
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

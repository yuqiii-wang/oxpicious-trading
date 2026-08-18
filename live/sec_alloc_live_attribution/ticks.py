"""LIGHT per-5-min tick loaders → live.sec_alloc_live_attribution.

Two paths, both writing the same table (PK upsert — no duplicates):

  WEIGHTED (ref-based, is_without_trading_amt = FALSE):
    fetch_missing_ticks() joins the ref table (prev closes); rows missing
    entirely OR present only as FALLBACK (TRUE) are (re)fetched and the
    upsert UPGRADES fallback rows in place to weighted rows. Tick scope is
    index/etf members only (fetch SQL enforces it); stocks hold weights in
    the ref but never get tick rows.

  FALLBACK (ref-less, is_without_trading_amt = TRUE):
    fetch_fallback_ticks() has NO ref dependency — prev close basis is the
    member's prev-day LAST 5-min bar close from stats.index_intraday_5min
    itself. Used when the ref for (benchmark, date) is not ready (heavy
    pass still running under the advisory lock elsewhere, or zero-ref
    pairs whose prev-day basic_stats data is lagging). Equal-weighted
    aggregation only — the UI disables the "by trading amt" toggle while
    only TRUE rows exist for the benchmark+date. Anti-join skips any
    existing row (fallback values are deterministic; weighted rows are
    never downgraded).
"""
from __future__ import annotations

import datetime
import time

from _common.build_commons import bulk_upsert_async

from .config import TICK_TABLE
from .fetch import fetch_fallback_ticks, fetch_missing_ticks


def _pct(close: float | None, prev_close: float | None) -> float | None:
    """close / prev_close - 1 as a FRACTION; None on None/zero prev."""
    if close is None or prev_close is None or prev_close == 0:
        return None
    return close / prev_close - 1.0


def compute_tick_rows(
    bars: list[dict],
    benchmark_code: str,
    *,
    is_without_trading_amt: bool,
) -> list[dict]:
    """Turn fetched tick bars into upsertable tick rows (pure).

    ``is_without_trading_amt`` marks provenance: TRUE = fallback row
    (no ref, equal-weight only), FALSE = ref-based weighted-capable row.
    """
    rows: list[dict] = []
    for b in bars:
        rows.append(
            {
                "code": b["code"],
                "date": b["date"],
                "time": b["time"],
                "sec_type": b["sec_type"],
                "benchmark_code": benchmark_code,
                "is_without_trading_amt": is_without_trading_amt,
                "is_without_benchmark": False,
                "code_price_pct_relative_prev_date_close": _pct(
                    b["tick_close"], b["code_prev_date_close"]
                ),
                "benchmark_price_pct_relative_prev_date_close": _pct(
                    b["bench_tick_close"], b["benchmark_prev_date_close"]
                ),
                # code_price_pct_vs_benchmark_price_pct is GENERATED — not written.
            }
        )
    return rows


async def _load_pair(
    conn,
    benchmark_code: str,
    live_date: datetime.date,
    *,
    fallback: bool,
) -> int:
    """Load pending tick rows for ONE pair via the chosen path."""
    bars = (
        await fetch_fallback_ticks(conn, benchmark_code, live_date)
        if fallback
        else await fetch_missing_ticks(conn, benchmark_code, live_date)
    )
    if not bars:
        return 0

    rows = compute_tick_rows(
        bars, benchmark_code, is_without_trading_amt=fallback
    )
    n = await bulk_upsert_async(
        conn,
        TICK_TABLE,
        rows,
        key_columns=["code", "date", "time", "sec_type", "benchmark_code"],
        batch_size=5000,
    )
    return n


async def load_ticks_pair(
    conn,
    benchmark_code: str,
    live_date: datetime.date,
) -> int:
    """WEIGHTED pass: load pending/upgrade-eligible ticks for ONE pair."""
    return await _load_pair(conn, benchmark_code, live_date, fallback=False)


async def load_fallback_ticks_pair(
    conn,
    benchmark_code: str,
    live_date: datetime.date,
) -> int:
    """FALLBACK pass: load ref-less ticks for ONE pair (TRUE rows)."""
    return await _load_pair(conn, benchmark_code, live_date, fallback=True)


async def _load_many(
    conn,
    pairs: list[tuple[str, datetime.date]],
    *,
    fallback: bool,
    label: str,
) -> int:
    total = 0
    for idx, (bench, dt) in enumerate(pairs, start=1):
        t_pair = time.time()
        n = await _load_pair(conn, bench, dt, fallback=fallback)
        total += n
        print(
            f"    [{label} {idx}/{len(pairs)}] {bench} @ {dt}: "
            f"{n:,} tick rows ({time.time() - t_pair:.1f}s)",
            flush=True,
        )
    return total


async def load_missing_ticks(
    conn,
    pairs: list[tuple[str, datetime.date]],
) -> int:
    """WEIGHTED pass over pairs with ref (missing OR upgrade-eligible)."""
    return await _load_many(conn, pairs, fallback=False, label="tick")


async def load_fallback_ticks(
    conn,
    pairs: list[tuple[str, datetime.date]],
) -> int:
    """FALLBACK pass over ref-less pairs (TRUE rows, equal-weight only)."""
    return await _load_many(conn, pairs, fallback=True, label="fbtk")

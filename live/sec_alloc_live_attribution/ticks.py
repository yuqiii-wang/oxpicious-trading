"""LIGHT per-5-min tick loaders → live.sec_alloc_live_attribution.

Two paths, both writing the same table (PK upsert — no duplicates):

  WEIGHTED (daily-close basis, is_without_trading_amt = FALSE):
    AttributionStore.fetch_missing_ticks computes prev closes AT TICK
    TIME from stats.index_basic_stats (the former
    live.sec_alloc_live_prev_ref materialization was consolidated away —
    same values, same basis). Rows missing entirely OR present only as
    FALLBACK (TRUE) are (re)fetched and the upsert UPGRADES fallback
    rows in place. Tick scope is index/etf members only (fetch SQL
    enforces it).

  FALLBACK (self-contained, is_without_trading_amt = TRUE):
    AttributionStore.fetch_fallback_ticks needs no daily-stats
    dependency — prev close basis is the member's prev-day LAST 5-min
    bar close from stats.index_intraday_5min itself. Used by the 5-min
    LIVE pass so equal-weighted data flows immediately. Anti-join skips
    any existing row (fallback values are deterministic; weighted rows
    are never downgraded).
"""
from __future__ import annotations

import datetime
import time

from pydantic import BaseModel

from _common.build_commons import bulk_upsert_async, copy_insert_async
from _common.sql_writes import render_sql_write

from .config import TICK_TABLE
from .fetch import AttributionStore

import logging
logger = logging.getLogger(__name__)


def _pct(close: float | None, prev_close: float | None) -> float | None:
    """close / prev_close - 1 as a FRACTION; None on None/zero inputs
    (a zero close is a source no-quote placeholder = NULL, not -100%)."""
    if (
        close is None or close == 0
        or prev_close is None or prev_close == 0
    ):
        return None
    return close / prev_close - 1.0


class TickRow(BaseModel):
    """One live.sec_alloc_live_attribution tick row (the write boundary's
    typed payload). ``model_dump()`` at the load calls produces exactly
    the dict layout the writers have always received; the GENERATED
    code_price_pct_vs_benchmark_price_pct is deliberately absent."""

    code: str
    date: datetime.date
    time: datetime.time
    sec_type: str
    benchmark_code: str
    is_without_trading_amt: bool
    is_without_benchmark: bool
    code_price_pct_relative_prev_date_close: float | None
    benchmark_price_pct_relative_prev_date_close: float | None


def compute_tick_rows(
    bars: list[dict],
    benchmark_code: str,
    *,
    is_without_trading_amt: bool,
) -> list[TickRow]:
    """Turn fetched tick bars into upsertable tick rows (pure).

    ``is_without_trading_amt`` marks the prev-close basis: TRUE = fallback
    row (prev-day last 5-min bar close, equal-weight only), FALSE =
    daily-close-basis row (prev-day official close from
    stats.index_basic_stats).
    """
    rows: list[TickRow] = []
    for b in bars:
        rows.append(TickRow(
            code=b["code"],
            date=b["date"],
            time=b["time"],
            sec_type=b["sec_type"],
            benchmark_code=benchmark_code,
            is_without_trading_amt=is_without_trading_amt,
            is_without_benchmark=False,
            code_price_pct_relative_prev_date_close=_pct(
                b["tick_close"], b["code_prev_date_close"]
            ),
            benchmark_price_pct_relative_prev_date_close=_pct(
                b["bench_tick_close"], b["benchmark_prev_date_close"]
            ),
        ))
    return rows


class TickWriter:
    """The pipeline's write layer (conn-scoped).

    Constructed once per run connection: ``TickWriter(conn)`` — the
    former conn-first loader functions are methods with the same names,
    SQL and sequencing; the fetch layer is the conn-scoped
    ``AttributionStore`` built alongside."""

    def __init__(self, conn) -> None:
        self.conn = conn
        self._store = AttributionStore(conn)

    async def _load_pair(
        self,
        benchmark_code: str,
        live_date: datetime.date,
        *,
        fallback: bool,
    ) -> int:
        """Load pending tick rows for ONE pair via the chosen path."""
        bars = (
            await self._store.fetch_fallback_ticks(benchmark_code, live_date)
            if fallback
            else await self._store.fetch_missing_ticks(benchmark_code, live_date)
        )
        if not bars:
            return 0

        rows = compute_tick_rows(
            bars, benchmark_code, is_without_trading_amt=fallback
        )
        if fallback:
            # Conflict-free by construction: fetch_fallback_ticks anti-joins
            # EVERY existing row (fallback values are deterministic; weighted
            # rows are never downgraded) — COPY fast path.
            n = await copy_insert_async(
                self.conn, TICK_TABLE, [r.model_dump() for r in rows]
            )
        else:
            # KEEP UPSERT: fetch_missing_ticks deliberately re-fetches rows
            # present only as FALLBACK and this write UPGRADES them in place
            # (TRUE -> FALSE) — a real, intended PK conflict.
            n = await bulk_upsert_async(
                self.conn,
                TICK_TABLE,
                [r.model_dump() for r in rows],
                key_columns=["code", "date", "time", "sec_type", "benchmark_code"],
                batch_size=5000,
            )
        return n

    async def load_ticks_pair(
        self,
        benchmark_code: str,
        live_date: datetime.date,
    ) -> int:
        """WEIGHTED pass: load pending/upgrade-eligible ticks for ONE pair."""
        return await self._load_pair(benchmark_code, live_date, fallback=False)

    async def load_fallback_ticks_pair(
        self,
        benchmark_code: str,
        live_date: datetime.date,
    ) -> int:
        """FALLBACK pass: load ref-less ticks for ONE pair (TRUE rows)."""
        return await self._load_pair(benchmark_code, live_date, fallback=True)

    async def _load_many(
        self,
        pairs: list[tuple[str, datetime.date]],
        *,
        fallback: bool,
        label: str,
    ) -> int:
        total = 0
        for idx, (bench, dt) in enumerate(pairs, start=1):
            t_pair = time.time()
            n = await self._load_pair(bench, dt, fallback=fallback)
            total += n
            logger.info(
                f"    [{label} {idx}/{len(pairs)}] {bench} @ {dt}: "
                f"{n:,} tick rows ({time.time() - t_pair:.1f}s)",
            )
        return total

    async def load_missing_ticks(
        self,
        pairs: list[tuple[str, datetime.date]],
    ) -> int:
        """WEIGHTED pass over pairs with ref (missing OR upgrade-eligible)."""
        return await self._load_many(pairs, fallback=False, label="tick")

    async def load_fallback_ticks(
        self,
        pairs: list[tuple[str, datetime.date]],
    ) -> int:
        """FALLBACK pass over pairs (TRUE rows, equal-weight only)."""
        return await self._load_many(pairs, fallback=True, label="fbtk")

    async def invalidate_ticks_for_date(
        self,
        live_date: datetime.date,
    ) -> int:
        """Delete this date's tick rows so both passes rebuild them.

        Used by the "Build Yday Ref" chain (--rebuild-latest-date): the chain
        refreshes the CSVs and rebuilds estimated daily rows BEFORE this
        pipeline runs, so existing ticks for the date (potentially computed
        from stale/estimated closes) are invalidated to force a rebuild from
        the fresh data. Fallback ticks are re-created by the 5-min LIVE
        process, daily-close-basis rows by the weighted pass that follows in
        the same run.

        Returns the number of tick rows deleted.
        """
        # live/sec_alloc_live_attribution/ticks/
        # sec_alloc_live_attribution/delete_for_date — the sql_writes
        # action file
        write = render_sql_write(
            "live/sec_alloc_live_attribution/ticks/"
            "sec_alloc_live_attribution/delete_for_date",
            live_date,
        )
        n_tick = await self.conn.execute(write.sql, *write.params)
        # asyncpg execute() returns the command tag, e.g. "DELETE 12345".
        try:
            return int(n_tick.split()[-1])
        except (ValueError, IndexError):
            return 0

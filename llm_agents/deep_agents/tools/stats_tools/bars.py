"""llm_agents.deep_agents.tools.stats_tools.bars — the daily-bar fetch
behind the stats tools.

ONE read per tool call: the security's full bar history from the
reconstructed daily views (``database/sql/stats/99_reconstruct_views.sql``
— keys ``(date, code)``, bars only: ``close IS NOT NULL`` filters the
identity LEFT JOIN rows so no rolling window ever swallows a NULL),
through the read replica (``_common.db_commons`` — the study-read
convention). Numerics arrive as ``::float8`` (never asyncpg Decimal)
and dates as ISO strings; the view's PRECOMPUTED MA/EMA columns ride
along so the tools reuse them when they cover the window
(ma5/20/60/120/255, ema6/12/20/26/60/120/255) and compute their own
only for the gaps (the 10-day short-term line and friends).

History floor 2020-01-01 (where index history starts): ~1600 bars —
enough warmup for a MA255 spread to carry a trailing-252-day
percentile, small enough to fetch per call. Calendar gaps are market
closures, never missing rows.

The fetches are methods of one process-wide :data:`BAR_SOURCE`
(2026-11-06 — the former module functions); the bar history is a typed
:class:`Bars` model.
"""
from __future__ import annotations

from datetime import date as date_obj
from typing import Dict, List

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

# sec_type → the reconstructed daily view (study_kit.db's mapping).
VIEWS: Dict[str, str] = {
    "index": "stats.v_index_daily",
    "etf": "stats.v_etf_margin",
    "stock": "stats.v_stock_daily",
    "futures": "stats.v_futures_daily",
}

# Where the bars start (index history floor); the warmup source for
# every computed window.
HISTORY_START = "2020-01-01"

# The view's precomputed columns per sec_type: SMA / EMA ladders (the
# futures view carries no tech columns — those windows always compute).
_TECH_COLUMNS = (
    "ma5", "ma20", "ma60", "ma120", "ma255",
    "ema6", "ema12", "ema20", "ema26", "ema60", "ema120", "ema255",
)


class Bars(BaseModel):
    """One security's bar history, newest last — everything the stats
    tools need. ``pre`` holds the view's precomputed MA/EMA columns
    (NaN where not yet defined); the arrays are plain host numpy
    (arbitrary-typed — the model carries, never computes)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    sec_type: str
    code: str
    name: str
    dates: List[str]
    close: np.ndarray
    high: np.ndarray
    low: np.ndarray
    amount: np.ndarray
    pre: Dict[str, np.ndarray] = Field(default_factory=dict)

    @property
    def last_date(self) -> str:
        return self.dates[-1]

    @property
    def last_close(self) -> float:
        return float(self.close[-1])


class BarSource:
    """The daily-bar source behind the stats tools (2026-11-06 — the
    former module functions): one read per tool call, the shared fetch,
    the market-totals denominator and the share gauge as methods of one
    object."""

    @staticmethod
    def _tech_projection(sec_type: str) -> str:
        if sec_type == "futures":
            return ""
        return (", " + ", ".join(f"{c}::float8 AS {c}"
                                 for c in _TECH_COLUMNS))

    @staticmethod
    def _price_columns(sec_type: str, adjusted: bool) -> str:
        if adjusted and sec_type == "etf":
            return ("adj_close::float8 AS close, adj_high::float8 AS high, "
                    "adj_low::float8 AS low")
        return "close::float8, high::float8, low::float8"

    async def fetch_bars(self, sec_type: str, code: str, *,
                         adjusted: bool = False) -> Bars:
        """One security's full bar history (newest last) off the read
        replica — the shared fetch behind every stats tool. Raises on an
        unknown sec_type or an empty history; the callers turn that into
        tool-result text."""
        view = VIEWS.get(sec_type)
        if view is None:
            raise ValueError(
                f"sec_type {sec_type!r}: expected one of {sorted(VIEWS)}")
        # Lazy import — the activation-order rule the llm_agents modules
        # follow for _common.
        from _common.db_commons import get_read_db_connection_async

        sql = (
            f"SELECT to_char(date, 'YYYY-MM-DD') AS d, "
            f"{self._price_columns(sec_type, adjusted)}, "
            f"trading_amount::float8, name "
            f"{self._tech_projection(sec_type)}"
            f" FROM {view} WHERE code = $1 AND date >= $2 "
            "AND close IS NOT NULL ORDER BY date ASC"
        )
        conn = await get_read_db_connection_async()
        try:
            rows = await conn.fetch(sql, code,
                                    date_obj.fromisoformat(HISTORY_START))
        finally:
            await conn.close()
        if not rows:
            raise ValueError(
                f"no bars for {sec_type} {code} since {HISTORY_START} — "
                "check the code (plain 6-digit form, e.g. 000001 SSE "
                "Composite) and the sec_type")
        pre: Dict[str, np.ndarray] = {}
        if sec_type != "futures":
            for col in _TECH_COLUMNS:
                pre[col] = np.array(
                    [np.nan if r[col] is None else r[col] for r in rows],
                    dtype=np.float64)
        return Bars(
            sec_type=sec_type, code=code,
            name=str(rows[0]["name"] or code),
            dates=[r["d"] for r in rows],
            close=np.array([r["close"] for r in rows], dtype=np.float64),
            high=np.array([r["high"] for r in rows], dtype=np.float64),
            low=np.array([r["low"] for r in rows], dtype=np.float64),
            amount=np.array(
                [np.nan if r["trading_amount"] is None
                 else r["trading_amount"] for r in rows], dtype=np.float64),
            pre=pre,
        )

    async def fetch_market_totals(self) -> Dict[str, float]:
        """Date → the market's total trading turnover (yuan) — the
        denominator of the house participation gauge (market share, not
        raw amount: share isolates the security's own participation while
        raw yuan drifts with overall market activity). Sum over the
        exchanges of ``stats.exchange_trading_amt``."""
        from _common.db_commons import get_read_db_connection_async

        sql = ("SELECT to_char(date, 'YYYY-MM-DD') AS d, "
               "SUM(total_trading_amount)::float8 AS total "
               "FROM stats.exchange_trading_amt WHERE date >= $1 "
               "GROUP BY date ORDER BY date ASC")
        conn = await get_read_db_connection_async()
        try:
            rows = await conn.fetch(sql,
                                    date_obj.fromisoformat(HISTORY_START))
        finally:
            await conn.close()
        return {r["d"]: r["total"] for r in rows if r["total"] is not None}

    @staticmethod
    def market_share(bars: Bars, totals: Dict[str, float]) -> np.ndarray:
        """The bars' market-share series (NaN where a date has no market
        total) — the participation gauge's base."""
        return np.array(
            [bar / totals[date] if date in totals and totals[date]
             else np.nan
             for bar, date in zip(bars.amount, bars.dates)],
            dtype=np.float64)


# The process-wide bar source (the module functions became its methods,
# 2026-11-06).
BAR_SOURCE = BarSource()

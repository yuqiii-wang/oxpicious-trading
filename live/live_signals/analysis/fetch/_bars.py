"""As-of bar resolution of the live breach check
(live.live_signals.analysis.fetch._bars).

The evaluated bar for date D (the on-demand --date mode): the LAST
intraday bar ON D when the intraday table has one (an end-of-day
replay of the live check — the breach records at that bar's time,
is_day_close_trigger = FALSE), else the code's OFFICIAL DAILY CLOSE on
D from the basic_stats baseline (recorded at 15:00 with
is_day_close_trigger = TRUE — the no-intraday-data fallback). Neither
exists ⇒ no price that day.
"""
from __future__ import annotations

import datetime

from asyncpg import Connection

from _common.sql_views import frag, render_sql_view

from live.live_signals.config import DAILY_TABLES, INTRADAY_TABLES


class AsOfBars:
    """As-of bar resolution (conn-scoped store).

    Constructed once per check connection: ``AsOfBars(conn)`` — every
    former conn-first module function of this file is a method with the
    same name, SQL and sequencing (``conn`` became ``self._conn``)."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    async def fetch_intraday_bar_on(
        self, sec_type: str, code: str, on_date: datetime.date,
    ) -> tuple | None:
        """The LAST intraday bar (date, time, close) of the code ON the
        date, or None when the intraday table has no bar that day."""
        table = INTRADAY_TABLES[sec_type]
        # live/live_signals/analysis/fetch/_bars/fetch_intraday_bar_on
        view = render_sql_view(
            "live/live_signals/analysis/fetch/_bars/fetch_intraday_bar_on",
            intraday_table=frag(table),
            code=code, on_date=on_date,
        )
        row = await self._conn.fetchrow(view.sql, *view.params)
        if row is None:
            return None
        return row["date"], row["time"], row["close"]

    async def fetch_daily_close_on(
        self, sec_type: str, code: str, on_date: datetime.date,
    ) -> float | None:
        """The code's OFFICIAL daily close on the date from the
        sec_type's basic_stats baseline, or None (no daily row that day —
        non-trading day, ingestion gap, or outside retention)."""
        table = DAILY_TABLES[sec_type]
        # live/live_signals/analysis/fetch/_bars/fetch_daily_close_on
        view = render_sql_view(
            "live/live_signals/analysis/fetch/_bars/fetch_daily_close_on",
            daily_table=frag(table),
            code=code, on_date=on_date,
        )
        row = await self._conn.fetchrow(view.sql, *view.params)
        return None if row is None else row["close"]

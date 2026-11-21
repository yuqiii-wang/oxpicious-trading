"""Fetch layer for analysis.volatility — the per-sec_type source frames
(daily OHLC bars, 5-minute intraday closes) read through the SQL-view
templates under database/sql_views/analysis/volatility/. Class-first:
one fetcher class owning the sec_type + code scope; the sec_type's own
source tables ride as fragment args (the pipeline config's
SEC_TYPE_SOURCES mapping). All views carry epoch float8 dates (the
cudf-safe DB-read convention) converted at the frame boundary via
epoch_col_to_dt64."""
from __future__ import annotations

import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64
from _common.sql_views import frag, render_sql_view
from analysis.volatility import config


class VolatilitySources:
    """The pipeline's source readers for one sec_type + code scope."""

    def __init__(self, conn, sec_type: str, codes: list[str]) -> None:
        self.conn = conn
        self.sec_type = sec_type
        self.codes = codes
        self.spec = config.SEC_TYPE_SOURCES[sec_type]

    async def fetch_daily_bars(self) -> pd.DataFrame:
        """Daily OHLC — [code, date, open, high, low, close] (one row
        per (code, date) bar in the sec_type's basic-stats table).
        is_ohl_estimated rows carry O ≈ H ≈ L ≈ C (the range estimators
        degenerate toward close-to-close there; documented in the DDL)."""
        view = render_sql_view(
            "analysis/volatility/daily_bars",
            bars_table=frag(self.spec.bars_table),
            codes=self.codes,
        )
        rows = await self.conn.fetch(view.sql, *view.params)
        if not rows:
            return pd.DataFrame(
                columns=["code", "date", "open", "high", "low", "close"])
        cols = rec_cols(rows)
        cols["date"] = epoch_col_to_dt64(cols.pop("date_epoch"))
        return pd.DataFrame(
            cols, columns=["code", "date", "open", "high", "low", "close"]
        ).sort_values(["code", "date"]).reset_index(drop=True)

    async def fetch_intraday_closes(self) -> pd.DataFrame | None:
        """The day's 5-minute bar closes per (code, date) from the
        sec_type's intraday table, ordered within the session (the SQL
        orders by code, date, time — the frame keeps that order so the
        bar-to-bar diffs are the intra-session returns; the overnight
        gap is excluded by construction). None when the sec_type has no
        intraday table (industry) — the rv leg stays NULL. No rows for
        dates outside the intraday retention window (~90 days)."""
        if self.spec.intraday_table is None:
            return None
        view = render_sql_view(
            "analysis/volatility/intraday_5min_closes",
            intraday_table=frag(self.spec.intraday_table),
            codes=self.codes,
        )
        rows = await self.conn.fetch(view.sql, *view.params)
        if not rows:
            return pd.DataFrame(columns=["code", "date", "close"])
        cols = rec_cols(rows)
        cols["date"] = epoch_col_to_dt64(cols.pop("date_epoch"))
        cols.pop("time_epoch")  # ordering already applied by the SQL
        return pd.DataFrame(
            cols, columns=["code", "date", "close"]
        ).sort_values(["code", "date"]).reset_index(drop=True)

    async def fetch_source_dates(self) -> set:
        """Dates the scoped codes have daily bars for, in the sec_type's
        own basic-stats table (python-date set — the incremental
        missing-date source side; never enters a frame)."""
        view = render_sql_view(
            "analysis/volatility/source_dates",
            bars_table=frag(self.spec.bars_table),
            codes=self.codes,
        )
        rows = await self.conn.fetch(view.sql, *view.params)
        return {r["date"] for r in rows if r["date"] is not None}

    async def fetch_target_dates(self, sec_code: int) -> set:
        """Dates the target table already carries for (sec_type, codes)
        (python-date set — the incremental missing-date have side)."""
        view = render_sql_view(
            "analysis/volatility/target_dates",
            sec_type=sec_code, codes=self.codes,
        )
        rows = await self.conn.fetch(view.sql, *view.params)
        return {r["date"] for r in rows if r["date"] is not None}

    async def resolve_universe(self) -> list[str]:
        """The sec_type's run universe: the config's fixed list when one
        is set (index = STUDY_INDEX_CODES), else data-driven — every
        code with bars in the sec_type's own basic-stats table (the
        etf / stock path)."""
        fixed = config.universe_for(self.sec_type)
        if fixed is not None:
            return list(fixed)
        view = render_sql_view(
            "analysis/volatility/universe_codes",
            bars_table=frag(self.spec.bars_table),
        )
        rows = await self.conn.fetch(view.sql, *view.params)
        return [r["code"] for r in rows if r["code"] is not None]

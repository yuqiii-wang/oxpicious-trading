"""Fetch layer for analysis.market_participation — the two source
frames (exchange proxy totals, whole-universe breadth) read through the
SQL-view templates under
database/sql_views/analysis/market_participation/. Class-first: one
fetcher class owning the window args."""
from __future__ import annotations

import datetime

import numpy as np
import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64
from _common.sql_views import render_sql_view


class ParticipationSources:
    """The pipeline's source readers. Both views carry epoch float8
    dates (the cudf-safe DB-read convention) converted at the frame
    boundary via epoch_col_to_dt64."""

    def __init__(self, conn) -> None:
        self.conn = conn

    async def fetch_exchange_totals(
        self, lo: datetime.date | None, hi: datetime.date | None,
    ) -> pd.DataFrame:
        """Per-date SS/SZ proxy turnover — [date, ss_trading_amount,
        sz_trading_amount] (one row per date where BOTH proxies
        reported)."""
        view = render_sql_view(
            "analysis/market_participation/exchange_totals")
        rows = await self.conn.fetch(view.sql, *view.params)
        if not rows:
            return pd.DataFrame(
                columns=["date", "ss_trading_amount",
                         "sz_trading_amount"])
        cols = rec_cols(rows)
        df = pd.DataFrame(
            cols, columns=["date_epoch", "ss_trading_amount",
                           "sz_trading_amount"])
        # date (the SQL's first column) is the python date itself.
        dates = np.asarray(cols["date"], dtype="datetime64[ns]")
        df["date"] = dates.astype("datetime64[us]")
        df = df.drop(columns=["date_epoch"])
        if lo is not None or hi is not None:
            lo64 = (np.datetime64(lo, "us") if lo is not None else None)
            hi64 = (np.datetime64(hi, "us") if hi is not None else None)
            if lo64 is not None:
                df = df[df["date"] >= lo64]
            if hi64 is not None:
                df = df[df["date"] <= hi64]
        return df.sort_values("date").reset_index(drop=True)

    async def fetch_breadth_daily(
        self, lo: datetime.date | None, hi: datetime.date | None,
    ) -> pd.DataFrame:
        """Per-date advance/decline breadth over the whole stock
        universe — [date, adv_count, dec_count, flat_count,
        median_pct_change, mean_pct_change] (float64 counts)."""
        view = render_sql_view(
            "analysis/market_participation/breadth_daily")
        rows = await self.conn.fetch(view.sql, *view.params)
        if not rows:
            return pd.DataFrame(
                columns=["date", "adv_count", "dec_count",
                         "flat_count", "median_pct_change",
                         "mean_pct_change"])
        cols = rec_cols(rows)
        cols["date"] = epoch_col_to_dt64(cols.pop("date_epoch"))
        df = pd.DataFrame(
            cols, columns=["date", "adv_count", "dec_count",
                           "flat_count", "median_pct_change",
                           "mean_pct_change"])
        if lo is not None or hi is not None:
            lo64 = (np.datetime64(lo, "us") if lo is not None else None)
            hi64 = (np.datetime64(hi, "us") if hi is not None else None)
            if lo64 is not None:
                df = df[df["date"] >= lo64]
            if hi64 is not None:
                df = df[df["date"] <= hi64]
        return df.sort_values("date").reset_index(drop=True)

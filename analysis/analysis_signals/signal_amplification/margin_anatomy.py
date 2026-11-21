"""The margin amplifier anatomy (analysis.analysis_signals.
signal_amplification.margin_anatomy).

MARGIN ANATOMY — the 'margin' parent type's per-day facts (2026-11-20):
the 融资余额 (margin financing balance) LEVEL vs its own trailing
quantile bars. LOW balance = leverage washed out (low risk, the price
already lower — BUY reversion amplification); HIGH balance = leverage
crowding (fragility — SELL exit amplification).

Source per sec_type (the margin_source sql view):
  index  analysis.margin_index_series.index_margin_balance (the
         analysis.margins package's weighted constituent aggregate —
         indices are not margin-eligible themselves)
  etf    stats.etf_liquidity_margin.rz_balance
  stock  stats.stock_liquidity_margin.rz_balance

Columns computed (whole-frame grouped ops — the anatomy module's wick
bar convention: trailing AMP_MARGIN_WINDOW-row quantile, >= min
periods obs, the day's own row INCLUDED — the amplifier days are
post-close records, same-day observable):
  margin_balance   the day's balance (yuan)
  margin_bar_low   the code's own trailing q10 — the margin_washout bar
  margin_bar_high  the code's own trailing q90 — the margin_crowding bar

Codes without margin data have no source rows; ``attach`` LEFT-joins
the columns onto the ohlcv anatomy frame so such codes keep NULL
margin columns — the amplifier masks are NaN-safe and never fire.

Layout: cudf GPU-first — one grouped_rolling_quantile pass per bar and
one merge; no host-numpy boundary.
"""
from __future__ import annotations

import datetime as _dt
import logging

import numpy as np
import pandas as pd

from _common.df_utils import (
    epoch_col_to_dt64,
    grouped_rolling_quantile,
)
from _common.sql_views import frag, render_sql_view

from analysis.analysis_signals import config as cfg

logger = logging.getLogger(__name__)

__all__ = ["MARGIN_ANATOMY_COLUMNS", "MarginAnatomy"]

# the per-sec_type source fragments (the ohlcv_ext _SOURCE_FRAGMENTS
# convention: the sec_type picks the margin source table + columns)
_MARGIN_FRAGMENTS: dict[str, dict[str, str]] = {
    "index": {
        # the analysis.margins package's weighted constituent aggregate
        # (indices are not margin-eligible — no per-index margin feed)
        "margin_from": "analysis.margin_index_series m",
        "code_expr": "m.index_code",
        "balance_expr": "m.index_margin_balance",
    },
    "etf": {
        "margin_from": "stats.etf_liquidity_margin m",
        "code_expr": "m.code",
        "balance_expr": "m.rz_balance",
    },
    "stock": {
        "margin_from": "stats.stock_liquidity_margin m",
        "code_expr": "m.code",
        "balance_expr": "m.rz_balance",
    },
}

# the anatomy columns this module contributes (merged onto the ohlcv
# anatomy frame; NULL where the code carries no margin data)
MARGIN_ANATOMY_COLUMNS = ["margin_balance", "margin_bar_low",
                          "margin_bar_high"]

_GROUP_KEYS = ["code"]


class MarginAnatomy:
    """Fetch the sec_type's margin series, compute the trailing margin
    bars, and attach the columns onto the ohlcv anatomy frame."""

    def __init__(self, conn, sec_type: str, codes: list[str]) -> None:
        self.conn = conn
        self.sec_type = sec_type
        self.codes = sorted(codes)

    # -- fetch ---------------------------------------------------------
    async def _fetch_source(self) -> pd.DataFrame:
        """The margin balance series for the whole scope (full history —
        the trailing bars need it)."""
        f = _MARGIN_FRAGMENTS[self.sec_type]
        view = render_sql_view(
            "analysis/analysis_signals/signal_amplification/margin_source",
            codes=self.codes,
            since=_EPOCH_START,
            margin_from=frag(f["margin_from"]),
            code_expr=frag(f["code_expr"]),
            balance_expr=frag(f["balance_expr"]),
        )
        rows = await self.conn.fetch(view.sql, *view.params)
        if not rows:
            return pd.DataFrame(
                columns=["code", "date", "margin_balance"])
        df = pd.DataFrame([dict(r) for r in rows])
        # the DB-read convention: epoch float8 -> datetime64[us]
        df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
        return df

    # -- compute -------------------------------------------------------
    def compute(self, src: pd.DataFrame) -> pd.DataFrame:
        """Add the trailing margin bars to the fetched source frame
        (see the module docstring for the bar semantics)."""
        if src.empty:
            for c in MARGIN_ANATOMY_COLUMNS[1:]:
                src[c] = pd.Series(dtype="float64")
            return src
        df = src.sort_values(_GROUP_KEYS + ["date"], kind="stable",
                             ignore_index=True)
        df["margin_bar_low"] = grouped_rolling_quantile(
            df, _GROUP_KEYS, "margin_balance",
            cfg.AMP_MARGIN_WINDOW, cfg.AMP_MARGIN_MIN_PERIODS,
            cfg.AMP_MARGIN_LOW_Q,
        )
        df["margin_bar_high"] = grouped_rolling_quantile(
            df, _GROUP_KEYS, "margin_balance",
            cfg.AMP_MARGIN_WINDOW, cfg.AMP_MARGIN_MIN_PERIODS,
            cfg.AMP_MARGIN_HIGH_Q,
        )
        return df

    # -- attach --------------------------------------------------------
    async def attach(self, anatomy: pd.DataFrame) -> pd.DataFrame:
        """Fetch + compute + LEFT-join the margin columns onto the
        ohlcv anatomy frame. Codes/dates without margin rows keep NULL
        columns (NaN-safe masks downstream)."""
        src = await self._fetch_source()
        if src.empty:
            logger.info("      margin anatomy: no margin rows in scope "
                        "— margin amplifiers dormant")
            for c in MARGIN_ANATOMY_COLUMNS:
                anatomy[c] = np.nan
            return anatomy
        margin = self.compute(src)
        n_codes = margin["code"].nunique()
        logger.info(f"      margin anatomy: {len(margin):,} margin rows "
                    f"over {n_codes} codes")
        return anatomy.merge(
            margin[_GROUP_KEYS + ["date"] + MARGIN_ANATOMY_COLUMNS],
            on=_GROUP_KEYS + ["date"], how="left",
        )


# the source history lower bound (the ohlcv_ext builder's convention —
# each code carries its own start; plain python date, no pd.Timestamp)
_EPOCH_START = _dt.date(2000, 1, 1)

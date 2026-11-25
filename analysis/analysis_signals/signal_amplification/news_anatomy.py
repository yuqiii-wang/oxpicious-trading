"""The news amplifier anatomy (analysis.analysis_signals.
signal_amplification.news_anatomy).

NEWS ANATOMY — the 'news' parent type's per-day facts (2026-11-22,
migrated from the deleted news_wash detection family the same day —
the news states AMPLIFY the existing signal families' claims, they
never create signals of their own): the daily news-text MARKET state
of ``text.news_text_daily`` (scope_kind 0 market_all) LEFT-joined per
DATE onto the ohlcv anatomy frame, plus the per-code derived axes the
amplifier masks ride.

The panel is MARKET-level (one row per trading day on the SSE
Composite's calendar), so the raw state columns are identical for
every code; the DERIVED axes are per-code (whole-frame grouped ops
over the merged frame — the market columns repeat per code, so the
grouped windows track each code's own rows):

  sent_z / lex_tone_z / flow_af / flow_neg / disp_z / n_scored
                     the fetched panel states verbatim (z-space for
                     sent_z / lex_tone_z / flow_af / disp_z; flow_neg
                     the day's strongly-negative-article count;
                     n_scored the coverage guard)
  wz / wfn / wt      the rolling-5 trading-row aggregates (mean
                     sent_z / sum flow_neg / mean lex_tone_z) — the
                     Heston-Sinha horizon finding: daily tone dies in
                     <=2 days but weekly aggregation persists ~a
                     quarter
  news_drop_leg      the UNDERLYING DIRECTION FILTER (bool): the
                     code's own price below its N-rows-ago close
                     (config AMP_NEWS_DROP_LEG_WINDOW — trigger-time
                     observable) — the daily news amplifiers' mask
                     leg; the news-state extreme alone is state, fired
                     WITH the falling curve it is the washout's
                     confirmation

Codes/dates without panel coverage keep NULL state columns and a
False drop leg — the amplifier masks are NaN-safe and never fire
there (an empty panel attaches all-NaN columns, the MarginAnatomy
convention).

Layout: cudf GPU-first — one merge per DATE + one grouped-shift /
grouped-rolling pass per derived column; no host-numpy boundary.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from _common.df_utils import (
    epoch_col_to_dt64,
    grouped_rolling_agg,
    grouped_shift,
)
from _common.sql_views import frag, render_sql_view

from analysis.analysis_signals import config as cfg

logger = logging.getLogger(__name__)

__all__ = ["NEWS_ANATOMY_COLUMNS", "NewsAnatomy"]

# the news-text metric panel's MARKET scope (scope_kind 0 rows only —
# the industry/sector event scopes carry no curve columns)
NEWS_TABLE = "text.news_text_daily"

# the fetched panel state columns (merged per DATE onto the anatomy
# frame; NULL where the panel has no row for the date)
_PANEL_COLUMNS = ["sent_z", "lex_tone_z", "flow_af", "flow_neg",
                  "disp_z", "n_scored"]

# the per-code derived axes (computed on the merged frame)
_DERIVED_COLUMNS = ["wz", "wfn", "wt", "news_drop_leg"]

# the anatomy columns this module contributes
NEWS_ANATOMY_COLUMNS = _PANEL_COLUMNS + _DERIVED_COLUMNS

_GROUP_KEYS = ["code"]


class NewsAnatomy:
    """Fetch the market news-state panel, attach it per DATE onto the
    ohlcv anatomy frame, and compute the per-code derived axes (the
    weekly aggregates + the drop leg) the amplifier masks ride."""

    def __init__(self, conn, sec_type: str, codes: list[str]) -> None:
        self.conn = conn
        self.sec_type = sec_type
        self.codes = sorted(codes)

    # -- fetch ---------------------------------------------------------
    async def _fetch_source(self) -> pd.DataFrame:
        """The market-scope news state panel — one row per trading day
        (full history; the weekly aggregates need it)."""
        view = render_sql_view(
            "analysis/analysis_signals/signal_amplification/news_source",
            news_table=frag(NEWS_TABLE),
        )
        rows = await self.conn.fetch(view.sql, *view.params)
        if not rows:
            return pd.DataFrame(columns=["date"] + _PANEL_COLUMNS)
        df = pd.DataFrame([dict(r) for r in rows])
        # the DB-read convention: epoch float8 -> datetime64[us]
        df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
        return df

    # -- compute -------------------------------------------------------
    def compute(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Add the per-code derived axes to the state-merged anatomy
        frame (see the module docstring for the column semantics)."""
        w = cfg.AMP_NEWS_WEEKLY_WINDOW
        frame = frame.assign(
            wz=grouped_rolling_agg(frame, _GROUP_KEYS, "sent_z", w,
                                   min_periods=w),
            wfn=grouped_rolling_agg(frame, _GROUP_KEYS, "flow_neg", w,
                                    agg="sum", min_periods=w),
            wt=grouped_rolling_agg(frame, _GROUP_KEYS, "lex_tone_z", w,
                                   min_periods=w),
        )
        # the drop leg: price below its N-rows-ago close (a NaN
        # comparison never qualifies — fill before the mask rides it)
        price_n_ago = grouped_shift(
            frame, _GROUP_KEYS, ["close"], out_names=["_close_n_ago"],
            periods=cfg.AMP_NEWS_DROP_LEG_WINDOW,
        )["_close_n_ago"]
        frame = frame.assign(
            news_drop_leg=(frame["close"] < price_n_ago).fillna(False),
        )
        return frame

    # -- attach --------------------------------------------------------
    async def attach(self, anatomy: pd.DataFrame) -> pd.DataFrame:
        """Fetch + merge the panel per DATE onto the ohlcv anatomy
        frame, then compute the derived axes. An empty panel attaches
        all-NaN state columns and a False drop leg (the news
        amplifiers go dormant — the MarginAnatomy convention)."""
        panel = await self._fetch_source()
        if panel.empty:
            logger.info("      news anatomy: no market-scope panel rows "
                        "— news amplifiers dormant")
            for c in _PANEL_COLUMNS + ["wz", "wfn", "wt"]:
                anatomy[c] = np.nan
            anatomy["news_drop_leg"] = False
            return anatomy
        out = anatomy.merge(panel, on=["date"], how="left")
        out = self.compute(out)
        logger.info(f"      news anatomy: {len(panel):,} panel days "
                    f"merged onto {len(out):,} anatomy rows")
        return out

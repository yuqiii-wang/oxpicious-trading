"""analysis.text_metrics.compute.metrics — the daily text-metric core.

COMPUTE LAYOUT (cudf-dtypes skill): whole-frame pandas is the default —
the per-(industry, date) aggregation is ONE ``groupby().sum()`` pass
over masked product columns (the derivation-pattern identity: masked
mean/std come from Σ(level·has_level) / Σhas_level, never per-group
lambdas), and every trailing statistic rides the shared
``grouped_rolling_agg`` / ``grouped_shift`` helpers (GPU under
cudf.pandas). No per-group python loops, no host-numpy boundary: this
metric family has no gather/index algorithm in it. Frames carry float64
measures + int64 counts + plain string ``industry_id`` group keys (the
cuDF-native hash path); NaN is the null carrier in float columns; dates
arrive as datetime64[us] (epoch_col_to_dt64 at the fetch boundary).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg, grouped_shift

from analysis.text_metrics.config import (
    MA_WINDOW,
    MARKET_INDUSTRY_ID,
    MIN_ARTICLES,
    MIN_Z_OBS,
    OUT_COLUMNS,
    SENTIMENT_BAND,
    Z_WINDOW,
)


class DailyTextMetrics:
    """Articles panel + keyword-tone panel → the analysis.text_metrics_
    daily output frame (one row per (industry_id, date) past the
    MIN_ARTICLES gate).

    Params are the config literals; ``run()`` is pure frame compute —
    no DB, no I/O — so the pipeline stays the only stateful layer.
    """

    def __init__(self) -> None:
        self.z_window = Z_WINDOW
        self.min_z_obs = MIN_Z_OBS
        self.ma_window = MA_WINDOW
        self.band = SENTIMENT_BAND
        self.market_id = MARKET_INDUSTRY_ID
        self.min_articles = MIN_ARTICLES

    def run(self, articles: pd.DataFrame,
            tone: pd.DataFrame) -> pd.DataFrame:
        """Full pipeline: daily aggregate → tone join → trailing z/ma →
        the MIN_ARTICLES gate. Output columns follow OUT_COLUMNS;
        ``date`` stays datetime64[us] (the write boundary converts)."""
        daily = self._daily_aggregate(articles)
        daily = self._attach_tone(daily, tone)
        daily = self._trailing_stats(daily)
        return self._gate(daily)

    # -- daily aggregate ---------------------------------------------------

    def _daily_aggregate(self, articles: pd.DataFrame) -> pd.DataFrame:
        """Per-article rows → the per-(industry_id, date) daily frame.

        The broad-market row (industry_id '') is computed by the SAME
        grouped pass: the frame is duplicated once with the industry tag
        flattened to '', so the market aggregate is the union of every
        industry's rows plus the untagged articles.
        """
        tagged = articles.loc[articles["industry_id"] != ""]
        market = tagged.assign(industry_id=self.market_id)
        panel_src = pd.concat([articles, market], ignore_index=True)

        level = panel_src["sentiment_level"]
        has_level = level.notna()
        # Pure-bool masks AFTER the null fill, cast before arithmetic
        # (the bool×numeric cudf rule).
        is_pos = ((level.fillna(0.0) >= self.band)
                  & has_level).astype("int64")
        is_neg = ((level.fillna(0.0) <= -self.band)
                  & has_level).astype("int64")
        work = panel_src.assign(
            article=panel_src["digestible"].astype("int64"),
            has_level=has_level.astype("int64"),
            pos=is_pos,
            neg=is_neg,
            level=level.fillna(0.0),
        )
        work["level_m"] = work["level"] * work["has_level"]
        work["level_sq"] = work["level"] ** 2 * work["has_level"]

        daily = work.groupby(["industry_id", "date"], sort=True).agg(
            article_count=("article", "sum"),
            digested_count=("has_level", "sum"),
            pos_count=("pos", "sum"),
            neg_count=("neg", "sum"),
            level_m=("level_m", "sum"),
            level_sq=("level_sq", "sum"),
        ).reset_index()

        n = daily.pop("digested_count").astype("float64")
        mean = daily.pop("level_m") / n
        # Population std (ddof=0) over the day's digested articles — the
        # dispersion evidence; the MIN_ARTICLES gate keeps n >= 3 so the
        # ddof choice is immaterial at the row level.
        var = (daily.pop("level_sq") / n - mean ** 2).clip(lower=0.0)
        daily["digested_count"] = n.astype("int64")
        daily["sentiment_mean"] = mean
        daily["sentiment_std"] = np.sqrt(var)
        return daily

    # -- keyword tone --------------------------------------------------------

    def _attach_tone(self, daily: pd.DataFrame,
                     tone: pd.DataFrame) -> pd.DataFrame:
        """Merge the per-(industry, date) bull/bear sums; the '' market
        row's sums are the DATE-TOTAL across every industry plus the
        untagged rows (the tone frame already carries '' for untagged —
        one more grouped sum builds the market side)."""
        if tone.empty:
            bull = np.zeros(len(daily), dtype="int64")
            bear = np.zeros(len(daily), dtype="int64")
        else:
            # Market total = the DATE-TOTAL over every tone row (tagged
            # industries + the '' untagged rows, each exactly once); the
            # per-industry rows keep their own sums. Concatenating the
            # raw '' rows back in would double-count the untagged
            # mentions (the 2026-11-22 940-vs-939 verify catch).
            tagged_tone = tone.loc[tone["industry_id"] != ""]
            market_tone = (tone.groupby("date", sort=True)[
                ["bull_mentions", "bear_mentions"]]
                .sum().reset_index()
                .assign(industry_id=self.market_id))
            tone_agg = pd.concat(
                [tagged_tone, market_tone], ignore_index=True).groupby(
                ["industry_id", "date"], sort=True)[
                ["bull_mentions", "bear_mentions"]].sum().reset_index()
            merged = daily.merge(
                tone_agg.rename(columns={
                    "bull_mentions": "kw_bull_mentions",
                    "bear_mentions": "kw_bear_mentions"}),
                on=["industry_id", "date"], how="left")
            daily = merged
            bull = (daily["kw_bull_mentions"]
                    .fillna(0).astype("int64").to_numpy())
            bear = (daily["kw_bear_mentions"]
                    .fillna(0).astype("int64").to_numpy())
        daily["kw_bull_mentions"] = bull
        daily["kw_bear_mentions"] = bear
        gross = (daily["kw_bull_mentions"]
                 + daily["kw_bear_mentions"]).astype("float64")
        daily["kw_tone"] = (
            (daily["kw_bull_mentions"]
             - daily["kw_bear_mentions"]).astype("float64") / gross
        ).where(gross > 0)  # NaN when the day had no directional mentions
        return daily

    # -- trailing statistics ---------------------------------------------------

    def _trailing_stats(self, daily: pd.DataFrame) -> pd.DataFrame:
        """Prior-window z-scores + the ma5 smoothing, per industry.

        The rolling aggregates include the current row, so each is
        shifted by 1 within the group BEFORE the z arithmetic — the
        current day never enters its own window (the Tetlock abnormal
        transform). Windows are counted in article-days (panel rows).
        """
        panel = daily.sort_values(["industry_id", "date"],
                                  ignore_index=True)

        # Prior-window article volume → flow_z.
        panel["count_f"] = panel["article_count"].astype("float64")
        panel["roll_count_mean"] = grouped_rolling_agg(
            panel, "industry_id", "count_f", self.z_window,
            min_periods=self.min_z_obs, agg="mean", sort=False)
        panel["roll_count_std"] = grouped_rolling_agg(
            panel, "industry_id", "count_f", self.z_window,
            min_periods=self.min_z_obs, agg="std", sort=False)
        panel = grouped_shift(
            panel, "industry_id",
            ["roll_count_mean", "roll_count_std"],
            out_names=["prior_count_mean", "prior_count_std"],
            periods=1, sort=False)
        sd = panel["prior_count_std"]
        panel["flow_z"] = (
            (panel["count_f"] - panel["prior_count_mean"]) / sd
        ).where(sd > 0)

        # Prior-window sentiment level → sentiment_z.
        panel["roll_sent_mean"] = grouped_rolling_agg(
            panel, "industry_id", "sentiment_mean", self.z_window,
            min_periods=self.min_z_obs, agg="mean", sort=False)
        panel["roll_sent_std"] = grouped_rolling_agg(
            panel, "industry_id", "sentiment_mean", self.z_window,
            min_periods=self.min_z_obs, agg="std", sort=False)
        panel = grouped_shift(
            panel, "industry_id",
            ["roll_sent_mean", "roll_sent_std"],
            out_names=["prior_sent_mean", "prior_sent_std"],
            periods=1, sort=False)
        sd = panel["prior_sent_std"]
        panel["sentiment_z"] = (
            (panel["sentiment_mean"] - panel["prior_sent_mean"]) / sd
        ).where(sd > 0)

        # Online smoothing: 5-article-day mean of sentiment_z (incl. the
        # current row — the crowdedness_score_ma5 pattern).
        panel["sentiment_ma5"] = grouped_rolling_agg(
            panel, "industry_id", "sentiment_z", self.ma_window,
            min_periods=1, agg="mean", sort=False)
        return panel.drop(
            columns=["count_f", "roll_count_mean", "roll_count_std",
                     "prior_count_mean", "prior_count_std",
                     "roll_sent_mean", "roll_sent_std",
                     "prior_sent_mean", "prior_sent_std"])

    # -- row gate -----------------------------------------------------------------

    def _gate(self, daily: pd.DataFrame) -> pd.DataFrame:
        """Rows exist only where defined: digested_count >= MIN_ARTICLES
        (the analysis.pe NULL rule)."""
        return daily.loc[daily["digested_count"] >= self.min_articles,
                         list(OUT_COLUMNS)].reset_index(drop=True)

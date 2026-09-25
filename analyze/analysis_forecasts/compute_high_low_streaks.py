"""MA-Spread High/Low streak MEAN-MID anchor annual-snapshot aggregation
(analysis_forecasts) — sparse tensor engine.

The mov_pairs engine's event-bucket machinery applied to the EXISTING
band-break excursion streaks of analysis.mov_ave_high_low_pct_streaks
(fetched by fetch.fetch_high_low_streaks with each streak's SIDE
derived in SQL off the unrounded end-date close vs the end month's
band). Every streak period is audited at its MEAN-MID anchor day:

    anchor = the ((n - 1) // 2 + 1)-th own trading day of the span
             [start_date, end_date], n = the stored day_count — the
             floor of the MEAN elapsed day (n-1)/2 ("mean mid elapsed
             day once entered a streak"; an 8-day streak anchors its
             4th day, a 7-day streak its 4th, a 1-day streak the day
             itself).

The anchor is EX-POST: the streak length — hence its mid — is known
only after the streak closes, so the buckets audit streak-period
behaviour (the 2026-09 study temp_scripts/study_high_low_streaks_
forecast.py shows a strong mean-reversion reading from the mid anchor:
below-band streaks drift UP, above-band streaks drift DOWN); they are
NOT a live trigger.

``build_streak_anchor_cells`` resolves every streak to its anchor
(t, c) grid cell ONCE (per-code cumulative own-row machinery on the
(T, C) grid — no python row loops):

  own    — bool (T, C): the code's OWN trading rows on the grid
           (frame rows ∩ the project CN trading calendar — the same
           row space the streaks step counted its day_count on);
  cum    — int32 (T, C): per-column cumulative own-row count;
  inv    — int32 (T, C): ordinal -> grid row (the inverse of cum);
  anchor — lo = searchsorted(grid, start), hi = searchsorted(grid,
           end, right); the (k+1)-th own row in the span is
           inv[cum[lo-1, c] + k, c] with k = min((n-1)//2, m-1), m =
           own rows in the span. The stored mid is CLAMPED to the
           frame's own rows: where the forecast frame drops rows the
           streaks step saw (estimated-close rows on index/etf), m <
           n and the anchor shifts to the nearest available mid row —
           the anchor must be a date the forward-change matrices know.

``compute_high_low_streaks_results`` then runs the _HlStreaksEngine
(WideDfEngine) over the anchor cells: window filter (anchor date in
(stat_date - WINDOW_YEARS, stat_date]), the live gate (codes join from
their own first-data date — actual windows, min(actual, WINDOW_YEARS)),
per-(period, pct_type) combo and
side, hype split of the ANCHOR cells, horizon aggregation
and result-row expansion. No cooldown —
each streak contributes exactly ONE trigger and streaks are inherently
separated (a streak ends only after a 6+-day in-band gap or a side
switch), the state-family shape (margin_ratio precedent).

The config JSONB records the bucket's streak-length context:
{"mean_day_count": float, "min_day_count": int, "max_day_count": int}
(asyncpg COPY needs a JSON text string).

Yields (stat_date, rows) so __main__ can write snapshot-major batches to
analysis_forecasts.high_low_streaks + forecast_results.
"""

from __future__ import annotations


from analyze.analysis_forecasts.config import (
    HIGH_LOW_STREAKS_PERIODS,
    HIGH_LOW_STREAKS_TYPES,
)

# The family's (band_period, pct_type) config combos — the same axes
# the forecast buckets audit (and the future high_low_streaks signal
# engine would emit on).
COMBOS = tuple(
    (period, pct_type)
    for period in HIGH_LOW_STREAKS_PERIODS
    for pct_type in HIGH_LOW_STREAKS_TYPES
)





import json

from collections.abc import Iterator
from datetime import date

import numpy as np
import pandas as pd

from analyze.analysis_forecasts._dfengine import WideDfEngine


class _HlStreaksEngine(WideDfEngine):
    """High/Low streak MEAN-MID anchor buckets — the streaks of
    analysis.mov_ave_high_low_pct_streaks are READ (no recomputation)
    and each contributes ONE trigger: the ((day_count-1)//2 + 1)-th
    trading day of its span (the mean-mid anchor, resolved against the
    union trading-day calendar). The anchor is EX-POST (the streak
    length is known only after the streak closes) — an audit of
    streak-period behaviour, not a live trigger. The streak's own span
    rides as the run_len / streak span (one trigger per streak; runs
    inherently separated), the bucket's config JSONB records the
    day_count context, and trigger_excess is NULL (band excursion, no
    scalar bar)."""

    BUCKET_COLS = ("band_period", "pct_type")
    MERGE = False            # anchors are pre-resolved — no merge pass

    def __init__(self, *, streaks_df, **kwargs) -> None:
        super().__init__(**kwargs)
        self.streaks_df = streaks_df

    def _prepare(self) -> None:
        super()._prepare()
        cal = self._cal.rename(columns={"date": "_start"})
        st = self.streaks_df.merge(
            cal[["_start", "_t"]], left_on="start_date", right_on="_start",
            how="inner",
        )
        # the ((day_count-1)//2 + 1)-th trading day of the span
        st["_anchor_t"] = st["_t"] + (st["day_count"] - 1) // 2
        cal2 = self._cal.rename(columns={"date": "_anchor_date",
                                         "_t": "_at"})
        st = st.merge(cal2, left_on="_anchor_t", right_on="_at",
                      how="inner")
        anchors = st[[
            "code", "_anchor_date", "side", "period", "pct_type",
            "day_count", "start_date", "end_date",
        ]].rename(columns={
            "_anchor_date": "date",
            "period": "band_period",
            "run_len": "run_len",
            "start_date": "streak_start",
            "end_date": "streak_end",
        })
        anchors["run_len"] = anchors["day_count"]
        anchors["excess"] = np.nan       # band excursion — no scalar bar
        self._anchors = anchors
        self._prepared = self._prepared.merge(
            anchors, on=["code", "date"], how="left",
        )
        self._prepared["_is_anchor"] = self._prepared["side"].notna()

    def _extra_window_cols(self) -> list[str]:
        # the per-streak anchor columns merged onto the prepared frame
        # (declared so _cells_to_rows' forward-join slice skips them —
        # they already ride on the cells)
        return ["_is_anchor", "side", "band_period", "pct_type",
                "run_len", "streak_start", "streak_end", "excess"]

    def emit_signals(self, win: pd.DataFrame) -> Iterator[pd.DataFrame]:
        cells = win[win["_is_anchor"]].copy()
        if cells.empty:
            return
        # The anchors are pre-resolved ex-post streak ends — one trigger
        # per streak at the constant delay 0 (no incremental anchors).
        cells["delay"] = 0
        yield cells

    def bucket_extras(self, cells, keys):
        """The bucket's config JSONB: the streak-length context (mean /
        min / max day_count)."""
        g = cells.groupby(keys, sort=False).agg(
            mean_day_count=("run_len", "mean"),
            min_day_count=("run_len", "min"),
            max_day_count=("run_len", "max"),
        ).reset_index()
        g["bucket_config"] = self._bucket_config_json(g)
        return g.drop(columns=["mean_day_count", "min_day_count",
                               "max_day_count"])

    @staticmethod
    def _bucket_config_json(g) -> list[str]:
        """The per-bucket streak-length context JSON — built from ONE
        device→host conversion per column (Series.tolist is not a cuDF
        op; per-group .tolist() would force a whole-operation pandas
        fallback per bucket at full-universe scale)."""
        means = g["mean_day_count"].to_numpy().tolist()
        mins = g["min_day_count"].to_numpy().tolist()
        maxs = g["max_day_count"].to_numpy().tolist()
        return [
            json.dumps({"mean_day_count": round(float(m), 2),
                        "min_day_count": int(mn),
                        "max_day_count": int(mx)})
            for m, mn, mx in zip(means, mins, maxs)
        ]


def compute_high_low_streaks_results(
    *, df, first_dates, regimes, codes, sec_type, specs, streaks_df,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_date, high_low_streaks bucket rows) per month."""
    engine = _HlStreaksEngine(
        df=df, first_dates=first_dates, regimes=regimes, codes=codes,
        sec_type=sec_type, specs=specs, streaks_df=streaks_df,
    )
    return engine.run()

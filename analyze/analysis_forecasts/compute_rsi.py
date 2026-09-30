"""RSI extreme-percentile bucket detection (analysis_forecasts)
— the metric file.

One shared ``PercentileEngine`` serves the percentile family:

  - mov_rsi: value = rsi_{W}days (Wilder RSI, W ∈ RSI_WINDOWS), buckets
    keyed (rsi_window, pct).

Per (code, config) the bar is the linearly-interpolated quantile of the
window's non-NULL values — the cudf.pandas quantile machinery of the
base ``_quantile_bars`` (pos = q·(valid_n−1); the exact
``_engine.quantile_threshold`` semantics) — and the tests are NESTED
per side, widest tail first (top-1% ⊆ top-2% ⊆ … ⊆ top-10%):

    top    → value ≥ τ(q = 1 − pct/100)     (highest pct% of values)
    bottom → value ≤ τ(q = pct/100)         (lowest pct% of values)

Everything else — the (code chunk × snapshot) partition, the
streak-merge (consecutive qualifying days → ONE signal with
incremental anchor triggers at delays 0..TRIGGER_DELAY_MAX),
the market-hype split, the forward-change aggregation, the blended
mixed row and the row emission — is inherited from
``_dfengine.WideDfEngine``. Yields (stat_date, rows) snapshot-major.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import numpy as np
import pandas as pd

from analyze.analysis_forecasts._dfengine import (
    WideDfEngine,
    _finite_mask,
)
from analyze.analysis_forecasts.config import (
    RSI_PCTS,
    RSI_SIDES,
    RSI_WINDOWS,
)


class PercentileEngine(WideDfEngine):
    """Extreme-percentile buckets for one indicator column family.

    Args (subclass constants/params):
        value_prefix: the fetched columns' prefix (e.g. "rsi") — the
            window slice carries f"{prefix}_{w}days" columns.
        window_col: the mov-table bucket key ("rsi_window").
        windows: the W values (RSI_WINDOWS).
        pcts: the percentile widths (RSI_PCTS).
        sides: ("top", "bottom").
    """

    BUCKET_COLS: tuple[str, ...] = ()
    MERGE = True               # consecutive qualifying days → ONE incremental-anchor signal
    value_prefix: str = ""
    window_col: str = ""
    windows: tuple = ()
    pcts: tuple = ()
    sides: tuple = RSI_SIDES

    # The derived-population family identities (analyze.
    # analysis_forecasts.ohlcv_boost / ohlcv_selection — the 2026-09-27
    # OHLCV CONSOLIDATION; the four legacy siblings ohlc_selection /
    # ohlc_boost / vol_selection / vol_boost were RETIRED): mov_rsi's
    # parent forecast keeps its FULL trigger population, plus the
    # ohlcv-score BOOST child (ohlcv_type 'ohlcv_boost' — the bottom
    # side re-weighted by the three-axis day-0 score: OHLC pattern +
    # vol_state × px_speed cell + RSI tail depth) and the ohlcv-score
    # SELECTION child (ohlcv_type 'ohlcv_selection' — the strict
    # score-positive subset of the same bottom-side population) — each
    # under its own forecast_id.
    ohlcv_boost_bucket: str | None = "mov_rsi"
    ohlcv_selection_bucket: str | None = "mov_rsi"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.BUCKET_COLS = (self.window_col, "pct")

    def _extra_window_cols(self) -> list[str]:
        return [f"{self.value_prefix}_{w}days" for w in self.windows]

    def emit_signals(self, win: pd.DataFrame) -> Iterator[pd.DataFrame]:
        id_vars = ["code", "date", "_t", "regime"]
        value_cols = self._extra_window_cols()

        # the window's own bounds — the cache needs the exclusive start
        lower = (win["date"].min() - pd.Timedelta(days=1))
        upper = win["date"].max()

        # ---- long (code, date, window, value) frame of the valid
        # observations — one melt, one vectorized validity filter.
        long = win.melt(
            id_vars=id_vars, value_vars=value_cols,
            var_name="_wcol", value_name="value",
        )
        long[self.window_col] = long["_wcol"].map(
            {col: w for col, w in zip(value_cols, self.windows)}
        )
        long = long[_finite_mask(long["value"])]
        if long.empty:
            return

        # ---- the per-config bars: one (group × q) table joined against
        # the group ranks (the base's sort + gather quantile). All
        # windows × sides × pcts resolve in ONE pass. (A rolling
        # full-history bar cache was measured SLOWER at 789s vs 743s —
        # the 5y window covers ~90% of all history, so the per-snapshot
        # O(N) cache passes cost as much as the sort they replace.)
        q_small = pd.DataFrame({
            self.window_col: [w for w in self.windows for _ in self.pcts
                              for _ in self.sides],
            "side": [s for _ in self.windows for _ in self.pcts
                     for s in self.sides],
            "pct": [p for _ in self.windows for p in self.pcts
                    for _ in self.sides],
            "q": [(1.0 - p / 100.0 if s == "top" else p / 100.0)
                  for _ in self.windows for p in self.pcts
                  for s in self.sides],
        })
        groups = long[[self.window_col, "code"]].drop_duplicates()
        q_spec = groups.merge(q_small, on=self.window_col, how="inner")
        bars = self._quantile_bars(
            long[[self.window_col, "code", "value"]],
            group_cols=[self.window_col, "code"],
            q_spec=q_spec,
        )
        # NaN bar = the column had no valid value at all — no bucket.
        bars = bars[bars["bar"].notna()]
        if bars.empty:
            return

        # ---- bucket tests, nested widest-first per side. Membership is
        # monotone in the tail width (top: bar_{p} >= bar_{P} for p <= P,
        # so {value >= bar_p} ⊆ {value >= bar_P}; bottom mirrors), so the
        # WIDEST tail is tested on the full observation frame and each
        # narrower tail is carved from the previous level's survivors —
        # the merge volume is len(long) + |hits at 10%| + |hits at 5%| +
        # ... instead of len(long) × n_pcts. Identical compares, identical
        # cells (invalid days never reach here; a NaN bar never forms —
        # all widths of a (window, code) group share one valid_n, so a
        # group's bars exist together). Codes whose own history does not
        # span the full window were already gated out by the partition's
        # live filter.
        keep = ["code", "date", "_t", "regime",
                self.window_col, "side", "pct"]
        cells_parts = []
        for side in self.sides:
            sb = bars[bars["side"] == side][
                [self.window_col, "code", "pct", "bar"]]
            cur = long
            for p in sorted(self.pcts, reverse=True):
                cur = cur.merge(
                    sb[sb["pct"] == p][
                        [self.window_col, "code", "bar"]
                    ].rename(columns={"bar": "_bar"}),
                    on=[self.window_col, "code"], how="inner",
                )
                cur = cur[
                    (cur["value"] >= cur["_bar"]) if side == "top"
                    else (cur["value"] <= cur["_bar"])
                ]
                hit = cur.assign(side=side, pct=p)
                hit["excess"] = hit["value"] - hit["_bar"]
                cells_parts.append(hit[keep + ["excess"]])
                cur = cur.drop(columns=["_bar"])
        cells = pd.concat(cells_parts, ignore_index=True)
        if cells.empty:
            return

        # Streak-merge + the forward aggregation / row building are the
        # base's — snapshot_rows consumes what this yields. regime is in
        # the run group so each regime bucket's anchor ladder stays
        # contiguous 0..max (a regime flip starts a fresh signal).
        yield self._streak_merge(
            cells,
            group_cols=[self.window_col, "side", "pct", "regime", "code"],
        )


class _RsiEngine(PercentileEngine):
    """mov_rsi — RSI extreme-percentile buckets."""

    value_prefix = "rsi"
    window_col = "rsi_window"
    windows = RSI_WINDOWS
    pcts = RSI_PCTS
    sides = RSI_SIDES


def _run(engine_cls, *, df, first_dates, regimes, codes, sec_type, specs):
    engine = engine_cls(
        df=df,
        first_dates=first_dates,
        regimes=regimes,
        codes=codes,
        sec_type=sec_type,
        specs=specs,
    )
    return engine.run()


def compute_rsi_results(
    *,
    df, first_dates, regimes, codes, sec_type, specs,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_date, mov_rsi bucket rows) per stat date."""
    return _run(_RsiEngine, df=df, first_dates=first_dates,
                regimes=regimes, codes=codes, sec_type=sec_type,
                specs=specs)

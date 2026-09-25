"""pe_state bucket annual-snapshot aggregation (analysis_forecasts) —
extreme-percentile engine.

The valuation extreme-PERCENTILE buckets over the PE series of
analysis.pe (see database/sql/analysis/analysis_forecasts/
11_pe_state.sql): per stat date's trailing 5-year window [lo, hi) of
the (T, C) wide grid, a (code, date) joins a bucket when its raw PE
sits in the top pct% (bucket extreme 'top' — the linearly-interpolated
quantile of the window's non-NULL pe values at q = 1 - pct/100) or
bottom pct% (extreme 'bottom', q = pct/100) of the window, per the
code's OWN distribution (the fetch layer's raw `pe` column; NaN here
means "no bucket" — no-earnings / invalid-PE days). Only EXTREME days
form buckets — the mov_rsi pct convention (the 2026-09 refactor of the
former z-STATE buckets; the mid/flat central bulk forms no bucket).

The family's defining semantics: pe is LOWER-the-better — the
top-pct% (expensive, stretched) days are bearish (side 'top'), the
bottom-pct% (cheap) days bullish (side 'bottom'); the bucket extreme
maps to the family side IDENTITY (the dividend-yield sibling,
compute_dividend, REVERSES the mapping).

Everything else — the (code chunk × stat month) partition, the
streak-merge (consecutive qualifying days → ONE signal with
incremental anchor triggers at delays 0..TRIGGER_DELAY_MAX;
the bucket's mean run length recorded on
forecast_identities.streak_signal_days), the market-hype split, the
forward-change aggregation, the blended mixed row and the row
emission — is inherited from ``_dfengine.WideDfEngine``. Yields
(stat_date, rows) snapshot-major. Each anchor's trigger excess (the
anchor day's pe minus the bucket's quantile bar, value − bar) rides
forecast_results.trigger_excess — the family now has a scalar
qualifying bar (the quantile), unlike the former band membership.
"""


from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date

import pandas as pd

from analyze.analysis_forecasts._dfengine import (
    WideDfEngine,
    _finite_mask,
)
from analyze.analysis_forecasts.config import (
    VAL_PCTS,
    VAL_SIDES,
)


class ValPctEngine(WideDfEngine):
    """Valuation extreme-percentile buckets over ONE series (the
    compute_rsi.PercentileEngine's detection, no window axis — one
    value column per family). Per (side, pct) the bar is the window's
    linearly-interpolated quantile (the base ``_quantile_bars``) and
    the test is one boolean pass per side; the bucket extreme maps to
    the family side via ``side_of_bucket`` (pe: identity — high PE =
    expensive = 'top'; dividend: flipped — high yield = cheap =
    'bottom'). Consecutive qualifying days streak-merge into ONE
    incremental-anchor signal (mov_rsi convention); the excess is the
    trigger value minus the bucket's quantile bar."""

    BUCKET_COLS = ("pct",)
    MERGE = True
    src_col: str = ""            # "pe" / "dividend_yield"
    side_of_bucket: dict = {}    # bucket extreme → family side
    pcts: tuple = VAL_PCTS       # the percentile widths (percent)
    sides: tuple = VAL_SIDES     # the bucket extremes ("top", "bottom")

    def _extra_window_cols(self) -> list[str]:
        return [self.src_col]

    def emit_signals(self, win: pd.DataFrame) -> Iterable[pd.DataFrame]:
        has = _finite_mask(win[self.src_col])
        if not has.any():
            return
        base = win.loc[has, ["code", "date", "_t", "regime"]].copy()
        base["value"] = win.loc[has, self.src_col]

        # ---- the per-config bars: one (code × side × pct) table joined
        # against the code ranks (the base's sort + gather quantile) —
        # all sides × pcts resolve in ONE pass (the constant-key merge
        # fans the (side, pct, q) grid out per code, the PercentileEngine
        # window-join idiom without a window axis).
        q_small = pd.DataFrame({
            "side": [s for _ in self.pcts for s in self.sides],
            "pct": [p for p in self.pcts for _ in self.sides],
            "q": [(1.0 - p / 100.0 if s == "top" else p / 100.0)
                  for p in self.pcts for s in self.sides],
        })
        groups = base[["code"]].drop_duplicates()
        groups["_k"] = 1
        q_spec = groups.merge(q_small.assign(_k=1), on="_k").drop(
            columns=["_k"],
        )
        bars = self._quantile_bars(
            base[["code", "value"]],
            group_cols=["code"],
            q_spec=q_spec,
        )
        # NaN bar = the code had no valid value at all — no bucket.
        bars = bars[bars["bar"].notna()]
        if bars.empty:
            return

        # ---- bucket tests, one vectorized pass per side (invalid days
        # never reach here; a NaN bar never forms — so they never enter
        # a bucket). Codes whose own history does not span the full
        # window were already gated out by the partition's live filter.
        keep = ["code", "date", "_t", "regime", "side", "pct"]
        cells_parts = []
        for side, test in (("top", "ge"), ("bottom", "le")):
            sb = bars[bars["side"] == side][["code", "pct", "bar"]]
            cand = base.merge(sb, on="code", how="inner")
            qual = (
                (cand["value"] >= cand["bar"]) if test == "ge"
                else (cand["value"] <= cand["bar"])
            )
            hit = cand[qual].copy()
            hit["side"] = self.side_of_bucket[side]
            hit["excess"] = hit["value"] - hit["bar"]
            cells_parts.append(hit[keep + ["excess"]])
        cells = pd.concat(cells_parts, ignore_index=True)
        if cells.empty:
            return

        # Streak-merge + the forward aggregation / row building are the
        # base's — month_rows consumes what this yields. regime is in
        # the run group so each regime bucket's anchor ladder stays
        # contiguous 0..max (a regime flip starts a fresh signal).
        yield self._streak_merge(
            cells, group_cols=["side", "pct", "regime", "code"],
        )


class _PeValPctEngine(ValPctEngine):
    src_col = "pe"
    # pe LOWER-the-better: the top-pct% (expensive) extremes are the
    # bearish 'top' side, the bottom-pct% (cheap) extremes 'bottom'.
    side_of_bucket = {"top": "top", "bottom": "bottom"}


def compute_pe_results(
    *, df, first_dates, regimes, codes, sec_type, specs,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_date, pe_state bucket rows) per month."""
    engine = _PeValPctEngine(
        df=df, first_dates=first_dates, regimes=regimes, codes=codes,
        sec_type=sec_type, specs=specs,
    )
    return engine.run()

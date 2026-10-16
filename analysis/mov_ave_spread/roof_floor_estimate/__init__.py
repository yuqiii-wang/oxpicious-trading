"""Quantitative roof/floor LEVEL ESTIMATES — internal step package of
analysis.mov_ave_spread.

Target table: analysis.mov_ave_spreads_roof_floor_estimates (LONG — one
row per (sec_type, code, date, period, method)). For every roof/floor
window period ∈ {60, 120, 255, 500, 750, 1275} this step estimates, per
(date, method), the RESISTANCE (roof) and SUPPORT (floor) price level AT
that date from the strictly-causal rolling window ending there — the
quantitative counterpart of the trendlines drawn by the Ma-Spread UI's
Roof/Floor Trendlines row (which reads the ANCHOR table,
mov_ave_spreads_roof_and_floor_lines).

Methods (static.label_dim 'roof_floor_method'; see methods/):

  trendline  the repo's own structural method — the roof/floor anchor
             LINE (two-anchor half-split semantics shared with the
             anchor table via _common.ohlc_vector) extrapolated to the
             row date: high_2nd + high_line_slope × days_since_last_high
             (per side; NULL when the anchors do not determine a line)
  donchian   the classic breakout channel — window max intraday high /
             min intraday low (Donchian bounds)
  quantile   a distributional envelope — the rolling 90th / 10th
             percentile of valid closes in the window
  channel    a sloped Raff-style linear-regression channel — the OLS fit
             of valid closes on trading-day position, evaluated at the
             row date, ± 2 × residual σ (rolling-sum identities, ≥ 3
             valid closes)
  boll       a Bollinger-style band at the window — SMA(period) ± 2 ×
             population σ of close
  median     the NaN-skipping per-(date, period) MEDIAN of the five
             methods above — the robust cross-method consensus row

All methods share the anchor table's window region rule
[max(0, i-period+1), i] in trading-day positions (partial windows at a
code's history start; NaN closes skipped).

Compute layout (cudf): donchian / quantile / channel / boll are whole-
frame grouped rolling passes (grouped_rolling_agg / grouped_rolling_
quantile) + elementwise where-chains — GPU under cudf.pandas; the
quantile pass is the accepted CPU-fallback convention (cuDF lacks
rolling quantile). The trendline method reuses the ohlc step's sanctioned
host core (compute_group_anchors_all_windows — sparse-table argmax) with
the same B-A3 batched .loc write, so its estimates are bit-identical
functions of the SAME anchor computation the anchor table stores; the
median method unwraps the five method columns to host numpy once
(np.nanmedian) and writes back host arrays.
"""
from analysis.mov_ave_spread.roof_floor_estimate.compute import (
    compute_estimate_columns,
)
from analysis.mov_ave_spread.roof_floor_estimate.long_frame import (
    build_estimate_long_frame,
)
from analysis.mov_ave_spread.roof_floor_estimate.step import (
    run_roof_floor_estimates,
)

__all__ = [
    "compute_estimate_columns",
    "build_estimate_long_frame",
    "run_roof_floor_estimates",
]

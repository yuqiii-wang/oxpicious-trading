"""analyze.analysis_signals.signals — the per-day signal engines, one
module per signal family (see _base for the shared machinery).

For each target stat month's trailing 5-year window [lo, hi) of the
(T, C) wide grid — the SAME window, thresholds, cooldown and
full-window history gate the analysis_forecasts bucket engines use —
detect the extreme days and emit signal rows:

  mov_rsi.compute_rsi_signals — rsi_{W}days in the top pct% (RSI_PCT =
      1 → action=sell) or bottom pct% (action=buy) of the window's
      non-NULL values (linear-interpolated percentile threshold).

  mov_std.compute_std_signals — price beyond the 2σ Bollinger band
      ma_{W} ± k·std_{W}days (upper → sell, lower → buy).

  mov_gap.compute_gap_signals — gap_{W}days (the W-day fractional
      price return, from analysis.mov_ave_rsi) in the top pct%
      (GAP_PCT = 1 → action=sell, sharp W-day rally) or bottom pct%
      (action=buy, sharp W-day selloff) of the window's non-NULL
      values.

  px_vol.compute_px_vol_signals — σ-standardized price speed × 量比
      z-score state cells (the analysis_forecasts.px_vol_state
      detection at signal granularity; adaptive constant t/z bars, no
      cooldown).

  margin_ratio.compute_margin_ratio_signals — margin-buy intensity
      (融资买入额/成交额 ratio) z-score states (the
      analysis_forecasts.margin_ratio_state detection at signal
      granularity; adaptive constant z bars, no cooldown; etf + stock
      only — index has no margin data).

  high_low_streaks.compute_hls_signals — the MEAN-MID anchor day of
      every MA-Spread band-break excursion streak (the
      analysis_forecasts.high_low_streaks bucket trigger days 1:1 —
      the forecasts engine's own anchor machinery reused verbatim;
      side top an ABOVE-band excursion → sell, bottom → buy; no
      cooldown; EX-POST anchor — write-once months gated by a resolve
      lag + a closed-streak guard, see the module docstring).

  mov_pairs.compute_pairs_signals — the CROSS days (golden / death
      cross) of the EXISTING relative-MA spreads ma5_vs_ma{W} (and the
      EMA sibling ema6_vs_ema{W} via the same source-agnostic code
      path): side top a CROSS UP → sell, bottom a CROSS DOWN → buy;
      cooldown 5, the forecast event-bucket convention.

  (The former opp_pair.compute_opp_pair_signals — industry
  opposite-pair trend forecasts — was removed: the pair buckets
  average ~600 trigger days yet ~0 pooled mean forward offset change,
  and the OOS study showed zero mean confirmation content. See
  analysis_signals.config for the study reference.)

Yields (stat_month, rows) so __main__ can write month-major (one
atomic transaction per month, keeping the month-granular incremental
detection crash-safe).
"""
from analyze.analysis_signals.signals._base import ConfirmMap
from analyze.analysis_signals.signals.high_low_streaks import (
    compute_hls_signals,
)
from analyze.analysis_signals.signals.margin_ratio import (
    compute_margin_ratio_signals,
)
from analyze.analysis_signals.signals.mov_gap import compute_gap_signals
from analyze.analysis_signals.signals.mov_pairs import compute_pairs_signals
from analyze.analysis_signals.signals.mov_rsi import compute_rsi_signals
from analyze.analysis_signals.signals.mov_std import compute_std_signals
from analyze.analysis_signals.signals.px_vol import compute_px_vol_signals

__all__ = [
    "ConfirmMap",
    "compute_gap_signals",
    "compute_hls_signals",
    "compute_margin_ratio_signals",
    "compute_pairs_signals",
    "compute_px_vol_signals",
    "compute_rsi_signals",
    "compute_std_signals",
]

"""Gap (N-day return) extreme-bucket monthly aggregation
(analysis_forecasts) — sparse tensor engine.

The mov_rsi engine (compute_rsi.py) applied to the gap_{W}days columns
(W-day fractional price return from analysis.mov_ave_rsi, W ∈ {2, 3}):

For each stat month's trailing 5-year window [lo, hi) of the (T, C) wide
grid and each gap window W:

  1. Sort the window slice of gap_{W} column-wise ONCE (np.sort puts NaN
     last) — every percentile threshold (top + bottom × 1/5/10/25) is then
     a linear-interpolated gather from the same sorted matrix.
  2. Bucket mask: top → V ≥ τ(q=1−pct/100) (sharp W-day rally);
     bottom → V ≤ τ(q=pct/100) (sharp W-day selloff). NaN comparisons
     are False, so invalid days never enter a bucket. Codes whose own
     history does not span the full window are gated out.
  3. The (side, pct) configs are stacked into ONE (T, C, K) bucket mask
     tensor (side-major), and the UNIFIED bucket-signal pipeline
     (wide.iter_bucket_subsets) runs the whole shared span ONCE on the
     flattened (T, C·K) stack (columns are config-independent):
     streak-merge, sparsification, live-gated per-config streak counts
     and the (side, hype) subset splits as group-ascending trigger-cell
     lists — every downstream reduction (hype split, per-horizon mean /
     high / low n-day forward change and P(reverse beyond the code's
     adaptive threshold) via wide.aggregate_horizons_sparse)
     works on those lists. The row payload is expanded by
     wide.build_result_rows.

Gap values are unbounded fractional returns (unlike 0–100 RSI) but the
percentile machinery is rank-based — identical code path.

Yields (stat_month, rows) so __main__ can split each row into the
mov_gap motivation dicts and the forecast_results result dicts and write
month-major.
"""


from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from analyze.analysis_forecasts.compute_rsi import PercentileEngine
from analyze.analysis_forecasts.config import GAP_PCTS, GAP_SIDES, GAP_WINDOWS


class _GapEngine(PercentileEngine):
    """mov_gap — the mov_rsi engine applied to the gap_{W}days columns
    (W-day fractional price return from analysis.mov_ave_rsi,
    W ∈ {2, 3}): same percentile bars, streak-mid anchors, hype split
    and horizon aggregation."""

    value_prefix = "gap"
    window_col = "gap_window"
    windows = GAP_WINDOWS
    pcts = GAP_PCTS
    sides = GAP_SIDES


def compute_gap_results(
    *, df, first_dates, episodes, codes, sec_type, specs,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, mov_gap bucket rows) per stat month."""
    engine = _GapEngine(
        df=df,
        first_dates=first_dates,
        episodes=episodes,
        codes=codes,
        sec_type=sec_type,
        specs=specs,
    )
    return engine.run()

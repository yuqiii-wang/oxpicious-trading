"""Wide (date × code) matrix machinery for analyze.analysis_forecasts.

The monthly aggregation engines work on 2-D numpy matrices of shape
(T, C) — T = union trading-day grid rows, C = codes — so a whole
sec_type is aggregated with vectorized passes instead of per-code
Python loops. The GPU work happens BELOW this package: the fetch layer
loads and derives every long-format column through cudf.pandas
DataFrames (vectorized grouped ops — grouped_shift / grouped_rolling_agg),
and ``grid.scatter_column`` unwraps the cudf.pandas proxy ONCE at the
pandas→numpy boundary — from there the engine math never pays a
GPU↔CPU transfer or a cudf fallback.

Pipeline per sec_type:
  1. ``build_month_specs`` — the target stat months (the completed
     month-ends plus the RUNNING month, keyed at its month-end but
     window-bounded at today) and each month's inclusive trailing-window
     start (month-end minus WINDOW_YEARS + 1 day).
  2. ``build_grid`` — factorize the long frame into the (T, C) grid.
  3. ``scatter_column`` — long column → wide matrix (one fancy-index
     assignment; NaN where the code has no row on a grid date).
  4. ``build_change_matrices`` — wide forward-change matrices (endpoint
     changes + the MM horizons' path-extreme swings) + validity
     shared by every engine.
  5. ``month_row_windows`` — per stat month, the [lo, hi) grid-row range
     of its trailing 5-year window.
  6. ``iter_bucket_subsets`` — the engines' UNIFIED bucket-signal
     pipeline: streak-merge (apply_streak_midpoints; or one-day
     signals) → sparsify → live-gated per-config counts → per-side →
     per-hype split, yielded as the group-ascending sparse cell lists
     of step 7.
  7. ``aggregate_horizons_sparse`` — batched per-(code, config) mean/
     std/high/low/reverse-prob stats of ALL forward horizons over the
     SPARSE trigger-cell lists of a stacked (T, C, K) bucket mask
     (bincount/reduceat — work scales with the trigger count, not the
     dense tensor). With ``win_ord`` it also gathers the per-horizon
     ragged TRIGGER-DATE lists (the calendar dates behind
     occurrence_count — the forecast_results.trigger_dates column).
  8. ``build_result_rows`` — expand one batch's gathered aggregates into
     the forecast_results fields of its emitted rows — 4 period rows
     per bucket (the three horizons plus the weight-blended 'mixed' row),
     vectorized (no per-row scalar rounding calls).
Module map: months (specs + window resolution) · grid (factorization +
scatters) · changes (forward-change matrices) · thresholds (reversal
bars) · signals (streak-merge + bucket subsets) · horizons (sparse
aggregation) · results (row expansion).
"""
from __future__ import annotations

from .months import (
    MonthSpec,
    MonthWindow,
    _shift_years,
    build_month_specs,
    month_row_windows,
)
from .grid import (
    build_grid,
    build_px_vol_state_matrices,
    date_ordinals,
    first_ords_from_dates,
    scatter_column,
)
from .changes import build_change_matrices
from .thresholds import reverse_thresholds, window_sigmas
from .signals import (
    apply_cooldown_rolling,
    apply_streak_midpoints,
    iter_bucket_subsets,
)
from .horizons import HorizonAgg, aggregate_horizons_sparse
from .results import (
    _round_none,
    build_result_rows,
    round6,
)

__all__ = [
    "MonthSpec",
    "MonthWindow",
    "_shift_years",
    "build_month_specs",
    "month_row_windows",
    "build_grid",
    "build_px_vol_state_matrices",
    "date_ordinals",
    "first_ords_from_dates",
    "scatter_column",
    "build_change_matrices",
    "reverse_thresholds",
    "window_sigmas",
    "apply_cooldown_rolling",
    "apply_streak_midpoints",
    "iter_bucket_subsets",
    "HorizonAgg",
    "aggregate_horizons_sparse",
    "_round_none",
    "build_result_rows",
    "round6",
]

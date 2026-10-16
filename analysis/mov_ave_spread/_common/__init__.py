"""Shared logic for analysis.mov_ave_spread — one module per concern:

  guards       — NUMERIC overflow guards + gap-ratio helpers
  derive       — source-frame column derivations (slopes, σ, trading amt)
  ohlc_vector  — vectorised roof/floor anchor algorithm (sparse-table
                 argmax host core; used by the ohlc-lines step)
  steps        — the internal-step pipeline skeleton (run_table_step)
"""
from analysis.mov_ave_spread._common.guards import (
    gap_col,
    null_if_overflow,
    null_if_overflow_counted,
    safe_ratio,
)
from analysis.mov_ave_spread._common.derive import (
    compute_ema_slopes_curvatures,
    compute_rolling_stds,
    compute_slopes_curvatures,
    compute_trading_amt_ma_slopes,
    compute_trading_amt_mas,
    compute_trading_amt_market_share_mas,
    compute_trading_amt_market_share_vs_mas,
)
from analysis.mov_ave_spread._common.steps import (
    filter_target_dates,
    infer_sec_types,
    purge_step_scope,
    resolve_target_dates,
    run_table_step,
    sec_type_code,
)

__all__ = [
    # guards
    "gap_col", "null_if_overflow", "null_if_overflow_counted", "safe_ratio",
    # derive
    "compute_ema_slopes_curvatures", "compute_rolling_stds",
    "compute_slopes_curvatures", "compute_trading_amt_ma_slopes",
    "compute_trading_amt_mas", "compute_trading_amt_market_share_mas",
    "compute_trading_amt_market_share_vs_mas",
    # steps plumbing
    "filter_target_dates", "infer_sec_types", "purge_step_scope",
    "resolve_target_dates", "run_table_step", "sec_type_code",
]

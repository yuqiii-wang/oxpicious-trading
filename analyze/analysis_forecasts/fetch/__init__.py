"""Async DB fetchers for analyze.analysis_forecasts.

Loads, per sec_type, the joined long-format input frame and the
per-family source tables the bucket engines consume:

  price   — the same price convention as the parent mov_ave_spread
            analysis (ETF = COALESCE(etf_adjustment.adj_close, close);
            index / stock = *_basic_stats.close),
  ma_{W}  — from stats.{sec_type}_tech_stats (ma5/20/60/120/255),
  rsi_{W} — from analysis.mov_ave_rsi (Wilder RSI columns),
  std_{W} — from analysis.mov_ave_spreads_detail (Bollinger sigma),
  pair_{W} — from analysis.mov_ave_spreads_detail (the EXISTING
            ma5_vs_ma{W} relative-MA spreads, W ∈ MOV_PAIRS_WINDOWS —
            the mov_pairs cross buckets' input; no new MA computation),
  ema_pair_{W} — from analysis.mov_ave_spreads_detail_ema (the
            EXISTING ema6_vs_ema{W} relative-EMA spreads, W ∈
            MOV_PAIRS_EMA_WINDOWS — the mov_pairs_ema buckets' input),
  trading_amount + rz_buy — the px_vol / margin_ratio family inputs
            (daily turnover + RONGZI margin buy; rz_buy is NULL for
            'index' — indices have no margin data, so the
            margin_ratio buckets never fire there),
  pe + dividend_yield — the pe_state / dividend_state family inputs
            (the two analysis.pe / analysis.dividends valuation tables;
            a NULL join means "no bucket" — invalid-PE days have no pe
            row and non-payers no dividend_yield row).

Plus the compact market-hype EPISODES list (stats.mov_ave_market_hypes)
and the per-family source-of-truth loaders (price_vs_amt registry,
high/low streaks, industry opposite pairs).

One source family per module — codes (universe + first dates) · inputs
(the joined frame) · forward (forward changes + path extremes, derived
on the cudf.pandas frame) · hype · px_vol · margin · valuation ·
streaks · opp_pair · identity.

All NUMERIC columns are cast to native float8 in SQL (Decimal objects
would poison the cudf.pandas fast path) and the date columns arrive as
epoch float8, materialized via epoch_col_to_dt64 — the frame the
engines receive is cudf-representable from construction, and the
forward-change / feature adds run as vectorized cudf.pandas grouped ops
over it (no per-code Python loops).
"""
from __future__ import annotations

from .codes import fetch_active_codes, fetch_first_dates
from .inputs import fetch_analysis_inputs
from .forward import (
    add_forward_changes,
    add_path_extremes,
)
from .hype import fetch_hyped_episodes
from .px_vol import (
    add_px_vol_features,
    assert_price_vs_amt_params,
    fetch_price_vs_amt_source,
    fetch_price_vs_amt_states,
)
from .margin import add_margin_ratio_features
from .valuation import add_valuation_features
from .streaks import fetch_high_low_streaks
from .opp_pair import (
    fetch_benchmark_closes,
    fetch_industry_closes,
    fetch_industry_first_dates,
    fetch_opp_pair_industries,
    fetch_opp_pair_pairs,
)
from .identity import fetch_forecast_identity

# Legacy private aliases (historical underscore names kept for the
# cross-package importers).
from ._sources import AMT_SOURCE as _AMT_SOURCE  # noqa: E402
from ._sources import PRICE_SOURCE as _PRICE_SOURCE  # noqa: E402

__all__ = [
    "fetch_active_codes",
    "fetch_first_dates",
    "fetch_analysis_inputs",
    "add_forward_changes",
    "add_path_extremes",
    "fetch_hyped_episodes",
    "assert_price_vs_amt_params",
    "fetch_price_vs_amt_source",
    "fetch_price_vs_amt_states",
    "add_margin_ratio_features",
    "add_valuation_features",
    "fetch_high_low_streaks",
    "fetch_benchmark_closes",
    "fetch_industry_closes",
    "fetch_industry_first_dates",
    "fetch_opp_pair_industries",
    "fetch_opp_pair_pairs",
    "fetch_forecast_identity",
]

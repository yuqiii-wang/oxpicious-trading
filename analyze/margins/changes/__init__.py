"""Margin trend change detection — package for ``analysis.margin_changes``.

Re-exports the pipeline entry point ``run_margin_changes``.

Submodules:
  - constants : tunable params, INSERT_COLUMNS, NUMERIC_COLS, DESCRIPTION
  - detection : trend episode segmentation (slope_ma5 sign + gap bridging
                + zscore magnitude significance filter)
  - price_rsi : fetch price RSI from mov_ave_rsi + compute margin/price
                RSI ratio (index only)
  - price_ohlc: fetch price OHLC from basic_stats tables + compute the 4
                OHLC margin/price ratios (all sec_types)
  - forcasts  : fetch forward price closes + compute 5d/20d/60d forward
                highs, lows, and days-to-extremes per trend episode
  - db_io     : truncate-then-COPY-insert into margin_changes
  - runner    : ``run_margin_changes`` entry point (orchestrates the above)
"""
from analyze.margins.changes.runner import run_margin_changes
from analyze.margins.changes.forcasts import run_margin_forcasts

__all__ = ["run_margin_changes", "run_margin_forcasts"]

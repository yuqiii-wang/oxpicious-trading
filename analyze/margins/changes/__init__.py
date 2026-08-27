"""Margin trend change detection — package for ``analysis.margin_changes``.

Re-exports the pipeline entry point ``run_margin_changes``.

Submodules:
  - constants : tunable params, INSERT_COLUMNS, NUMERIC_COLS, DESCRIPTION
  - detection : trend episode segmentation (slope_ma5 sign + gap bridging
                + zscore magnitude significance filter)
  - trading_amt: fetch trading_amount + compute
                rz_buy_vs_trading_amt_ratio per trend episode
  - db_io     : truncate-then-COPY-insert into margin_changes
  - runner    : ``run_margin_changes`` entry point (orchestrates the above)
"""
from analyze.margins.changes.runner import run_margin_changes

__all__ = ["run_margin_changes"]

"""Generic trading / execution / portfolio backtest engine.

Strategy-agnostic: takes a DataFrame whose signal layer has already
populated a single consolidated ``signal_confidence`` column (signed
[-100, 100]; >0 BUY, <0 SELL rising-edge, 0 none) plus the auxiliary
``signal_value`` magnitude, and one strategy callback
(``signal_reason_fn``) for the human-readable reason text. All financial
math lives here; a strategy package (e.g. ``singleton_trading``) supplies
only the signal layer.

Public entry points (re-exported below):
  - :func:`run_backtest`        — full run across codes
  - :func:`backtest_single_code` — one code's date series
  - :func:`compute_daily_rows`  — daily portfolio state (unrealized P&L, Sharpe)
  - :func:`compute_total_buy_cost` — peak capital deployed
  - :func:`summarize`           — run-level summary stats

Module layout:
  - ``constants`` — FEE_RATE, SLIPPAGE_BAND, NORMALIZATION_BASE,
    SHARES_PER_QTY, TRADING_DAYS_PER_YEAR
  - ``formula``    — documented financial formulas (fill, slippage, fee,
    position, cash, realized_pnl, cost basis, total_buy_cost, return)
  - ``engine``     — portfolio backtest engine + daily state computation

The engine uses a worst-case OHLC fill model (BUY fills at the day's
highest plausible price, SELL at the lowest) as a conservative stress-test.
See ``formula.py`` for the exact formulas and ``constants.py`` for the
model parameters.
"""
from __future__ import annotations

# Re-export the public engine surface so callers can import everything from
# ``strategy._trading`` without reaching into submodules.
from strategy._trading.engine import (  # noqa: F401
    backtest_single_code,
    run_backtest,
    compute_daily_rows,
    compute_total_buy_cost,
    summarize,
)

__all__ = [
    "backtest_single_code",
    "run_backtest",
    "compute_daily_rows",
    "compute_total_buy_cost",
    "summarize",
]

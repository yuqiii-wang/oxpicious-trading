"""MA-spread backtest adapter.

Wires the signal layer (``strategy._signal``) to the execution layer
(``strategy._trading``):

  1. The signal layer reads data per MA-trading requirements and runs the
     MA trading algorithm, consolidating the result into a singular signed
     ``signal_confidence`` ∈ [-100, 100] (>0 BUY, <0 SELL, 0 none).
  2. This adapter applies that signal to the fetched DataFrame, then
     delegates to ``strategy._trading.engine.run_backtest`` with the
     MA-specific signal-reason builder.

All trading / execution / portfolio math — worst-case OHLC fills,
slippage, fees, position / cash / realized-P&L accounting, daily portfolio
state, Sharpe ratios, run-level summary — lives in ``strategy._trading``.
This module only supplies the MA-specific signal layer + reason text.

Execution model summary (see ``_trading.engine`` for the full docstring):
  - Signal at CLOSE of date T; order FILLS same day at a WORST-CASE OHLC price.
  - BUY = max(high, close + 0.2·(high−low));  SELL = min(low, close − 0.2·(high−low)).
  - Slippage = |fill − close| / 100  (per-100-shares scale).
  - Fee = 0.2% of BUY notional, BUY only (0 for SELL).
  - Last bar reserved for final liquidation.
"""
from __future__ import annotations

from strategy._trading.engine import (  # noqa: F401
    run_backtest as _run_backtest_engine,
    compute_daily_rows,           # re-exported for __main__
    compute_total_buy_cost,       # re-exported for callers / runner
    summarize,                    # re-exported for __main__
)
from strategy._signal import (
    apply_signals,                # reads data → MA algo → consolidated signal
    build_signal_reason,          # MA-specific reason text
)


def run_backtest(df, params, sec_type, codes):
    """Run the MA-spread backtest: apply the signal layer, then the engine.

    Thin wrapper preserving the public signature expected by the runner
    (``run_backtest(df, params, sec_type, codes)``). Applies the MA-spread
    signal layer (which adds the consolidated ``signal_confidence`` column)
    and delegates to ``strategy._trading.engine.run_backtest``.
    """
    if not df.empty:
        df = apply_signals(df, params)
    return _run_backtest_engine(
        df, params, sec_type, codes,
        signal_reason_fn=build_signal_reason,
    )

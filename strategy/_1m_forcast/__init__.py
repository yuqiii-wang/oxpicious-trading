"""strategy._1m_forcast — 1-month forward sell-confidence forecast.

Replaces the single last-day FINAL LIQUIDATION SELL (in
``strategy._trading.engine.backtest_single_code``) with a 20-trading-day
forward-looking, scenario-based SELL confidence schedule:

  - Take the last 20 trading days' OHLC + the 255-day daily-return std.
  - sigma_20d = daily log-return std over last 20 days.
  - sigma_255d = daily log-return std over last 255 days.
  - sigma_255d_max = max rolling 255d std over the past year (peak vol).
  - Compute FOUR scale ratios:
      255d_std_scale       = sigma_255d / sigma_20d  (current long-term / recent)
      255d_std_half_scale  = 0.5 * (sigma_255d / sigma_20d)
      20d_std_scale        = 1.0  (20d baseline, unscaled)
      255d_max_std_scale   = sigma_255d_max / sigma_20d  (peak 1y / recent)
  - Generate 10 forecast curves from the 20d history OHLC:
      2 for 255d_std_scale:       mirror + flip
      2 for 255d_std_half_scale:  mirror + flip at half the 255d/20d ratio
      2 for 20d_std_scale:        mirror + flip at unscaled 1.0
      2 for 255d_max_std_scale:   mirror + flip at the peak 1y std ratio
      2 for 0.5σ random:          random walk + opposite trend (negated steps)
  - A computed ``mean`` (average of all 10 per day) drives the sell schedule
    persisted to trade_decision + strategy_daily.
  - For each curve, derive a 20-day SELL confidence schedule that fully
    liquidates the remaining position by day 20 (day-20 conf = 100).
  - P&L forecast = last actual total_pnl + cumulative realized P&L from the
    sell schedule (starts where the backtest's Total P&L curve ends).
  - Persist all 11 scenarios (10 curves + mean) × 20 days to
    ``strategy.forecast_1m``.

Standalone CLI: ``python -m strategy._1m_forcast``. Operates on existing
``singleton_trading`` runs (reads seq_ids); test with ``--sec-type index``
first, then re-run for etf/stock.

Module layout:
  - ``constants`` — horizon, scenarios, scale ratios, table name
  - ``compute``   — pure functions: sigma, mirror/flip OHLC, sell schedule, P&L
  - ``fetch``     — DB reads: run end state + trailing 20d/255d OHLC + RSI
  - ``upsert``    — DB write to strategy.forecast_1m + forecast_1m_stats
  - ``decisions`` — inserts the mean scenario as trade_decision + daily rows
"""
from __future__ import annotations

STRATEGY_NAME = "singleton_trading"  # the backtest runs this forecasts for

__all__ = ["STRATEGY_NAME"]

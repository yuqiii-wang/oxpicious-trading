"""Constants for the 1-month forward sell-confidence forecast.

Replaces the single last-day FINAL LIQUIDATION SELL (in
``strategy._trading.engine.backtest_single_code``) with a 20-trading-day
forward-looking, scenario-based SELL confidence schedule.

Model summary (v4 — 10 mirror/flip/random curves)
-------------------------------------------------
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
  - A computed "mean" (average of all 10 per day) drives the sell schedule
    that gets persisted to trade_decision + strategy_daily.
  - Each curve's SELL schedule is take-profit + baseline, normalized so the
    whole position is liquidated by day 20 (confidence=100).
  - P&L forecast = last actual total_pnl + cumulative realized P&L from the
    sell schedule (starts where the backtest's Total P&L curve ends).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Forecast horizon
# ---------------------------------------------------------------------------
HORIZON_DAYS = 20

# Lookback for the long-term volatility reference (1 trading year).
LONG_TERM_DAYS = 255

# ---------------------------------------------------------------------------
# DB table
# ---------------------------------------------------------------------------
FORECAST_TABLE = "strategy.forecast_1m"

# ---------------------------------------------------------------------------
# Scenarios: 10 curves at 4 scale ratios + 2 random walks.
# Each mirror/flip entry: (label, scale_key, flip).
#   scale_key selects the std ratio:
#     "255d_std_scale"       → sigma_255d/sigma_20d  (current long-term / recent)
#     "255d_std_half_scale"  → 0.5 * (sigma_255d/sigma_20d)
#     "20d_std_scale"        → 1.0
#     "255d_max_std_scale"   → sigma_255d_max/sigma_20d  (peak 1y rolling 255d std / recent)
#   flip=False → mirror (time-reversed), flip=True → flip (time-reversed + inverted)
# The 2 random-walk scenarios are handled separately.
# ---------------------------------------------------------------------------
SCENARIOS: tuple[tuple[str, str, bool], ...] = (
    ("mir_255d_std_scale",       "255d_std_scale",       False),
    ("flip_255d_std_scale",      "255d_std_scale",       True),
    ("mir_255d_std_half_scale",  "255d_std_half_scale",  False),
    ("flip_255d_std_half_scale", "255d_std_half_scale",  True),
    ("mir_20d_std_scale",        "20d_std_scale",        False),
    ("flip_20d_std_scale",       "20d_std_scale",        True),
    ("mir_255d_max_std_scale",   "255d_max_std_scale",   False),
    ("flip_255d_max_std_scale",  "255d_max_std_scale",   True),
)

# The 2 random-walk scenarios (0.5σ random + opposite trend).
RANDOM_SCENARIOS: tuple[str, ...] = ("rand", "rand_opp")

# The computed mean (average of all 10 per day). NOT a forecast curve itself —
# it drives the sell schedule persisted to trade_decision + the UI mean line.
MEAN_SCENARIO = "mean"

# All 11 scenario labels in display order (10 curves + mean).
ALL_SCENARIOS: tuple[str, ...] = tuple(s[0] for s in SCENARIOS) + RANDOM_SCENARIOS + (MEAN_SCENARIO,)

# The 10 display curves (excludes mean, which is computed separately).
DISPLAY_SCENARIOS: tuple[str, ...] = tuple(s[0] for s in SCENARIOS) + RANDOM_SCENARIOS

# ---------------------------------------------------------------------------
# SELL signal model
# ---------------------------------------------------------------------------
SELL_SIGNAL_BASELINE = 1.0
CONFIDENCE_SCALE = 100.0
MIN_HISTORY_CLOSSES = 5

# ---------------------------------------------------------------------------
# Simulated RSI drift
# ---------------------------------------------------------------------------
RSI_DRIFT_SCALE = 15.0

# ---------------------------------------------------------------------------
# Random walk parameters
# ---------------------------------------------------------------------------
# Both rand and rand_opp use 0.5 * sigma_20d as the step size.
# rand:     close[t] = close[t-1] * (1 + gauss(0, 0.5σ))
# rand_opp: close[t] = close[t-1] * (1 - gauss(0, 0.5σ))  (opposite trend)
MEAN_RANDOM_SCALE = 0.5
MEAN_SEED_BASE = 42

# ---------------------------------------------------------------------------
# Trade decision integration
# ---------------------------------------------------------------------------
# Prefix for forecast sell decisions in trade_decision.signal_reason.
# Used by the UI to add a delimiter between actual and forecast decisions.
FORECAST_SELL_PREFIX = "FORECAST SELL"

# Marker for the forecast sell dot series on the chart (mean scenario).
FORECAST_SELL_MARKER = "FC Sell"

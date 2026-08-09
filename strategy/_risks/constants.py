"""Constants specific to the internal risk analytics package.

These are the only risk-specific knobs: the DB tables the risk pipeline
writes to, the rolling-concentration window length, and the period-share
threshold above which a year/season/month is flagged as a concentration
hotspot. Everything else (sec_type universe, batch size, capital defaults)
lives in ``strategy._common.constants``.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# DB tables — risk-specific (the strategy_seq / trade_decision tables shared
# with the backtest live in strategy._common.constants).
# ---------------------------------------------------------------------------
RISK_SEQ_TABLE = "strategy.strategy_risk_seq"
RISK_PERIOD_TABLE = "strategy.strategy_risk_period"

# ---------------------------------------------------------------------------
# Concentration / hotspot thresholds
# ---------------------------------------------------------------------------
# Rolling window (in days) for the chronological concentration metric:
# max_30d_abs_pnl is the largest 30-day sum of |realized_pnl| across all
# SELLs. 30 trading days ≈ one calendar month — long enough to capture a
# regime episode, short enough that a single clustered period dominates.
WINDOW_DAYS = 30

# A period (year/season/month) is flagged as a concentration hotspot when
# its share of total |pnl| exceeds this threshold. 0.25 = a single period
# accounts for >25% of all absolute P&L → likely regime-driven, not spread.
HOTSPOT_SHARE_THRESHOLD = 0.25

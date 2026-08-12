"""Constants specific to the internal risk analytics package.

These are the only risk-specific knobs: the DB tables the risk pipeline
writes to, the rolling-concentration window length, and the period-share
threshold above which a year/season/month is flagged as a concentration
hotspot. Everything else (sec_type universe, batch size, capital defaults)
lives in ``strategy._common.constants``.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# DB tables — risk-specific (the strategy_seq / trade_decision tables shared
# with the backtest live in strategy._common.constants).
# ---------------------------------------------------------------------------
RISK_SEQ_TABLE = "strategy.strategy_risks"
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


# ---------------------------------------------------------------------------
#  Exponential rolling-window risk score
# ---------------------------------------------------------------------------
# The risk_score is now driven by EXPONENTIALLY-scaled rolling-window losses
# across multiple time horizons, calibrated to three loss-anchor points:
#     1 month  (30 days)  → 25% of total_abs_pnl lost
#     1 season (90 days)  → 50% of total_abs_pnl lost
#     1 year   (365 days) → 75% of total_abs_pnl lost
# These anchors fit a LOGARITHMIC curve L(T) = a·ln(T) + b (T in months)
# better than a pure exponential (the anchors grow sub-exponentially with T).
# Anchor fit: from (1, 0.25) → b = 0.25; from (3, 0.50) → a = 0.25/ln(3).
# The 1-year anchor (0.75) is slightly over-predicted (~0.82) — acceptable.
#
# Per window W, the loss_fraction = |worst W-day rolling loss| / total_abs_pnl
# (LOSSES ONLY — gain windows contribute 0). Each window contributes:
#     exp(k · loss_fraction / threshold_W) - 1
# so that hitting the threshold (ratio = 1) contributes exactly 1.0 (with
# k = ln 2). Contributions sum across windows → realized risk component.
#
# Unrealized losses (worst intra-window MTM dip + the dip's window-end
# residual) use the SAME exponential formula but are weighted at 30% vs
# realized, per spec (less weighted than realized losses).
#
# Rolling window lengths in DAYS. 1 day = worst single-day loss spike;
# 30/90/365 = month/season/year horizons.
RISK_WINDOW_DAYS = (1, 30, 90, 365)

# Log-fit coefficients: L(T) = _LOG_FIT_A · ln(T_months) + _LOG_FIT_B
_LOG_FIT_A = 0.25 / math.log(3)   # ≈ 0.2274
_LOG_FIT_B = 0.25
# Floor for the fitted threshold — the log curve goes negative for very
# short windows (1 day extrapolates to ≈ -0.52), so clamp to a small
# positive fraction. A single day losing ≥ 5% of total capital is the
# 1-day "threshold-hit" point.
MIN_LOSS_THRESHOLD = 0.05

# Exponential scaling constant. k = ln 2 ⇒ ratio=1 → contribution = 1.0,
# ratio=2 → contribution = 3.0, ratio=0.5 → contribution = 0.41.
RISK_EXP_K = math.log(2)

# Cap on loss_fraction / threshold to keep the exponential bounded. At
# ratio=4 the contribution is exp(ln2·4)-1 = 15 — already a strong signal.
# Prevents astronomical scores when an anomalous run has a tiny capital
# base (e.g. near-zero total_buy_cost) producing a huge loss fraction.
MAX_LOSS_RATIO = 4.0

# Unrealized losses (max intra-window dip + window-end residual) weighted
# at 30% of realized, per spec.
UNREALIZED_WEIGHT = 0.30


# ---------------------------------------------------------------------------
#  Consecutive losing-month streak penalty
# ---------------------------------------------------------------------------
# A persistent multi-month losing streak is a regime/ruin signal that the
# rolling-window loss components under-represent: a strategy bleeding a
# little EVERY month for many months has each month's loss below the
# window thresholds, yet the run is dangerous. This component grows
# EXPONENTIALLY with the length of the longest back-to-back losing-month
# run so sustained losses push the grade up fast.
#
# Streak basis: months that have SELL activity, ordered chronologically
# (no-trade months are SKIPPED — they neither extend nor break the run, so
# sparse/quarterly strategies can still build a streak). A "losing month"
# is one whose summed realized_pnl < 0.
#
# Contribution = exp(k · streak / THRESHOLD) - 1, kicking in only when
# streak >= LOSING_STREAK_MIN (a single isolated losing month contributes
# 0 — "continuous losses for MULTIPLE months" is the signal). Reuses
# RISK_EXP_K (k = ln 2) and the MAX_LOSS_RATIO cap. With THRESHOLD = 2:
#   2-mo streak → 1.0, 3-mo → 1.83, 4-mo → 3.0, 6-mo → 7.0, 8-mo → 15.0 (cap)
LOSING_STREAK_MIN = 2           # < this → no streak contribution
LOSING_STREAK_THRESHOLD_MONTHS = 2  # streak length that yields ratio = 1.0

# Risk-grade boundaries on the new absolute risk_score scale (k = ln 2 →
# one window at threshold = 1.0; 4 realized windows + 8 unrealized signals
# at 30% give a typical "everything at threshold" ceiling near 6.4, plus
# the losing-streak component which can dominate for long losing runs).
RISK_GRADE_LOW_BOUND = 1.0       # < 1.0 → LOW  (no window reaches threshold)
RISK_GRADE_MODERATE_BOUND = 3.0  # < 3.0 → MODERATE (one window past threshold)
RISK_GRADE_ELEVATED_BOUND = 6.0  # < 6.0 → ELEVATED; else HIGH


# ---------------------------------------------------------------------------
#  Per-period HIGH-risk override components
# ---------------------------------------------------------------------------
# Three additional risk_score components based on per-period P&L as a
# fraction of total_buy_cost (peak capital deployed). Each is scaled so
# that hitting its threshold contributes RISK_GRADE_ELEVATED_BOUND (6.0)
# to the score — enough to push the grade to HIGH on its own. Below the
# threshold the contribution is exponential (proportional), so a
# near-threshold period still meaningfully raises the score without
# dominating it. These are added to the rolling-window + streak components
# inside _compute_risk_score (NOT a grade override — the grade is still
# derived from the total score via the boundary logic above).
#
#   1. Monthly additional unrealized loss: the month-over-month MTM change
#      in unrealized_pnl (end-of-month minus end-of-previous-month; first
#      month bases off 0). If any month's MTM delta is more negative than
#      -MONTHLY_UNREALIZED_LOSS_HIGH_THRESHOLD * capital → HIGH.
#   2. Monthly gain: realized_pnl + max intra-month unrealized_pnl (raw
#      sum, matches the UI "Total P&L" bar). If any month's gain >
#      MONTHLY_GAIN_HIGH_THRESHOLD * capital → HIGH.
#   3. Seasonal gain: realized_pnl + max intra-season unrealized_pnl. If
#      any season's gain > SEASONAL_GAIN_HIGH_THRESHOLD * capital → HIGH.
MONTHLY_UNREALIZED_LOSS_HIGH_THRESHOLD = 0.10   # month MTM delta < -10% of capital
MONTHLY_GAIN_HIGH_THRESHOLD = 0.50              # month gain > 50% of capital
SEASONAL_GAIN_HIGH_THRESHOLD = 0.80             # season gain > 80% of capital


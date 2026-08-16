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
RISK_FACTORS_TABLE = "strategy.strategy_risk_factors"

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
#  LITTLE risk grade — criteria-based, below LOW
# ---------------------------------------------------------------------------
# LITTLE is a STRUCTURAL grade for the safest strategies: almost no losing
# trades AND stable gains (low gain coefficient of variation) AND net
# profitable. Checked BEFORE score-based grades — if the criteria are met,
# the strategy is LITTLE regardless of score (the criteria guarantee safety).
#
# Criteria (ALL must hold):
#   1. loss_ratio = n_losses / n_sells < LITTLE_LOSS_RATIO  (< 10% losing)
#   2. gain_cv = gain_std / gain_mean < LITTLE_GAIN_CV_MAX  (gains stable)
#   3. total_realized_pnl > 0  (net profitable)
LITTLE_LOSS_RATIO = 0.10       # max fraction of losing trades for LITTLE
LITTLE_GAIN_CV_MAX = 1.5       # max gain coefficient of variation (std/mean)


# ---------------------------------------------------------------------------
#  Per-period statistical distribution risk components
# ---------------------------------------------------------------------------
# Replaces the old fixed-percentage rules (10%/50%/80% of capital) with a
# SELF-CALIBRATING statistical approach: thresholds come from the strategy's
# own period P&L distribution rather than fixed capital fractions.
#
# For each period type (month/season/year) the per-period Total P&L
# (realized + MTM change) is split into gains (>0) and losses (<0). Mean,
# variance, and std are computed for each distribution. Two signals drive
# the contribution:
#
#   A. Distribution asymmetry (BIDIRECTIONAL — "higher penalty to loss var,
#      less to gain var"):
#      Loss-side ratios (loss / gain, > 1 means losses dominate):
#        loss_var_ratio   = loss_var   / gain_var    (or HIGH if gain_var=0)
#        loss_mean_ratio  = loss_mean  / gain_mean   (or HIGH if gain_mean=0)
#        loss_dom_ratio   = max(loss_var_ratio, loss_mean_ratio)
#      Gain-side ratios (gain / loss, > 1 means gains dominate) — mirror:
#        gain_var_ratio   = gain_var   / loss_var    (or HIGH if loss_var=0)
#        gain_mean_ratio  = gain_mean  / loss_mean    (or HIGH if loss_mean=0)
#        gain_dom_ratio   = max(gain_var_ratio, gain_mean_ratio)
#      The DOMINANT direction (larger dom_ratio) drives the signal:
#        loss dominates → +scale · (exp(k·(loss_dom-1)) - 1)        [weight 1.0]
#        gain dominates → -scale · GAIN_DISCOUNT_WEIGHT ·
#                                  (exp(k·(gain_dom-1)) - 1)        [weight 0.3]
#      Loss side carries full weight; gain side carries 30% (matches
#      UNREALIZED_WEIGHT convention) — a 2x loss dominance contributes the
#      same magnitude as a ~6x gain dominance, so gains cannot trivially
#      wipe out real loss risk.
#
#   B. Tail loss: any single period whose loss z-score (|loss| vs the loss
#      distribution mean/std) exceeds LOSS_TAIL_2STD_TRIGGER (2σ) is a
#      "significant loss" event. The exceedance beyond 2σ drives the
#      exponential contribution. At LOSS_TAIL_3STD_HIGH (3σ, one std
#      beyond the 2σ trigger) the contribution is 6.0 — HIGH on its own.
#      The WORST (highest-z) period drives the signal.
#
# Both signals use exp(k · ratio) - 1 (k = ln 2) with the MAX_LOSS_RATIO
# cap, scaled by RISK_GRADE_ELEVATED_BOUND. They are SUMMED across the
# three period types and added to the rolling-window + streak components
# inside _compute_risk_score. NOT a grade override — the grade is still
# derived from the total score via the boundary logic above.
#
# Score floor: _period_override_risk returns max(0, score) so gain-side
# discounts cannot push the period-override component negative (they can
# cancel the tail-loss signal WITHIN the component, but cannot leak
# negativity into realized/unrealized/streak components).

LOSS_TAIL_2STD_TRIGGER = 2.0        # z-score: "significant loss" threshold (2σ)
LOSS_TAIL_3STD_HIGH = 3.0           # z-score: "HIGH on its own" (3σ) — ratio = 1.0
LOSS_DOMINANCE_RATIO_HIGH = 2.0     # loss/gain ratio: "HIGH on its own" (ratio - 1 = 1.0)
MIN_PERIODS_FOR_STATS = 2           # need ≥ 2 data points to compute variance/std

# Weight applied to the gain-side asymmetry discount (loss side = 1.0).
# 0.3 matches UNREALIZED_WEIGHT convention — gain variance is rewarded as
# a risk-reducer but at less than half the loss-side weight, so a single
# outlier gain period cannot trivially mask real loss risk.
GAIN_DISCOUNT_WEIGHT = 0.3


# ---------------------------------------------------------------------------
#  Fault-tolerance amplified stress risk component
# ---------------------------------------------------------------------------
# When the strategy has fault_tolerance > 0, each SELL decision carries
# ft_stressed_conf_up / ft_stressed_conf_down — the signal_confidence the
# algo WOULD have produced if OHLC moved UP or DOWN on that date. The
# "amplified" strategy picks the direction that makes the strategy trade
# MORE aggressively (BUY/SELL-at-loss: higher confidence; SELL-at-gain:
# lower confidence → less gain locked in). The PnL degradation vs baseline
# is this component's driver.
#
# Contribution = RISK_GRADE_ELEVATED_BOUND × (exp(k · ratio) - 1) where
# ratio = |pnl_delta| / (FT_LOSS_THRESHOLD × capital_base). At threshold
# (pnl_delta = 10% of capital) → contributes 6.0 (HIGH on its own).
FT_LOSS_THRESHOLD = 0.10  # 10% of capital_base — at-threshold → HIGH


"""Configuration for analyze.analysis_signals.

Per-day trading signals in the ``analysis_signals`` schema (see
database/sql/analysis/analysis_signals/): one row per
(code, sec_type, signal_type, signal_sub_type, date) recording the day
an extreme-day condition fired, the threshold it crossed, a
human-readable reason, the full detection params (JSON), the forecast
confidence, and the action.

Signals COOPERATE with analyze.analysis_forecasts: a stat_month M gets
signals only when the forecasts module already has rows for M in the
matching config (mov_rsi at pct = 1 / mov_std at k = 2.0 / mov_gap at
pct = 1), and the detection reuses the exact same machinery — the
trailing 5-year window (M - 5y, M], linear-interpolated window
percentile thresholds (RSI / gap) or ma ± k·std band levels (std),
cooldown suppression and the full-5y-history gate (first data strictly
before the window start).
A signal date is emitted only within its own snapshot month M, so each
date is owned by exactly one snapshot (clean date-level PK).

Adaptive forecast-confirmation gate (QRp_P90): a detected day is
RECORDED only when the matching analysis_forecasts bucket (same
code/sec_type/stat_month/window/side/pct|k/cooldown config) qualifies —
for AT LEAST ONE forecast_results period (next/5d/20d/60d) that
period's reverse_prob is at or above the P90 quantile of its
calibration population (same sec_type + signal family + side + period,
ALL buckets of all PRIOR stat_months — an M-1 calibration gate: no
look-ahead). The quantile thresholds are self-adaptive per security
type and market regime; while a (month, side, period) population has
fewer than GATE_MIN_POP bucket-periods the calibrated quantile is
meaningless and that period falls back to the legacy reverse_prob > 0
rule. The row's confidence = the bucket's cross-period
MAX(reverse_prob) (reverse_prob = P(n-day forward change is a REVERSAL
> 1% against the bucket side)).
"""
from __future__ import annotations

from analyze.analysis_forecasts.config import (
    COOLDOWN_DAYS as _FORECAST_COOLDOWN_DAYS,
)

# Cooldown suppression after an accepted signal day (trading days) —
# mirrors the forecast buckets' cooldown (PK member cooldown_days there;
# recorded in the signals' params JSON here). The forecasts config keeps
# a tuple to compare variants; signals emit the current (first) value.
COOLDOWN_DAYS = _FORECAST_COOLDOWN_DAYS[0]

# ---- Adaptive confirmation gate (QRp_P90) -----------------------------------

# Population-quantile rank of the adaptive confirmation gate: a bucket's
# cross-period confidence (MAX reverse_prob over next/5d/20d/60d) must be
# >= this quantile of its population (same sec_type/family/side, all
# buckets of all PRIOR stat_months) to confirm a signal day. Selected by
# the adaptive-threshold study as the only rule valid across index/etf/
# stock (etf/stock lack base rates, ruling out lift-based rules) and
# out-of-sample stable (split-half OOS: mean rp 0.8-1.0 at ~10% pass).
GATE_Q = 0.90

# Minimum population size for the calibrated quantile to be trusted; a
# (target month, side) population below this falls back to the legacy
# "confidence > 0" rule (cold-start months at the head of the history).
GATE_MIN_POP = 30

# ---- Target table -----------------------------------------------------------

TABLE_SIGNALS = "analysis_signals.signals"

ANALYSIS_NAME = "analysis_signals"
DETAIL_NAME = "signals"

DESCRIPTION = (
    "Per-day buy/sell signals (ETF + Index + Stock) mirroring the "
    "analysis_forecasts extreme-day detection at signal granularity: "
    "mov_rsi — rsi_{W}days in the TOP 1% (action=sell) or BOTTOM 1% "
    "(action=buy) of the trailing 5-year window ending at the snapshot "
    "month, W in 6/10/14/20/60; mov_std — price beyond the 2σ Bollinger "
    "band ma_{W} ± 2.0·std_{W}days (upper → sell, lower → buy), W in "
    "5/20/60; mov_gap — gap_{W}days (the W-day fractional price return "
    "from analysis.mov_ave_rsi) in the TOP 1% (sharp W-day rally → "
    "sell) or BOTTOM 1% (sharp W-day selloff → buy), W in 2/3. Each row "
    "carries the crossed threshold (signal_threshold), forecast "
    "confidence (MAX reverse_prob across all periods), a "
    "human-readable reason and the full detection params as JSON. "
    "Months are gated to the stat_months already present in "
    "analysis_forecasts (mov_rsi pct=1 / mov_std k=2.0 / mov_gap "
    "pct=1) — the forecasts start month sets the first signal date; "
    "detection uses the same window, thresholds, cooldown (5 trading "
    "days) and full-window history gate as the forecast buckets, and "
    "each date is emitted only within its own snapshot month. A day is "
    "recorded only when its bucket clears the adaptive QRp_P90 gate: "
    "for at least one forecast period (next/5d/20d/60d) that period's "
    "reverse_prob is at or above the 90th percentile of its calibration "
    "population (same sec_type/family/side/period, all buckets of all "
    "prior stat_months — no look-ahead; legacy reverse_prob > 0 "
    f"fallback below {GATE_MIN_POP} population bucket-periods). Each "
    "row also carries is_active "
    "(TRUE only on the sec_type's latest signal date, refreshed after "
    "every run) so consumers can pick up the current threshold set. "
    "Incremental at month granularity; --force deletes the sec_type's "
    "rows and recomputes every gated month."
)

# ---- Detection configs (subset of analysis_forecasts configs) ---------------

# mov_rsi: RSI extreme-percentile signals — pct fixed to the top/bottom 1%.
RSI_PCT = 1

# mov_std: Bollinger-breach signals — σ multiple fixed to 2.0.
STD_K = 2.0

# mov_gap: gap extreme-percentile signals — pct fixed to the top/bottom 1%
# (same width as the RSI family).
GAP_PCT = 1

# Signal sub_type naming: f"rsi{W}" for mov_rsi, f"std{W}" for mov_std,
# f"gap{W}" for mov_gap.
def sub_type_rsi(w: int) -> str:
    return f"rsi{w}"


def sub_type_std(w: int) -> str:
    return f"std{w}"


def sub_type_gap(w: int) -> str:
    return f"gap{w}"

# side → action mapping (shared by all signal types: the extreme side
# is a SELL-side extreme for top/upper, a BUY-side extreme for
# bottom/lower).
SIDE_ACTION = {"top": "sell", "bottom": "buy", "upper": "sell", "lower": "buy"}

# ---- Write columns ----------------------------------------------------------

# analysis_signals.signals columns in write order (PK first).
SIGNAL_COLUMNS = [
    "code", "sec_type", "signal_type", "signal_sub_type", "date",
    "action", "signal_threshold", "confidence", "reason", "params",
]

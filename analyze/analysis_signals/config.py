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

Adaptive forecast-confirmation gate (QRp_P90 + per-security layers): a
detected day is RECORDED only when the matching analysis_forecasts
bucket (same code/sec_type/stat_month/window/side/pct|k/cooldown
config) qualifies — for AT LEAST ONE forecast_results period
(next/5d/20d/60d) that period's reverse_prob is at or above its
calibration threshold (same sec_type + signal family + side + period,
ALL buckets of all PRIOR stat_months — an M-1 calibration gate: no
look-ahead) AND the code's prior mean rp for that (side, period) is
positive where known — the mean has to see reverse too, not just the
single bucket-period (the same M-1 per-code mean the tier / baseline
columns read; an unknown mean — no prior bucket-periods — does not
block). Two calibrated threshold modes (per-security gate study,
2026-09):
  - mov_rsi — SEC QRp_P90: rp >= the population P90 (the mov_rsi rp
    distribution is saturated at 1.0, where the per-code rank gate
    degenerates; the study's PROVEN_DIR dominates it instead).
  - mov_std / mov_gap — HYB QRp_P90: threshold = w·code_P90 +
    (1-w)·population_P90 with shrinkage weight w = code_n/(code_n +
    K_SHRINK); below HYBRID_MIN_POP prior bucket-periods for the code
    the weight is 0 (pure population gate). Uniformly tighter OOS
    quality than the population-only gate at modestly lower volume.
While a (month, side, period) population has fewer than GATE_MIN_POP
bucket-periods the calibrated quantile is meaningless and that period
falls back to the legacy reverse_prob > 0 rule.

Per-security confidence calibration (validated by the study: a code's
prior mean reverse_prob predicts its future mean rp with correlation
0.80-0.97 across index/etf/stock):
  - tier — 'proven' when at least one qualifying period's code has a
    prior mean rp >= PROVEN_RP; 'proven_dir' when the code's prior
    mean directional move >= PROVEN_DIR_AVE; else 'standard'. Code
    stats need >= PROVEN_MIN_POP prior bucket-periods to count.
  - code_baseline — the code's prior mean rp for the confidence's
    (argmax) period.
  - code_rank — coarse within-code percentile of the confidence among
    the code's own prior buckets (floor estimate from the code's prior
    P25/P50/P75/P90/P95; NULL below RANK_MIN_POP prior buckets).
The row's confidence = the bucket's cross-period MAX(reverse_prob)
(reverse_prob = P(n-day forward change is a REVERSAL beyond the
bucket's adaptive reverse_threshold — k·σ of the code's window forward
changes per horizon, see analysis_forecasts config — against the
bucket side)).
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

# ---- Per-security layers (gate study, 2026-09) -------------------------------

# Shrinkage strength of the HYB QRp_P90 gate (mov_std / mov_gap): the
# calibrated threshold is w·(code's own prior P90) + (1-w)·(population
# P90) with w = code_n / (code_n + K_SHRINK) — an empirical-Bayes blend
# that trusts a code's own history in proportion to how much of it
# exists. OOS: uniformly tighter mean rp / dir_ave than the
# population-only gate at ~10-16% lower volume.
K_SHRINK = 100

# Minimum prior bucket-periods for a code before its own P90 enters the
# HYB threshold at all (below this the weight is 0 → pure population
# gate).
HYBRID_MIN_POP = 30

# 'proven' tier bar: at least one qualifying period's code has a prior
# mean reverse_prob >= this (mov_rsi precision tier: +17-21% dir_ave at
# ~40% of gate volume).
PROVEN_RP = 0.70

# 'proven_dir' tier bar: the code's prior mean DIRECTIONAL move (mean
# ave_change in the signal direction, fractional) >= this. The default
# live tier for the rp-saturated mov_rsi family (+11-14% dir_ave at
# 64-78% of gate volume).
PROVEN_DIR_AVE = 0.01

# Minimum prior bucket-periods for a code before the proven tiers apply
# (below this the tier is 'standard').
PROVEN_MIN_POP = 100

# Minimum prior bucket-periods for the within-code confidence rank to be
# emitted (below this code_rank is NULL).
RANK_MIN_POP = 30

# tier points (SQL) → tier label.
TIER_NAMES = {0: "standard", 1: "proven_dir", 2: "proven"}

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
    "recorded only when its bucket clears the adaptive confirmation "
    "gate (calibrated rolling M-1, no look-ahead): for at least one "
    "forecast period (next/5d/20d/60d) that period's reverse_prob must "
    "clear its calibration threshold — the population P90 "
    f"(QRp_P{int(100 * GATE_Q)}) for mov_rsi, and for mov_std/mov_gap "
    "the per-security HYB blend w·code_P90 + (1-w)·population_P90 "
    f"(w = code_n/(code_n + {K_SHRINK}), pure population below "
    f"{HYBRID_MIN_POP} prior bucket-periods) — and the code's prior "
    "mean reverse_prob for that (side, period) must also be positive "
    "where known (the mean sees reverse too, not just the single "
    "bucket-period); legacy reverse_prob > 0 "
    f"fallback below {GATE_MIN_POP} population bucket-periods. Each "
    "row also carries the per-security calibration (validated by the "
    "gate study: prior-vs-future mean rp correlation 0.80-0.97): tier "
    "('proven' = a qualifying period's code has prior mean rp >= "
    f"{PROVEN_RP}; 'proven_dir' = prior mean directional move >= "
    f"{PROVEN_DIR_AVE}; 'standard' otherwise; code stats need >= "
    f"{PROVEN_MIN_POP} prior bucket-periods), code_baseline (the "
    "code's prior mean rp for the confidence's argmax period) and "
    "code_rank (within-code percentile floor of the confidence). "
    "opp_pair — by INDUSTRY pair (buckets "
    "analysis_forecasts.opp_pair_state, gated to its stat_months): "
    "when ONE side industry's benchmark-offset MA trend crosses below "
    "the 0 bar (its W-day relative MA return < the benchmark's, W in "
    "20/60), a BUY row is emitted on the OTHER side industry (the "
    "forecast target; sec_type 'index', no cooldown, constant 0 "
    "threshold) — confidence = the pair bucket's cross-period "
    "MAX(reverse_prob) = the pair forecast's CONFIRMATION probability "
    "(B rises when A drops), gate calibration keyed by the target. "
    "Each row also carries is_active "
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


# ---- px_vol family (recent price-change × trading-amount states) -----------

# signal_type for the px_vol family (buckets:
# analysis_forecasts.px_vol_state, see the forecasts config). The
# threshold set is the forecasts px_vol config verbatim (sigma_window /
# lb_window / k bars / z bars / sigma_floor) — recorded in params JSON.
SIGNAL_TYPE_PX_VOL = "px_vol"

# sub_type naming: f"{px_speed}_{vol_state}" (e.g. "sharp_up_heavy").
def sub_type_px_vol(px_speed: str, vol_state: str) -> str:
    return f"{px_speed}_{vol_state}"


# px_vol side → action mapping. The flat speed is deliberately NOT
# emitted (no directional claim — its buckets carry NULL reverse_prob
# and the studies show no signal value); only the 10 sided cells
# signal. Note the DIRECTION inversion vs mov_*: for px_vol a TOP
# (sharp/slow UP) day is a momentum entry per the 2026-09 studies
# (急涨×放量 fwd5 lift +2.2σ, OOS-stable) — but as an EXTREME-day
# family it keeps the mov_* convention of action = the reversal
# direction the confidence measures (top → sell = fade the extreme,
# bottom → buy). The reason/params carry the cell so consumers can
# apply either reading.
PX_VOL_SIDE_ACTION = {"top": "sell", "bottom": "buy"}

# The HYB QRp_P90 gate (per-code shrinkage blend, like mov_std /
# mov_gap) — px_vol rp is unsaturated (state buckets, no cooldown).
PX_VOL_GATE_HYBRID = True

# px_vol signal_sub_type cell list (10 sided cells; flat excluded).
def px_vol_cells() -> list[tuple[str, str]]:
    from analyze.analysis_forecasts.config import (
        PX_VOL_SPEEDS,
        PX_VOL_VOL_STATES,
    )
    return [
        (s, v) for s in PX_VOL_SPEEDS if s != "flat" for v in PX_VOL_VOL_STATES
    ]


# ---- margin_ratio family (margin-buy intensity z states) --------------------

# signal_type for the margin_ratio family (buckets:
# analysis_forecasts.margin_ratio_state — the 融资买入额/成交额 ratio z
# states, see the forecasts config). The threshold set is the forecasts
# margin_ratio config verbatim (z_window / z_min_periods / z bars) —
# recorded in params JSON. ETF + Stock only (index has no margin data).
SIGNAL_TYPE_MARGIN_RATIO = "margin_ratio"

# sub_type naming: f"ratio_{state}" (e.g. "ratio_vhigh").
def sub_type_margin_ratio(state: str) -> str:
    return f"ratio_{state}"


# margin_ratio side → action mapping. Only the 4 z-crossing states
# signal: high/vhigh (side top — the study's crowding/bearish states)
# → sell, vlow/low (side bottom — mild-bullish) → buy. no_buy is
# deliberately NOT emitted (an absence state with no threshold — its
# mild positive lift is not actionable per-day) and mid is not emitted
# (the neutral bulk, like px_vol's flat).
MARGIN_RATIO_SIGNAL_STATES = ("vlow", "low", "high", "vhigh")

MARGIN_RATIO_SIDE_ACTION = {"top": "sell", "bottom": "buy"}

# The HYB QRp_P90 gate (per-code shrinkage blend, like px_vol) —
# margin_ratio rp is unsaturated (state buckets, no cooldown).
MARGIN_RATIO_GATE_HYBRID = True

# ---- opp_pair family (industry opposite-pair trend forecasts) ---------------

# signal_type for the opp_pair family (buckets:
# analysis_forecasts.opp_pair_state — by industry pair, when ONE side's
# benchmark-offset MA trend is dropping, the forecast result is the
# OTHER side industry's future trend; see the forecasts config). The
# signal is emitted on the TARGET industry (pair_industry_id): A's
# trend dropping → B forecast up.
SIGNAL_TYPE_OPP_PAIR = "opp_pair"

# sub_type naming: f"pair{W}" (e.g. "pair20" / "pair60").
def sub_type_opp_pair(w: int) -> str:
    return f"pair{w}"


# The signal fires on the bucket trigger crossing the 0 bar:
# rel_A(t) = MA_A[t]/MA_A[t-W] - MA_M[t]/MA_M[t-W] < 0 (industry A's
# W-day benchmark-offset MA-trend return below the benchmark's — the
# composites offset math; "an industry whose trend grows while the
# benchmark grows more is DROPPING"). Recorded as signal_threshold.
OPP_PAIR_TREND_BAR = 0.0

# Constant action: side 'bottom' (A dropping → B expected up) → buy on
# the TARGET industry B; confidence = the bucket's cross-period
# MAX(reverse_prob) = P(B's forward offset change > B's adaptive bar) —
# the pair forecast's CONFIRMATION probability.
OPP_PAIR_GATE_HYBRID = True

# side → action mapping (shared by all signal types: the extreme side
# is a SELL-side extreme for top/upper, a BUY-side extreme for
# bottom/lower).
SIDE_ACTION = {"top": "sell", "bottom": "buy", "upper": "sell", "lower": "buy"}

# ---- Write columns ----------------------------------------------------------

# analysis_signals.signals columns in write order (PK first).
SIGNAL_COLUMNS = [
    "code", "sec_type", "signal_type", "signal_sub_type", "date",
    "action", "signal_threshold", "confidence", "tier", "code_baseline",
    "code_rank", "reason", "params",
]

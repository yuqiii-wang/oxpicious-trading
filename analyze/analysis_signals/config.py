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

Forecast-confirmation gate (absolute reversal rule): a detected day is
RECORDED only when the matching analysis_forecasts bucket (same
code/sec_type/stat_month/window/side/pct|k/cooldown config) qualifies —
for AT LEAST ONE forecast_results period (next/5d/20d/60d) that
period's reverse_prob exceeds GATE_RP_MIN (reverse P > 1% — a material
reversal probability, not a bare > 0 tail) AND that period's mean
forward change is a REVERSAL (dir_ave > 0 — the bucket's average
outcome reverses, so the signal holds, not just a fat reversal tail).
Both conjuncts read the bucket's own historical outcomes from
analysis_forecasts.forecast_results.

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

# ---- Forecast-confirmation gate (absolute reversal rule) --------------------

# A detected day is recorded only when its matching forecast bucket
# qualifies in at least one forecast_results period (next/5d/20d/60d):
# that period's reverse_prob must exceed this bar (reverse P > 1% — a
# material reversal probability, not a bare > 0 tail) AND its mean
# forward change must be a REVERSAL (dir_ave > 0 — the bucket's average
# outcome reverses, so the signal holds, not just a fat reversal tail).
GATE_RP_MIN = 0.01

# ---- Per-security layers (gate study, 2026-09) -------------------------------

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
    "recorded only when its bucket clears the forecast-confirmation "
    "gate (absolute reversal rule): for at least one forecast period "
    "(next/5d/20d/60d) that period's reverse_prob must exceed "
    + repr(GATE_RP_MIN) + " (reverse P > 1% — a material reversal "
    "probability) AND that period's mean forward change must be a "
    "reversal (dir_ave > 0 — the bucket's average outcome reverses, "
    "so the signal holds, not just a fat reversal tail). Each "
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

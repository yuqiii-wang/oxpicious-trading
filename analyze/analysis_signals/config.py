"""Configuration for analyze.analysis_signals.

Per-day trading signals in the ``analysis_signals`` schema (see
database/sql/analysis/analysis_signals/): one row per
(code, sec_type, signal_type, signal_sub_type, date) recording the day
an extreme-day condition fired, the threshold it crossed, a
human-readable reason, the full detection params (JSON), the driving
-factor confidence, and the action.

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

Forecast-confirmation gate (forecast-result rule): a detected day is
RECORDED only when the matching analysis_forecasts bucket (same
code/sec_type/stat_month/window/side/pct|k/cooldown config) qualifies —
for AT LEAST ONE forecast_results period (next/5d/20d/60d) ALL of:
that period's reverse_prob exceeds GATE_RP_MIN (reverse P > 1% — a
material reversal probability, not a bare > 0 tail), that period's mean
forward change is a REVERSAL (dir_ave > 0 — the bucket's average
outcome reverses, so the signal holds, not just a fat reversal tail),
that period's reverse_prob BEATS THE BASE RATE (rp >
base_rates.base_down_prob / base_up_prob for top/bottom sides — the
unconditional same-window, same-threshold reversal probability; a
bucket at the base rate has no edge, however large the absolute rp;
the conjunct falls back to TRUE when the code has no base_rates row),
that period's mean forward change BEATS THE BASE DRIFT (dir_ave >
base_ave_change, sign-aligned — the magnitude twin of the probability
lift; a bucket whose average reversal is smaller than the window's own
drift has no mean edge; falls back to TRUE without a base_rates row),
the period's stats are backed by at least GATE_MIN_OCCURRENCE observed
forward outcomes, and the mean reversal is statistically real —
dir_ave · sqrt(occurrence_count) >= GATE_T_STAT_MIN · std_change.
All conjuncts read the bucket's own historical outcomes from
analysis_forecasts (forecast_results + base_rates). OOS (future-half
buckets, temp_scripts/study_confidence_oos.py): buckets dropped by the
magnitude-lift conjunct realize ~half the mean forward reversal of the
passers (mov_gap index 0.0076 vs 0.0380, px_vol stock 0.0360 vs 0.0779).

Driving-factor CONFIDENCE (replaces the reverse_prob max — rp is
saturated at long horizons: among gate-passers the 20d/60d rp sits at
0.6-1.0 quantized by 5-7 occurrences, so MAX(rp) mostly ranked buckets
by horizon, and long horizons mechanically realize larger |dir_ave|).
The confidence is a weighted composite of the four factors that
actually drive a bucket's reversal edge, all computed in the SIGNAL'S
direction (dir_ave sign-flipped for top/upper — a buy signal's
confidence speaks about the upward reversal, a sell's about the
downward) and all horizon-free, so values compare across periods,
families and sec_types:

  evidence    t = dir_ave·√occ / std_change   — is the reversal
              statistically real       f = t / (t + CONF_T_ANCHOR)
  efficiency  sharpe = dir_ave / std_change — risk-adjusted size of the
              per-observation reversal f = 1 - exp(-s/CONF_SHARPE_ANCHOR)
  consistency lift_prob = rp - base_prob   — how much more often the
              bucket reverses than any day f = 1 - exp(-l/CONF_LIFT_PROB_ANCHOR)
  calibration the code's PRIOR mean base composite (same code/side/
              period, months < M, windows pooled — the gate study's
              prior-vs-future correlation 0.80-0.97, transplanted to
              the composite) — neutral CONF_PRIOR_NEUTRAL below
              CONF_PRIOR_MIN_POP prior bucket-periods.

confidence = W_EVIDENCE·f_evidence + W_EFFICIENCY·f_efficiency
           + W_CONSISTENCY·f_consistency + W_PRIOR·f_calibration
at the qualifying period with the best composite (the confidence's
argmax period is recorded in params JSON as conf_period — the horizon
the confidence speaks about; the OOS study shows the composite ranks
future realized reversals better than rp WITHIN every period, e.g.
mov_gap stock next 0.62→0.79 / 60d 0.49→0.70 Spearman).

Per-security calibration columns (kept from the 2026-09 gate study —
prior-vs-future mean rp correlation 0.80-0.97; row metadata only,
they do not gate):
  - tier — MAX over QUALIFYING periods: 'proven' (2) when the code's
    prior mean composite >= CONF_PROVEN (top ~10% of codes by prior
    bucket quality), 'proven_dir' (1) when the code's prior mean
    DIRECTIONAL move >= PROVEN_DIR_AVE else 'standard' (0). Code stats
    need >= PROVEN_MIN_POP prior bucket-periods.
  - code_baseline — the code's prior mean composite for the
    confidence's argmax period.
  - code_rank — coarse within-code percentile FLOOR of the confidence
    (from the code's own prior P25/P50/P75/P90/P95 of the composite),
    NULL below RANK_MIN_POP prior bucket-periods.

The full factor breakdown (t, sharpe, lift_prob, prior + the three
factor scores) is recorded on every row in the params JSON under
confidence_factors.
"""
from __future__ import annotations

# Cooldown suppression after an accepted signal day (trading days) —
# recorded in the signals' params JSON. This is the DETECTION-side
# de-dup and stays even though the forecast buckets migrated to
# streak-merge (2026-09): a live trigger cannot know an ongoing
# streak's mid day ex-post, so day-level fixed-skip suppression remains
# the honest live convention (the forecast-side merged signals are
# read through the confirmation gate, not re-detected here).
COOLDOWN_DAYS = 5

# high_low_streaks streak RESOLVE LAG (completed months) — defined here
# (before DESCRIPTION, which references it): a target stat_month M gets
# its high_low_streaks signals only when the forecasts table has months
# at least this far beyond M (see the family block below for the
# rationale).
HL_STREAKS_RESOLVE_LAG_MONTHS = 2

# ---- Forecast-confirmation gate (forecast-result rule) ----------------------

# A detected day is recorded only when its matching forecast bucket
# qualifies in at least one forecast_results period (next/5d/20d/60d):
# that period's reverse_prob must exceed this bar (reverse P > 1% — a
# material reversal probability, not a bare > 0 tail) AND its mean
# forward change must be a REVERSAL (dir_ave > 0 — the bucket's average
# outcome reverses, so the signal holds, not just a fat reversal tail)
# AND its reverse_prob must beat the unconditional base rate
# (base_rates.base_down_prob / base_up_prob per side — lift; skipped
# when the code has no base_rates row) AND its mean forward change must
# beat the base drift (the magnitude lift — see the module docstring)
# AND the period's stats must be backed by enough observed outcomes and
# a statistically real mean (see GATE_MIN_OCCURRENCE / GATE_T_STAT_MIN).
GATE_RP_MIN = 0.01

# Minimum observed forward outcomes (forecast_results.occurrence_count)
# for a bucket-period's stats to qualify. A bucket with fewer observed
# n-day forward changes has a reverse_prob quantized to coarse steps
# (1 observed reversal out of 3 "beats" any base rate) — detection
# noise, the std5 failure mode generalized (std5's median bucket has 1
# observation; px_vol median 3-5, margin_ratio vlow median 2).
GATE_MIN_OCCURRENCE = 5

# Student-t style bar for the mean reversal: the bucket's mean forward
# change must clear t · std_change / sqrt(occurrence_count) — i.e.
# dir_ave · sqrt(occurrence_count) >= t · std_change — statistically
# distinguishable from 0 given the bucket's OWN dispersion and sample
# size, not just positive. Rolling-half out-of-sample validation
# (temp_scripts/study_signal_strength.py): buckets passing this bar
# realize 2-3x the future mean dir_ave of buckets passing only the
# absolute rules (mov_rsi etf 2.84% vs 0.97%, mov_std index 0.95% vs
# 0.36%, px_vol etf +1.07% vs -0.05%); the next-day horizon's fat
# no-edge rp tail no longer qualifies. The overlapping forward windows
# inflate effective n slightly, so 2.0 is nominal-conservative.
GATE_T_STAT_MIN = 2.0

# ---- Driving-factor confidence (composite) -----------------------------------
#
# confidence = W_EVIDENCE·f(t) + W_EFFICIENCY·f(sharpe)
#            + W_CONSISTENCY·f(lift_prob) + W_CALIBRATION·f(prior)
# every factor in [0, 1] (floored at 0), every anchor interpretable.

# Evidence anchor: f = t / (t + CONF_T_ANCHOR) — 0.5 at a t-stat of 3,
# 0.40 at the gate bar (t = 2). (t = dir_ave·√occ / std_change.)
CONF_T_ANCHOR = 3.0

# Efficiency anchor: f = 1 - exp(-sharpe / CONF_SHARPE_ANCHOR) — the
# ~median gate-passer sharpe (1.0-1.2 across families) maps to ~0.57.
CONF_SHARPE_ANCHOR = 1.2

# Consistency anchor: f = 1 - exp(-lift_prob / CONF_LIFT_PROB_ANCHOR) —
# a 50pp hit-rate lift over the base rate maps to 0.63.
CONF_LIFT_PROB_ANCHOR = 0.5

# Factor weights (sum to 1): evidence and efficiency carry the bulk
# (they are the strongest within-period OOS predictors), consistency a
# quarter, the per-code prior the rest (mixed pooled lift, kept for the
# validated per-code self-calibration).
CONF_W_EVIDENCE = 0.30
CONF_W_EFFICIENCY = 0.30
CONF_W_CONSISTENCY = 0.25
CONF_W_CALIBRATION = 0.15

# Neutral calibration prior for codes with no usable history (the
# population's typical prior mean composite is ~0.15-0.25 — NOT 0.5,
# which would make history-less codes outscore every proven code).
CONF_PRIOR_NEUTRAL = 0.20

# Minimum prior bucket-periods (same code/side/period, months < M,
# windows pooled) before the code's own prior mean composite replaces
# the neutral prior.
CONF_PRIOR_MIN_POP = 10

# ---- Per-security layers (gate study, 2026-09) -------------------------------

# 'proven' tier bar: the code's prior mean composite >= this — ~P90 of
# the per-(code, side, period) prior-mean distribution (p90 spans
# 0.21-0.49 across families/periods), i.e. the top ~10% of codes by
# prior bucket quality.
CONF_PROVEN = 0.40

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
    "20/60 (W=5 dropped — its ~1-day buckets carry no base-rate lift: "
    "pure noise); mov_gap — gap_{W}days (the W-day fractional price return "
    "from analysis.mov_ave_rsi) in the TOP 1% (sharp W-day rally → "
    "sell) or BOTTOM 1% (sharp W-day selloff → buy), W in 2/3; "
    "mov_pairs — the CROSS days of the EXISTING relative-MA spreads "
    "ma5_vs_ma{W} (side=top a CROSS UP / golden cross → action=sell, "
    "bottom a CROSS DOWN / death cross → action=buy), W in 60/120/255, "
    "cooldown 5; mov_pairs_ema — the EMA sibling on ema6_vs_ema{W} "
    "(same sides / cooldown); "
    "high_low_streaks — the MEAN-MID anchor day (the "
    "((day_count-1)//2 + 1)-th trading day) of every band-break "
    "excursion streak of analysis.mov_ave_high_low_pct_streaks, side "
    "top an ABOVE-band excursion (close on end_date above the end "
    "month's high_val band → sell), bottom BELOW-band (→ buy; the "
    "mean-reversion reading the forecasts study measured from the mid "
    "anchor); the anchor is EX-POST, so a month is computed only once "
    "every streak anchored in it is final (a "
    f"{HL_STREAKS_RESOLVE_LAG_MONTHS}-completed-month resolve lag + a "
    "closed-streak guard). Each row "
    "carries the crossed threshold (signal_threshold), the driving"
    "-factor confidence, a human-readable reason and the full detection "
    "params as JSON. "
    "Months are gated to the stat_months already present in "
    "analysis_forecasts (mov_rsi pct=1 / mov_std k=2.0 / mov_gap "
    "pct=1) — the forecasts start month sets the first signal date; "
    "detection uses the same window, thresholds, cooldown (5 trading "
    "days) and full-window history gate as the forecast buckets, and "
    "each date is emitted only within its own snapshot month. A day is "
    "recorded only when its bucket clears the forecast-confirmation "
    "gate (forecast-result rule): for at least one forecast period "
    "(next/5d/20d/60d) that period's reverse_prob must exceed "
    + repr(GATE_RP_MIN) + " (reverse P > 1% — a material reversal "
    "probability) AND that period's mean forward change must be a "
    "reversal (dir_ave > 0 — the bucket's average outcome reverses, "
    "so the signal holds, not just a fat reversal tail) AND that "
    "period's reverse_prob must beat the unconditional base rate "
    "(analysis_forecasts.base_rates, per side — lift; the conjunct "
    "falls back to TRUE when the code has no base_rates row) AND the "
    "period's mean forward change must beat the base drift (the "
    "magnitude lift — OOS: dropped buckets realize ~half the mean "
    "reversal) AND the period must be backed by at least "
    + repr(GATE_MIN_OCCURRENCE) + " observed forward outcomes AND a "
    "statistically real mean reversal (dir_ave · sqrt(occurrence_count)"
    " >= " + repr(GATE_T_STAT_MIN) + " · std_change — rolling-half OOS: "
    "bar-passers realize 2-3x the future mean reversal of absolute-rule"
    "-only passers). Confidence is a weighted composite of the "
    "underlying driving factors, computed in the signal's direction "
    "(buy → the upward reversal, sell → the downward): evidence "
    "f = t/(t+3) with t = dir_ave·√occ/std_change, efficiency "
    "f = 1-exp(-sharpe/1.2) with sharpe = dir_ave/std_change, "
    "consistency f = 1-exp(-lift_prob/0.5) with lift_prob = reverse_prob"
    " - base_prob, and per-code calibration (the code's prior mean "
    "composite, neutral 0.20 below 10 prior bucket-periods) — weights "
    "0.30/0.30/0.25/0.15, taken at the qualifying period with the best "
    "composite (params JSON carries conf_period + the full "
    "confidence_factors breakdown). reverse_prob is saturated at long "
    "horizons (0.6-1.0 for 20d/60d) so it no longer IS the confidence; "
    "all composite factors are horizon-free and comparable across "
    "periods, families and sec_types. Each row also carries the "
    "per-security calibration (validated by the gate study: "
    "prior-vs-future mean rp correlation 0.80-0.97): tier ('proven' = "
    f"the code's prior mean composite >= {CONF_PROVEN}; 'proven_dir' = "
    f"prior mean directional move >= {PROVEN_DIR_AVE}; 'standard' "
    f"otherwise; code stats need >= {PROVEN_MIN_POP} prior bucket"
    "-periods), code_baseline (the code's prior mean composite for the "
    "confidence's argmax period) and code_rank (within-code percentile "
    "floor of the confidence). "
    "The former opp_pair family (industry opposite-pair trend "
    "forecasts) is removed from signal emission: its buckets average "
    "~600 trigger days yet a pooled mean forward offset change of ~0 "
    "and OOS-validated zero mean content — forecasts keep computing "
    "analysis_forecasts.opp_pair_state, only the signals are gone. "
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

# mov_std SIGNAL windows — a subset of the forecasts' MA_WINDOWS. W=5
# is NOT emitted: its 2σ breaches average only ~1.3 bucket days per
# (code, month) (vs ~16 at W=20), the median bucket never reverses
# (median reverse_prob = 0 at every horizon) and the bucket dir_ave
# shows no base-rate lift (≈0 or negative vs the unconditional
# reference) — the sub_type is pure detection noise. Forecasts keep
# computing the W=5 buckets; only the signal family drops them.
STD_SIGNAL_MA_WINDOWS = (20, 60)

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

# ---- mov_pairs / mov_pairs_ema families (MA / EMA-pair crosses) -------------

# signal_type for the MA-pair cross family (buckets:
# analysis_forecasts.mov_pairs — the ma5_vs_ma{W} golden / death cross
# days, W ∈ {60, 120, 255}; see the forecasts config) and its EMA
# sibling (analysis_forecasts.mov_pairs_ema — ema6_vs_ema{W}). Signal
# days = the CROSS days themselves (the sign flip of the stored spread
# — the forecast engine's own matrix machinery reused verbatim), with
# the SAME cooldown_days suppression as the event buckets (PK member
# there; params JSON here).
SIGNAL_TYPE_MOV_PAIRS = "mov_pairs"
SIGNAL_TYPE_MOV_PAIRS_EMA = "mov_pairs_ema"

# sub_type naming: f"pair{W}" for mov_pairs, f"emapair{W}" for
# mov_pairs_ema (e.g. "pair60" / "emapair255").
def sub_type_pair(w: int) -> str:
    return f"pair{w}"


def sub_type_ema_pair(w: int) -> str:
    return f"emapair{w}"


# The cross is a sign flip of the stored relative spread — the level
# crossed is the ZERO line (signal_threshold = 0; the live mirror's
# signal value is the day's spread itself, so signal_excess reads as
# the spread's distance from 0 on the cross day).
PAIRS_CROSS_THRESHOLD = 0.0

# ---- high_low_streaks family (MA-Spread High/Low streak mid anchors) --------

# signal_type for the high_low_streaks family (buckets:
# analysis_forecasts.high_low_streaks — every band-break excursion
# streak audited at its MEAN-MID anchor day, see the forecasts config).
# Signal days = the anchor days themselves (detection reuses the
# forecasts engine's anchor machinery verbatim), gated by the bucket's
# forecast-result rule like every family.
SIGNAL_TYPE_HL_STREAKS = "high_low_streaks"

# sub_type naming: f"p{band_period}_{pct_type}" (e.g. "p255_1" = the
# 255-row band at pct_type 1%).
def sub_type_hls(band_period: int, pct_type: int) -> str:
    return f"p{band_period}_{pct_type}"


# Streak RESOLVE LAG (completed months): see HL_STREAKS_RESOLVE_LAG_
# MONTHS above (defined before DESCRIPTION, which references it). The
# anchor is EX-POST — the mid of a COMPLETED streak — and signal months
# are write-once (never refreshed), so a month is computed only once
# every streak anchored in it is final: a streak is resolved once >
# GAP_TOLERANCE in-band trading rows follow its end (~8 trading days),
# which any month 2+ snapshots later guarantees (the streaks table is
# wholesale-rebuilt with all data on every mov_ave_spread run).
# Belt-and-braces: the engine also drops anchors whose end sits within
# GAP_TOLERANCE + 1 grid rows of the fetched data edge.
#
# In-band re-entry gap tolerance of the streak construction — mirrors
# analyze.mov_ave_spread.config.HIGH_LOW_PCT_GAP_TOLERANCE (re-declared
# to keep the import direction one-way, the forecasts config
# precedent). A streak ends only after a gap of GAP_TOLERANCE + 1
# consecutive in-band trading days or a side switch.
HL_STREAKS_GAP_TOLERANCE = 5

# side → action mapping (same as every family: the above-band excursion
# is a SELL-side extreme, the below-band excursion a BUY-side one — the
# mean-reversion reading the forecasts study measured from the mid
# anchor: below-band streaks drift UP, above-band streaks DOWN).
HL_STREAKS_SIDE_ACTION = {"top": "sell", "bottom": "buy"}

# The opp_pair family (industry opposite-pair trend forecasts, sub_type
# pair{W}) was REMOVED from signal emission (2026-09 strengthening
# study, temp_scripts/study_signal_strength.py): its forecast_results
# carry ~600 trigger days per pair bucket yet a pooled mean forward
# offset change of ~0.000 — the gate confirmed ~98% of target-months
# and emitted 83k rows of coin-flip signals, and the rolling-half OOS
# check showed even statistically-strengthened buckets realize ~0 mean
# confirmation (-0.09% vs -0.62% for current-only). The forecasts keep
# computing analysis_forecasts.opp_pair_state; only the signal family
# is gone.

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

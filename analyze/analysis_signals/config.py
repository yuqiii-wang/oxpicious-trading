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
on the bucket's MIXED forecast_results period row (period 'mixed' —
the FIXED-weight blend of the four horizon rows at the forecasts
config's MIXED_HORIZON_WEIGHTS: 5d 0.50 / next 0.30 / 20d 0.15 /
60d 0.05, materialized so EVERY forecast horizon of the same signal
trigger contributes to the signal; the blended base rate lives on the
matching base_rates 'mixed' row) ALL of:
the mixed row's reverse_prob exceeds GATE_RP_MIN (reverse P > 1% — a
material reversal probability, not a bare > 0 tail), its blended mean
forward change is a REVERSAL (dir_ave > 0 — the bucket's average
outcome reverses, so the signal holds, not just a fat reversal tail),
its reverse_prob BEATS THE BASE RATE (rp >
base_rates.base_down_prob / base_up_prob for top/bottom sides — the
unconditional same-window, same-threshold blended reversal
probability; a bucket at the base rate has no edge, however large the
absolute rp; the conjunct falls back to TRUE when the code has no
base_rates row),
its blended mean forward change BEATS THE BASE DRIFT (dir_ave >
base_ave_change, sign-aligned — the magnitude twin of the probability
lift; a bucket whose average reversal is smaller than the window's own
drift has no mean edge; falls back to TRUE without a base_rates row).
AND the driving-factor confidence (below, WITH the swing factor) must
clear the family's CONF_FLOOR bar — the 2026-09 signal-reduction pass
(study: temp_scripts/study_signal_thresholds.py,
docs/signal_reduction_study.md): the confidence ladder is monotone on
realized forward returns with a zero/negative bottom, so the ≈P30 floor
drops the weak tail (mov_rsi -30% volume at +45% mean edge).
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
           + W_SWING·f_swing
computed on the mixed row (params JSON records conf_period = 'mixed' —
the blended forward profile the confidence speaks about; the OOS study
showed the composite ranks future realized reversals better than rp).
f_swing is the SOFT max/low-ratio factor (2026-09 reduction study: the
bucket's max_low_change_ratio ramps 0→1 over CONF_SWING_LO..CONF_SWING_HI
— realized signal returns rise monotonically with the swing ratio on
BOTH sides; it re-ranks confidence and never removes a signal by
itself, and the pct=1 extreme-day detection base is force-included).

Per-security calibration columns (kept from the 2026-09 gate study —
prior-vs-future mean rp correlation 0.80-0.97; row metadata only,
they do not gate):
  - tier — 'proven' (2) when the code's
    prior mean composite >= CONF_PROVEN (top ~10% of codes by prior
    bucket quality), 'proven_dir' (1) when the code's prior mean
    DIRECTIONAL move >= PROVEN_DIR_AVE else 'standard' (0). Code stats
    need >= PROVEN_MIN_POP prior bucket-periods.
  - code_baseline — the code's prior mean composite (mixed period).
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
# qualifies in the mixed forecast_results period row (next/5d/20d/60d
# weight-blended): that period's reverse_prob must exceed this bar
# (reverse P > 1% — a material reversal probability, not a bare > 0
# tail) AND its mean forward change must be a REVERSAL (dir_ave > 0 —
# the bucket's average outcome reverses, so the signal holds, not just
# a fat reversal tail) AND its reverse_prob must beat the unconditional
# base rate (base_rates.base_down_prob / base_up_prob per side — lift;
# skipped when the code has no base_rates row) AND its mean forward
# change must beat the base drift (the magnitude lift — see the module
# docstring).
#
# REMOVED 2026-09: the sample-size bar (GATE_MIN_OCCURRENCE) and the
# significance bar (GATE_T_STAT_MIN) — the 2026-09 streak-merge leaves
# pct-width buckets with only ~3-6 merged signals (median occurrence
# 3-4 on the mixed row), so the two bars dropped ~96% of the
# strongest-edge pct=1 buckets while the lift conjuncts passed ~96%.
# occ / std_change still feed the confidence's evidence / efficiency
# factors.
GATE_RP_MIN = 0.01

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

# ---- Swing factor (the MAX/LOW-RATIO rule, SOFT — never a gate) -------
#
# 2026-09 signal-reduction study (temp_scripts/study_signal_thresholds.py,
# docs/signal_reduction_study.md): the proposed max/low-ratio rule
# (ratio < 1.1 for buy / > 1.2 for sell) was tested as a factor, NOT a
# deterministic filter. Realized directional forward returns of the
# gate-passed mov_rsi signals by the bucket's max_low_change_ratio (the
# mixed row's widest within-window swing, (1 + max path high) / (1 + min
# path low)) show the direction is monotone UPWARD for BOTH sides —
# buy: <1.05 +0.87% / 1.10-1.15 +2.28% / >1.30 +4.28%; sell: <1.05
# +0.43% / 1.20-1.30 +0.98% / >1.30 +1.71% — so the user's sell bar
# (> 1.2 = better) is confirmed while the buy bar is inverted by the
# data (LOW swing buckets realize the LEAST, not the most). The factor
# therefore ramps 0 → 1 as the ratio rises from CONF_SWING_LO to
# CONF_SWING_HI (the user's 1.1/1.2 bars sit inside the ramp as
# soft mid-anchors, honoring the "factor not threshold" intent):
#
#   f_swing = clamp((max_low_change_ratio - CONF_SWING_LO)
#                   / (CONF_SWING_HI - CONF_SWING_LO), 0, 1)
#
# FORCE-INCLUDE (design guarantee): the swing ratio is a composite
# FACTOR ONLY — it can re-rank confidence but can never remove a
# signal by itself; the pct=1 extreme-day detection base is untouched,
# and no deterministic max/low-ratio cutoff exists anywhere in the
# pipeline. A weak swing ratio lowers the composite (which may then
# miss the family's CONF_FLOOR together with the other factors); it
# never vetoes a day on its own.
CONF_W_SWING = 0.15
CONF_SWING_LO = 1.05
CONF_SWING_HI = 1.20

# NULL max_low_change_ratio (bucket without swing stats) → neutral
# mid-ramp factor value.
CONF_SWING_NEUTRAL_RATIO = 1.125

# Factor weights (sum to 1): evidence and efficiency carry the bulk
# (they are the strongest within-period OOS predictors), consistency a
# fifth, the per-code prior and the swing factor the rest (weights
# re-normalized 2026-09 when the swing factor joined the composite).
CONF_W_EVIDENCE = 0.25
CONF_W_EFFICIENCY = 0.25
CONF_W_CONSISTENCY = 0.20
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

# ---- Per-family CONFIDENCE FLOOR (2026-09 reduction study) --------------
#
# A bucket qualifies only when its composite confidence (WITH the swing
# factor) also clears the family's floor — one more conjunct of the
# forecast-result rule (gate.fetch_confirm). The study's confidence-
# decile ladder is cleanly monotone on realized forward returns with a
# zero/negative bottom: mov_rsi deciles 1/2 realize -1.11% / +0.13% vs
# +2.95..+3.37% at deciles 7-10.
#
# PASS 2 (2026-09-15, "reduce to 1/4"): floors raised to the measured
# knee of each family's realized-return ladder on the pass-1 population
# (the quantile where the mean is still rising — mov_gap peaks at its
# P50 so stays there, the rest sit at P75-P80 of the pass-1 confirmed
# distribution). Combined with the agreement>=3 rule below this targets
# ~1/4 of the pass-1 volume (~115k rows) at roughly double its pooled
# mean (2.05% -> ~3.5%) and ~77% hit.
CONF_FLOOR: dict[str, float] = {
    "mov_rsi": 0.442,
    "mov_std": 0.426,
    "mov_gap": 0.408,
    "px_vol": 0.436,
    "margin_ratio": 0.364,
    "mov_pairs": 0.362,
    "mov_pairs_ema": 0.361,
    "high_low_streaks": 0.510,
}

# ---- Per-metric forecast-to-signal SOFT MAX ratio ----------------------------
#
# Design ceiling (NOT enforced in code): each family's surviving signal
# rows should stay at or below this fraction of its emitted slice's
# forecast stream (occurrence-weighted trigger-days of the slice's
# buckets). It bounds how much of a metric's forecast population may be
# signalled, per metric, while leaving room for genuinely strong slices —
# e.g. the 2.5σ/3.0σ Bollinger tiers sit outside the historical norm but
# carry the family's best reversal edge, so an exact quota would drop
# them. Measured post-rebuild ratios land at ~2-20%; a slice whose ratio
# approaches the ceiling is a signal to TIGHTEN that family's floors, not
# a hard filter.
SIGNAL_MAX_FORECAST_RATIO = 0.25

# ---- Emission filters (2026-09 reduction study) --------------------------
#
# Per-family detection-slice removals backed by realized returns (the
# study's P1/P7 profiles; slices kept only where the directional edge
# is real). These narrow WHAT the engines emit — the forecast buckets
# keep computing every slice.

# mov_rsi: a day must be a top/bottom-1% extreme on >= this many RSI
# windows of the SAME action to emit. Realized mean by agreeing-window
# count: 1w +1.32% / 2w +1.38% / 3w +1.92% / 4w +2.87%. Pass 2 raises
# the bar to 3 (with the P50 floor: 12.2k rows at +4.10% / 79.1% hit —
# the pass-2 measured combo "conf>=p50 & nw>=3"). Forecasts keep the
# single-window buckets; this is a signal-emission confirmation only.
RSI_MIN_WINDOW_AGREEMENT = 3

# mov_rsi sides per sec_type — the index SELL side realized NEGATIVE
# mean directional returns at the short windows (rsi6/10/14/20 index
# sells: -0.12%..-0.37%, hit <50% — index strength carries momentum,
# fading it loses). Index emits buys only; etf / stock keep both sides.
RSI_SIGNAL_SIDES: dict[str, tuple[str, ...]] = {
    "index": ("bottom",),
}

# mov_std: LOWER band only (action=buy). The upper (sell) side realized
# -0.32% (W=20) / -0.46% (W=60) mean at ~52% hit across 80k rows — the
# Bollinger upper breach is continuation, not reversion (the std W=5
# "pure detection noise" precedent, now the upper side at every W).
STD_SIGNAL_SIDES: tuple[str, ...] = ("lower",)

# mov_pairs / mov_pairs_ema: BOTTOM (death cross → buy) only. The cross
# -UP sell side realized +0.05..+0.21% at ~53-54% hit — coin-flip volume
# (33k + 33k rows), while the buy side carries +1.3..+1.8%.
PAIRS_SIGNAL_SIDES: tuple[str, ...] = ("bottom",)

# mov_pairs / mov_pairs_ema detection windows: 120d + 255d crosses only.
# Re-proposed 2026-09-15 from the per-metric semantics study (replacing
# the earlier 60d-only cut): on the emitted bottom/death-cross side the
# 60d cross is the WEAKEST slice (occ-weighted dir_ave +0.33%, gate-pass
# 32.6%) while 120d (+0.49%/36.6%) and 255d (+0.61%/38.0%) carry the
# family's real regime-flip edge; the top side is negative at every
# window. Declared before DESCRIPTION, which references it.
PAIRS_SIGNAL_WINDOWS: tuple[int, ...] = (120, 255)

# ---- Signal ORDER (2026-09 pass-2: best-signals-first) -------------------
#
# The (signal_type, action) ladder ordered by their measured pooled mean
# directional forward return DESCENDING on the pass-2 population
# (temp_scripts/study_signal_thresholds.py + docs/signal_reduction_study.md
# §5b). This is the ORDER BY backbone of the signals table's
# ``signal_order`` column: rows are ranked within their own
# (sec_type, stat_month) pool by this ladder first, then by confidence
# DESC inside a group — so the best signals (per the findings) always
# sort to the front, and the 1/8 trim below keeps exactly that front.
SIGNAL_ORDER: tuple[tuple[str, str], ...] = (
    ("high_low_streaks", "sell"),   # +6.57% / 90.6% hit
    ("mov_rsi", "buy"),             # +4.30% / 79.5% hit
    ("high_low_streaks", "buy"),    # +4.23% / 81.0% hit
    ("mov_rsi", "sell"),            # +2.92% / 72.3% hit
    ("mov_gap", "buy"),             # +2.78% / 67.0% hit
    ("mov_gap", "sell"),            # +2.76% / 71.0% hit
    ("mov_pairs_ema", "buy"),       # +2.72% / 71.3% hit
    ("mov_pairs", "buy"),           # +2.54% / 69.6% hit
    ("px_vol", "sell"),             # +2.23% / 69.1% hit
    ("mov_std", "buy"),             # +2.21% / 65.6% hit
    ("margin_ratio", "buy"),        # +2.04% / 62.9% hit
    ("px_vol", "buy"),              # +1.74% / 61.5% hit
)

# The per-month trim: after ranking, only the top 1/SIGNAL_TOP_FRACTION
# of each (sec_type, stat_month) pool survives (signal_order <=
# max(1, n // SIGNAL_TOP_FRACTION); the rest are deleted by the run that
# wrote the month). 8 → the pass-2 120.4k population trims to ~15k.
SIGNAL_TOP_FRACTION = 8

# ---- Target table -----------------------------------------------------------

TABLE_SIGNALS = "analysis_signals.signals"

ANALYSIS_NAME = "analysis_signals"
DETAIL_NAME = "signals"

DESCRIPTION = (
    "Per-day buy/sell signals (ETF + Index + Stock) mirroring the "
    "analysis_forecasts extreme-day detection at signal granularity: "
    "mov_rsi — rsi_{W}days in the TOP 1% (action=sell) or BOTTOM 1% "
    "(action=buy) of the trailing 5-year window ending at the snapshot "
    "month, W in 6/10/14/20/60 (2026-09 reduction: index emits the buy "
    "side only — index sells realized negative — and a day must be a "
    "1% extreme on at least " + str(RSI_MIN_WINDOW_AGREEMENT) + " "
    "windows of the same side); mov_std — price beyond the per-window-σ "
    "Bollinger band ma_{W} ± k·std_{W}days (lower → buy; the upper sell "
    "side dropped 2026-09 — it realized -0.3..-0.5% mean forward), the "
    "per-metric σ slices W=60 and W=20 at k = 2.0/2.5/3.0σ — see "
    "STD_SIGNAL_K; the σ rides in the sub_type (std{W}_{k:g}) since a "
    "deep-breach day fires every shallower tier of its window (the "
    "forecasts grid keeps computing every window×k); mov_gap — gap_{W}days (the W-day fractional price "
    "return from analysis.mov_ave_rsi) in the TOP 1% (sharp W-day "
    "rally → sell) or BOTTOM 1% (sharp W-day selloff → buy), W in 2/3; "
    "mov_pairs — the CROSS days of the EXISTING relative-MA spreads "
    "ma5_vs_ma{W} (bottom a CROSS DOWN / death cross → action=buy; the "
    "golden-cross sell side dropped 2026-09 — coin-flip edge), W in "
    + str(PAIRS_SIGNAL_WINDOWS) + " (the per-metric slice; the 60d cross "
    "stays forecast-only), cooldown 5; mov_pairs_ema — the EMA sibling on "
    "ema6_vs_ema{W} (buy side only, same rules); "
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
    "gate (forecast-result rule) on the bucket's MIXED forecast "
    "period row (period 'mixed' — analysis_forecasts materializes the "
    "fixed-weight blend of the four horizon rows at 5d 0.50 / next "
    "0.30 / 20d 0.15 / 60d 0.05, so every forecast horizon of the same "
    "signal trigger contributes to the signal): the blended reverse_"
    "prob must exceed "
    + repr(GATE_RP_MIN) + " (reverse P > 1% — a material reversal "
    "probability) AND the blended mean forward change must be a "
    "reversal (dir_ave > 0 — the bucket's average outcome reverses, "
    "so the signal holds, not just a fat reversal tail) AND the "
    "blended reverse_prob must beat the unconditional blended base "
    "rate "
    "(analysis_forecasts.base_rates period='mixed', per side — lift; "
    "the conjunct "
    "falls back to TRUE when the code has no base_rates row) AND the "
    "blended mean forward change must beat the base drift (the "
    "magnitude lift) AND the driving-factor confidence must clear the "
    "family's floor (CONF_FLOOR — ≈ the P30 of the family's confirmed "
    "confidence; the confidence ladder is monotone on realized forward "
    "returns with a zero/negative bottom: mov_rsi deciles 1/2 realize "
    "-1.11% / +0.13% vs ~3% at deciles 7-10). occurrence / t-stat bars "
    "were removed 2026-09 "
    "(the streak-merge leaves pct-width buckets ~3-6 merged signals — "
    "the bars dropped ~96% of the strongest-edge pct=1 buckets); occ "
    "and std_change still feed the confidence's evidence / efficiency "
    "factors. Confidence is a weighted composite of the "
    "underlying driving factors, computed in the signal's direction "
    "(buy → the upward reversal, sell → the downward): evidence "
    "f = t/(t+3) with t = dir_ave·√occ/std_change, efficiency "
    "f = 1-exp(-sharpe/1.2) with sharpe = dir_ave/std_change, "
    "consistency f = 1-exp(-lift_prob/0.5) with lift_prob = reverse_prob"
    " - base_prob, and per-code calibration (the code's prior mean "
    "composite, neutral 0.20 below 10 prior bucket-periods) — weights "
    "0.25/0.25/0.20/0.15, plus the SWING factor "
    "f = clamp((max_low_change_ratio - 1.05)/0.15, 0, 1) at weight "
    + repr(CONF_W_SWING) + " (the 2026-09 max/low-ratio rule as a SOFT "
    "factor, never a deterministic gate — realized signal returns rise "
    "monotonically with the bucket's swing ratio on BOTH sides, so the "
    "proposed '<1.1 for buy' bar is inverted by the data while '>1.2 "
    "for sell' sits in the ramp; the pct=1 extreme-day detection base "
    "is force-included — a weak swing ratio only re-ranks confidence "
    "and can never remove a signal by itself), computed on the mixed "
    "row (params JSON "
    "carries conf_period='mixed' + the full "
    "confidence_factors breakdown). reverse_prob is saturated at long "
    "horizons (0.6-1.0 for 20d/60d) so it no longer IS the confidence; "
    "all composite factors are horizon-free and comparable across "
    "families and sec_types. Each row also carries the "
    "per-security calibration (validated by the gate study: "
    "prior-vs-future mean rp correlation 0.80-0.97): tier ('proven' = "
    f"the code's prior mean composite >= {CONF_PROVEN}; 'proven_dir' = "
    f"prior mean directional move >= {PROVEN_DIR_AVE}; 'standard' "
    f"otherwise; code stats need >= {PROVEN_MIN_POP} prior bucket"
    "-periods), code_baseline (the code's prior mean composite for the "
    "confidence's argmax period) and code_rank (within-code percentile "
    "floor of the confidence). "
    "The 2026-09 signal-reduction pass (study: "
    "temp_scripts/study_signal_thresholds.py, "
    "docs/signal_reduction_study.md) additionally narrows emission to "
    "the edge-backed slices: px_vol only the four study cells "
    "(sharp_up_heavy / slow_up_heavy sell, sharp_dn_normal / "
    "slow_dn_shrink buy — the volume leg confirms the extreme; the "
    "normal-volume up cells and heavy down cells realized ~0 or "
    "negative), margin_ratio only the vlow/low buys (the high/vhigh "
    "crowding sells realized -0.07% at ~52% hit across 90.8k rows), "
    "mov_std lower-only, mov_pairs / mov_pairs_ema buy-only, mov_rsi "
    "index-buy-only + "
    + str(RSI_MIN_WINDOW_AGREEMENT) + "-window same-side agreement, "
    "plus per-family confidence floors set at the measured knee of "
    "each family's realized-return ladder (pass 2, 2026-09-15: ≈ the "
    "P75-P80 of the pass-1 confirmed distribution — mov_gap at its "
    "P50 where the ladder peaks) — across both passes total volume "
    "drops ~87% (896k rows -> ~115k ≈ 1/4 of the pass-1 461k) while "
    "the pooled mean directional forward return roughly triples "
    "(~1.0% -> ~3.5%) at ~77% hit. "
    "Finally every row carries signal_order (INTEGER, 1 = best): the "
    "rank within its own (sec_type, stat_month) pool by the "
    "SIGNAL_ORDER findings ladder (the (signal_type, action) groups by "
    "measured pooled mean directional forward return DESCENDING — "
    "high_low_streaks sell / mov_rsi buy / high_low_streaks buy / ... "
    "— see config), then confidence DESC; the run deletes every row "
    "beyond the top 1/SIGNAL_TOP_FRACTION (1/8) of each month, so "
    "reading signal_order ASC always picks the best signals first. "
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
#
# PER-METRIC (2026-09-15): each family emits its own slice of the
# forecast grid — the percentile families keep the TOP tier (RSI: the
# top/bottom 1% tier is 1 of the forecast grid's 4 pct widths → a 1/4
# forecast-tier-to-signal ratio), the band family narrows to its two
# best (window, σ) configs, the cross families to the 60d cross — and
# each family's forecast-to-signal ratio is measured separately (see
# docs/signal_reduction_study.md §5e).

# mov_rsi: RSI extreme-percentile signals — pct fixed to the top/bottom
# 1% (the top tier of the forecasts' pct grid 1/5/10/25: 1 of 4 tiers).
RSI_PCT = 1

# mov_std: Bollinger-breach signals — PER-WINDOW σ multiples: for each
# window the σ tiers whose breach semantics carry a real snap-back edge
# (occ-weighted mixed-row dir_ave, 2025-01+ study): W=60 2.0/2.5/3.0σ →
# +2.01/+2.53/+3.27% (monotone deeper-is-stronger), W=20 2.0/2.5/3.0σ →
# +1.21/+1.68/+1.92% (deep breaches ≥2.5σ revert strongest). W=5 carries
# no edge at any σ; every upper-side slice stays out (the family is
# lower/buy only). Forecasts keep computing the full window×k grid; only
# this slice signals, with the σ in the sub_type (std{W}_{k:g}) since a
# deep breach day fires every shallower tier of the same window.
STD_SIGNAL_K: dict[int, tuple[float, ...]] = {
    60: (2.0, 2.5, 3.0),
    20: (2.0, 2.5, 3.0),
}

# mov_std SIGNAL windows — derived from STD_SIGNAL_K (W=5 was dropped
# 2026-09: its breaches average only ~1.3 bucket days per (code, month),
# the median bucket never reverses and the bucket dir_ave shows no
# base-rate lift — pure detection noise).
STD_SIGNAL_MA_WINDOWS = tuple(STD_SIGNAL_K)

# mov_gap: gap extreme-percentile signals — pct fixed to the top/bottom 1%
# (the top tier of the forecasts' pct grid, same 1/4 tier ratio as RSI).
GAP_PCT = 1

# Signal sub_type naming: f"rsi{W}" for mov_rsi, f"std{W}_{k:g}"
# for mov_std, f"gap{W}" for mov_gap.
def sub_type_rsi(w: int) -> str:
    return f"rsi{w}"


def sub_type_std(w: int, k: float) -> str:
    # :g matches Postgres float8::text ("2" / "2.5" / "3") — the UI's
    # in_signals EXISTS builds the same string from the bucket's k.
    return f"std{w}_{k:g}"


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

# px_vol signal_sub_type cells — the 2026-09 reduction study keeps only
# the four cells with a real directional edge (the volume leg CONFIRMS
# the extreme: fading volume-backed rallies and buying shrink-backed
# sell-offs works; the "normal"-volume up cells and heavy down cells
# realize ~0 or negative — sharp_up_normal -0.46%, slow_up_normal
# -0.33%, slow_dn_heavy -0.16%, sharp_up_shrink -6.85% on 626 rows).
# Kept (realized mean directional return): sharp_up_heavy sell +0.87% /
# slow_up_heavy sell +1.10% / sharp_dn_normal buy +1.16% /
# slow_dn_shrink buy +1.36%. The flat speed stays un-emitted.
PX_VOL_SIGNAL_CELLS: tuple[tuple[str, str], ...] = (
    ("sharp_up", "heavy"),
    ("slow_up", "heavy"),
    ("sharp_dn", "normal"),
    ("slow_dn", "shrink"),
)

# px_vol signal_sub_type cell list (the study's keep-set; flat excluded).
def px_vol_cells() -> list[tuple[str, str]]:
    return list(PX_VOL_SIGNAL_CELLS)


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


# margin_ratio side → action mapping. Only the LOW-z states signal
# (2026-09 reduction study: the high/vhigh crowding sells realized
# -0.07% mean at 51-52% hit across 90.8k rows — no edge — while the
# vlow/low buys carry +1.1..+2.3%): high/vhigh are no longer emitted.
# no_buy remains un-emitted (an absence state with no threshold) and
# mid is not emitted (the neutral bulk, like px_vol's flat).
MARGIN_RATIO_SIGNAL_STATES = ("vlow", "low")

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

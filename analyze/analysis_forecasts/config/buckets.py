"""Bucket-family parameters (analyze.analysis_forecasts.config)."""
from __future__ import annotations

# ---- Bucket definitions ----------------------------------------------------

# RSI windows — mirrors analysis.mov_ave_rsi (Wilder RSI columns).
RSI_WINDOWS = (3, 6, 10, 14, 20, 60)

# Percentile widths for the RSI extreme buckets (percent).
RSI_PCTS = (1, 5, 10, 25)

# Bucket sides: top = overbought (highest-pct% RSI days),
#               bottom = oversold (lowest-pct% RSI days).
RSI_SIDES = ("top", "bottom")

# MA / sigma windows — mirrors analysis.mov_ave_spreads_detail std_*days
# and stats.*_tech_stats ma{5,20,60,120,255}.
MA_WINDOWS = (5, 20, 60)

# Sigma multiples defining the Bollinger bounds.
STD_MULTIPLES = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0)

# Breach sides: upper = price > ma + k*std, lower = price < ma - k*std.
STD_SIDES = ("upper", "lower")

# ---- Streak-merge signal semantics (2026-09; replaces cooldown) -------
#
# The bucket engines share ONE unified signal pipeline
# (wide.iter_bucket_subsets) with two modes:
#
#   MULTI-DAY STREAK (merge=True — mov_rsi / mov_std /
#   px_vol_state): dates that keep satisfying the bucket condition
#   CONTINUOUSLY are treated as ONE forecast signal with INCREMENTAL
#   anchors (2026-09-21: replaces the single mid anchor): the run emits
#   one trigger at EACH of its first TRIGGER_DELAY_MAX + 1 days (delay
#   0 = the run's first qualifying day, the moment the signal becomes
#   observable — up to delay min(run_len - 1, TRIGGER_DELAY_MAX)).
#   Each (bucket, delay) forecast row's forward stats are conditioned
#   on the signal having lasted that long, so a persistent streak can
#   be checked one day at a time as it live extends. The bucket's MEAN
#   run length is recorded on
#   analysis_forecasts.forecast_identities.streak_signal_days and its
#   mean anchor delay on delayed_signal_days (the result rows'
#   streak_starts / streak_ends / streak_days carry each merged
#   signal's run span). px_vol_state moved onto the streak convention
#   in 2026-09 (its state runs used to admit every qualifying day at
#   a constant streak_signal_days = 1); pe_state / dividend_state
#   joined in the same year's pct refactor (their former z-state
#   cells were one-day signals).
#
#   ONE-DAY (merge=False — mov_pairs / mov_pairs_ema): every qualifying
#   day is its own signal with a 1-day run — a single delay-0 anchor.
#   A cross day's predecessor sits on the other side of zero, so
#   consecutive cross days are mutually exclusive and a streak-merge
#   pass would be a no-op — the engines skip it and
#   streak_signal_days is the 1 constant.
#
# margin_ratio / opp_pair (state families without run semantics) and
# high_low_streaks (its own ex-post streak anchors) keep their bespoke
# loops, all at delay 0. The mov tables' cooldown_days PK column was
# dropped with the 2026-09 migration; the SIGNALS layer keeps its own
# detection-side COOLDOWN (a live trigger cannot know an ongoing
# streak's length ex-post — see analyze.analysis_signals.config).

# The trigger anchors a streak emits: one per day 0..TRIGGER_DELAY_MAX
# while it keeps qualifying (the forecast_results.delay axis; the
# identities' delayed_signal_days cap matches).
TRIGGER_DELAY_MAX = 5

# ---- px_vol_state: recent price-change × trading-amount state buckets -------
#
# Fourth bucket family (see database/sql/analysis/analysis_forecasts/
# 05_px_vol_state.sql + the 2026-09 temp_scripts studies
# study_ma_spread_index_9grid*.py / study_ma_spread_index_sharp_slow.py /
# study_px_vol_state_forecast.py): a day joins a bucket when BOTH its
# σ-standardized price speed AND its z-scored log trading-amount LEVEL
# fall in the named states. Every rolling stat is shifted 1 row (no
# look-ahead) and uses
# the code's OWN trailing moments, so the bars adapt per index
# (σ_ret spans ~1%–2.9% daily across equity-like indices; fixed ±2% /
# 量比 1.5/0.8 bars systematically misfire on high/low-σ codes).
#
# Calibration: k/z bars are chosen to reproduce the legacy fixed
# thresholds' POOLED trigger rates on equity-like indices (up 8.4% /
# down 7.6% / 放量 4.0% / 缩量 14.2%), so bucket sample shares stay
# comparable with the studies. The z bars were calibrated on the
# RETIRED ratio5d metric and carry over to log_level as the starting
# calibration (re-measure the pooled rates after a rebuild).

# Rolling windows (rows): σ_ret of ret_1d + the log-amount level
# moments; PX_VOL_LB_WINDOW is the classic 量比 base window
# (trading_amount mean of t-lb_window..t-1) — evidence-only since the
# log_level refactor. All shifted 1 row before use.
PX_VOL_SIGMA_WINDOW = 255
PX_VOL_SIGMA_MIN_DAYS = 60
PX_VOL_LB_WINDOW = 5

# The vol leg's metric (recorded on every analysis.mov_ave_price_vs_amt
# row as amt_metric and audited by fetch.assert_price_vs_amt_params):
#   "log_level" — px_z = z-scored log(trading_amount) vs the code's own
#                 trailing PX_VOL_SIGMA_WINDOW-row moments (shift 1) —
#                 the LEVEL statement heavy/shrink claim. The retired
#                 "ratio5d" (z-scored 量比 vs its own moments) fired
#                 heavy on drought bounces: in a declining-volume
#                 regime the 5-day base collapses, so a day whose
#                 amount sat far below the code's level scored
#                 ratio ≈ 1.7 → z > 2 ("Amt Up" on visibly low amt).
PX_VOL_AMT_METRIC = "log_level"

# t = ret_1d / σ_ret state bars.
PX_VOL_K_SLOW_UP = 1.26
PX_VOL_K_SLOW_DN = 1.29
PX_VOL_K_SHARP = 2.0

# Amount-level z state bars (px_z).
PX_VOL_Z_HEAVY = 2.0
PX_VOL_Z_SHRINK = -0.92

# σ_ret floor: below this the code is bond-like (σ ≈ 0.01–0.02% for
# 中证短融/企债) and no bucket ever fires — tiny wiggles would be
# classified as extremes.
PX_VOL_SIGMA_FLOOR = 0.005

# Speed-state names in config-axis order (k // 3 = speed, k % 3 = state).
PX_VOL_SPEEDS: tuple[str, ...] = (
    "sharp_up", "slow_up", "flat", "slow_dn", "sharp_dn",
)
PX_VOL_VOL_STATES: tuple[str, ...] = ("heavy", "normal", "shrink")

# Reversal side per speed (mirrors the mov_* side semantics so
# analysis_signals.gate consumes the table unchanged); flat buckets
# carry side='flat' and a NULL reverse_prob (no directional claim).
PX_VOL_SPEED_SIDE: dict[str, str] = {
    "sharp_up": "top", "slow_up": "top",
    "flat": "flat",
    "slow_dn": "bottom", "sharp_dn": "bottom",
}

# Speed / state name → ordinal (the (T, C) state matrices' int encoding,
# see wide.build_px_vol_state_matrices — the scatter of the
# analysis.mov_ave_price_vs_amt registry rows). The -1 sentinel ("no
# valid state that day") is assigned at matrix-build time.
PX_VOL_SPEED_ORD: dict[str, int] = {
    s: i for i, s in enumerate(PX_VOL_SPEEDS)
}
PX_VOL_VOL_ORD: dict[str, int] = {
    v: i for i, v in enumerate(PX_VOL_VOL_STATES)
}

# ---- margin_ratio_state: margin-buy intensity state buckets ----------------
#
# Fifth bucket family (see database/sql/analysis/analysis_forecasts/
# 06_margin_ratio.sql + the 2026-09 temp_scripts/study_margin_ratio_forecast.py,
# docs/margin_ratio_study.md): a day joins a bucket by its 融资买入额/成交额
# ratio (rz_buy / trading_amount, RONGZI only, etf + stock) state vs the
# code's OWN trailing distribution. The 2026-09 study verified the
# Margin Trends hypothesis: the ratio is indicative of short-term future
# volatility (vol5 rank-IC +0.054, 90% of months positive) and trend
# (trend5 IC -0.040, 82% negative) — a CROWDING (contrarian) signal:
# high ratio → weaker forward returns + higher realized vol.
#
# z = (ratio - μ)/σ of the code's rolling Z_WINDOW-row (min
# Z_MIN_PERIODS non-NULL ratio observations) moments, SHIFTED 1 row
# (no look-ahead — px_vol convention). States on buy days (rz_buy > 0,
# trading_amount > 0): vlow z <= -2 / low (-2,-1] / mid (-1,+1] /
# high (+1,+2] / vhigh z > +2; plus no_buy (rz_buy <= 0). Undefined z
# (short history) → no bucket; index has no margin data → no buckets.

# States in z-axis order (no_buy first, then ascending z buckets).
MARGIN_RATIO_STATES: tuple[str, ...] = (
    "no_buy", "vlow", "low", "mid", "high", "vhigh",
)

# Reversal side per state (mirrors the mov_* / px_vol side semantics so
# analysis_signals.gate consumes the table unchanged): high/vhigh are
# the crowding states (side 'top' — reverse_prob = P(the forward
# window's path low < -thr), the study's bearish reading); vlow/low/
# no_buy are the mild-bullish states (side 'bottom'); mid carries
# side='flat' and NULL reverse_prob (no directional claim — the central
# bulk has none).
MARGIN_RATIO_STATE_SIDE: dict[str, str] = {
    "no_buy": "bottom", "vlow": "bottom", "low": "bottom",
    "mid": "flat",
    "high": "top", "vhigh": "top",
}

# Rolling moments of ratio (rows ≈ 5y of trading days; shifted 1 row).
MARGIN_RATIO_Z_WINDOW = 1220
MARGIN_RATIO_Z_MIN_PERIODS = 250

# z state bars (recorded on every row).
MARGIN_RATIO_VLOW_BAR = -2.0
MARGIN_RATIO_LOW_BAR = -1.0
MARGIN_RATIO_HIGH_BAR = 1.0
MARGIN_RATIO_VHIGH_BAR = 2.0

# ---- opp_pair_state: industry opposite-pair trend forecasts -----------------
#
# Sixth bucket family (see database/sql/analysis/analysis_forecasts/
# 07_opp_pair_state.sql): PAIR forecasts over the industry composite
# trends of analysis_composites.industry_corr_benchmark_offsets — by
# industry pair, when ONE industry's (benchmark-offset) trend is
# dropping, the forecast RESULT is the future trend of the OTHER side
# industry. All trend legs live on the benchmark-OFFSET space the
# composites analysis defines: with MA_W the trailing-W-row rolling
# mean of the industry composite mean_close (pool_size 'all') and MA_M
# the benchmark's (000300) MA_W, the W-day offset trend change of
# industry X ending at t is
#     (MA_X[t] − k·MA_M[t]) − (MA_X[t−W] − k·MA_M[t−W])
#     with k = MA_X[t−W] / MA_M[t−W]
# which is exactly 0 at the lookback start, so normalized by the
# industry's own MA level it reduces to the RELATIVE MA RETURN
#     rel_X(t) = MA_X[t]/MA_X[t−W] − MA_M[t]/MA_M[t−W]
# (an industry whose trend grows while the benchmark grows MORE is
# DROPPING after the offset — rel_X < 0). The forward target is the
# same offset math on the other side industry B over [t, t+n]:
#     fwd_B(t,n) = MA_B[t+n]/MA_B[t] − MA_M[t+n]/MA_M[t]

# Bucket trigger industry / sec_type constants. sec_type='index' keeps
# the shared month-gating / gate machinery working (industry_id codes
# are type='index' classification members); the pair universe itself is
# the offsets table's industry pairs, NOT stats.index_identity.
OPP_PAIR_SEC_TYPE = "index"

# Trend windows W (trading-day rows) of the MA curves — the same
# composite-trend smoothing scale as the offsets analysis's short/medium
# windows (255 is a regime filter, too slow for day-level buckets).
OPP_PAIR_TREND_WINDOWS = (20, 60)

# Offset benchmark + pool slice the PAIR SET is read from
# (analysis_composites.industry_corr_benchmark_offsets PK members).
OPP_PAIR_BENCHMARK = "000300"
OPP_PAIR_POOL_SIZE = "all"

# Constant reversal side: the trigger is the dropping (down) state of
# the FIRST industry, so side='bottom' — reverse_prob = P(the OTHER
# side industry's forward offset change > +reverse_threshold), i.e. the
# CONFIRMATION probability of the opposite-pair forecast (B rises when
# A drops), NOT a reversal probability. Mirrors the mov_* side semantics
# so analysis_signals.gate consumes the table unchanged (side bottom →
# action buy on the OTHER side industry).
OPP_PAIR_SIDE = "bottom"

# ---- mov_pairs: MA-pair cross (golden / death cross) event buckets ----------
#
# Seventh bucket family (see database/sql/analysis/analysis_forecasts/
# 08_mov_pairs.sql): CROSS-EVENT buckets built on the EXISTING
# relative-MA-spread columns of analysis.mov_ave_spreads_detail — the
# parent mov_ave_spread analysis's own spread definitions (no new MA
# computation). TWO fast legs share the family (the motivation rows'
# fast_leg column): 'ma5' reads the ma5_vs_ma{W} = (ma5 - ma_{W}) /
# ma_{W} columns, 'price' the price_vs_ma{W} = (price - ma_{W}) / ma_{W}
# columns (the close-price cross). A day joins a bucket when the stored
# spread changes sign that day: side 'top' a CROSS UP / golden cross
# (spread[t] > 0 and spread[t-1] <= 0 — the fast leg rises through the
# slow MA), side 'bottom' a CROSS DOWN / death cross (spread[t] < 0 and
# spread[t-1] >= 0). NULL spreads never trigger (either leg still
# warming up); triggers are ONE-DAY signals (a cross day's predecessor
# sits on the other side of zero, so consecutive cross days are
# mutually exclusive — streak_signal_days is the 1 constant) and
# hype-split exactly like the mov_rsi / mov_std event families.

# Slow MA leg of the pair (trading days) — selects the
# analysis.mov_ave_spreads_detail {fast_leg}_vs_ma{W} column (ma5_vs_ma{W}
# / price_vs_ma{W}). The 5/20 windows exist in the source too but are
# not built (5-vs-20 / price-vs-5 flip too often to be a regime cross;
# widen the tuple to add them).
MOV_PAIRS_WINDOWS = (60, 120, 255)

# (fast_leg, fetched-spread-column prefix) pairs of the family — the
# engine melts each leg's pair_{W} / px_pair_{W} columns under its
# fast_leg label.
MOV_PAIRS_LEGS: tuple[tuple[str, str], ...] = (
    ("ma5", "pair"),
    ("price", "px_pair"),
)

# Cross sides: top = cross up (golden cross, the pair turns bullish),
# bottom = cross down (death cross, the pair turns bearish) — the same
# side semantics the gate and the other mov_* families use.
MOV_PAIRS_SIDES = ("top", "bottom")

# ---- mov_pairs_ema: EMA-pair cross buckets (mov_pairs' EMA sibling) ---------
#
# The identical cross-event machinery on the EXISTING relative-EMA-spread
# columns of analysis.mov_ave_spreads_detail_ema (fast legs 'ema6' on
# ema6_vs_ema{W} = (ema6 - ema_{W}) / ema_{W} and 'price' on
# price_vs_ema{W} = (price - ema_{W}) / ema_{W} — the close-price cross,
# the source table has no ema5). Same table shape, PK, fast_leg column,
# sides and one-day signal semantics as mov_pairs.

# Slow EMA leg of the pair (trading days) — selects the
# analysis.mov_ave_spreads_detail_ema {fast_leg}_vs_ema{W} column
# (ema6_vs_ema{W} / price_vs_ema{W}). The 6/20 windows exist too but are
# not built (same rationale as the MA family).
MOV_PAIRS_EMA_WINDOWS = (60, 120, 255)

# (fast_leg, fetched-spread-column prefix) pairs of the EMA family.
MOV_PAIRS_EMA_LEGS: tuple[tuple[str, str], ...] = (
    ("ema6", "ema_pair"),
    ("price", "px_ema_pair"),
)

# Same cross sides as the MA family.
MOV_PAIRS_EMA_SIDES = MOV_PAIRS_SIDES

# ---- high_low_streaks: MA-Spread High/Low streak mean-mid anchor buckets ----
#
# Ninth bucket family (see database/sql/analysis/analysis_forecasts/
# 10_high_low_streaks.sql + the 2026-09 study
# temp_scripts/study_high_low_streaks_forecast.py): every band-break
# EXCURSION STREAK of analysis.mov_ave_high_low_pct_streaks (the
# mov_ave_spread analysis's own streak table — no streak recomputation;
# run python -m analyze.mov_ave_spread first) is audited at its MEAN-MID
# anchor day. A streak with day_count = n contributes its
# ((n-1)//2 + 1)-th trading day of the span as the ONE trigger day —
# the floor of the MEAN elapsed day (n-1)/2 ("mean mid elapsed day once
# entered a streak"; an 8-day low streak anchors its 4th day). The
# anchor is EX-POST: the streak length is known only after the streak
# closes, so the buckets audit streak-period behaviour (the study shows
# a strong mean-reversion reading: below-band streaks drift UP from the
# mid anchor, above-band streaks drift DOWN — e.g. stock period=255
# pct=1: bottom +5.6% mean fwd5d, top -8.0%), they are NOT a live
# trigger.
#
# side (the streaks table has no side column): the END date is
# out-of-band by construction, so the side is read off the UNROUNDED
# close on end_date vs the end date's OWN month band in
# analysis.mov_ave_high_low_pct — top when close > high_val, bottom
# when close < low_val (the exact test the streaks step used; a tie
# falls to the NEARER band — comparing the stored 2dp-rounded close
# instead is ambiguous for ~36% of ETF rows, whose adjusted prices are
# small enough that 2dp rounding creates ties).

# Band axes mirroring the source table (mov_ave_spread config's
# HIGH_LOW_PCT_PERIODS / HIGH_LOW_PCT_TYPES — re-declared locally to
# keep the import direction one-way, the RSI_WINDOWS precedent).
HIGH_LOW_STREAKS_PERIODS = (60, 120, 255, 500, 750, 1275)
HIGH_LOW_STREAKS_TYPES = (1, 5, 10)

# Streak sides: top = above-band excursion (close[end_date] > the end
# month band's high_val), bottom = below-band (< low_val) — the same
# side semantics the gate and the other families use.
HIGH_LOW_STREAKS_SIDES = ("top", "bottom")

# ---- pe_state / dividend_state: valuation extreme-percentile buckets -------
#
# Tenth and eleventh bucket families (see database/sql/analysis/
# analysis_forecasts/11_pe_state.sql + 12_dividend_state.sql):
# extreme-PERCENTILE buckets over the two valuation series of
# analysis.pe / analysis.dividends, ONE FAMILY PER METRIC (each table
# carries one series and no metric column) — the mov_rsi pct
# convention (2026-09 refactor of the former z-STATE buckets): per
# stat month's trailing 5-year window, a day joins a bucket when its
# series value sits in the top pct% (bucket extreme 'top', the
# window's linearly-interpolated quantile bar at q = 1 - pct/100) or
# bottom pct% (extreme 'bottom', q = pct/100) of the window's non-NULL
# values, per the code's OWN distribution. Only EXTREME days form
# buckets (the central bulk has none — the RSI semantics; the former
# z ladder's mid/flat state is gone).
#
# The families' defining semantics is the OPPOSITE side mapping: PE is
# LOWER-the-better (a high-PE day is an expensive / stretched valuation
# → its top extreme is BEARISH, family side 'top'; the bottom extreme
# cheap → 'bottom'), while the yield is HIGHER-the-better (a high-yield
# day is a cheap, well-supported valuation → its top extreme is BULLISH,
# family side 'bottom'; the bottom extreme → 'top'). The side is
# materialized on every row so the gate / UI read it unchanged.

# Percentile widths for the extreme buckets (percent) — the RSI_PCTS
# grid; one calibration shared by both families.
VAL_PCTS: tuple[int, ...] = (1, 5, 10, 25)

# Bucket extremes of the series (the RSI_SIDES semantics: 'top' = the
# highest-pct% values of the window, 'bottom' = the lowest). The family
# side each extreme carries is the engines' side map (pe: identity;
# dividend: flipped — the mappings above).
VAL_SIDES: tuple[str, ...] = ("top", "bottom")

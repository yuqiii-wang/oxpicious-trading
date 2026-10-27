-- ============================================================================
--  static.docs seed — 'ui.ma-spread' area (the MA-Spread panel's AI-Ask
--  spec + its button-group info popovers).
--
--  Purpose-scoped slice (the sibling 00_ DDL carries the table + the
--  template contract). This file is the AREA'S source of truth: applying
--  it re-syncs the prefix (DELETE ui.ma-spread%, INSERT these rows).
-- ============================================================================

DELETE FROM static.docs WHERE doc_id LIKE 'ui.ma-spread%';

INSERT INTO static.docs (doc_id, title, intro, series, notes, ordinal) VALUES
(
  'ui.ma-spread.page',
  NULL,
  'MA-spread pairs for {{label}}: 9 pairs (5 price/MA + 4 MA5/MA), each a short curve against its long MA — short above long is the momentum regime, below it the pullback regime; all pairs share one date axis so the nine spreads read on the same window. Every curve carries its slope (1st derivative) and curvature (2nd derivative) — the leg''s momentum and its acceleration.',
  NULL, NULL, 0
),
(
  'ui.ma-spread.panel',
  NULL,
  'MA-spread study for {{code}} — pair {{pair}}: the short curve vs the long MA. Short above long is the momentum regime, below it the pullback regime; the spread''s sign flips are the ma-cross reclaim/breakdown events. Overlay families carry the trigger levels: Bollinger ±kσ on the long MA (band-walk = trend persistence, a ±kσ pierce = the fixed-σ trigger; the z already is the Gaussian depth score), rolling roof/floor trendlines and cross-method estimated levels (a deep close past the roof — 1-3%+ — measured a continuation BUY; a deep floor break — 2-5%+ — the capitulation V-bottom BUY; shallow touches carry no edge), break-streak zones (stretched runs past the band edge — fade-prone at extremes), and price-speed × amount-state shading (move quality: amount confirms or discounts the price speed). % mode rebases OHLC + MAs to % change from the first valid close; amount pairs read the trading-amount envelope instead of price.',
  '[
    {"key": "price", "name": "Price", "description": "mid (high+low)/2 — the price reference for the amount envelope"},
    {"key": "amtAbove", "name": "Amt Above", "unit": "亿", "description": "daily trading amount at/above the selected amount MA — participation on the strong side"},
    {"key": "amtBelow", "name": "Amt Below", "unit": "亿", "description": "daily trading amount below the selected amount MA"},
    {"key": "amtMa", "name": "Amt MA{{w}}", "unit": "亿", "description": "{{w}}-day trading-amount MA — the liquidity baseline; liquidity trends lead price"},
    {"key": "shortLeg", "name": "{{name}}", "description": "the pair''s short leg (price or short MA/EMA) — the fast side of the spread"},
    {"key": "longLeg", "name": "{{name}}", "description": "the long moving average — the trend anchor the short leg is measured against"},
    {"key": "rsi", "name": "RSI{{w}}", "description": "{{w}}-day Wilder RSI (0-100, right axis); >70 extended (exit-timing watch — overbought persists under drift-up), <30 washed-out (capitulation watch); the live triggers use per-code trailing percentile tails, not these fixed levels"},
    {"key": "derivSlope", "name": "{{name}}", "description": "{{base}} 1st derivative (daily diff, price units per trading day) — the leg''s momentum; sign flips mark turn attempts"},
    {"key": "derivCurv", "name": "{{name}}", "description": "{{base}} 2nd derivative — the slope''s rate of change (acceleration); extremes precede price turns"},
    {"key": "deltaLow", "name": "Δ Low({{w}}d)", "description": "close − the last floor anchor''s value: positive = price above the floor anchor; crossing below = floor break (washout watch)"},
    {"key": "deltaHigh", "name": "Δ High({{w}}d)", "description": "close − the last roof anchor''s value: negative = price below the roof anchor; crossing above = roof breakout (continuation watch)"},
    {"key": "amtUp", "name": "Amt Up", "unit": "亿", "description": "daily trading amount on up days — the buy-side participation"},
    {"key": "amtDown", "name": "Amt Down", "unit": "亿", "description": "daily trading amount on down days — the sell-side participation"},
    {"key": "bollUpper", "name": "Upper (+{{k}}σ)", "description": "long MA + k×σ Bollinger edge — the fixed-σ upper trigger level"},
    {"key": "bollLower", "name": "Lower (−{{k}}σ)", "description": "long MA − k×σ Bollinger edge — the fixed-σ lower trigger level"},
    {"key": "highW", "name": "High({{w}}d)", "description": "{{w}}d rolling window high — the Donchian roof extreme"},
    {"key": "lowW", "name": "Low({{w}}d)", "description": "{{w}}d rolling window low — the Donchian floor extreme"},
    {"key": "roof", "name": "Roof({{w}}d)", "description": "structural resistance trendline through the window''s top-2 highs"},
    {"key": "floor", "name": "Floor({{w}}d)", "description": "structural support trendline through the window''s top-2 lows"},
    {"key": "estRoof", "name": "Est roof ({{method}})", "description": "cross-method consensus (median of trendline/donchian/quantile/channel/boll) estimated resistance at the anchor date — the rf_break roof trigger; deep closes past it (1-3%+) measured a continuation BUY, shallow touches drift up (no sell)"},
    {"key": "estFloor", "name": "Est floor ({{method}})", "description": "the mirror estimated support — the rf_break floor trigger; deep breaks (2-5%+ below) measured a capitulation V-bottom BUY, the deeper the harder the bounce"},
    {"key": "streakHigh", "name": "High {{label}}", "description": "merged spans of closes beyond the window''s static top band edge — the stretched run (extended streaks are fade-prone)"},
    {"key": "streakLow", "name": "Low {{label}}", "description": "merged spans of closes beyond the window''s static bottom band edge — the washout run"},
    {"key": "pxVol", "name": "{{label}}", "description": "dates matching the price-speed × amount-state regime — move-quality context (amount confirms or discounts the speed)"},
    {"key": "trend", "name": "Trend({{label}})", "description": "the signals tier''s trend spans (analysis.trend_days — MA255-gap extremes + RSI re-entry momentum episodes): the regime context every signal family conditions on"}
  ]'::jsonb,
  '[
    {"key": "streakBridge", "text": "Break streaks bridge ≤5-day in-band gaps against the anchor window''s static band edge."},
    {"key": "shadeFamilies", "text": "Only one shade family is active at a time (trend / RSI percentile / streaks / px-vol / amount days / regimes) — the plotted shading names its single source."}
  ]'::jsonb,
  0
),
(
  'ui.ma-spread.group.pairsSimple',
  'Pairs (Simple MA)',
  'Price or short MA vs long simple MA (n = 5/20/60/120/255; MA_n = mean of the last n closes). Green fill = short above long, red = below; each curve carries its slope (1st derivative) and curvature (2nd derivative). Indication: short > long = momentum-long regime, a cross marks the trend flip, a wide gap = extended — fade-prone.',
  NULL, NULL, 1
),
(
  'ui.ma-spread.group.pairsEma',
  'Pairs (Exponential MA)',
  'The TYPICAL EMA pairs: EMA_t = α·x_t + (1−α)·EMA_{t−1} with α = 2/(n+1), n = 6/12/26 — 6 the A-share short, 12/26 the classic MACD pair (the EMA12/EMA26 spread IS the MACD line). Recent bars weigh more. Indication: EMA crosses fire earlier than the simple-MA ones (faster flip detection) but whipsaw more in ranges.',
  NULL, NULL, 2
),
(
  'ui.ma-spread.group.tradingAmt',
  'Trading Amt/MA',
  'Trading amount vs its own MAs (Amt_n = mean amount over the last n days, 亿): the envelope view splits each day at the selected MA into Amt Above / Amt Below. Indication: expanding amount confirms the move — breakouts on above-MA amount stick; a trend on shrinking amount is losing sponsorship — don''t chase.',
  NULL, NULL, 3
),
(
  'ui.ma-spread.group.rsiTrends',
  'RSI Trends',
  'The Wilder-RSI overlay''s two rows. Days row — the Wilder-RSI window lines (3/6/10/14/20/60d; EWM alpha=1/N, adjust=False, min_periods=N) on the offset right axis; the picked windows'' lines replace the amount bars while active. Pct row — the Top-% history-rank shade from analysis.mov_ave_rsi_ranks: green where the RSI ranked within the top pct/2 of the window''s own history (rises), red where within the bottom pct/2 (drops); per window the half cutoff is rank ≤ ceil(pct/2 × N_w), N_w = the window''s count of ranked days (ranks dense 1..N, rank 1 = the highest reading). The days row scopes the ranks — a single picked window shades its own extremes (rsi3 1% and rsi60 1% differ); every window contributes the SAME base fill and dates where SEVERAL picked windows'' extremes overlap stack the shade darker. Indication: top-rank RSI days mark stretched momentum (fade-prone), bottom-rank days washed-out selling (bounce-prone); deep shade = multi-window confluence.',
  NULL, NULL, 4
),
(
  'ui.ma-spread.group.momentum',
  'Momentum',
  'Lookback-adaptive momentum (rate of change): momentum[n] = price[t]/price[t-n] − 1 per trading day, from analysis.mov_ave_momentum. The FIXED anchors are 5d (one trading week) and 20d (one trading month); the ADAPTIVE short/mid/long terms pick their lookback n SELF-ADAPTIVELY BY VOLATILITY per (code, market regime): n_term = clip(round((target_move/σ)²), lo, hi) with σ = the median EWMA daily vol (sqrt EWMA(ret_1d², span=20)) over the code''s days in that regime — equal-expected-move targeting, so elevated-vol regimes (hot/panic) shorten the lookback toward the 5/20/60 anchors while calm regimes stretch it (a calm index carries ~6/25/100d). Targets: short 3% clip [5,60], mid 6% [20,120], long 12% [60,255]; ordering enforced. Indication: positive adaptive momentum = the regime-appropriate trend is up; short term flipping sign ahead of the long = momentum divergence — trend rotation.',
  NULL, NULL, 5
),
(
  'ui.ma-spread.group.roofFloorWindow',
  'Roof/Floor Trendlines',
  'Roof/floor trendlines over the trailing w trading days (w = 60…1275; the 20d is not a roof/floor window — its top + 2nd extrema almost coincide, degenerating the lines): the High_w / Low_w channel bounds each date (High_w = max High, Low_w = min Low within the window); the roof line runs through the window''s top + 2nd highs and the floor line through its top + 2nd lows, stopping at the chosen anchor date. The Est-levels view adds the QUANTITATIVE estimate of the roof/floor levels at that date (analysis.mov_ave_spreads_roof_floor_estimates, per method: trendline = the drawn anchor line extrapolated, donchian = the window''s max-high/min-low, quantile = the rolling 90th/10th close percentile, channel = OLS fit ± 2σ, boll = SMA ± 2σ, median = the consensus of the five). The rebound audit (analysis.mov_ave_spreads_roof_floor_rebounds) reports, per side at the selected window: how many recorded extremes the code has, how many REBOUNDED ≥5% within 60d and by how much, and how the level fared on retest (held vs broken, bounce/depth amplitudes, holds later broken); only events before the chosen date whose level sits within ±5% of their side''s median event level are the typical-level cohort. With the window active the amount bars yield to the change-since-last-anchor plot: Δ Low(Wd) = close − the LAST low anchor''s value (change_since_last_low), positive = price above the floor anchor; Δ High(Wd) = close − the LAST high anchor''s value (change_since_last_high), negative = price below the roof anchor; a bar crossing zero = price beyond that side''s anchor (breakout). Indication: a close outside the channel = breakout; a falling roof meeting a rising floor = range compression coiling before a directional move.',
  NULL, NULL, 6
),
(
  'ui.ma-spread.group.highLowStreaks',
  'High/Low Streaks',
  'Over the trailing w rows (w = 60…1275) the band holds daily lows above their p-th percentile (low_val) and highs below their (100−p)-th (high_val), p = 1/5/10; a streak = maximal run of closes outside the band, in-band gaps ≤5 days bridged. Indication: a long streak = stretched — fade-prone; high streaks that keep extending = genuine trend; low-side streaks tend to exhaust into capitulation lows.',
  NULL, NULL, 7
),
(
  'ui.ma-spread.group.pxVolStates',
  'Px-Vol States (Price × Amt)',
  'Per-day price speed × amount state: t = 1-day return ÷ trailing 255-day σ_ret (sharp |t| ≥ 2.0, slow ≥ 1.26); amount z-scored log level vs its trailing year (increasing ≥ +2.0, decreasing ≤ −0.92). One speed pick × one amount pick — matching dates shade by the combo. Indication: sharp × heavy (darkest) = conviction growth; rises on fading amount lack sponsorship; sharp drop + heavy = panic that marks capitulation lows.',
  NULL, NULL, 8
),
(
  'ui.ma-spread.group.ohlcGaps',
  'Top-10% OHLC Days',
  'The day-shape extremes of the code''s own OHLC history — the two shapes the 2026-09 studies measured on the index universe:
spike fade (冲高回落) — the day''s UPPER wick (high − max(open, close), prev-close normalized) lands in its trailing-252d TOP DECILE: the spike that failed to hold.
flush recover (探底回升) — the LOWER wick (min(open, close) − low) lands in its trailing-252d top decile: the flush that was bought back.
Flags need ≥120 trailing observations; data comes from analysis.mov_ave_spreads_detail_ohlc_gaps (estimated rows are excluded at build time). A day can carry both shapes.
Indication: on the index sample the spike-fade day carried the strongest 5d drift of any pattern (+53bp vs the +9bp base) and the flush-recover day a short next-day dip — both feed the forecasts OHLC-boost weights, so shaded days are the ones the boost actually re-weights.',
  NULL, NULL, 9
),
(
  'ui.ma-spread.group.marketRegimes',
  'Market Regimes (vol × amt)',
  'Market-wide regime spans from vol_z / amt_z vs each one''s trailing 255 days (bars ±1.0σ): calm = quiet stretch, hot = elevated vol + volume expansion, panic = elevated vol without the volume, quiet = volume expansion without price movement. Indication: momentum / breakout tactics pay in hot spans; panic argues de-risking; quiet flags accumulation or distribution awaiting price follow-through.',
  NULL, NULL, 10
),
(
  'ui.ma-spread.group.trendBands',
  'Trend (signals tier)',
  'The code''s trend phases, from the SIGNALS TIER''s per-(code, date) classification (analysis.trend_days via the trend-spans API — the RSI re-entry classifier the rule cells split by; replaced the retired client-side close-vs-MA60 classifier 2026-10-15, whose crash-inflated MA60 kept labeling post-crash recoveries down for months): rise/fall = Wilder RSI(10) momentum episodes (fixed 50±12.5 zones, ≤5-row re-entry absorb, the Kaufman 10-row net-change sign as the direction guard; a deep RSI ≤30/≥70 anchor opens a 40-day window in which the midline re-cross with the guard flips the trend — the post-crash rebound fix) plus the 2026-10-21 TRAVEL CLAIM: a MA255-gap-extreme day whose leg has traveled ≥4 average daily moves from the prior 60-row high/low with the guard agreeing claims rise/fall directly — a large fluctuation in progress reads directional even while the RSI zone sits weeks away; top/bottom = the STATIONED gap extremes vs their own trailing q90/q10 (small fluctuation parked at the band edge); unknown = neither episode. Indication: rise spans = the momentum-long regime (ride them); fall = stand aside / fade weak bounces; top/bottom = the stationed extremes of either side.',
  NULL, NULL, 11
);

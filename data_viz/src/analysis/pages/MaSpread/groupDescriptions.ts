/**
 * Description texts for the MA-Spread panel's button-group titles.
 *
 * Every button-group row in MaSpreadPanel (the two pair sections, Trading
 * Amt/MA, Roof/Floor Trendlines, High/Low Streaks, Px-Vol States, Market
 * Regimes)
 * shows a small info mark next to its title; clicking it opens a popover
 * with this text. The SectionLabel component renders both the title and the
 * popover — the panel only passes the group id, so all wording lives here.
 *
 * Text style mirrors the AI Ask intro: a concise definition with the math
 * spelled out (windows, thresholds, formulas — same symbols the captions
 * use), closing with an "Indication:" clause that maps the read to trading.
 */

/** One button group's visible title + its popover description. */
export interface GroupInfo {
  /** The row's title as shown on the panel (without dynamic suffixes). */
  title: string;
  /** Longer explanation shown in the click-to-open description popover. */
  description: string;
}

/** Union of every MA-Spread button-group id (one entry per title row). */
export type MaSpreadGroupId =
  | "pairsSimple"
  | "pairsEma"
  | "tradingAmt"
  | "rsiTrends"
  | "momentum"
  | "roofFloorWindow"
  | "highLowStreaks"
  | "pxVolStates"
  | "ohlcGaps"
  | "marketRegimes"
  | "trendBands";

export const MA_SPREAD_GROUP_INFO: Record<MaSpreadGroupId, GroupInfo> = {
  pairsSimple: {
    title: "Pairs (Simple MA)",
    description:
      "Price or short MA vs long simple MA (n = 5/20/60/120/255; MA_n = mean " +
      "of the last n closes). Green fill = short above long, red = below; " +
      "tooltips add each curve's slope (1st derivative) and curvature (2nd " +
      "derivative). Indication: short > long = momentum-long regime, a cross " +
      "marks the trend flip, a wide gap = extended — fade-prone.",
  },
  pairsEma: {
    title: "Pairs (Exponential MA)",
    description:
      "Same pairs on exponential MAs: EMA_t = α·x_t + (1−α)·EMA_{t−1} with " +
      "α = 2/(n+1), n = 6/20/60/120/255 (EMA6 replaces MA5) — recent bars " +
      "weigh more. Indication: EMA crosses fire earlier than the simple-MA " +
      "ones (faster flip detection) but whipsaw more in ranges.",
  },
  tradingAmt: {
    title: "Trading Amt/MA",
    description:
      "Trading amount vs its own MAs (Amt_n = mean amount over the last n " +
      "days, 亿): the envelope view splits each day at the selected MA into " +
      "Amt Above / Amt Below. Indication: expanding amount confirms the move " +
      "— breakouts on above-MA amount stick; a trend on shrinking amount is " +
      "losing sponsorship — don't chase.",
  },
  rsiTrends: {
    title: "RSI Trends",
    description:
      "The Wilder-RSI overlay's two rows; the section rests fully OFF. " +
      "Days row — multi-pick the Wilder-RSI window lines (3/6/10/14/20/60d; " +
      "EWM alpha=1/N, adjust=False, min_periods=N) drawn on the offset " +
      "right axis (0-400 so the RSI 0-100 hugs the bottom quarter, dashed " +
      "70/30 guides); any pick replaces the trading-amt bars while the " +
      "header toggle sits at Amt (Slopes/Curvatures override), and the " +
      "FIRST pick auto-arms the 1% shade (un-picking the last day takes " +
      "the shade off). Pct row — the Top-% history-rank shade from " +
      "analysis.mov_ave_rsi_ranks, split evenly across the rank axis " +
      "treated as signed: green where the RSI ranked within the top pct/2 " +
      "of the window's own history (rises), red where within the bottom " +
      "pct/2 (drops); per window the half cutoff is rank ≤ ceil(pct/2 × " +
      "N_w), N_w = the window's count of ranked days (ranks dense 1..N, " +
      "rank 1 = the highest reading); clicking the active pct again " +
      "clears it. The days row scopes the ranks — a single picked window " +
      "shades its own extremes (rsi3 1% and rsi60 1% differ); every " +
      "window contributes the SAME base fill and dates where SEVERAL " +
      "picked windows' extremes overlap stack the shade darker. " +
      "Indication: top-rank RSI days mark stretched momentum (fade-prone), " +
      "bottom-rank days washed-out selling (bounce-prone); deep shade = " +
      "multi-window confluence; the tooltip's RSI row shows all six " +
      "windows regardless of the days pick.",
  },
  momentum: {
    title: "Momentum",
    description:
      "Lookback-adaptive momentum (rate of change): momentum[n] = " +
      "price[t]/price[t-n] − 1 per trading day, from " +
      "analysis.mov_ave_momentum. Two buttons pick the FIXED anchors " +
      "(5d = one trading week, 20d = one trading month); three pick the " +
      "ADAPTIVE short/mid/long terms whose lookback n is chosen " +
      "SELF-ADAPTIVELY BY VOLATILITY per (code, market regime): " +
      "n_term = clip(round((target_move/σ)²), lo, hi) with σ = the " +
      "median EWMA daily vol (sqrt EWMA(ret_1d², span=20), the same vol " +
      "the regime classification z-scores) over the code's days in that " +
      "regime — equal-expected-move targeting, so elevated-vol regimes " +
      "(hot/panic) shorten the lookback toward the 5/20/60 anchors while " +
      "calm regimes stretch it (a calm index carries ~6/25/100d). " +
      "Targets: short 3% clip [5,60], mid 6% [20,120], long 12% " +
      "[60,255]; ordering enforced. Multi-pick draws the picked terms' " +
      "lines on the offset right axis (band-mapped signed ratios, zero " +
      "guide; labels in %), replacing the amt bars while the header " +
      "toggle sits at Amt (RSI Trends/Slopes/Curvatures override). The " +
      "caption shows the LATEST day's effective lookbacks + regime; the " +
      "tooltip spells each adaptive term's per-date lookback " +
      "(short·14d). Indication: positive adaptive momentum = the " +
      "regime-appropriate trend is up; short term flipping sign ahead " +
      "of the long = momentum divergence — trend rotation.",
  },
  roofFloorWindow: {
    title: "Roof/Floor Trendlines",
    description:
      "Roof/floor trendlines over the trailing w trading days (w = 60…1275; " +
      "the 20d is not a roof/floor window — its top + 2nd extrema almost " +
      "coincide, degenerating the lines): the dashed High_w / Low_w channel " +
      "bounds each date " +
      "(High_w = max High, Low_w = min Low within the window); clicking a " +
      "chart date draws the roof line through the window's top + 2nd highs " +
      "and the floor line through its top + 2nd lows, stopping at that " +
      "date (latest by default; click again to clear). " +
      "The Est-levels toggle adds the QUANTITATIVE estimate of the " +
      "roof/floor levels at the chosen date (analysis.mov_ave_spreads_" +
      "roof_floor_estimates, per method: trendline = the drawn anchor " +
      "line extrapolated, donchian = the window's max-high/min-low, " +
      "quantile = the rolling 90th/10th close percentile, channel = " +
      "OLS fit ± 2σ, boll = SMA ± 2σ, median = the consensus of the " +
      "five) as TWO horizontal dashed lines; the method chips pick " +
      "which method's levels draw. The rebound-audit captions (loaded " +
      "with the first window pick, from analysis.mov_ave_spreads_roof_" +
      "floor_rebounds) report, per side at the selected window: how " +
      "many recorded extremes the code has, how many REBOUNDED ≥5% " +
      "within 60d and by how much, and how the level fared on retest " +
      "(held vs broken, bounce/depth amplitudes, holds later broken). " +
      "The same events draw on the chart as LARGER triangle markers at " +
      "the qualifying rebounded recorded extremes (roof-orange up / " +
      "floor-blue down): only events BEFORE the chosen date (the " +
      "clicked anchor date, latest by default) whose level sits within " +
      "±5% of their side's median event level (the median roof / " +
      "median floor of that cohort) draw — events after the chosen " +
      "date and at far-from-typical levels stay off the chart. " +
      "Clicking a window button is EXCLUSIVE: every other section's " +
      "picks clear (RSI Trends, High/Low Streaks, Px-Vol States, Top-10% " +
      "OHLC Days, Market Regimes, Trend), the header toggle returns to " +
      "Amt, and " +
      "an Amt/MA envelope view falls back to Price/MA5 — while the " +
      "trading-amt bars yield the right axis to the enabled window's " +
      "change-since-last-anchor plot: Δ Low(Wd) = close − the LAST low " +
      "anchor's value (change_since_last_low), floor-blue bars ABOVE the " +
      "dashed zero central line, positive = price above the floor " +
      "anchor; Δ High(Wd) = close − the LAST high anchor's value " +
      "(change_since_last_high), roof-orange bars BELOW the line, " +
      "negative = price below the roof anchor; a bar crossing the " +
      "central line = price beyond that side's anchor (breakout). RSI " +
      "Trends / Slopes / Curvatures picks made afterwards take the " +
      "right-axis slot back. " +
      "Indication: a close outside the " +
      "channel = breakout; a falling roof meeting a rising floor = range " +
      "compression coiling before a directional move.",
  },
  highLowStreaks: {
    title: "High/Low Streaks",
    description:
      "Over the trailing w rows (w = 60…1275) the band holds daily lows " +
      "above their p-th percentile (low_val) and highs below their (100−p)-th " +
      "(high_val), p = 1/5/10; a streak = maximal run of closes outside the " +
      "band, in-band gaps ≤5 days bridged. Indication: a long streak = " +
      "stretched — fade-prone; high streaks that keep extending = genuine " +
      "trend; low-side streaks tend to exhaust into capitulation lows.",
  },
  pxVolStates: {
    title: "Px-Vol States (Price × Amt)",
    description:
      "Per-day price speed × amount state: t = 1-day return ÷ trailing " +
      "255-day σ_ret (sharp |t| ≥ 2.0, slow ≥ 1.26); amount z-scored log " +
      "level vs its trailing year (increasing ≥ +2.0, decreasing ≤ −0.92). " +
      "Pick one of each — matching dates shade green rise / red drop / gray " +
      "flat, depth = combo strength. Indication: sharp × heavy (darkest) = " +
      "conviction growth; rises on fading amount lack sponsorship; sharp " +
      "drop + heavy = panic that marks capitulation lows.",
  },
  ohlcGaps: {
    title: "Top-10% OHLC Days",
    description:
      "The day-shape extremes of the code's own OHLC history — the two " +
      "shapes the 2026-09 studies measured on the index universe:\n" +
      "spike fade (冲高回落) — the day's UPPER wick " +
      "(high − max(open, close), prev-close normalized) lands in its " +
      "trailing-252d TOP DECILE: the spike that failed to hold.\n" +
      "flush recover (探底回升) — the LOWER wick " +
      "(min(open, close) − low) lands in its trailing-252d top " +
      "decile: the flush that was bought back.\n" +
      "Flags need ≥120 trailing observations; data comes from " +
      "analysis.mov_ave_spreads_detail_ohlc_gaps (estimated rows are " +
      "excluded at build time).\n" +
      "Toggles are independent and a day can carry both shapes; the " +
      "chart shades spike-fade days light red and flush-recover days " +
      "light green, and the tooltip shows the day's gap / intra / wick legs.\n" +
      "Indication: on the index sample the spike-fade day carried the " +
      "strongest 5d drift of any pattern (+53bp vs the +9bp base) and " +
      "the flush-recover day a short next-day dip — both feed the " +
      "forecasts OHLC-boost weights, so shaded days are the ones the " +
      "boost actually re-weights.",
  },
  marketRegimes: {
    title: "Market Regimes (vol × amt)",
    description:
      "Market-wide regime spans from vol_z / amt_z vs each one's trailing " +
      "255 days (bars ±1.0σ): calm = quiet stretch, hot = elevated vol + " +
      "volume expansion, panic = elevated vol without the volume, quiet = " +
      "volume expansion without price movement; picked regimes shade their " +
      "spans. Indication: momentum / breakout tactics pay in hot spans; " +
      "panic argues de-risking; quiet flags accumulation or distribution " +
      "awaiting price follow-through.",
  },
  trendBands: {
    title: "Trend (signals tier)",
    description:
      "The code's trend phases, from the SIGNALS TIER's per-(code, date) " +
      "classification (analysis.trend_days via the trend-spans API — the " +
      "RSI re-entry classifier the rule cells split by; replaced the " +
      "retired client-side close-vs-MA60 classifier 2026-10-15, whose " +
      "crash-inflated MA60 kept labeling post-crash recoveries down for " +
      "months): rise/fall = Wilder RSI(14) momentum episodes (fixed " +
      "50±7.5 zones, ≤5-row re-entry absorb, MA20-slope agreement; a " +
      "deep RSI ≤30/≥70 anchor opens a 40-day window in which the " +
      "midline re-cross flips the trend — the post-crash rebound fix); " +
      "top/bottom = the price-vs-MA255 gap extremes vs their own " +
      "trailing q90/q10; unknown = neither episode (the unshaded " +
      "background). Multi-pick shades each picked phase's date spans " +
      "(light red top / green bottom / blue rise / amber fall). The " +
      "shade families share the panel's ONE shade slot: activating any " +
      "shade row (Trend, RSI pct, High/Low Streaks, Px-Vol, Top-10% " +
      "OHLC Days, Market Regimes) clears the other rows' picks, so only " +
      "one family's shades draw at a time. Indication: rise spans = the " +
      "momentum-long regime (ride them); fall = stand aside / fade weak " +
      "bounces; top/bottom = the overextended extremes of either side.",
  },
};

/**
 * Description texts for the MA-Spread panel's button-group titles.
 *
 * Every button-group row in MaSpreadPanel (the two pair sections, Trading
 * Amt/MA, OHLC Window, High/Low Streaks, Px-Vol States, Market Regimes)
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
  | "ohlcWindow"
  | "highLowStreaks"
  | "pxVolStates"
  | "marketRegimes";

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
  ohlcWindow: {
    title: "OHLC Window",
    description:
      "Rolling channel over the trailing w trading days (w = 20…1275): " +
      "High_w = max High, Low_w = min Low; a chart-date click draws the " +
      "roof/floor lines through the window's top + 2nd highs / top + 2nd " +
      "lows, stopping at that date. Indication: a close outside the channel " +
      "= breakout; a falling roof meeting a rising floor = range compression " +
      "coiling before a directional move.",
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
};

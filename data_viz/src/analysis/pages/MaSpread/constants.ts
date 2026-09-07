/**
 * Shared constants for the MA-Spread analysis page sub-modules.
 */

/**
 * Page size — number of MaSpreadPanel cards shown per page. Kept small
 * because each panel renders one chart with a date-range slider and a row
 * of 9 pair chips, so larger pages get unwieldy.
 */
export const PAGE_SIZE = 1;

/**
 * Rolling-OHLC window buttons (trading days) shown beneath the Trading
 * Amt/MA pair row. Mirrors OHLC_WINDOWS in analyze/mov_ave_spread/config.py
 * and the analysis.mov_ave_spreads_detail_ohlc column families — clicking a
 * button enables that window's rolling High/Low envelope and arms the
 * roof/floor trendline interaction (click a date on the chart to draw).
 */
export const OHLC_WINDOWS = [20, 60, 120, 255, 500, 750, 1275] as const;

/**
 * High/low band-BREAK streak lookback buttons (trading rows) — the first
 * layer of the nested High/Low Streaks row. Mirrors
 * HIGH_LOW_PCT_PERIODS in analyze/mov_ave_spread/config.py and the
 * analysis.mov_ave_high_low_pct period values.
 */
export const HIGH_LOW_STREAK_PERIODS = [255, 500, 750, 1275] as const;

/**
 * High/low band-BREAK streak tightness buttons (percent) — the second
 * layer, expanded when a period is selected. pct_type p of the band keeps
 * daily LOWs above their p-th percentile (low_val) and daily HIGHs below
 * their (100-p)-th percentile (high_val); a streak is a maximal run of
 * closes outside the band (up to 5 in-band days bridged).
 */
export const HIGH_LOW_STREAK_PCTS = [1, 5, 10] as const;
